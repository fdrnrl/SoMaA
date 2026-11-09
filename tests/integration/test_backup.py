"""Резервное копирование и восстановление (раздел 14, T-35).

Нужны pg_dump и pg_restore версии 16 — они есть в образе Dockerfile. На компьютере без них
тест пропускается; запуск в контейнере: make test-backup.
"""

import shutil
import time
from pathlib import Path

import pytest
from psycopg import sql

from sma.backup.restore import MAIN_TABLES, restore, table_counts
from sma.backup.run import backup_once, remove_old
from sma.db import connect
from sma.generator.generate import generate
from sma.loader.cycle import LoaderSettings, run_cycle
from sma.loader.sources import FileSource
from tests.conftest import LOADER_TABLES

SMALL = Path(__file__).resolve().parents[2] / "config" / "generator" / "small.yaml"
TARGET = "sma_test_restore"
needs_pg_tools = pytest.mark.skipif(
    shutil.which("pg_dump") is None or shutil.which("pg_restore") is None,
    reason="нет pg_dump/pg_restore 16 на этом компьютере — запустите make test-backup",
)


def test_remove_old_keeps_last(tmp_path):
    for day in range(1, 26):
        (tmp_path / f"sma_202612{day:02d}_1030.dump").write_bytes(b"x")
    removed = remove_old(tmp_path, keep=20)
    assert len(removed) == 5
    left = sorted(path.name for path in tmp_path.glob("*.dump"))
    assert left[0] == "sma_20261206_1030.dump" and len(left) == 20


@needs_pg_tools
def test_backup_restore(test_database, tmp_path):
    """T-35: копия и восстановление; число строк совпадает; время < 10 минут."""
    dataset = tmp_path / "small"
    generate(SMALL, dataset, 2026)
    with connect(dbname=test_database) as conn:
        conn.execute(f"TRUNCATE {LOADER_TABLES} RESTART IDENTITY CASCADE")
        conn.commit()
        for exam_type in ("EGE", "OGE", "VPR", "NIKO"):
            run_cycle(conn, FileSource(dataset / exam_type.lower()), exam_type, LoaderSettings())
    with connect(dbname="postgres", autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TARGET))
        )

    started = time.monotonic()
    path = backup_once(tmp_path / "backups", dbname=test_database)
    seconds = restore(path, TARGET)
    assert time.monotonic() - started < 600
    assert seconds < 600

    source, copy = table_counts(test_database), table_counts(TARGET)
    assert copy == source
    assert source["core.answers"] > 0
    with connect(dbname=test_database) as conn:
        status, size = conn.execute(
            "SELECT status, size_bytes FROM ops.backup_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert status == "ok" and size == path.stat().st_size
    with connect(dbname=TARGET) as conn:
        assert conn.execute("SELECT count(*) FROM mart.sessions_current").fetchone()[0] > 0
    assert set(MAIN_TABLES) <= set(source)


@needs_pg_tools
def test_restore_refuses_non_empty_database(test_database, tmp_path):
    path = backup_once(tmp_path, dbname=test_database)
    with pytest.raises(SystemExit):
        restore(path, test_database)
