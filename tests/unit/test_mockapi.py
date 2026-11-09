"""Тестовый API (раздел 9): контракт 6.1, режимы сбоев, управляющие адреса."""

import json
import math
import time
from datetime import timedelta
from pathlib import Path

import pytest

from sma.generator.generate import generate
from sma.loader.sources import FileSource
from sma.loader.validate import validate_record
from sma.mockapi.server import create_app
from sma.timeutil import parse_datetime

PROFILE = Path(__file__).resolve().parents[2] / "config" / "generator" / "small.yaml"
TOKEN = "test-token"
ADMIN_TOKEN = "admin-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("small")
    generate(PROFILE, out, 2026)
    return out


@pytest.fixture
def app(dataset):
    return create_app(dataset, TOKEN, admin_enabled=True, admin_token=ADMIN_TOKEN)


@pytest.fixture
def client(app):
    return app.test_client()


def get(client, endpoint, platform="oge", headers=AUTH, **params):
    return client.get(
        f"/{platform}/api/v1/analytics/{endpoint}", query_string=params, headers=headers
    )


def admin(client, path, **body):
    return client.post(f"/_admin/{path}", json=body, headers=ADMIN)


def all_items(client, endpoint, platform="oge", **params):
    first = get(client, endpoint, platform, **params).get_json()
    items = list(first["items"])
    for page in range(2, first["total_pages"] + 1):
        items += get(client, endpoint, platform, page=page, **params).get_json()["items"]
    return items


def test_requires_token(client):
    assert get(client, "answers", headers={}).status_code == 401
    assert get(client, "answers", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert get(client, "answers", headers={"Authorization": TOKEN}).status_code == 401


def test_only_get(client):
    response = client.post("/oge/api/v1/analytics/answers", headers=AUTH)
    assert response.status_code == 405


@pytest.mark.parametrize(
    "params",
    [
        {"since": "вчера"},
        {"since": "2026-12-05T10:00:00"},
        {"page": 0},
        {"page": "x"},
        {"limit": 1001},
        {"limit": 0},
    ],
)
def test_bad_params(client, params):
    assert get(client, "answers", **params).status_code == 400


def test_unknown_platform_and_endpoint(client):
    assert get(client, "answers", platform="gia").status_code == 404
    assert get(client, "results").status_code == 404


def test_wrapper_order_and_fields(client, dataset):
    body = get(client, "answers").get_json()
    assert list(body) == ["page", "total_pages", "items"]
    items = body["items"]
    moments = [(parse_datetime(i["updated_at"]), i["record_id"]) for i in items]
    assert moments == sorted(moments)
    from_file = {i["record_id"]: i for i in FileSource(dataset / "oge").read_items("answers")}
    assert list(items[0]) == list(from_file[items[0]["record_id"]]), "ключи в порядке контракта"


def test_since_filter_is_strict(client):
    items = all_items(client, "answers")
    middle = items[len(items) // 2]["updated_at"]
    after = all_items(client, "answers", since=middle)
    assert after
    assert all(parse_datetime(i["updated_at"]) > parse_datetime(middle) for i in after)


def test_pages_with_limit(client):
    everything = all_items(client, "answers")
    first = get(client, "answers", limit=100).get_json()
    assert first["total_pages"] == math.ceil(len(everything) / 100)
    paged = all_items(client, "answers", limit=100)
    assert paged == everything


def test_live_status_is_object(client):
    body = get(client, "live-status").get_json()
    assert body["exam_type"] == "OGE" and "items" not in body


def test_empty_list_is_not_error(client):
    response = get(client, "proctoring-events", platform="vpr")
    assert response.status_code == 200
    assert response.get_json() == {"page": 1, "total_pages": 1, "items": []}


def test_kim_hidden_until_exam_end(app, client):
    state = app.config["MOCK_STATE"]
    assert get(client, "dictionaries/kim").get_json()["items"], (
        "снимок after_check — после экзамена"
    )
    state.clock["oge"] = state.exam_end["oge"] - timedelta(minutes=1)
    assert get(client, "dictionaries/kim").get_json()["items"] == []


def test_mode_down(client):
    admin(client, "mode", platform="oge", mode="down")
    assert get(client, "answers").status_code == 503
    assert get(client, "answers", platform="ege").status_code == 200, "только одна платформа"


def test_mode_broken_json(client):
    admin(client, "mode", platform="oge", mode="broken_json")
    response = get(client, "answers")
    with pytest.raises(json.JSONDecodeError):
        json.loads(response.get_data(as_text=True))


def test_mode_no_wrapper(client):
    admin(client, "mode", platform="oge", mode="no_wrapper")
    assert isinstance(get(client, "answers").get_json(), list)


def test_mode_slow(client):
    admin(client, "mode", platform="oge", mode="slow", delay_sec=0.3)
    started = time.monotonic()
    assert get(client, "sessions").status_code == 200
    assert time.monotonic() - started >= 0.3


def test_admin_needs_its_token(client):
    response = client.post("/_admin/mode", json={"platform": "oge", "mode": "down"}, headers=AUTH)
    assert response.status_code == 401
    assert get(client, "answers").status_code == 200


def test_admin_disabled_by_default(dataset):
    plain = create_app(dataset, TOKEN).test_client()
    assert plain.post("/_admin/reset", headers=ADMIN).status_code == 404


def test_inject_replaces_by_record_id(client):
    items = all_items(client, "answers")
    record = dict(items[0])
    record["score"] = 0 if record["score"] else 1
    assert (
        admin(client, "inject", platform="oge", endpoint="answers", record=record).status_code
        == 200
    )
    again = {i["record_id"]: i for i in all_items(client, "answers")}
    assert len(again) == len(items)
    assert again[record["record_id"]]["score"] == record["score"]


@pytest.mark.parametrize(
    "kind, reason",
    [
        ("missing_field", "missing_field"),
        ("pii_field", "pii_field"),
        ("wrong_type", "wrong_type"),
        ("bad_enum", "bad_enum"),
    ],
)
def test_inject_bad_makes_rejectable_record(client, kind, reason):
    body = admin(client, "inject_bad", platform="oge", endpoint="answers", kind=kind).get_json()
    served = {i["record_id"]: i for i in all_items(client, "answers")}
    record = served[body["record_id"]]
    check = validate_record(record, "answers", "OGE")
    assert not check.ok
    assert (reason, body["field"]) in [(p.reason_code, p.field) for p in check.problems]
    newest = max(parse_datetime(i["updated_at"]) for i in served.values() if i is not record)
    assert parse_datetime(record["updated_at"]) > newest, "новее всех, чтобы попасть в since"


def test_requests_log_and_reset(client):
    get(client, "answers", since="2026-12-05T10:00:00+03:00", page=1)
    log = client.get("/_admin/requests", headers=ADMIN).get_json()
    assert log[-1]["endpoint"] == "answers"
    assert log[-1]["since"] == "2026-12-05T10:00:00+03:00"
    assert log[-1]["page"] == "1" and log[-1]["status"] == 200
    admin(client, "mode", platform="oge", mode="down")
    admin(client, "reset")
    assert get(client, "answers").status_code == 200
    assert len(client.get("/_admin/requests", headers=ADMIN).get_json()) == 1


def test_server_needs_token(dataset):
    with pytest.raises(ValueError):
        create_app(dataset, token="")
