"""Витрины mart на наборе medium (этап M5): содержимое, Q-MART-01..03, время обновления, права."""

import json
import time

import pandas as pd
import psycopg
import pytest

from sma.db import connect
from sma.loader import store
from sma.loader.cycle import LoaderSettings, run_cycle
from sma.loader.sources import FileSource
from tests.conftest import LOADER_TABLES

EXAMS = ["EGE", "OGE", "VPR", "NIKO"]
VIEWS = [
    "sessions_current",
    "answers_flat",
    "proctoring_counts",
    "expert_progress",
    "live",
    "data_freshness",
]
MATERIALIZED = [
    "school_results",
    "region_results",
    "task_stats",
    "task_stats_region",
    "kim_stats",
    "variant_comparison",
]
FINISHED = "status = 'finished' AND result_status <> 'pending'"


@pytest.fixture(scope="module")
def medium_db(test_database, medium_dataset):
    """sma_test с загруженным набором medium и обновленными витринами (один раз на модуль)."""
    dataset = medium_dataset
    with connect(dbname=test_database) as conn:
        conn.execute(f"TRUNCATE {LOADER_TABLES} RESTART IDENTITY CASCADE")
        conn.commit()
        for exam_type in EXAMS:
            run_cycle(conn, FileSource(dataset / exam_type.lower()), exam_type, LoaderSettings())
        store.refresh_marts(conn)
    yield test_database
    with connect(dbname=test_database) as conn:
        conn.execute(f"TRUNCATE {LOADER_TABLES} RESTART IDENTITY CASCADE")
        conn.commit()


@pytest.fixture
def conn(medium_db):
    """Соединение с загруженной базой; все изменения теста откатываются."""
    with connect(dbname=medium_db) as connection:
        yield connection
        connection.rollback()


def frame(conn, query: str, params=()) -> pd.DataFrame:
    """Результат запроса как DataFrame."""
    cursor = conn.execute(query, params)
    columns = [column.name for column in cursor.description]
    return pd.DataFrame(cursor.fetchall(), columns=columns)


def scalar(conn, query: str, params=()):
    return conn.execute(query, params).fetchone()[0]


def current_sessions(conn) -> pd.DataFrame:
    """Актуальные сессии, посчитанные в pandas прямо из core (без витрин)."""
    raw = frame(
        conn,
        "SELECT exam_type, record_id, participant_id, subject_code, region_code, school_code, "
        "status, result_status, primary_score, max_primary_score, exam_block, updated_at "
        "FROM core.sessions WHERE NOT deleted",
    )
    raw = raw.sort_values(["updated_at", "record_id"])
    sessions = raw.drop_duplicates(["exam_type", "participant_id"], keep="last")
    done = sessions[(sessions.status == "finished") & (sessions.result_status != "pending")].copy()
    done["primary_score"] = done["primary_score"].astype(float)
    done["pct"] = 100 * done["primary_score"] / done["max_primary_score"]
    done["grade"] = done["exam_block"].map(lambda block: block.get("grade"))
    return done


def test_all_marts_exist(conn):
    views = {
        row[0] for row in conn.execute("SELECT viewname FROM pg_views WHERE schemaname = 'mart'")
    }
    materialized = {
        row[0]
        for row in conn.execute("SELECT matviewname FROM pg_matviews WHERE schemaname = 'mart'")
    }
    assert views == set(VIEWS)
    assert materialized == set(MATERIALIZED)


def test_refresh_under_5_seconds(conn):
    """Раздел 12: обновление всех материализованных витрин на medium — не дольше 5 с."""
    started = time.monotonic()
    store.refresh_marts(conn)
    assert time.monotonic() - started < 5


def test_marts_not_empty(conn):
    for name in VIEWS + MATERIALIZED:
        if name == "data_freshness" or name == "live":
            continue
        assert scalar(conn, f"SELECT count(*) FROM mart.{name}") > 0, name


def test_q_mart_01_participants_add_up(conn):
    """Q-MART-01: Σ participants в school_results = число актуальных сессий finished, не pending."""
    sessions = scalar(conn, f"SELECT count(*) FROM mart.sessions_current WHERE {FINISHED}")
    assert scalar(conn, "SELECT sum(participants) FROM mart.school_results") == sessions
    assert scalar(conn, "SELECT sum(participants) FROM mart.region_results") == sessions


def test_q_mart_02_school_means_match_pandas(conn):
    """Q-MART-02: средние, sd, медиана, процент и доля двоек школ — как в пандас по core."""
    done = current_sessions(conn)
    key = ["exam_type", "subject_code", "school_code"]
    expected = done.groupby(key).agg(
        participants=("primary_score", "size"),
        mean_score=("primary_score", "mean"),
        sd_score=("primary_score", "std"),
        median_score=("primary_score", "median"),
        mean_pct=("pct", "mean"),
        share_grade2=("grade", lambda grades: (grades == 2).mean()),
    )
    mart = frame(conn, "SELECT * FROM mart.school_results").set_index(key).sort_index()
    expected = expected.sort_index()
    assert list(mart.index) == list(expected.index)
    assert (mart["participants"] == expected["participants"]).all()
    for column in ("mean_score", "sd_score", "median_score", "mean_pct"):
        difference = (mart[column].astype(float) - expected[column]).abs()
        assert difference.max() <= 0.01, column
    grade_exams = mart.index.get_level_values("exam_type").isin(["OGE", "VPR"])
    shares = mart.loc[grade_exams, "share_grade2"].astype(float)
    assert (shares - expected.loc[grade_exams, "share_grade2"]).abs().max() <= 0.0001
    assert mart.loc[~grade_exams, "share_grade2"].isna().all(), "у ЕГЭ и НИКО отметок нет"


def test_q_mart_02_region_means_match_pandas(conn):
    """Q-MART-02: среднее региона — по участникам, а не по средним школ."""
    done = current_sessions(conn)
    key = ["exam_type", "subject_code", "region_code"]
    expected = done.groupby(key).agg(
        mean_score=("primary_score", "mean"),
        mean_pct=("pct", "mean"),
        schools=("school_code", "nunique"),
    )
    mart = frame(conn, "SELECT * FROM mart.region_results").set_index(key).sort_index()
    expected = expected.sort_index()
    assert (mart["mean_score"].astype(float) - expected["mean_score"]).abs().max() <= 0.01
    assert (mart["mean_pct"].astype(float) - expected["mean_pct"]).abs().max() <= 0.01
    assert (mart["schools"] == expected["schools"]).all()
    assert mart["region_name"].notna().all()


def test_region_absent_and_annulled_shares(conn):
    """Доли неявок и аннулирований — от всех сессий региона."""
    mismatches = scalar(
        conn,
        """
        SELECT count(*)
        FROM mart.region_results r
        JOIN (SELECT exam_type, subject_code, region_code,
                     avg(CASE WHEN status = 'absent' THEN 1 ELSE 0 END) AS absent,
                     avg(CASE WHEN status = 'annulled' THEN 1 ELSE 0 END) AS annulled
              FROM mart.sessions_current GROUP BY 1, 2, 3) s
          USING (exam_type, subject_code, region_code)
        WHERE abs(r.share_absent - s.absent) > 0.0001 OR abs(r.share_annulled - s.annulled) > 0.0001
        """,
    )
    assert mismatches == 0
    assert scalar(conn, "SELECT max(share_absent) FROM mart.region_results") > 0
    assert scalar(conn, "SELECT max(share_annulled) FROM mart.region_results") > 0


def test_q_mart_03_task_stats_counts(conn):
    """Q-MART-03: task_stats.n по заданию = число ответов сданных работ в answers_flat."""
    mismatches = scalar(
        conn,
        """
        SELECT count(*)
        FROM mart.task_stats t
        FULL JOIN (
            SELECT exam_type, subject_code, variant_id, task_number, count(*) AS n
            FROM mart.answers_flat WHERE status = 'finished'
            GROUP BY exam_type, subject_code, variant_id, task_number
        ) a USING (exam_type, subject_code, variant_id, task_number)
        WHERE t.n IS DISTINCT FROM a.n
        """,
    )
    assert mismatches == 0


def test_task_stats_shares_match_pandas(conn):
    answers = frame(
        conn,
        "SELECT exam_type, variant_id, task_number, answer_norm, is_correct "
        "FROM mart.answers_flat WHERE status = 'finished'",
    )
    key = ["exam_type", "variant_id", "task_number"]
    expected = answers.groupby(key).agg(
        share_correct=("is_correct", "mean"),
        share_empty=("answer_norm", lambda values: (values == "").mean()),
    )
    mart = frame(conn, "SELECT * FROM mart.task_stats").set_index(key).sort_index()
    expected = expected.sort_index()
    assert (mart["share_correct"].astype(float) - expected["share_correct"]).abs().max() <= 0.0001
    assert (mart["share_empty"].astype(float) - expected["share_empty"]).abs().max() <= 0.0001


def test_top_wrong_answers(conn):
    """top_wrong: не больше 5, по убыванию доли, без верных и пустых ответов."""
    rows = frame(conn, "SELECT * FROM mart.task_stats")
    answers = frame(
        conn,
        "SELECT exam_type, variant_id, task_number, answer_norm, is_correct "
        "FROM mart.answers_flat WHERE status = 'finished'",
    )
    correct = set(
        answers[answers.is_correct][
            ["exam_type", "variant_id", "task_number", "answer_norm"]
        ].itertuples(index=False, name=None)
    )
    for row in rows.itertuples():
        top = row.top_wrong
        assert len(top) <= 5
        shares = [item["share"] for item in top]
        assert shares == sorted(shares, reverse=True)
        assert sum(shares) <= float(1 - row.share_correct - row.share_empty) + 0.001
        for item in top:
            assert item["answer"] != ""
            assert (row.exam_type, row.variant_id, row.task_number, item["answer"]) not in correct


def test_task_stats_region_adds_up_to_task_stats(conn):
    mismatches = scalar(
        conn,
        """
        SELECT count(*) FROM mart.task_stats t
        JOIN (SELECT exam_type, subject_code, variant_id, task_number, sum(n) AS n
              FROM mart.task_stats_region GROUP BY 1, 2, 3, 4) r
          USING (exam_type, subject_code, variant_id, task_number)
        WHERE t.n <> r.n
        """,
    )
    assert mismatches == 0


def test_sessions_current_columns(conn):
    rows = frame(conn, f"SELECT * FROM mart.sessions_current WHERE {FINISHED}")
    by_exam = {exam: rows[rows.exam_type == exam] for exam in EXAMS}
    assert by_exam["OGE"]["grade"].notna().all() and by_exam["VPR"]["grade"].notna().all()
    assert by_exam["EGE"]["test_score"].notna().all()
    assert by_exam["NIKO"]["result_level"].notna().all()
    assert rows["region_name"].notna().all() and rows["school_name"].notna().all()


def test_sessions_current_takes_newest_version_and_skips_deleted(conn):
    pid, record_id = conn.execute(
        "SELECT participant_id, record_id FROM core.sessions WHERE exam_type = 'OGE' LIMIT 1"
    ).fetchone()
    conn.execute(
        """
        INSERT INTO core.sessions
        SELECT exam_type, 'duplicate-newer', participant_id, subject_code, grade_level, variant_id,
               region_code, school_code, room_code, status, started_at, finished_at, 0,
               max_primary_score, result_status, exam_block, updated_at + interval '1 hour',
               deleted, payload, loaded_at, load_id
        FROM core.sessions WHERE record_id = %s
        """,
        (record_id,),
    )
    row = conn.execute(
        "SELECT record_id, primary_score FROM mart.sessions_current WHERE participant_id = %s",
        (pid,),
    ).fetchone()
    assert row == ("duplicate-newer", 0), "дубль естественного ключа — берется новая версия"
    conn.execute("UPDATE core.sessions SET deleted = true WHERE participant_id = %s", (pid,))
    assert (
        scalar(conn, "SELECT count(*) FROM mart.sessions_current WHERE participant_id = %s", (pid,))
        == 0
    )


def test_refresh_shows_new_data(conn):
    school = conn.execute(
        "SELECT exam_type, school_code, mean_score FROM mart.school_results LIMIT 1"
    ).fetchone()
    conn.execute(
        f"UPDATE core.sessions SET primary_score = max_primary_score "
        f"WHERE exam_type = %s AND school_code = %s AND {FINISHED}",
        (school[0], school[1]),
    )
    store.refresh_marts(conn)
    mean = scalar(
        conn,
        "SELECT mean_score FROM mart.school_results WHERE exam_type = %s AND school_code = %s",
        (school[0], school[1]),
    )
    assert mean > school[2]


def test_kim_stats(conn):
    rows = frame(conn, "SELECT * FROM mart.kim_stats")
    assert (rows["n_answers"] > 0).all()
    shares = rows["mean_share_score"].astype(float)
    assert ((shares >= 0) & (shares <= 1)).all()
    assert set(rows["exam_type"]) == set(EXAMS)


def test_variant_comparison(conn):
    rows = frame(conn, "SELECT * FROM mart.variant_comparison")
    for _, task in rows.groupby(["exam_type", "task_number"]):
        assert len(task) == 2, "в medium по два варианта"
        assert float(task["diff_from_others"].sum()) == pytest.approx(0, abs=0.0002)


def test_proctoring_counts_match_core(conn):
    mart = frame(conn, "SELECT exam_type, sum(counted_events) AS c, sum(focus_lost) AS f "
                       "FROM mart.proctoring_counts GROUP BY exam_type")  # fmt: skip
    core = frame(
        conn,
        "SELECT exam_type, count(*) FILTER (WHERE event_type <> 'focus_lost') AS c, "
        "count(*) FILTER (WHERE event_type = 'focus_lost') AS f "
        "FROM core.proctoring_events GROUP BY exam_type",
    )
    merged = mart.merge(core, on="exam_type", suffixes=("_mart", "_core"))
    assert (merged["c_mart"] == merged["c_core"]).all()
    assert (merged["f_mart"] == merged["f_core"]).all()
    assert "VPR" not in set(mart["exam_type"]), "у ВПР нет прокторинга"


def test_expert_progress_after_check(conn):
    rows = frame(conn, "SELECT * FROM mart.expert_progress")
    assert set(rows["exam_type"]) == set(EXAMS)
    assert (rows["pending"] == 0).all()
    assert (rows["share_checked"].astype(float) == 1).all()


def test_live_screen(conn):
    rows = frame(conn, "SELECT * FROM mart.live ORDER BY exam_type")
    assert list(rows["exam_type"]) == sorted(EXAMS)
    assert rows["last_successful_load_at"].notna().all()
    assert all(value == {} for value in rows["anomalies_by_rule"])
    conn.execute(
        "INSERT INTO analytics.anomalies (rule, exam_type, object_type, object_id) "
        "VALUES ('A1', 'OGE', 'school', '77001'), ('A3', 'OGE', 'participant', 'p_000001')"
    )
    anomalies = scalar(conn, "SELECT anomalies_by_rule FROM mart.live WHERE exam_type = 'OGE'")
    assert anomalies == {"A1": 1, "A3": 1}


def test_data_freshness(conn):
    rows = frame(conn, "SELECT * FROM mart.data_freshness")
    expected = scalar(conn, "SELECT count(DISTINCT (platform, endpoint)) FROM ops.load_log")
    assert len(rows) == expected
    assert rows["last_ok_at"].notna().all() and rows["last_error_at"].isna().all()
    assert (rows["lag_sec"] >= 0).all()
    conn.execute(
        "INSERT INTO ops.load_log (platform, endpoint, finished_at, status, error_reason) "
        "VALUES ('OGE', 'answers', now(), 'error', 'http_503')"
    )
    reason = scalar(
        conn,
        "SELECT last_error_reason FROM mart.data_freshness "
        "WHERE platform = 'OGE' AND endpoint = 'answers'",
    )
    assert reason == "http_503"


@pytest.mark.parametrize("name", VIEWS + MATERIALIZED)
def test_reader_can_read_every_mart(conn, name):
    """sma_reader (аналитики, Streamlit) читает все витрины, в том числе live и data_freshness,
    хотя прав на ops и analytics у него нет: представление работает с правами владельца.
    """
    conn.execute("SET ROLE sma_reader")
    conn.execute(f"SELECT * FROM mart.{name} LIMIT 1").fetchall()


def test_only_loader_can_refresh(conn):
    conn.execute("SET ROLE sma_loader")
    conn.execute("SELECT mart.refresh_all()")
    conn.rollback()
    conn.execute("SET ROLE sma_reader")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT mart.refresh_all()")


def test_top_wrong_is_json_array(conn):
    value = scalar(conn, "SELECT top_wrong FROM mart.task_stats LIMIT 1")
    assert isinstance(value, list)
    json.dumps(value)
