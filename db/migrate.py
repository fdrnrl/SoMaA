"""Применяет SQL-миграции из db/migrations по порядку имен файлов.

Запуск: python db/migrate.py [--db ИМЯ_БАЗЫ]

Примененные файлы записываются в ops.schema_migrations, поэтому повторный запуск
ничего не ломает: он применяет только новые файлы. Уже примененный файл не меняют —
изменения схемы оформляют новым файлом (006_..., 007_...).
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from psycopg import Connection, sql

# Чтобы при запуске «python db/migrate.py» находился пакет sma из корня проекта.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sma.db import connect  # noqa: E402

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# Роль базы → переменная окружения с ее паролем (раздел 7.1).
ROLES = {
    "sma_loader": "SMA_LOADER_PASSWORD",
    "sma_reader": "SMA_READER_PASSWORD",
    "sma_app": "SMA_APP_PASSWORD",
}

CREATE_MIGRATIONS_TABLE = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.schema_migrations (
    filename   text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
"""


def ensure_roles(conn: Connection) -> None:
    """Создает роли sma_loader, sma_reader, sma_app или обновляет их пароли из окружения."""
    for role, env_name in ROLES.items():
        password = os.environ.get(env_name, "")
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        command = sql.SQL("ALTER ROLE" if exists else "CREATE ROLE")
        if password:
            query = sql.SQL("{} {} LOGIN PASSWORD {}").format(
                command, sql.Identifier(role), sql.Literal(password)
            )
        else:
            logger.warning(
                "Пароль роли %s не задан (%s): вход по паролю невозможен", role, env_name
            )
            query = sql.SQL("{} {} LOGIN").format(command, sql.Identifier(role))
        conn.execute(query)


def migration_files() -> list[Path]:
    """Все файлы миграций по порядку имен."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def applied_migrations(conn: Connection) -> set[str]:
    """Имена уже примененных файлов."""
    rows = conn.execute("SELECT filename FROM ops.schema_migrations").fetchall()
    return {row[0] for row in rows}


def apply_migrations(conn: Connection) -> list[str]:
    """Применяет новые миграции, каждую в своей транзакции. Возвращает имена примененных.

    Соединение должно быть в режиме autocommit: транзакции открываются здесь явно.
    """
    conn.execute(CREATE_MIGRATIONS_TABLE)
    ensure_roles(conn)
    done = applied_migrations(conn)
    applied = []
    for path in migration_files():
        if path.name in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO ops.schema_migrations (filename) VALUES (%s)", (path.name,))
        logger.info("Применена миграция %s", path.name)
        applied.append(path.name)
    if not applied:
        logger.info("Новых миграций нет")
    return applied


def main() -> None:
    parser = argparse.ArgumentParser(description="Применить миграции базы СМА")
    parser.add_argument("--db", help="имя базы (по умолчанию POSTGRES_DB из окружения)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    with connect(dbname=args.db, autocommit=True) as conn:
        apply_migrations(conn)


if __name__ == "__main__":
    main()
