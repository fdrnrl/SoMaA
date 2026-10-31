"""Миграции 001–004: схемы, столбцы, ключи, индексы, ограничения, права (этап M1)."""

from datetime import UTC, datetime

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from db.migrate import apply_migrations, migration_files
from sma.db import connect

TEXT = "text"
SMALLINT = "smallint"
INTEGER = "integer"
BIGINT = "bigint"
BOOLEAN = "boolean"
TSTZ = "timestamp with time zone"
JSONB = "jsonb"
TEXT_ARRAY = "text[]"

# Столбец → (тип, может ли быть NULL).
KEY = {"exam_type": (TEXT, False), "record_id": (TEXT, False)}
SERVICE = {
    "updated_at": (TSTZ, False),
    "deleted": (BOOLEAN, False),
    "payload": (JSONB, False),
    "loaded_at": (TSTZ, False),
    "load_id": (BIGINT, True),
}
DICTIONARY_SERVICE = {
    "updated_at": (TSTZ, False),
    "loaded_at": (TSTZ, False),
    "load_id": (BIGINT, True),
}

EXPECTED_COLUMNS = {
    "sessions": {
        **KEY,
        "participant_id": (TEXT, False),
        "subject_code": (TEXT, False),
        "grade_level": (SMALLINT, False),
        "variant_id": (TEXT, False),
        "region_code": (TEXT, False),
        "school_code": (TEXT, False),
        "room_code": (TEXT, True),
        "status": (TEXT, False),
        "started_at": (TSTZ, True),
        "finished_at": (TSTZ, True),
        "primary_score": (SMALLINT, True),
        "max_primary_score": (SMALLINT, False),
        "result_status": (TEXT, False),
        "exam_block": (JSONB, True),
        **SERVICE,
    },
    "answers": {
        **KEY,
        "participant_id": (TEXT, False),
        "task_number": (SMALLINT, False),
        "answer": (TEXT, False),
        "answer_norm": (TEXT, False),
        "is_correct": (BOOLEAN, False),
        "score": (SMALLINT, False),
        "max_score": (SMALLINT, False),
        "answered_at": (TSTZ, False),
        "time_spent_sec": (INTEGER, True),
        "audio_plays": (SMALLINT, True),
        **SERVICE,
    },
    "expert_reviews": {
        **KEY,
        "participant_id": (TEXT, False),
        "task_number": (SMALLINT, False),
        "check_status": (TEXT, False),
        "check_stage": (TEXT, True),
        "expert_code": (TEXT, True),
        "score": (SMALLINT, True),
        "max_score": (SMALLINT, False),
        "reviewed_at": (TSTZ, True),
        **SERVICE,
    },
    "proctoring_events": {
        **KEY,
        "participant_id": (TEXT, False),
        "event_type": (TEXT, False),
        "occurred_at": (TSTZ, False),
        "task_number": (SMALLINT, True),
        **SERVICE,
    },
    "kim_tasks": {
        **KEY,
        "subject_code": (TEXT, False),
        "exam_year": (SMALLINT, False),
        "variant_id": (TEXT, False),
        "task_number": (SMALLINT, False),
        "part": (SMALLINT, False),
        "check_type": (TEXT, False),
        "max_score": (SMALLINT, False),
        "level": (TEXT, False),
        "content_codes": (TEXT_ARRAY, False),
        "skill_codes": (TEXT_ARRAY, False),
        "section": (TEXT, False),
        "block": (TEXT, True),
        **SERVICE,
    },
    "scales": {
        **KEY,
        "subject_code": (TEXT, False),
        "exam_year": (SMALLINT, False),
        "scale_type": (TEXT, False),
        "points": (JSONB, False),
        "conditions": (JSONB, False),
        "scale_version": (INTEGER, False),
        "valid_from": (TSTZ, False),
        **SERVICE,
    },
    "regions": {
        "region_code": (TEXT, False),
        "name": (TEXT, False),
        **DICTIONARY_SERVICE,
    },
    "schools": {
        "school_code": (TEXT, False),
        "region_code": (TEXT, False),
        "name": (TEXT, False),
        **DICTIONARY_SERVICE,
    },
    "subjects": {
        "exam_type": (TEXT, False),
        "subject_code": (TEXT, False),
        "name": (TEXT, False),
        "grade_level": (SMALLINT, False),
        "max_primary_score": (SMALLINT, False),
        "threshold_score": (SMALLINT, False),
        **DICTIONARY_SERVICE,
    },
    "live_status": {
        "exam_type": (TEXT, False),
        "in_progress": (INTEGER, False),
        "finished": (INTEGER, False),
        "annulled": (INTEGER, False),
        "absent": (INTEGER, False),
        "generated_at": (TSTZ, False),
        "loaded_at": (TSTZ, False),
        "load_id": (BIGINT, True),
    },
}

EXPECTED_PRIMARY_KEYS = {
    "sessions": ["exam_type", "record_id"],
    "answers": ["exam_type", "record_id"],
    "expert_reviews": ["exam_type", "record_id"],
    "proctoring_events": ["exam_type", "record_id"],
    "kim_tasks": ["exam_type", "record_id"],
    "scales": ["exam_type", "record_id"],
    "regions": ["region_code"],
    "schools": ["school_code"],
    "subjects": ["exam_type", "subject_code"],
    "live_status": ["exam_type"],
}

EXPECTED_INDEXES = [
    ("sessions", ["exam_type", "participant_id"]),
    ("sessions", ["exam_type", "subject_code", "region_code", "school_code"]),
    ("sessions", ["updated_at"]),
    ("answers", ["exam_type", "participant_id"]),
    ("answers", ["exam_type", "task_number"]),
    ("expert_reviews", ["exam_type", "participant_id"]),
    ("proctoring_events", ["exam_type", "participant_id"]),
    ("kim_tasks", ["exam_type", "subject_code", "exam_year", "variant_id", "task_number"]),
]

NOW = datetime(2026, 12, 5, 10, 0, tzinfo=UTC)
ROW_SERVICE = {"updated_at": NOW, "deleted": False, "payload": Jsonb({})}

# Минимальные корректные строки: только NOT NULL столбцы.
VALID_ROWS = {
    "sessions": {
        "exam_type": "OGE",
        "record_id": "r1",
        "participant_id": "p_7f3a9c",
        "subject_code": "02",
        "grade_level": 9,
        "variant_id": "v1",
        "region_code": "77",
        "school_code": "77003",
        "status": "finished",
        "max_primary_score": 31,
        "result_status": "final",
        **ROW_SERVICE,
    },
    "answers": {
        "exam_type": "OGE",
        "record_id": "r1",
        "participant_id": "p_7f3a9c",
        "task_number": 1,
        "answer": " 0.25",
        "answer_norm": "0,25",
        "is_correct": True,
        "score": 1,
        "max_score": 1,
        "answered_at": NOW,
        **ROW_SERVICE,
    },
    "expert_reviews": {
        "exam_type": "OGE",
        "record_id": "r1",
        "participant_id": "p_7f3a9c",
        "task_number": 20,
        "check_status": "pending",
        "max_score": 2,
        **ROW_SERVICE,
    },
    "proctoring_events": {
        "exam_type": "OGE",
        "record_id": "r1",
        "participant_id": "p_7f3a9c",
        "event_type": "copy",
        "occurred_at": NOW,
        **ROW_SERVICE,
    },
}

# (таблица, столбец, недопустимое значение) — должно отклоняться ограничением CHECK.
BAD_VALUES = [
    ("sessions", "status", "done"),
    ("sessions", "result_status", "maybe"),
    ("expert_reviews", "check_status", "done"),
    ("expert_reviews", "check_stage", "second"),
    ("proctoring_events", "event_type", "screenshot"),
]

# (роль, таблица, право, есть ли право)
EXPECTED_PRIVILEGES = [
    ("sma_loader", "core.answers", "INSERT", True),
    ("sma_loader", "core.answers", "UPDATE", True),
    ("sma_loader", "core.answers", "DELETE", False),
    ("sma_loader", "ops.load_log", "INSERT", True),
    ("sma_reader", "core.sessions", "SELECT", True),
    ("sma_reader", "core.sessions", "INSERT", False),
    ("sma_reader", "ops.load_log", "SELECT", False),
    ("sma_app", "analytics.anomalies", "INSERT", True),
    ("sma_app", "app.users", "UPDATE", True),
    ("sma_app", "core.sessions", "SELECT", False),
]

EXPECTED_OTHER_TABLES = {
    "ops": [
        "load_log",
        "rejected_records",
        "watermarks",
        "dictionary_snapshots",
        "live_status_history",
        "quality_runs",
        "quality_issues",
        "backup_log",
        "schema_migrations",
    ],
    "analytics": ["anomalies", "analysis_runs", "p1_groups", "school_index"],
    "app": ["users", "login_log"],
}


def table_columns(conn, schema, table):
    """Столбцы таблицы: имя → (тип, может ли быть NULL)."""
    rows = conn.execute(
        """
        SELECT a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
        """,
        (schema, table),
    ).fetchall()
    return {name: (data_type, nullable) for name, data_type, nullable in rows}


def primary_key(conn, schema, table):
    """Столбцы первичного ключа по порядку."""
    rows = conn.execute(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = %s AND tc.table_name = %s
        ORDER BY kcu.ordinal_position
        """,
        (schema, table),
    ).fetchall()
    return [row[0] for row in rows]


def index_columns(conn, schema, table):
    """Список индексов таблицы, каждый — список столбцов по порядку."""
    rows = conn.execute(
        """
        SELECT array_agg(a.attname ORDER BY k.ord)
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
        WHERE n.nspname = %s AND c.relname = %s
        GROUP BY i.indexrelid
        """,
        (schema, table),
    ).fetchall()
    return [row[0] for row in rows]


def load_id_reference(conn, table):
    """На какую таблицу ссылается внешний ключ столбца load_id (или None)."""
    row = conn.execute(
        """
        SELECT c.confrelid::regclass::text
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
        WHERE c.contype = 'f' AND n.nspname = 'core' AND t.relname = %s AND a.attname = 'load_id'
        """,
        (table,),
    ).fetchone()
    return row[0] if row else None


def insert(conn, table, row):
    """INSERT строки (словарь столбец → значение) в core.<table>."""
    query = sql.SQL("INSERT INTO core.{} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(name) for name in row),
        sql.SQL(", ").join(sql.Placeholder() for _ in row),
    )
    conn.execute(query, list(row.values()))


def test_all_schemas_exist(db):
    rows = db.execute("SELECT nspname FROM pg_namespace").fetchall()
    names = {row[0] for row in rows}
    assert {"core", "ops", "mart", "analytics", "app"} <= names


def test_all_migration_files_recorded(db):
    rows = db.execute("SELECT filename FROM ops.schema_migrations ORDER BY filename").fetchall()
    assert [row[0] for row in rows] == [path.name for path in migration_files()]


def test_second_run_applies_nothing(test_database):
    with connect(dbname=test_database, autocommit=True) as conn:
        assert apply_migrations(conn) == []


@pytest.mark.parametrize("table", EXPECTED_COLUMNS)
def test_core_columns(db, table):
    actual = table_columns(db, "core", table)
    assert actual, f"таблица core.{table} не найдена"
    assert actual == EXPECTED_COLUMNS[table]


@pytest.mark.parametrize("table", EXPECTED_PRIMARY_KEYS)
def test_core_primary_key(db, table):
    assert primary_key(db, "core", table) == EXPECTED_PRIMARY_KEYS[table]


@pytest.mark.parametrize("table, columns", EXPECTED_INDEXES)
def test_core_index(db, table, columns):
    assert columns in index_columns(db, "core", table)


@pytest.mark.parametrize("table", EXPECTED_COLUMNS)
def test_core_load_id_references_load_log(db, table):
    assert load_id_reference(db, table) == "ops.load_log"


@pytest.mark.parametrize("table", VALID_ROWS)
def test_valid_row_is_accepted(db, table):
    insert(db, table, VALID_ROWS[table])


@pytest.mark.parametrize("table, column, value", BAD_VALUES)
def test_check_rejects_bad_value(db, table, column, value):
    row = {**VALID_ROWS[table], column: value}
    with pytest.raises(psycopg.errors.CheckViolation):
        insert(db, table, row)


def test_same_record_id_twice_is_rejected(db):
    """Первичный ключ не дает вставить две строки с одним (exam_type, record_id)."""
    insert(db, "answers", VALID_ROWS["answers"])
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert(db, "answers", VALID_ROWS["answers"])


@pytest.mark.parametrize("schema", EXPECTED_OTHER_TABLES)
def test_other_schemas_tables(db, schema):
    rows = db.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s", (schema,)).fetchall()
    assert {row[0] for row in rows} == set(EXPECTED_OTHER_TABLES[schema])


@pytest.mark.parametrize("role, table, privilege, expected", EXPECTED_PRIVILEGES)
def test_role_privileges(db, role, table, privilege, expected):
    row = db.execute("SELECT has_table_privilege(%s, %s, %s)", (role, table, privilege)).fetchone()
    assert row[0] is expected
