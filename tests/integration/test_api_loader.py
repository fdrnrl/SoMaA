"""Загрузчик + тестовый API по HTTP + PostgreSQL (этап M4): T-02–T-05, T-24, T-32, T-34."""

import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
import requests
from werkzeug.serving import make_server

from sma import contract
from sma.generator.generate import generate
from sma.loader import store
from sma.loader.cycle import ALL_ENDPOINTS, LoaderSettings, run_cycle
from sma.loader.run import Clock, PlatformJob, run_platform
from sma.loader.sources import ApiSource, FileSource
from sma.mockapi.server import create_app
from sma.timeutil import parse_datetime

PROFILE = Path(__file__).resolve().parents[2] / "config" / "generator" / "small.yaml"
TOKEN = "test-token"
ADMIN_TOKEN = "admin-token"
SETTINGS = LoaderSettings()


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("small")
    generate(PROFILE, out, 2026)
    return out


@pytest.fixture(scope="module")
def server_url(dataset):
    """Настоящий HTTP-сервер тестового API в отдельном потоке на свободном порту."""
    app = create_app(dataset, TOKEN, admin_enabled=True, admin_token=ADMIN_TOKEN)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


class Mock:
    """Клиент тестового API для тестов: источники и управляющие адреса."""

    def __init__(self, url: str):
        self.url = url

    def source(self, platform="oge", **kwargs) -> ApiSource:
        return ApiSource(f"{self.url}/{platform}", TOKEN, **kwargs)

    def admin(self, path: str, **body) -> dict | list:
        headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        if path == "requests":
            response = requests.get(f"{self.url}/_admin/requests", headers=headers, timeout=5)
        else:
            response = requests.post(
                f"{self.url}/_admin/{path}", json=body, headers=headers, timeout=5
            )
        response.raise_for_status()
        return response.json()


@pytest.fixture
def mock(server_url):
    client = Mock(server_url)
    client.admin("reset")
    return client


def count(conn, table, where="TRUE", params=()) -> int:
    return conn.execute(f"SELECT count(*) FROM {table} WHERE {where}", params).fetchone()[0]


def load_answers(conn, source):
    return run_cycle(conn, source, "OGE", SETTINGS, ("answers",))[0]


def newest_answer(dataset) -> dict:
    items = FileSource(dataset / "oge").read_items("answers")
    return max(items, key=lambda item: parse_datetime(item["updated_at"]))


def test_api_loads_same_as_files(loader_db, mock, dataset):
    results = run_cycle(loader_db, mock.source(), "OGE", SETTINGS)
    assert [r.endpoint for r in results if r.status != "ok"] == []
    assert all(r.http_status == 200 for r in results)
    files = FileSource(dataset / "oge")
    assert count(loader_db, "core.answers") == len(files.read_items("answers"))
    assert count(loader_db, "core.kim_tasks") == len(files.read_items("dictionaries/kim"))


def test_api_new_record_within_60s(loader_db, mock, dataset):
    """T-02: новая запись на платформе после следующего цикла — в core."""
    source = mock.source()
    load_answers(loader_db, source)
    record = dict(newest_answer(dataset))
    record["record_id"] = "new-answer"
    record["task_number"] = 1
    newest = parse_datetime(record["updated_at"])
    record["updated_at"] = (newest + timedelta(minutes=1)).isoformat()
    mock.admin("inject", platform="oge", endpoint="answers", record=record)

    started = time.monotonic()
    result = load_answers(loader_db, source)
    assert time.monotonic() - started < 60
    assert result.status == "ok" and result.saved >= 1
    assert count(loader_db, "core.answers", "record_id = 'new-answer'") == 1


def test_api_changed_record_is_updated(loader_db, mock, dataset):
    """T-04: та же запись с новым updated_at и другим баллом — обновлена, строк не прибавилось."""
    source = mock.source()
    load_answers(loader_db, source)
    rows_before = count(loader_db, "core.answers")
    record = dict(newest_answer(dataset))
    record["is_correct"] = not record["is_correct"]
    record["score"] = int(record["is_correct"])
    record["updated_at"] = (parse_datetime(record["updated_at"]) + timedelta(hours=1)).isoformat()
    mock.admin("inject", platform="oge", endpoint="answers", record=record)

    load_answers(loader_db, source)
    assert count(loader_db, "core.answers") == rows_before
    score = loader_db.execute(
        "SELECT score FROM core.answers WHERE record_id = %s", (record["record_id"],)
    ).fetchone()[0]
    assert score == record["score"]


def test_missing_field_rejected(loader_db, mock):
    """T-03: inject_bad missing_field → не сохранено; в ops.rejected_records поле и record_id."""
    bad = mock.admin("inject_bad", platform="oge", endpoint="answers", kind="missing_field")
    result = load_answers(loader_db, mock.source())
    assert result.status == "partial" and result.rejected == 1
    assert count(loader_db, "core.answers", "record_id = %s", (bad["record_id"],)) == 0
    row = loader_db.execute(
        "SELECT record_id, reason_code, field FROM ops.rejected_records"
    ).fetchone()
    assert row == (bad["record_id"], "missing_field", bad["field"])


@pytest.mark.parametrize("key", sorted(contract.FORBIDDEN_KEYS))
def test_pii_rejected(loader_db, mock, key):
    """T-32: запись с любым запрещенным ключом не сохраняется."""
    bad = mock.admin("inject_bad", platform="oge", endpoint="answers", kind="pii_field", key=key)
    result = load_answers(loader_db, mock.source())
    assert result.rejected == 1
    assert count(loader_db, "core.answers", "record_id = %s", (bad["record_id"],)) == 0
    reason, payload = loader_db.execute(
        "SELECT reason_code, payload FROM ops.rejected_records"
    ).fetchone()
    assert reason == "pii_field"
    assert payload[key] == "<removed>"


@pytest.mark.parametrize(
    "mode, reason",
    [("down", "http_503"), ("broken_json", "bad_json"), ("no_wrapper", "no_wrapper")],
)
def test_api_failure_modes(loader_db, mock, mode, reason):
    """T-05: сбой → ops.load_log со статусом error и причиной; водяной знак не сдвинут."""
    source = mock.source()
    load_answers(loader_db, source)
    mark = store.get_watermark(loader_db, "OGE", "answers")
    mock.admin("inject_bad", platform="oge", endpoint="answers", kind="bad_enum")
    mock.admin("mode", platform="oge", mode=mode)

    result = load_answers(loader_db, source)
    assert (result.status, result.error_reason) == ("error", reason)
    assert store.get_watermark(loader_db, "OGE", "answers") == mark
    last = loader_db.execute(
        "SELECT status, error_reason FROM ops.load_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert last == ("error", reason)

    mock.admin("mode", platform="oge", mode="normal")
    assert load_answers(loader_db, source).status == "partial", "загрузчик продолжает работу"


def test_api_slow_is_timeout(loader_db, mock):
    """T-05: ответ дольше таймаута → error timeout (таймаут уменьшен, чтобы тест был быстрым)."""
    mock.admin("mode", platform="oge", mode="slow", delay_sec=1.5)
    result = load_answers(loader_db, mock.source(timeout_sec=0.5))
    assert (result.status, result.error_reason) == ("error", "timeout")
    assert store.get_watermark(loader_db, "OGE", "answers") is None


def test_network_error(loader_db):
    source = ApiSource("http://127.0.0.1:9/oge", TOKEN, timeout_sec=1)
    result = load_answers(loader_db, source)
    assert (result.status, result.error_reason) == ("error", "network")


def test_wrong_token_is_401(loader_db, server_url):
    source = ApiSource(f"{server_url}/oge", "wrong-token")
    result = load_answers(loader_db, source)
    assert (result.error_reason, result.http_status) == ("http_401", 401)


def test_restart_resume(loader_db, mock, dataset):
    """T-34: остановка после части страниц и повторный запуск — данных столько же, сколько
    без остановки, и без дублей.
    """
    source = mock.source(page_size=100)
    fetched = {"pages": 0}

    def stop_after_three_pages() -> bool:
        fetched["pages"] += 1
        return fetched["pages"] > 3

    first = run_cycle(loader_db, source, "OGE", SETTINGS, ("answers",), stop_after_three_pages)[0]
    assert (first.status, first.error_reason) == ("error", "stopped")
    assert (
        0
        < count(loader_db, "core.answers")
        < len(FileSource(dataset / "oge").read_items("answers"))
    )
    assert store.get_watermark(loader_db, "OGE", "answers") is None

    again = load_answers(loader_db, source)
    assert again.status == "ok"
    expected = len(FileSource(dataset / "oge").read_items("answers"))
    assert count(loader_db, "core.answers") == expected


def test_scheduler_with_real_server(loader_db, mock):
    """T-24 на настоящем сервере: потоки, интервалы и остановка.

    Интервал уменьшен до 2 с, чтобы тест шел секунды, а не минуты.
    """
    intervals = {endpoint: 2.0 for endpoint in ALL_ENDPOINTS}
    job = PlatformJob("OGE", mock.source(), intervals, SETTINGS)
    stop = threading.Event()
    thread = threading.Thread(target=run_platform, args=(job, loader_db, stop, Clock()))
    thread.start()
    time.sleep(5)
    stop.set()
    thread.join(timeout=10)
    assert not thread.is_alive(), "планировщик останавливается по сигналу"

    log = mock.admin("requests")
    for endpoint in ALL_ENDPOINTS:
        starts = [
            r["monotonic"] for r in log if r["endpoint"] == endpoint and r["page"] in ("1", None)
        ]
        assert len(starts) >= 2, endpoint
        gaps = [later - earlier for earlier, later in zip(starts, starts[1:], strict=False)]
        assert min(gaps) >= 2 - 0.05, (endpoint, gaps)
    assert count(loader_db, "ops.load_log", "status = 'error'") == 0
