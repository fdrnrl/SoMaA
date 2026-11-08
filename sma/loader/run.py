"""Точка входа загрузчика (раздел 8.7).

    python -m sma.loader.run --once                    один цикл по всем платформам и выход
    python -m sma.loader.run --once --platform OGE     только одна платформа
    python -m sma.loader.run --once --data-root data/generated/medium
                                                       другой набор для источников file

Работа по расписанию (без --once): планировщик с потоками — этап M4.
Загрузчик подключается к базе под ролью sma_loader (пароль — SMA_LOADER_PASSWORD).
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from sma.db import connect
from sma.loader.cycle import EndpointResult, LoaderSettings, run_cycle
from sma.loader.sources import FileSource, Source
from sma.timeutil import parse_datetime

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = "config/platforms.yaml"


def load_config(path: Path) -> dict:
    """Читает config/platforms.yaml."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def settings_from(config: dict) -> LoaderSettings:
    return LoaderSettings(
        since_overlap_sec=config["since_overlap_sec"],
        initial_since=parse_datetime(config["initial_since"]),
    )


def make_source(platform: dict, data_root: Path | None) -> Source:
    """Источник платформы по конфигу. data_root заменяет папку набора: <data_root>/<exam>."""
    if platform["source"] == "file":
        folder = Path(platform["path"])
        if data_root is not None:
            folder = Path(data_root) / platform["exam_type"].lower()
        return FileSource(folder)
    if platform["source"] == "api":
        raise SystemExit("Источник api появится на этапе M4 (ApiSource)")
    raise SystemExit(f"Неизвестный источник {platform['source']!r} в config/platforms.yaml")


def run_once(config: dict, platform_filter: str | None, data_root: Path | None) -> list:
    """Один цикл по всем включенным платформам. Возвращает итоги всех адресов."""
    settings = settings_from(config)
    platforms = [p for p in config["platforms"] if p.get("enabled", True)]
    if platform_filter:
        platforms = [p for p in platforms if p["exam_type"] == platform_filter]
        if not platforms:
            raise SystemExit(f"Платформа {platform_filter} не найдена или выключена")

    results: list[EndpointResult] = []
    password = os.environ.get("SMA_LOADER_PASSWORD", "")
    with connect(user="sma_loader", password=password) as conn:
        for platform in platforms:
            source = make_source(platform, data_root)
            results += run_cycle(conn, source, platform["exam_type"], settings)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Загрузчик данных платформ в хранилище СМА")
    parser.add_argument("--once", action="store_true", help="один цикл и выход")
    parser.add_argument("--platform", help="только эта платформа: EGE, OGE, VPR, NIKO")
    parser.add_argument(
        "--data-root", type=Path, help="папка набора генератора для источников file"
    )
    parser.add_argument("--config", type=Path, help="конфиг платформ (по умолчанию из .env)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    load_dotenv()

    config_path = args.config or Path(os.environ.get("SMA_PLATFORMS_CONFIG", DEFAULT_CONFIG))
    config = load_config(config_path)
    if not args.once:
        raise SystemExit("Работа по расписанию появится на этапе M4. Пока используйте --once.")

    results = run_once(config, args.platform, args.data_root)
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
