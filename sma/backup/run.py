"""Резервное копирование базы (раздел 14, NFR-15).

    python -m sma.backup.run --once           одна копия и выход
    python -m sma.backup.run --every 30       копия каждые 30 минут, пока не остановят

Копия — pg_dump в формате custom (-Fc): backups/sma_YYYYMMDD_HHMM.dump. Хранятся
последние KEEP копий. Каждая копия записывается в ops.backup_log. Нужен pg_dump той же
основной версии, что и сервер (16): он есть в образе Dockerfile.
"""

import argparse
import logging
import os
import signal
import subprocess
import threading
from pathlib import Path

from dotenv import load_dotenv

from sma.db import connect, connection_settings
from sma.timeutil import utc_now

logger = logging.getLogger(__name__)

BACKUPS_DIR = Path("backups")
KEEP = 20


def pg_environment(dbname: str | None = None) -> dict[str, str]:
    """Переменные окружения для pg_dump и pg_restore: адрес, пользователь, пароль."""
    settings = connection_settings(dbname)
    return {
        **os.environ,
        "PGHOST": settings["host"],
        "PGPORT": str(settings["port"]),
        "PGUSER": settings["user"],
        "PGPASSWORD": settings["password"],
        "PGDATABASE": settings["dbname"],
    }


def remove_old(folder: Path, keep: int = KEEP) -> list[Path]:
    """Удаляет копии, кроме последних keep. Возвращает удаленные."""
    copies = sorted(folder.glob("sma_*.dump"))
    old = copies[:-keep] if len(copies) > keep else []
    for path in old:
        path.unlink()
    return old


def backup_once(folder: Path = BACKUPS_DIR, dbname: str | None = None) -> Path:
    """Одна копия базы. При ошибке pg_dump — запись error в ops.backup_log и исключение."""
    folder.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    path = folder / f"sma_{started:%Y%m%d_%H%M}.dump"
    with connect(dbname=dbname, autocommit=True) as conn:
        log_id = conn.execute(
            "INSERT INTO ops.backup_log (started_at, file_path) VALUES (%s, %s) RETURNING id",
            (started, str(path)),
        ).fetchone()[0]
        result = subprocess.run(
            ["pg_dump", "--format=custom", f"--file={path}"],
            env=pg_environment(dbname),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            conn.execute(
                "UPDATE ops.backup_log SET finished_at = now(), status = 'error', message = %s "
                "WHERE id = %s",
                (result.stderr.strip()[:2000], log_id),
            )
            raise RuntimeError(f"pg_dump завершился с ошибкой: {result.stderr.strip()}")
        size = path.stat().st_size
        conn.execute(
            "UPDATE ops.backup_log SET finished_at = now(), status = 'ok', size_bytes = %s "
            "WHERE id = %s",
            (size, log_id),
        )
    removed = remove_old(folder)
    logger.info("Копия %s (%.1f МБ), удалено старых: %d", path, size / 1e6, len(removed))
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Резервное копирование базы СМА")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true", help="одна копия и выход")
    group.add_argument("--every", type=float, help="интервал в минутах, например 30")
    parser.add_argument("--dir", type=Path, default=BACKUPS_DIR, help="папка для копий")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    load_dotenv()

    if args.once:
        backup_once(args.dir)
        return
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        try:
            backup_once(args.dir)
        except Exception:  # следующая попытка — по расписанию
            logger.exception("Резервная копия не создана")
        stop.wait(args.every * 60)


if __name__ == "__main__":
    main()
