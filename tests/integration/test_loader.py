"""Загрузчик из файлов в настоящий PostgreSQL (этап M3): T-03, T-04, T-06, T-32."""

import copy
import json
import shutil
from datetime import UTC, datetime, timedelta

import pytest

from sma import contract
from sma.generator.generate import generate
from sma.generator.writer import write_endpoint
from sma.loader import store
from sma.loader.cycle import ALL_ENDPOINTS, LoaderSettings, run_cycle
from sma.loader.sources import FileSource
from tests.unit.test_validate import PROFILE, SESSION

EXAMS = ["EGE", "OGE", "VPR", "NIKO"]
SETTINGS = LoaderSettings()
CORE_TABLES = {
    "sessions": "sessions",
    "answers": "answers",
    "expert-reviews": "expert_reviews",
    "proctoring-events": "proctoring_events",
    "dictionaries/kim": "kim_tasks",
    "dictionaries/scales": "scales",
}


@pytest.fixture(scope="module")
def small_dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("small")
    generate(PROFILE, out, 2026)
    return out


def count(conn, table: str, where: str = "TRUE", params=()) -> int:
    return conn.execute(f"SELECT count(*) FROM {table} WHERE {where}", params).fetchone()[0]


def file_items(folder, endpoint: str) -> list[dict]:
    items = []
    for path in sorted(folder.glob(f"{endpoint.replace('/', '-')}*.json")):
        items += json.loads(path.read_text(encoding="utf-8"))["items"]
    return items


def load_all(conn, dataset) -> list:
    results = []
    for exam_type in EXAMS:
        source = FileSource(dataset / exam_type.lower())
        results += run_cycle(conn, source, exam_type, SETTINGS)
    return results


def table_counts(conn) -> dict[str, int]:
    tables = list(CORE_TABLES.values()) + ["regions", "schools", "subjects", "live_status"]
    return {table: count(conn, f"core.{table}") for table in tables}


def test_cycle_loads_all_endpoints(loader_db, small_dataset):
    """T-06: весь набор small из файлов — в core, без ошибок и отклонений."""
    results = load_all(loader_db, small_dataset)
    assert [r for r in results if r.status != "ok"] == []
    assert len(results) == len(EXAMS) * len(ALL_ENDPOINTS)

    for exam_type in EXAMS:
        folder = small_dataset / exam_type.lower()
        for endpoint, table in CORE_TABLES.items():
            expected = len(file_items(folder, endpoint))
            assert count(loader_db, f"core.{table}", "exam_type = %s", (exam_type,)) == expected
    regions = {i["region_code"] for i in file_items(small_dataset / "oge", "dictionaries/regions")}
    assert count(loader_db, "core.regions") == len(regions), "справочники платформ сливаются"
    assert count(loader_db, "core.subjects") == len(EXAMS)
    assert count(loader_db, "core.live_status") == len(EXAMS)
    assert count(loader_db, "ops.load_log", "status = 'ok'") == len(results)
    assert count(loader_db, "ops.rejected_records") == 0
    # Водяной знак есть у каждого адреса с since, где пришла хоть одна запись
    # (у ВПР список прокторинга пуст — знака нет, и следующий цикл снова спросит все).
    with_records = sum(
        1
        for exam_type in EXAMS
        for endpoint in CORE_TABLES
        if file_items(small_dataset / exam_type.lower(), endpoint)
    )
    assert count(loader_db, "ops.watermarks") == with_records
    assert count(loader_db, "ops.dictionary_snapshots") == len(EXAMS) * 3


def test_idempotent_reload(loader_db, small_dataset):
    """T-04: повторная загрузка не добавляет строк."""
    load_all(loader_db, small_dataset)
    before = table_counts(loader_db)
    load_all(loader_db, small_dataset)
    assert table_counts(loader_db) == before


def test_rows_point_to_their_load(loader_db, small_dataset):
    load_all(loader_db, small_dataset)
    orphans = count(loader_db, "core.answers", "load_id IS NULL")
    assert orphans == 0
    row = loader_db.execute(
        "SELECT l.endpoint, l.platform FROM core.answers a JOIN ops.load_log l ON l.id = a.load_id "
        "LIMIT 1"
    ).fetchone()
    assert row[0] == "answers"


def test_payload_matches_columns(loader_db, small_dataset):
    """Q-ACC-01 в малом: исходная запись в payload совпадает со столбцами."""
    load_all(loader_db, small_dataset)
    rows = loader_db.execute(
        "SELECT answer, answer_norm, score, payload FROM core.answers LIMIT 50"
    ).fetchall()
    for answer, answer_norm, score, payload in rows:
        assert payload["answer"] == answer
        assert payload["score"] == score
        assert " " not in answer_norm


def upsert_answer(conn, score: int, updated_at: datetime) -> tuple[int, int]:
    record = {
        "record_id": "a-1",
        "participant_id": "p_7f3a9c",
        "exam_type": "OGE",
        "task_number": 21,
        "answer": "5",
        "is_correct": score > 0,
        "score": score,
        "max_score": 2,
        "answered_at": "2026-12-05T10:31:00+03:00",
        "time_spent_sec": 40,
        "audio_plays": None,
        "updated_at": updated_at.isoformat(),
        "deleted": False,
    }
    load_id = store.start_load(conn, "OGE", "answers", None)
    result = store.upsert_rows(conn, "answers", [store.make_row("answers", record, load_id)])
    conn.commit()
    return result


def test_newer_version_updates_and_older_is_skipped(loader_db):
    """T-04: измененный балл обновляется; старая версия не перетирает новую."""
    first = datetime(2026, 12, 5, 12, 0, tzinfo=UTC)
    assert upsert_answer(loader_db, 0, first) == (1, 0)
    assert upsert_answer(loader_db, 2, first + timedelta(hours=1)) == (1, 0)
    assert upsert_answer(loader_db, 1, first) == (0, 1), "устаревшая версия пропущена"
    score = loader_db.execute("SELECT score FROM core.answers WHERE record_id = 'a-1'").fetchone()
    assert score[0] == 2
    assert count(loader_db, "core.answers") == 1


def copy_platform(dataset, tmp_path, exam_type="OGE"):
    folder = tmp_path / exam_type.lower()
    shutil.copytree(dataset / exam_type.lower(), folder)
    return folder


def test_missing_field_rejected(loader_db, small_dataset, tmp_path):
    """T-03: запись без обязательного поля не сохраняется, причина — в ops.rejected_records."""
    folder = copy_platform(small_dataset, tmp_path)
    items = file_items(folder, "answers")
    broken = items[0]
    del broken["score"]
    write_endpoint(folder, "answers", items)

    results = run_cycle(loader_db, FileSource(folder), "OGE", SETTINGS, ("answers",))
    assert results[0].status == "partial"
    assert results[0].rejected == 1 and results[0].saved == len(items) - 1
    assert count(loader_db, "core.answers", "record_id = %s", (broken["record_id"],)) == 0
    rejected = loader_db.execute(
        "SELECT record_id, reason_code, field FROM ops.rejected_records"
    ).fetchall()
    assert rejected == [(broken["record_id"], "missing_field", "score")]


@pytest.mark.parametrize("key", sorted(contract.FORBIDDEN_KEYS))
def test_pii_rejected(loader_db, tmp_path, key):
    """T-32: запись с запрещенным полем не сохраняется, а его значение не попадает в журнал."""
    good = copy.deepcopy(SESSION)
    bad = copy.deepcopy(SESSION)
    bad["record_id"] = "with-pii"
    bad["participant_id"] = "p_000001"
    bad["oge"][key] = "секрет"
    write_endpoint(tmp_path, "sessions", [good, bad])

    results = run_cycle(loader_db, FileSource(tmp_path), "OGE", SETTINGS, ("sessions",))
    assert (results[0].saved, results[0].rejected) == (1, 1)
    assert count(loader_db, "core.sessions", "record_id = 'with-pii'") == 0
    reason, field, payload = loader_db.execute(
        "SELECT reason_code, field, payload FROM ops.rejected_records"
    ).fetchone()
    assert (reason, field) == ("pii_field", f"oge.{key}")
    assert payload["oge"][key] == "<removed>"
    assert "секрет" not in json.dumps(payload, ensure_ascii=False)


def test_unknown_field_dropped_record_saved(loader_db, tmp_path):
    record = {**copy.deepcopy(SESSION), "comment": "лишнее поле"}
    write_endpoint(tmp_path, "sessions", [record])
    results = run_cycle(loader_db, FileSource(tmp_path), "OGE", SETTINGS, ("sessions",))
    assert results[0].status == "ok" and results[0].saved == 1
    payload = loader_db.execute("SELECT payload FROM core.sessions").fetchone()[0]
    assert "comment" not in payload


def test_fetch_error_keeps_watermark(loader_db, tmp_path):
    """Нет файла → ops.load_log со статусом error; водяной знак не сдвигается."""
    mark = datetime(2026, 12, 5, 10, 0, tzinfo=UTC)
    store.set_watermark(loader_db, "OGE", "answers", mark)
    loader_db.commit()
    results = run_cycle(loader_db, FileSource(tmp_path), "OGE", SETTINGS, ("answers",))
    assert results[0].status == "error"
    assert results[0].error_reason == "file_not_found"
    assert store.get_watermark(loader_db, "OGE", "answers") == mark
    status, reason = loader_db.execute("SELECT status, error_reason FROM ops.load_log").fetchone()
    assert (status, reason) == ("error", "file_not_found")


def test_error_on_one_endpoint_does_not_stop_others(loader_db, small_dataset, tmp_path):
    folder = copy_platform(small_dataset, tmp_path)
    (folder / "expert-reviews.json").unlink()
    results = run_cycle(loader_db, FileSource(folder), "OGE", SETTINGS)
    statuses = {r.endpoint: r.status for r in results}
    assert statuses["expert-reviews"] == "error"
    assert statuses["answers"] == "ok" and statuses["live-status"] == "ok"


def test_watermark_moves_to_max_updated(loader_db, small_dataset):
    folder = small_dataset / "oge"
    run_cycle(loader_db, FileSource(folder), "OGE", SETTINGS, ("answers",))
    newest = max(datetime.fromisoformat(i["updated_at"]) for i in file_items(folder, "answers"))
    assert store.get_watermark(loader_db, "OGE", "answers") == newest


def test_live_status_and_history(loader_db, small_dataset):
    source = FileSource(small_dataset / "oge")
    run_cycle(loader_db, source, "OGE", SETTINGS, ("live-status",))
    run_cycle(loader_db, source, "OGE", SETTINGS, ("live-status",))
    assert count(loader_db, "core.live_status") == 1
    assert count(loader_db, "ops.live_status_history") == 2


def test_loader_role_has_enough_rights(loader_db, small_dataset):
    """Загрузчик работает под ролью sma_loader — ее прав хватает на весь цикл."""
    loader_db.execute("SET ROLE sma_loader")
    loader_db.commit()
    results = run_cycle(loader_db, FileSource(small_dataset / "vpr"), "VPR", SETTINGS)
    assert [r.endpoint for r in results if r.status != "ok"] == []


def test_parallel_dictionary_upserts_do_not_deadlock(test_database, loader_db):
    """Потоки платформ одновременно пишут одни и те же школы в разном порядке.

    Раньше это давало deadlock (нашли при 10-минутном прогоне с mock-api): каждый поток
    блокировал строки в своем порядке. Теперь upsert_rows сортирует строки по ключу.
    """
    import threading

    from sma.db import connect

    moment = datetime(2026, 11, 25, 6, 0, tzinfo=UTC)
    rows = [
        {"school_code": f"77{k:03d}", "region_code": "77", "name": f"Школа № {k}",
         "updated_at": moment, "load_id": None}
        for k in range(1, 201)
    ]  # fmt: skip
    errors: list[Exception] = []

    def writer(ordered: list[dict]) -> None:
        with connect(dbname=test_database) as conn:
            for _ in range(20):
                try:
                    store.upsert_rows(conn, "dictionaries/schools", ordered)
                    conn.commit()
                except Exception as error:  # noqa: BLE001 — собираем любую ошибку потока
                    conn.rollback()
                    errors.append(error)

    threads = [
        threading.Thread(target=writer, args=(rows,)),
        threading.Thread(target=writer, args=(list(reversed(rows)),)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert count(loader_db, "core.schools") == 200


def test_nul_record_rejected_rest_of_page_saved(loader_db, tmp_path):
    """Запись с NUL отклоняется, остальные записи страницы сохраняются (загрузка не застревает)."""
    good = copy.deepcopy(SESSION)
    bad = {
        **copy.deepcopy(SESSION),
        "record_id": "with-nul",
        "participant_id": "p_000002",
        "room_code": "03\x0012",
    }
    write_endpoint(tmp_path, "sessions", [good, bad])
    results = run_cycle(loader_db, FileSource(tmp_path), "OGE", SETTINGS, ("sessions",))
    assert (results[0].status, results[0].saved, results[0].rejected) == ("partial", 1, 1)
    reason, payload = loader_db.execute(
        "SELECT reason_code, payload FROM ops.rejected_records"
    ).fetchone()
    assert reason == "bad_format" and payload["room_code"] == "03\ufffd12"
