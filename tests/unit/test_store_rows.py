"""Строки core из проверенных записей и расчет since — без базы данных."""

from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from sma.loader.cycle import LoaderSettings, since_for
from sma.loader.store import make_row, newest_per_key, table_key, upsert_query
from tests.unit.test_validate import ANSWER, SCHOOL, SESSION


def test_since_with_overlap():
    """since = водяной знак − 5 с."""
    watermark = datetime(2026, 12, 5, 10, 0, tzinfo=UTC)
    assert since_for(watermark, LoaderSettings()) == watermark - timedelta(seconds=5)


def test_since_without_watermark_is_initial():
    settings = LoaderSettings()
    assert since_for(None, settings) == settings.initial_since


def test_answer_row():
    row = make_row("answers", ANSWER, load_id=7)
    assert row["answer"] == " 0.25"
    assert row["answer_norm"] == "0,25"
    assert row["answered_at"] == datetime(2026, 12, 5, 7, 31, tzinfo=UTC)
    assert row["record_id"] == "a-1" and row["load_id"] == 7
    assert isinstance(row["payload"], Jsonb)
    assert row["payload"].obj == ANSWER


def test_session_row_has_exam_block():
    row = make_row("sessions", SESSION, load_id=1)
    assert row["exam_block"].obj == SESSION["oge"]
    assert "oge" not in row
    assert row["started_at"].utcoffset() == timedelta(hours=3)


def test_merged_dictionary_row():
    """В общих справочниках нет record_id, deleted и payload."""
    row = make_row("dictionaries/schools", SCHOOL, load_id=3)
    assert set(row) == {"school_code", "region_code", "name", "updated_at", "load_id"}
    assert table_key("dictionaries/schools") == ["school_code"]


def test_newest_per_key_keeps_latest_version():
    old = {"exam_type": "OGE", "record_id": "r", "updated_at": datetime(2026, 1, 1, tzinfo=UTC)}
    new = {**old, "updated_at": datetime(2026, 1, 2, tzinfo=UTC)}
    other = {**old, "record_id": "q"}
    assert newest_per_key([new, old, other], ["exam_type", "record_id"]) == [new, other]


def test_upsert_query_protects_newer_version():
    key = ["exam_type", "record_id"]
    query = upsert_query("answers", [*key, "score", "updated_at"], key)
    sql_text = query.as_string(None)
    assert 'ON CONFLICT ("exam_type", "record_id")' in sql_text
    assert 'WHERE EXCLUDED.updated_at >= core."answers".updated_at' in sql_text
    assert '"score" = EXCLUDED."score"' in sql_text
    assert '"record_id" = EXCLUDED' not in sql_text, "ключ не обновляется"
