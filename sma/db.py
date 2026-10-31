"""Подключение к PostgreSQL по настройкам из окружения (.env)."""

import os

import psycopg
from dotenv import load_dotenv


def connection_settings(
    dbname: str | None = None, user: str | None = None, password: str | None = None
) -> dict:
    """Собирает параметры подключения: явные аргументы, иначе переменные окружения."""
    # Переменные, уже заданные в окружении (например, Makefile или CI), .env не перезаписывает.
    load_dotenv()
    return {
        "host": os.environ.get("SMA_DB_HOST", "localhost"),
        "port": int(os.environ.get("SMA_DB_PORT", "5432")),
        "dbname": dbname or os.environ.get("POSTGRES_DB", "sma"),
        "user": user or os.environ.get("POSTGRES_USER", "sma_owner"),
        "password": password if password is not None else os.environ.get("POSTGRES_PASSWORD", ""),
    }


def connect(
    dbname: str | None = None,
    user: str | None = None,
    password: str | None = None,
    autocommit: bool = False,
) -> psycopg.Connection:
    """Открывает соединение с базой."""
    return psycopg.connect(**connection_settings(dbname, user, password), autocommit=autocommit)
