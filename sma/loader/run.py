"""Точка входа и планировщик загрузчика (раздел 8.7).

    python -m sma.loader.run                           по расписанию, пока не остановят
    python -m sma.loader.run --once                    один цикл по всем адресам и выход
    python -m sma.loader.run --platform OGE            только одна платформа
    python -m sma.loader.run --once --data-root data/generated/medium
                                                       другой набор для источников file
    python -m sma.loader.run --api-root http://localhost:8000
                                                       другой адрес для источников api

Один поток на платформу; внутри платформы адреса по очереди, у каждого свой интервал
(intervals_sec) и не чаще раза в min_gap_per_endpoint_sec (NFR-04). Ctrl+C или SIGTERM —
загрузчик дожидается конца текущей страницы и закрывает соединения. После перезапуска
он продолжает с водяных знаков: ничего не теряется и не дублируется (NFR-14).

Загрузчик подключается к базе под ролью sma_loader (пароль — SMA_LOADER_PASSWORD).
"""

import argparse
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import psycopg
import yaml
from dotenv import load_dotenv

from sma.db import connect
from sma.loader import store
from sma.loader.cycle import (
    ALL_ENDPOINTS,
    FACT_ENDPOINTS,
    FULL_DICTIONARIES,
    KIM_SCALES,
    LIVE_STATUS,
    EndpointResult,
    LoaderSettings,
    load_endpoint,
)
from sma.loader.sources import ApiSource, FileSource, Source
from sma.timeutil import parse_datetime

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = "config/platforms.yaml"
RECONNECT_PAUSE_SEC = 5
MART_REFRESH_SEC = 30  # витрины — не чаще раза в 30 с и только если были новые данные


# --- Настройки ---------------------------------------------------------------------------


def load_config(path: Path) -> dict:
    """Читает config/platforms.yaml."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def settings_from(config: dict) -> LoaderSettings:
    return LoaderSettings(
        since_overlap_sec=config["since_overlap_sec"],
        initial_since=parse_datetime(config["initial_since"]),
    )


def endpoint_intervals(config: dict) -> dict[str, float]:
    """Интервал опроса каждого адреса в секундах, не меньше min_gap_per_endpoint_sec."""
    groups = config["intervals_sec"]
    gap = config["min_gap_per_endpoint_sec"]
    intervals = {}
    for endpoint in FACT_ENDPOINTS:
        intervals[endpoint] = groups["facts"]
    for endpoint in KIM_SCALES:
        intervals[endpoint] = groups["kim_scales"]
    for endpoint in FULL_DICTIONARIES:
        intervals[endpoint] = groups["dictionaries"]
    intervals[LIVE_STATUS] = groups["live_status"]
    return {endpoint: max(float(seconds), float(gap)) for endpoint, seconds in intervals.items()}


def enabled_platforms(config: dict, platform_filter: str | None) -> list[dict]:
    platforms = [p for p in config["platforms"] if p.get("enabled", True)]
    if platform_filter:
        platforms = [p for p in platforms if p["exam_type"] == platform_filter]
        if not platforms:
            raise SystemExit(f"Платформа {platform_filter} не найдена или выключена")
    return platforms


def make_source(
    platform: dict, config: dict, data_root: Path | None = None, api_root: str | None = None
) -> Source:
    """Источник платформы по конфигу.

    data_root заменяет папку набора для file (<data_root>/<exam>),
    api_root — адрес для api (<api_root>/<exam>).
    """
    exam = platform["exam_type"].lower()
    if platform["source"] == "file":
        folder = Path(data_root) / exam if data_root else Path(platform["path"])
        return FileSource(folder)
    if platform["source"] == "api":
        base_url = f"{api_root.rstrip('/')}/{exam}" if api_root else platform["base_url"]
        token = os.environ.get(platform["token_env"], "")
        if not token:
            raise SystemExit(f"Не задан токен {platform['token_env']} в .env")
        return ApiSource(base_url, token, timeout_sec=config["request_timeout_sec"])
    raise SystemExit(f"Неизвестный источник {platform['source']!r} в config/platforms.yaml")


def loader_connection() -> psycopg.Connection:
    """Соединение под ролью sma_loader."""
    return connect(user="sma_loader", password=os.environ.get("SMA_LOADER_PASSWORD", ""))


# --- Планировщик -------------------------------------------------------------------------


class Clock:
    """Настоящее время. В тестах его заменяет «быстрые часы»."""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float, stop: threading.Event) -> None:
        """Ждать seconds секунд или до сигнала остановки — что раньше."""
        stop.wait(seconds)


LoadFn = Callable[..., EndpointResult]


@dataclass
class PlatformJob:
    """Все, что нужно потоку одной платформы."""

    platform: str
    source: Source
    intervals: dict[str, float]
    settings: LoaderSettings
    retry_sec: float = 10  # после ошибки — повтор через min_gap, а не через весь интервал


def run_platform(
    job: PlatformJob,
    conn: psycopg.Connection,
    stop: threading.Event,
    clock: Clock | None = None,
    load_fn: LoadFn = load_endpoint,
    reconnect: Callable[[], psycopg.Connection] | None = None,
    changed: threading.Event | None = None,
) -> None:
    """Опрашивает адреса одной платформы по расписанию, пока не выставлен stop.

    Все адреса сначала опрашиваются сразу, затем каждый — через свой интервал после
    начала предыдущего запроса (после ошибки — через retry_sec). Страницы одного цикла
    идут подряд. Если сохранена хоть одна запись — выставляется changed (витрины
    нужно обновить).
    """
    clock = clock or Clock()
    next_run = {endpoint: clock.now() for endpoint in ALL_ENDPOINTS}
    while not stop.is_set():
        for endpoint in ALL_ENDPOINTS:
            if stop.is_set() or next_run[endpoint] > clock.now():
                continue
            started = clock.now()
            failed = True
            try:
                result = load_fn(
                    conn, job.source, job.platform, endpoint, job.settings, stop.is_set
                )
                failed = result.status == "error"
                if changed is not None and result.saved > 0:
                    changed.set()
            except psycopg.Error as problem:
                logger.error("%s %s: база недоступна — %s", job.platform, endpoint, problem)
                if reconnect is None:
                    raise
                conn = reopen(conn, reconnect, stop, clock)
            pause = job.retry_sec if failed else job.intervals[endpoint]
            next_run[endpoint] = started + min(pause, job.intervals[endpoint])
        wait = min(next_run.values()) - clock.now()
        if wait > 0 and not stop.is_set():
            clock.sleep(wait, stop)


def reopen(
    conn: psycopg.Connection,
    reconnect: Callable[[], psycopg.Connection],
    stop: threading.Event,
    clock: Clock,
) -> psycopg.Connection:
    """Закрыть сломанное соединение и открывать новое, пока не получится (или не остановят)."""
    try:
        conn.close()
    except psycopg.Error:
        pass
    while not stop.is_set():
        try:
            return reconnect()
        except psycopg.OperationalError as problem:
            logger.error(
                "Нет соединения с базой, повтор через %d с: %s", RECONNECT_PAUSE_SEC, problem
            )
            clock.sleep(RECONNECT_PAUSE_SEC, stop)
    return conn


def platform_worker(job: PlatformJob, stop: threading.Event, changed: threading.Event) -> None:
    """Поток одной платформы: свое соединение с базой на все время работы."""
    conn = loader_connection()
    try:
        run_platform(job, conn, stop, reconnect=loader_connection, changed=changed)
    except Exception:
        logger.exception("%s: поток загрузчика упал", job.platform)
        stop.set()
        raise
    finally:
        conn.close()
        logger.info("%s: поток загрузчика остановлен", job.platform)


class MartRefresher:
    """Обновляет витрины, если с прошлого раза загружены новые данные.

    Потоки платформ независимы, поэтому «после полного цикла по всем платформам» означает:
    раз в MART_REFRESH_SEC, если хоть один поток что-то сохранил. Задержка до витрины —
    интервал опроса (60 с) + MART_REFRESH_SEC + время обновления — не больше 120 с (NFR-01).
    """

    def __init__(self, changed: threading.Event):
        self.changed = changed
        self.conn: psycopg.Connection | None = None
        self.next_at = time.monotonic()

    def tick(self) -> None:
        """Вызывается часто; сама решает, пора ли обновлять."""
        if time.monotonic() < self.next_at or not self.changed.is_set():
            return
        self.next_at = time.monotonic() + MART_REFRESH_SEC
        self.changed.clear()
        started = time.monotonic()
        try:
            if self.conn is None or self.conn.closed:
                self.conn = loader_connection()
            store.refresh_marts(self.conn)
            logger.info("Витрины обновлены за %.1f с", time.monotonic() - started)
        except psycopg.Error as problem:
            logger.error("Витрины не обновлены: %s", problem)
            self.changed.set()  # попробовать снова в следующий раз
            if self.conn is not None:
                self.conn.close()
            self.conn = None

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()


def run_forever(jobs: list[PlatformJob]) -> None:
    """Запускает потоки платформ, обновляет витрины и ждет сигнала остановки."""
    stop = threading.Event()
    changed = threading.Event()
    refresher = MartRefresher(changed)

    def on_signal(signum, _frame) -> None:
        logger.info("Получен сигнал %s: дожидаемся конца текущих страниц", signum)
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="loader") as pool:
        futures = [pool.submit(platform_worker, job, stop, changed) for job in jobs]
        while not stop.is_set() and not all(f.done() for f in futures):
            refresher.tick()
            stop.wait(1)
    refresher.close()
    for future in futures:
        if future.exception() is not None:
            raise SystemExit(1)


def run_once(jobs: list[PlatformJob]) -> list[EndpointResult]:
    """Один цикл по всем адресам всех платформ, обновление витрин и выход."""
    results = []
    with loader_connection() as conn:
        for job in jobs:
            for endpoint in ALL_ENDPOINTS:
                results.append(
                    load_endpoint(conn, job.source, job.platform, endpoint, job.settings)
                )
        if any(result.saved for result in results):
            started = time.monotonic()
            store.refresh_marts(conn)
            logger.info("Витрины обновлены за %.1f с", time.monotonic() - started)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Загрузчик данных платформ в хранилище СМА")
    parser.add_argument("--once", action="store_true", help="один цикл и выход")
    parser.add_argument("--platform", help="только эта платформа: EGE, OGE, VPR, NIKO")
    parser.add_argument("--data-root", type=Path, help="папка набора генератора для file")
    parser.add_argument(
        "--api-root", help="адрес тестового API для api, например http://localhost:8000"
    )
    parser.add_argument("--config", type=Path, help="конфиг платформ (по умолчанию из .env)")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(threadName)s %(name)s %(levelname)s %(message)s"
    )
    load_dotenv()

    config_path = args.config or Path(os.environ.get("SMA_PLATFORMS_CONFIG", DEFAULT_CONFIG))
    config = load_config(config_path)
    settings = settings_from(config)
    intervals = endpoint_intervals(config)
    jobs = [
        PlatformJob(
            platform["exam_type"],
            make_source(platform, config, args.data_root, args.api_root),
            intervals,
            settings,
            retry_sec=config["min_gap_per_endpoint_sec"],
        )
        for platform in enabled_platforms(config, args.platform)
    ]
    logger.info("Конфиг %s, платформы: %s", config_path, [job.platform for job in jobs])

    if not args.once:
        run_forever(jobs)
        return
    results = run_once(jobs)
    errors = [r for r in results if r.status == "error"]
    logger.info(
        "Готово: адресов %d, сохранено %d, отклонено %d, ошибок %d",
        len(results),
        sum(r.saved for r in results),
        sum(r.rejected for r in results),
        len(errors),
    )
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
