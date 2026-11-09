"""Режим воспроизведения тестового API (раздел 9.2) и путь версий до core."""

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest
from werkzeug.serving import make_server

from sma.loader.cycle import LoaderSettings, run_cycle
from sma.loader.sources import ApiSource
from sma.mockapi.server import create_app, exam_start_of
from sma.mockapi.timeline import ReplayClock
from sma.timeutil import parse_datetime
from tests.unit.test_timeline import make_dataset

TOKEN = "test-token"
SETTINGS = LoaderSettings()


class ManualClock:
    """Часы экзамена, которые тест переводит сам."""

    def __init__(self, moment: datetime):
        self.moment = moment

    def now(self) -> datetime:
        return self.moment


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    return make_dataset(tmp_path_factory, "replay", appeal=0.3)


@pytest.fixture(scope="module")
def moments(dataset) -> dict[str, datetime]:
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    snapshots = manifest["platforms"]["OGE"]["snapshots"]
    return {name: parse_datetime(value) for name, value in snapshots.items()}


@pytest.fixture(scope="module")
def replay(dataset):
    """Сервер в режиме воспроизведения с ручными часами."""
    clock = ManualClock(exam_start_of(dataset))
    app = create_app(dataset, TOKEN, replay=clock)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield clock, f"http://127.0.0.1:{server.server_port}", app.test_client()
    server.shutdown()


def scalar(conn, query: str, params=()):
    return conn.execute(query, params).fetchone()[0]


def test_replay_before_exam_is_empty(replay, dataset):
    clock, _, client = replay
    clock.moment = exam_start_of(dataset) - timedelta(hours=1)
    response = client.get(
        "/oge/api/v1/analytics/sessions", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.get_json()["items"] == []
    status = client.get(
        "/oge/api/v1/analytics/live-status", headers={"Authorization": f"Bearer {TOKEN}"}
    ).get_json()
    assert status["in_progress"] == status["finished"] == 0
    assert parse_datetime(status["generated_at"]) == clock.moment


def test_replay_transitions(loader_db, replay, moments, dataset):
    """pending → checked → appeal_changed доходит до core; строк не прибавляется."""
    clock, url, _ = replay
    source = ApiSource(f"{url}/oge", TOKEN)

    def load_at(name: str) -> None:
        clock.moment = moments[name]
        results = run_cycle(loader_db, source, "OGE", SETTINGS)
        assert [r.endpoint for r in results if r.status == "error"] == []

    load_at("during_exam")
    statuses = {row[0] for row in loader_db.execute("SELECT DISTINCT status FROM core.sessions")}
    assert statuses & {"started", "in_progress"}
    assert scalar(loader_db, "SELECT count(*) FROM core.kim_tasks") == 0, "КИМ — после экзамена"

    load_at("after_exam")
    assert scalar(loader_db, "SELECT count(*) FROM core.sessions WHERE status IN "
                             "('started', 'in_progress')") == 0  # fmt: skip
    assert scalar(loader_db, "SELECT count(*) FROM core.expert_reviews "
                             "WHERE check_status = 'checked'") == 0  # fmt: skip
    assert scalar(loader_db, "SELECT count(*) FROM core.kim_tasks") > 0
    pending = scalar(loader_db, "SELECT count(*) FROM core.expert_reviews")

    load_at("after_check")
    assert scalar(loader_db, "SELECT count(*) FROM core.expert_reviews "
                             "WHERE check_status = 'pending'") == 0  # fmt: skip
    assert scalar(loader_db, "SELECT count(*) FROM core.expert_reviews") == pending
    assert scalar(loader_db, "SELECT count(*) FROM core.sessions WHERE status = 'finished' "
                             "AND result_status = 'pending'") == 0  # fmt: skip

    load_at("after_appeal")
    key = json.loads((dataset / "answer_key.json").read_text(encoding="utf-8"))
    appeals = set(key["exams"]["OGE"]["appeals"])
    rows = loader_db.execute(
        "SELECT participant_id FROM core.sessions WHERE result_status = 'appeal_changed'"
    ).fetchall()
    changed = {row[0] for row in rows}
    assert changed == appeals and appeals
    stage_appeal = scalar(
        loader_db, "SELECT count(*) FROM core.expert_reviews WHERE check_stage = 'appeal'"
    )
    assert stage_appeal == len(appeals)
    assert scalar(loader_db, "SELECT count(*) FROM core.sessions") == 40
    assert scalar(loader_db, "SELECT count(*) FROM core.expert_reviews") == pending


def test_replay_clock_runs_faster():
    start = datetime(2026, 12, 5, 7, 0, tzinfo=UTC)
    clock = ReplayClock(start, speed=600, start_offset_sec=60)
    first = clock.now()
    assert first >= start + timedelta(seconds=60)
    threading.Event().wait(0.05)
    assert clock.now() - first >= timedelta(seconds=25), "0,05 с × 600 ≈ 30 с экзамена"
