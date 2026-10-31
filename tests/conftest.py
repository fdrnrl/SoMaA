"""Общие фикстуры тестов.

Интеграционные тесты работают с отдельной базой sma_test: она создается заново
при каждом запуске pytest, поэтому рабочая база sma не затрагивается.
"""

import os

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


@pytest.fixture
def db(test_database):
    """Соединение с sma_test. Все изменения, сделанные тестом, в конце откатываются."""
    with connect(dbname=test_database) as conn:
        yield conn
        conn.rollback()
