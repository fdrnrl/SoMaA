"""Расчет аналитики по данным базы, сохранение в analytics и чтение дашбордом (миграция 006).

Путь целиком: набор medium → загрузчик → витрины → sma.analytics_job (модуль аналитика)
→ analytics → витрины mart.anomalies, mart.analysis_runs, mart.school_index, mart.p1_groups
→ слой данных дашборда (dashboard.data) под ролью sma_reader.
"""

import json
import os

import pytest

from dashboard import data as dashboard_data
from sma import analytics_job
from sma.db import connect
from sma.loader import store
from sma.loader.cycle import LoaderSettings, run_cycle
from sma.loader.sources import FileSource
from tests.conftest import LOADER_TABLES

EXAMS = ["EGE", "OGE", "VPR", "NIKO"]
ANALYTICS = (
    "analytics.anomalies, analytics.p1_groups, analytics.school_index, analytics.analysis_runs"
)
SOURCES = {
    "anomalies": "mart.anomalies",
    "runs": "mart.analysis_runs",
    "schools": "mart.school_index",
    "p1_groups": "mart.p1_groups",
}


def clear(conn) -> None:
    conn.execute(f"TRUNCATE {LOADER_TABLES}, {ANALYTICS} RESTART IDENTITY CASCADE")
    conn.commit()


@pytest.fixture(scope="module")
def loaded(test_database, medium_dataset):
    with connect(dbname=test_database) as conn:
        clear(conn)
        for exam_type in EXAMS:
            run_cycle(
                conn, FileSource(medium_dataset / exam_type.lower()), exam_type, LoaderSettings()
            )
        store.refresh_marts(conn)
    yield test_database
    with connect(dbname=test_database) as conn:
        clear(conn)


@pytest.fixture
def app(loaded):
    """Соединение под ролью sma_app — как у расчета в загрузчике. Результаты чистятся."""
    with connect(dbname=loaded) as conn:
        conn.execute(f"TRUNCATE {ANALYTICS} RESTART IDENTITY CASCADE")
        conn.execute("SET ROLE sma_app")
        conn.commit()
        yield conn
        conn.rollback()
        conn.execute("RESET ROLE")
        conn.execute(f"TRUNCATE {ANALYTICS} RESTART IDENTITY CASCADE")
        conn.commit()


def reader(dbname: str):
    """Соединение для дашборда под ролью sma_reader."""

    def factory():
        return connect(
            dbname=dbname, user="sma_reader", password=os.environ.get("SMA_READER_PASSWORD", "")
        )

    return factory


def one(conn, query: str, params=()):
    return conn.execute(query, params).fetchone()[0]


def test_run_saves_everything_in_one_completed_run(app, medium_dataset):
    run_id = analytics_job.run_analysis(app, "manual")
    status, scope, data_as_of, summary = app.execute(
        "SELECT status, scope, data_as_of, summary FROM analytics.analysis_runs WHERE run_id = %s",
        (run_id,),
    ).fetchone()
    assert status == "ok" and data_as_of is not None
    assert {item["exam_type"] for item in scope} == set(EXAMS)
    assert summary["anomalies"] > 0 and summary["schools"] > 0
    assert one(app, "SELECT count(DISTINCT run_id) FROM analytics.anomalies") == 1
    assert (
        one(app, "SELECT count(*) FROM analytics.school_index WHERE run_id <> %s", (run_id,)) == 0
    )


def test_planted_anomalies_saved(app, medium_dataset):
    """А1 и А3 из ключа генератора — в analytics.anomalies (расчет — модуль аналитика)."""
    analytics_job.run_analysis(app)
    key = json.loads((medium_dataset / "answer_key.json").read_text(encoding="utf-8"))
    saved = set(
        app.execute("SELECT rule, exam_type, object_id FROM analytics.anomalies").fetchall()
    )
    for exam_type, info in key["exams"].items():
        for item in info["planted"]:
            if item["rule"] == "A1":
                assert ("A1", exam_type, item["school_code"]) in saved
            if item["rule"] == "A3":
                assert ("A3", exam_type, item["participant_id"]) in saved
    a2 = app.execute("SELECT details FROM analytics.anomalies WHERE rule = 'A2' LIMIT 1").fetchone()
    assert len(a2[0]["participant_ids"]) >= 5, "состав группы А2 сохранен в details"


def test_rerun_keeps_anomaly_ids_and_marks_confirmation(app):
    """Повторный расчет: те же anomaly_id, без дублей; run_id — первый, last_run_id — новый."""
    first = analytics_job.run_analysis(app)
    before = dict(
        app.execute("SELECT object_id || rule, anomaly_id FROM analytics.anomalies").fetchall()
    )
    second = analytics_job.run_analysis(app)
    after = dict(
        app.execute("SELECT object_id || rule, anomaly_id FROM analytics.anomalies").fetchall()
    )
    assert after == before
    assert set(
        app.execute("SELECT DISTINCT run_id, last_run_id FROM analytics.anomalies").fetchall()
    ) == {(first, second)}
    flags = set(app.execute("SELECT is_active, is_new FROM mart.anomalies").fetchall())
    assert flags == {(True, False)}, "подтверждены последним расчетом, но не новые"


def test_failed_run_is_hidden_and_leaves_no_rows(app, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("сбой расчета")

    monkeypatch.setattr(analytics_job, "analyze_platform", broken)
    with pytest.raises(RuntimeError):
        analytics_job.run_analysis(app)
    status, error = app.execute("SELECT status, error FROM analytics.analysis_runs").fetchone()
    assert status == "error" and "сбой" in error
    assert one(app, "SELECT count(*) FROM mart.analysis_runs") == 0
    assert one(app, "SELECT count(*) FROM analytics.anomalies") == 0


def test_dashboard_reads_results(app, loaded):
    """Дашборд своим слоем данных читает результаты под sma_reader (путь «расчет → показ»)."""
    run_id = analytics_job.run_analysis(app)
    with dashboard_data.open_snapshot(sources=SOURCES, connection_factory=reader(loaded)) as snap:
        anomalies = snap.anomalies()
        runs = snap.analysis_runs()
        schools = snap.school_counts(run_id)
        oge = snap.anomalies("OGE")
    assert anomalies and runs[0]["run_id"] == run_id
    assert {row["rule"] for row in anomalies} >= {"A1", "A2", "A3"}
    assert all(row["exam_type"] == "OGE" for row in oge)
    assert isinstance(schools, list)


def test_dashboard_sees_p1_groups_of_school(app, loaded):
    run_id = analytics_job.run_analysis(app)
    exam, subject, school = app.execute(
        "SELECT exam_type, subject_code, school_code FROM analytics.p1_groups LIMIT 1"
    ).fetchone()
    with dashboard_data.open_snapshot(sources=SOURCES, connection_factory=reader(loaded)) as snap:
        groups = snap.p1_groups(run_id, exam, subject, school)
    assert groups and all(len(g["participant_ids"]) >= 2 for g in groups)


def test_empty_result_differs_from_missing_run(app, monkeypatch):
    """Нет данных → завершенный расчет с пустой областью и без строк, а не «расчета нет»."""
    monkeypatch.setattr(analytics_job, "read_inputs", lambda conn: {})
    run_id = analytics_job.run_analysis(app)
    assert one(app, "SELECT scope FROM mart.analysis_runs WHERE run_id = %s", (run_id,)) == []
    assert one(app, "SELECT count(*) FROM mart.anomalies") == 0


def test_scheduler_runs_periodically_and_once_after_exam(app, monkeypatch):
    """FR-30 через планировщик модуля аналитика: первый тик — расчет,
    следующий раньше 10 минут — нет.
    """
    monkeypatch.setattr(analytics_job, "app_connection", lambda: _NoClose(app))
    runner = analytics_job.AnalysisRunner()
    runner.tick(0)
    runner.tick(analytics_job.AnalysisRunner.CHECK_SEC + 1)
    reasons = [
        row[0] for row in app.execute("SELECT reason FROM analytics.analysis_runs ORDER BY run_id")
    ]
    assert reasons == ["final"], "набор medium — экзамен завершен: один финальный расчет"


class _NoClose:
    """Соединение теста для `with app_connection() as conn` без закрытия."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False
