"""Витрины на каждом этапе экзамена (как на Demo Day): набор medium «проигрывается» тестовым
API, загрузчик забирает данные по HTTP, витрины обновляются — и на каждом этапе совпадают
с версиями записей генератора, а проверки качества не находят error и warning.
"""

import json
import threading
from collections import Counter

import pytest
from werkzeug.serving import make_server

from sma.db import connect
from sma.generator.timeline import snapshot
from sma.loader import store
from sma.loader.cycle import LoaderSettings, run_cycle
from sma.loader.sources import ApiSource
from sma.mockapi.server import create_app
from sma.quality.checks import run_checks
from sma.timeutil import parse_datetime
from tests.conftest import LOADER_TABLES

EXAMS = ["EGE", "OGE", "VPR", "NIKO"]
STAGES = ["during_exam", "after_exam", "after_check", "after_appeal"]
TOKEN = "test-token"


class ManualClock:
    moment = None

    def now(self):
        return self.moment


@pytest.fixture(scope="module")
def replay(medium_dataset, test_database):
    """Тестовый API в режиме воспроизведения и пустые таблицы загрузчика."""
    with connect(dbname=test_database) as conn:
        conn.execute(f"TRUNCATE {LOADER_TABLES} RESTART IDENTITY CASCADE")
        conn.commit()
    clock = ManualClock()
    server = make_server("127.0.0.1", 0, create_app(medium_dataset, TOKEN, replay=clock))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield clock, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    with connect(dbname=test_database) as conn:
        conn.execute(f"TRUNCATE {LOADER_TABLES} RESTART IDENTITY CASCADE")
        conn.commit()


def expected_sessions(dataset, exam_type: str, moment) -> list[dict]:
    """Сессии, видимые на платформе в момент moment, — по timeline генератора."""
    path = dataset / exam_type.lower() / "timeline" / "sessions.jsonl"
    versions = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return snapshot(versions, moment)


def test_marts_at_every_stage(replay, medium_dataset, test_database):
    clock, url = replay
    manifest = json.loads((medium_dataset / "manifest.json").read_text(encoding="utf-8"))
    moments = manifest["platforms"]["OGE"]["snapshots"]
    with connect(dbname=test_database) as conn:
        for stage in STAGES:
            clock.moment = parse_datetime(moments[stage])
            for exam_type in EXAMS:
                source = ApiSource(f"{url}/{exam_type.lower()}", TOKEN)
                results = run_cycle(conn, source, exam_type, LoaderSettings())
                assert [r.endpoint for r in results if r.status == "error"] == [], stage
            store.refresh_marts(conn)

            issues = [i for i in run_checks(conn) if i.severity in ("error", "warning")]
            assert issues == [], (stage, issues)
            for exam_type in EXAMS:
                sessions = expected_sessions(medium_dataset, exam_type, clock.moment)
                statuses = Counter(s["status"] for s in sessions)
                results_ready = sum(
                    s["status"] == "finished" and s["result_status"] != "pending" for s in sessions
                )
                got = Counter(dict(conn.execute(
                    "SELECT status, count(*) FROM mart.sessions_current WHERE exam_type = %s "
                    "GROUP BY status", (exam_type,)).fetchall()))  # fmt: skip
                assert got == statuses, (stage, exam_type)
                done = conn.execute(
                    "SELECT coalesce(sum(participants), 0) FROM mart.school_results "
                    "WHERE exam_type = %s",
                    (exam_type,),
                ).fetchone()[0]
                assert done == results_ready, (stage, exam_type)
                live = conn.execute(
                    "SELECT in_progress, finished, absent FROM mart.live WHERE exam_type = %s",
                    (exam_type,),
                ).fetchone()
                running = statuses["started"] + statuses["in_progress"]
                assert live == (running, statuses["finished"], statuses["absent"]), stage
                kim = conn.execute(
                    "SELECT count(*) FROM mart.kim_stats WHERE exam_type = %s", (exam_type,)
                ).fetchone()[0]
                assert (kim > 0) == (stage != "during_exam"), "КИМ — только после экзамена"


def test_planted_anomalies_visible_in_marts(replay, medium_dataset, test_database):
    """После всех этапов (предыдущий тест) закладки из ключа видны в витринах."""
    key = json.loads((medium_dataset / "answer_key.json").read_text(encoding="utf-8"))
    with connect(dbname=test_database) as conn:
        for exam_type, info in key["exams"].items():
            planted = {rule: [p for p in info["planted"] if p["rule"] == rule]
                       for rule in ("A1", "A3", "P4a")}  # fmt: skip
            top_school = conn.execute(
                """
                WITH big AS (SELECT * FROM mart.school_results
                             WHERE exam_type = %s AND participants >= 10),
                     scored AS (SELECT school_code, count(*) OVER r AS n,
                                       (mean_score - avg(mean_score) OVER r)
                                       / nullif(stddev_samp(mean_score) OVER r, 0) AS z
                                FROM big WINDOW r AS (PARTITION BY region_code))
                SELECT school_code FROM scored WHERE n >= 3 ORDER BY z DESC NULLS LAST LIMIT 1
                """,
                (exam_type,),
            ).fetchone()[0]
            assert top_school == planted["A1"][0]["school_code"], exam_type
            a3 = {row[0] for row in conn.execute(
                "SELECT participant_id FROM mart.proctoring_counts "
                "WHERE exam_type = %s AND counted_events >= 3", (exam_type,))}  # fmt: skip
            assert a3 == {p["participant_id"] for p in planted["A3"]}, exam_type
            same_scores = {row[0] for row in conn.execute(
                "SELECT school_code FROM mart.school_results "
                "WHERE exam_type = %s AND sd_score = 0", (exam_type,))}  # fmt: skip
            assert same_scores == {p["school_code"] for p in planted["P4a"]}, exam_type
