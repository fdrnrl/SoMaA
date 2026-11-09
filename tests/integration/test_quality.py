"""Проверки качества (раздел 13): чистый medium без ошибок, каждая проверка находит
специально испорченную запись. Порча — в транзакции, которая в конце откатывается.
"""

import pytest

from sma.db import connect
from sma.loader import store
from sma.loader.cycle import LoaderSettings, run_cycle
from sma.loader.sources import FileSource
from sma.quality import checks
from sma.quality.report import run_quality
from tests.conftest import LOADER_TABLES

EXAMS = ["EGE", "OGE", "VPR", "NIKO"]
FINAL_SESSION = (
    "SELECT record_id FROM core.sessions WHERE exam_type = %s AND status = 'finished' "
    "AND result_status = 'final' ORDER BY record_id LIMIT 1"
)
ANY_ANSWER = "SELECT record_id FROM core.answers WHERE exam_type = 'OGE' ORDER BY record_id LIMIT 1"


@pytest.fixture(scope="module")
def loaded(test_database, medium_dataset):
    with connect(dbname=test_database) as conn:
        conn.execute(f"TRUNCATE {LOADER_TABLES} RESTART IDENTITY CASCADE")
        conn.commit()
        for exam_type in EXAMS:
            source = FileSource(medium_dataset / exam_type.lower())
            run_cycle(conn, source, exam_type, LoaderSettings())
        store.refresh_marts(conn)
    yield test_database
    with connect(dbname=test_database) as conn:
        conn.execute(f"TRUNCATE {LOADER_TABLES} RESTART IDENTITY CASCADE")
        conn.commit()


@pytest.fixture
def conn(loaded):
    with connect(dbname=loaded) as connection:
        yield connection
        connection.rollback()


def one(conn, query: str, params=()):
    return conn.execute(query, params).fetchone()[0]


def copy_answer(conn, source: str, **changes) -> None:
    """Копия ответа с новым record_id и изменениями."""
    columns = [
        "exam_type", "record_id", "participant_id", "task_number", "answer", "answer_norm",
        "is_correct", "score", "max_score", "answered_at", "time_spent_sec", "audio_plays",
        "updated_at", "deleted", "payload", "loaded_at", "load_id",
    ]  # fmt: skip
    values = [f"%({c})s" if c in changes else c for c in columns]
    conn.execute(
        f"INSERT INTO core.answers ({', '.join(columns)}) "
        f"SELECT {', '.join(values)} FROM core.answers WHERE record_id = %(source)s",
        {**changes, "source": source},
    )


def codes(issues, severity=None) -> set[str]:
    return {i.check_code for i in issues if severity is None or i.severity == severity}


def test_clean_medium_has_no_errors(conn):
    """Критерий M7: отчет о качестве на medium без error (и без warning)."""
    issues = checks.run_checks(conn)
    assert codes(issues, "error") == set()
    assert codes(issues, "warning") == set()


def test_report_files_and_tables(conn, tmp_path):
    run_id, issues = run_quality(conn, tmp_path)
    markdown = next(tmp_path.glob("quality_*.md")).read_text(encoding="utf-8")
    assert f"№ {run_id}" in markdown and "## Сводка" in markdown and "## Ограничения" in markdown
    assert "синтетические" in markdown
    assert next(tmp_path.glob("quality_*.json")).exists()
    total = one(conn, "SELECT issues_total FROM ops.quality_runs WHERE id = %s", (run_id,))
    assert total == len(issues)


# (проверка, порча данных SQL, параметры)
CORRUPTIONS = {
    "Q-COMPL-01": [
        ("INSERT INTO ops.load_log (platform, endpoint, status, received, rejected) "
         "VALUES ('OGE', 'answers', 'partial', 2000, 1500)", ()),
    ],
    "Q-COMPL-02": [
        (f"UPDATE core.sessions SET primary_score = NULL WHERE record_id = ({FINAL_SESSION})",
         ("EGE",)),
    ],
    "Q-CONS-02": [
        ("INSERT INTO core.sessions SELECT exam_type, 'second', participant_id, '05', grade_level, "
         "variant_id, region_code, school_code, room_code, status, started_at, finished_at, "
         "primary_score, max_primary_score, result_status, exam_block, updated_at, deleted, "
         f"payload, loaded_at, load_id FROM core.sessions WHERE record_id = ({FINAL_SESSION})",
         ("OGE",)),
    ],
    "Q-CONS-03": [
        (f"UPDATE core.sessions SET primary_score = primary_score + 1 "
         f"WHERE record_id = ({FINAL_SESSION})", ("VPR",)),
    ],
    "Q-CONS-04": [
        ("UPDATE core.sessions SET exam_block = jsonb_set(exam_block, '{test_score}', '101') "
         f"WHERE record_id = ({FINAL_SESSION})", ("EGE",)),
    ],
    "Q-CONS-05": [
        ("UPDATE core.sessions SET finished_at = started_at - interval '1 hour' "
         f"WHERE record_id = ({FINAL_SESSION})", ("NIKO",)),
    ],
    "Q-CONS-06": [
        ("UPDATE core.sessions SET primary_score = 5 WHERE record_id = (SELECT record_id "
         "FROM core.sessions WHERE status = 'absent' ORDER BY record_id LIMIT 1)", ()),
    ],
    "Q-CONS-07": [
        (f"UPDATE core.sessions SET region_code = '99' WHERE record_id = ({FINAL_SESSION})",
         ("OGE",)),
    ],
    "Q-CONS-08": [
        ("INSERT INTO ops.dictionary_snapshots (platform, endpoint, received_at, payload) "
         "SELECT platform, endpoint, now() + interval '1 hour', "
         "jsonb_set(payload, '{0,name}', '\"Другое название\"') "
         "FROM ops.dictionary_snapshots WHERE platform = 'EGE' "
         "AND endpoint = 'dictionaries/regions' LIMIT 1", ()),
    ],
    "Q-CONS-10": [
        ("UPDATE core.scales SET points = points - 1 WHERE exam_type = 'OGE'", ()),
    ],
    "Q-OUT-01": [
        (f"UPDATE core.answers SET time_spent_sec = 999999 WHERE record_id = ({ANY_ANSWER})", ()),
    ],
    "Q-MISS-01": [
        ("UPDATE core.answers SET time_spent_sec = NULL WHERE participant_id IN ("
         "SELECT participant_id FROM core.sessions WHERE exam_type = 'OGE' AND school_code = ("
         "SELECT min(school_code) FROM core.sessions WHERE exam_type = 'OGE'))", ()),
    ],
}  # fmt: skip


@pytest.mark.parametrize("code", sorted(CORRUPTIONS))
def test_check_finds_corruption(conn, code):
    for query, params in CORRUPTIONS[code]:
        conn.execute(query, params)
    check = dict(checks.CHECKS)[code]
    assert code in codes(check(conn))


def test_q_uniq_01_duplicate_natural_key(conn):
    copy_answer(conn, one(conn, ANY_ANSWER), record_id="duplicate")
    assert "Q-UNIQ-01" in codes(checks.q_uniq_01(conn))


def test_q_cons_01_answer_without_session(conn):
    copy_answer(conn, one(conn, ANY_ANSWER), record_id="orphan", participant_id="p_zzzzzz")
    assert "Q-CONS-01" in codes(checks.q_cons_01(conn))


def test_q_cons_09_task_not_in_kim(conn):
    copy_answer(conn, one(conn, ANY_ANSWER), record_id="task-99", task_number=99)
    assert "Q-CONS-09" in codes(checks.q_cons_09(conn))


def test_q_time_01_slow_loading_during_exam(conn):
    conn.execute("UPDATE core.live_status SET in_progress = 5 WHERE exam_type = 'OGE'")
    conn.execute(
        "UPDATE core.answers SET loaded_at = now(), updated_at = now() - interval '200 seconds' "
        "WHERE exam_type = 'OGE'"
    )
    issues = checks.q_time_01(conn)
    assert any(i.check_code == "Q-TIME-01" and i.severity == "error" for i in issues)


def test_q_time_01_skipped_without_exam(conn):
    assert checks.q_time_01(conn) == []


def test_q_acc_01_payload_differs(conn):
    conn.execute(
        "UPDATE core.answers SET score = 1 - score WHERE exam_type || record_id = ("
        "SELECT exam_type || record_id FROM core.answers ORDER BY md5(exam_type || record_id) "
        "LIMIT 1)"
    )
    assert "Q-ACC-01" in codes(checks.q_acc_01(conn))


def test_q_mart_checks_find_stale_marts(conn):
    """Витрина, не обновленная после изменения core, не совпадает с пересчетом."""
    conn.execute(
        f"UPDATE core.sessions SET deleted = true, primary_score = 0 "
        f"WHERE record_id = ({FINAL_SESSION})",
        ("OGE",),
    )
    conn.execute(
        "UPDATE core.answers SET deleted = true WHERE record_id IN ("
        "SELECT a.record_id FROM core.answers a "
        "JOIN core.sessions s USING (exam_type, participant_id) "
        "WHERE a.exam_type = 'EGE' AND s.status = 'finished' ORDER BY a.record_id LIMIT 3)"
    )
    assert "Q-MART-01" in codes(checks.q_mart_01(conn))
    assert "Q-MART-02" in codes(checks.q_mart_02(conn))
    assert "Q-MART-03" in codes(checks.q_mart_03(conn))
    conn.execute("SELECT mart.refresh_all()")
    assert checks.q_mart_01(conn) == [] and checks.q_mart_03(conn) == []


def test_every_section_13_check_is_tested():
    tested = set(CORRUPTIONS) | {"Q-UNIQ-01", "Q-CONS-01", "Q-CONS-09", "Q-TIME-01", "Q-ACC-01",
                                 "Q-MART-01", "Q-MART-02", "Q-MART-03"}  # fmt: skip
    assert tested == {code for code, _ in checks.CHECKS}
