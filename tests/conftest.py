"""Общие фикстуры тестов.

Интеграционные тесты работают с отдельной базой sma_test: она создается заново
при каждом запуске pytest, поэтому рабочая база sma не затрагивается.
"""

import os
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from db.migrate import apply_migrations
from sma.db import connect

TEST_DB = os.environ.get("SMA_TEST_DB", "sma_test")


@pytest.fixture(scope="session")
def test_database() -> str:
    """Создает пустую базу sma_test и применяет к ней все миграции. Возвращает имя базы."""
    try:
        admin = connect(dbname="postgres", autocommit=True)
    except psycopg.OperationalError as error:
        message = f"Нет подключения к PostgreSQL. Запустите базу: make up\n{error}"
        pytest.fail(message, pytrace=False)
    with admin:
        name = sql.Identifier(TEST_DB)
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(name))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(name))
    with connect(dbname=TEST_DB, autocommit=True) as conn:
        apply_migrations(conn)
    return TEST_DB


@pytest.fixture(scope="session")
def medium_dataset(tmp_path_factory):
    """Набор medium (с аномалиями и самопроверкой) — один на весь запуск тестов: ~45 с."""
    from sma.generator.generate import generate

    profile = Path(__file__).resolve().parents[1] / "config" / "generator" / "medium.yaml"
    out = tmp_path_factory.mktemp("medium")
    generate(profile, out, 2026)
    return out


@pytest.fixture
def db(test_database):
    """Соединение с sma_test. Все изменения, сделанные тестом, в конце откатываются."""
    with connect(dbname=test_database) as conn:
        yield conn
        conn.rollback()


# Таблицы, в которые пишет загрузчик. Загрузчик сам делает commit, поэтому
# откатом их не очистить — только TRUNCATE.
LOADER_TABLES = (
    "core.sessions, core.answers, core.expert_reviews, core.proctoring_events, "
    "core.kim_tasks, core.scales, core.regions, core.schools, core.subjects, core.live_status, "
    "ops.load_log, ops.rejected_records, ops.watermarks, ops.dictionary_snapshots, "
    "ops.live_status_history"
)


@pytest.fixture
def loader_db(test_database):
    """Соединение с пустыми таблицами загрузчика; после теста они снова очищаются."""
    with connect(dbname=test_database) as conn:
        conn.execute(f"TRUNCATE {LOADER_TABLES} RESTART IDENTITY CASCADE")
        conn.commit()
        yield conn
        conn.rollback()
        conn.execute("RESET ROLE")
        conn.execute(f"TRUNCATE {LOADER_TABLES} RESTART IDENTITY CASCADE")
        conn.commit()
