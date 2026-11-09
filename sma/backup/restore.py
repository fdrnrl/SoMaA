"""Восстановление базы из копии (раздел 14, T-35: не дольше 10 минут).

    python -m sma.backup.restore --file backups/sma_20261205_1030.dump --target-db sma_restore

Восстанавливает в новую или пустую базу (существующую непустую не трогает) и выводит
время и число строк в основных таблицах источника и восстановленной базы.
"""

import argparse
import logging
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv
from psycopg import sql

from sma.backup.run import pg_environment
from sma.db import connect

logger = logging.getLogger(__name__)

MAIN_TABLES = [
    "core.sessions",
    "core.answers",
    "core.expert_reviews",
    "core.proctoring_events",
    "core.kim_tasks",
    "core.scales",
    "core.regions",
    "core.schools",
    "ops.load_log",
]


def table_counts(dbname: str | None) -> dict[str, int]:
    """Число строк в основных таблицах базы."""
    with connect(dbname=dbname) as conn:
        return {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in MAIN_TABLES
        }


def prepare_target(target_db: str) -> None:
    """Создает базу target_db; если она есть и в ней уже есть таблицы — ошибка."""
    with connect(dbname="postgres", autocommit=True) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (target_db,)
        ).fetchone()
        if not exists:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db)))
            return
    with connect(dbname=target_db) as conn:
        tables = conn.execute(
            "SELECT count(*) FROM pg_tables "
            "WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
        ).fetchone()[0]
    if tables:
        raise SystemExit(
            f"База {target_db} не пуста: восстанавливать можно только в новую или пустую"
        )


def restore(file: Path, target_db: str) -> float:
    """pg_restore копии в target_db. Возвращает время в секундах."""
    if not file.exists():
        raise SystemExit(f"Нет файла копии {file}")
    prepare_target(target_db)
    started = time.monotonic()
    result = subprocess.run(
        ["pg_restore", "--no-owner", f"--dbname={target_db}", str(file)],
        env=pg_environment(target_db),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pg_restore завершился с ошибкой: {result.stderr.strip()}")
    return time.monotonic() - started


def main() -> None:
    parser = argparse.ArgumentParser(description="Восстановление базы СМА из копии")
    parser.add_argument("--file", required=True, type=Path, help="файл копии .dump")
    parser.add_argument("--target-db", default="sma_restore", help="новая или пустая база")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    load_dotenv()

    before = table_counts(None)
    seconds = restore(args.file, args.target_db)
    after = table_counts(args.target_db)
    logger.info("Восстановлено в %s за %.1f с", args.target_db, seconds)
    print(f"{'таблица':<26}{'источник':>12}{'копия':>12}")
    for table in MAIN_TABLES:
        mark = (
            ""
            if before[table] == after[table]
            else "  ← отличается (данные изменились после копии)"
        )
        print(f"{table:<26}{before[table]:>12}{after[table]:>12}{mark}")


if __name__ == "__main__":
    main()
