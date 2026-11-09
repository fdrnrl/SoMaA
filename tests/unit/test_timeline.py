"""Версии записей во времени и снимки набора (раздел 10.6)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from sma.generator.generate import generate
from sma.generator.timeline import live_status, snapshot
from sma.generator.writer import endpoint_file_stem
from sma.loader.sources import FileSource
from sma.loader.validate import validate_record
from sma.timeutil import parse_datetime

SMALL = Path(__file__).resolve().parents[2] / "config" / "generator" / "small.yaml"
EXAMS = ["EGE", "OGE", "VPR", "NIKO"]
TIMELINE = ["sessions", "answers", "expert-reviews", "proctoring-events",
            "dictionaries/kim", "dictionaries/scales"]  # fmt: skip
ORDER = {"started": 0, "in_progress": 1, "finished": 2, "annulled": 2}


def make_dataset(tmp_path_factory, name: str, **changes) -> Path:
    """Набор по профилю small с изменениями (rates.appeal, snapshot)."""
    profile = yaml.safe_load(SMALL.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if key == "appeal":
            profile["rates"]["appeal"] = value
        else:
            profile[key] = value
    folder = tmp_path_factory.mktemp(name)
    path = folder / "profile.yaml"
    path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    generate(path, folder / "data", 2026)
    return folder / "data"


@pytest.fixture(scope="module")
def appeal_dataset(tmp_path_factory):
    return make_dataset(tmp_path_factory, "appeal", appeal=0.3)


def versions(dataset: Path, exam_type: str, endpoint: str) -> list[dict]:
    path = dataset / exam_type.lower() / "timeline" / f"{endpoint_file_stem(endpoint)}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def by_record(items: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for item in items:
        grouped.setdefault(item["record_id"], []).append(item)
    for history in grouped.values():
        history.sort(key=lambda v: parse_datetime(v["updated_at"]))
    return grouped


def test_snapshot_takes_latest_visible_version():
    moment = datetime(2026, 12, 5, 10, 0, tzinfo=UTC)
    items = [
        {"record_id": "a", "updated_at": moment.isoformat(), "v": 1},
        {"record_id": "a", "updated_at": (moment + timedelta(hours=1)).isoformat(), "v": 2},
        {"record_id": "b", "updated_at": (moment + timedelta(hours=2)).isoformat(), "v": 1},
    ]
    assert [r["v"] for r in snapshot(items)] == [2, 1]
    assert [r["v"] for r in snapshot(items, moment + timedelta(minutes=30))] == [1]
    assert snapshot(items, moment - timedelta(seconds=1)) == []


def test_live_status_counts():
    sessions = [{"status": s} for s in ("started", "in_progress", "finished", "absent", "annulled")]
    status = live_status("OGE", sessions, datetime(2026, 12, 5, 10, 0, tzinfo=UTC))
    assert (status["in_progress"], status["finished"], status["absent"], status["annulled"]) == (
        2, 1, 1, 1,
    )  # fmt: skip


@pytest.mark.parametrize("exam_type", EXAMS)
def test_timeline_files_and_versions_valid(appeal_dataset, exam_type):
    """Каждая версия в timeline — корректная запись контракта (плюс поле visible_from)."""
    for endpoint in TIMELINE:
        items = versions(appeal_dataset, exam_type, endpoint)
        for item in items:
            assert item["visible_from"] == item["updated_at"]
            check = validate_record(item, endpoint, exam_type)
            assert check.ok, (endpoint, check.problems)
            assert check.dropped == ["visible_from"]


@pytest.mark.parametrize("exam_type", EXAMS)
def test_snapshot_files_match_timeline(appeal_dataset, exam_type):
    manifest = json.loads((appeal_dataset / "manifest.json").read_text(encoding="utf-8"))
    moment = parse_datetime(manifest["platforms"][exam_type]["snapshot_at"])
    source = FileSource(appeal_dataset / exam_type.lower())
    for endpoint in TIMELINE:
        expected = snapshot(versions(appeal_dataset, exam_type, endpoint), moment)
        expected = sorted((r["record_id"], r["updated_at"]) for r in expected)
        actual = sorted((r["record_id"], r["updated_at"]) for r in source.read_items(endpoint))
        assert actual == expected, endpoint


@pytest.mark.parametrize("exam_type", EXAMS)
def test_session_lifecycle(appeal_dataset, exam_type):
    """started → in_progress → finished (pending) → final → appeal_changed; время растет."""
    for history in by_record(versions(appeal_dataset, exam_type, "sessions")).values():
        moments = [parse_datetime(v["updated_at"]) for v in history]
        assert moments == sorted(moments) and len(set(moments)) == len(moments)
        if history[0]["status"] == "absent":
            assert len(history) == 1 and history[0]["result_status"] == "final"
            continue
        assert history[0]["status"] == "started" and history[0]["result_status"] == "pending"
        steps = [ORDER[v["status"]] for v in history]
        assert steps == sorted(steps)
        results = [v["result_status"] for v in history]
        if history[-1]["status"] == "annulled":
            assert history[-1]["primary_score"] is None and results[-1] == "final"
            continue
        finished = [v for v in history if v["status"] == "finished"]
        assert finished[0]["result_status"] == "pending" and finished[0]["primary_score"] is None
        assert finished[1]["result_status"] == "final" and finished[1]["primary_score"] is not None
        assert results[-1] in ("final", "appeal_changed")


@pytest.mark.parametrize("exam_type", EXAMS)
def test_review_lifecycle_and_appeal(appeal_dataset, exam_type):
    """pending (score null) → checked first → для апелляции checked appeal; итог сессии
    после апелляции = сумма последних баллов.
    """
    reviews = by_record(versions(appeal_dataset, exam_type, "expert-reviews"))
    appealed = set()
    for history in reviews.values():
        first, checked = history[0], history[1]
        assert (first["check_status"], first["score"], first["check_stage"]) == (
            "pending",
            None,
            None,
        )
        assert (checked["check_status"], checked["check_stage"]) == ("checked", "first")
        if len(history) == 3:
            assert history[2]["check_stage"] == "appeal"
            assert abs(history[2]["score"] - checked["score"]) == 1
            appealed.add(history[2]["participant_id"])
    sessions = {h[-1]["participant_id"]: h[-1] for h in by_record(
        versions(appeal_dataset, exam_type, "sessions")).values()}  # fmt: skip
    changed = {pid for pid, s in sessions.items() if s["result_status"] == "appeal_changed"}
    assert changed == appealed and changed, "при доле апелляций 0,3 они есть"

    latest_answers = snapshot(versions(appeal_dataset, exam_type, "answers"))
    latest_reviews = snapshot(versions(appeal_dataset, exam_type, "expert-reviews"))
    for pid in changed:
        total = sum(a["score"] for a in latest_answers if a["participant_id"] == pid)
        total += sum(r["score"] for r in latest_reviews if r["participant_id"] == pid)
        assert sessions[pid]["primary_score"] == total


def test_during_exam_snapshot(tmp_path_factory):
    dataset = make_dataset(tmp_path_factory, "during", snapshot="during_exam")
    source = FileSource(dataset / "oge")
    statuses = {s["status"] for s in source.read_items("sessions")}
    assert statuses & {"started", "in_progress"}
    assert source.read_items("dictionaries/kim") == [], "КИМ — только после экзамена"
    assert all(r["check_status"] == "pending" for r in source.read_items("expert-reviews"))
    assert source.read_live_status()["in_progress"] > 0


def test_after_exam_snapshot(tmp_path_factory):
    dataset = make_dataset(tmp_path_factory, "after", snapshot="after_exam")
    source = FileSource(dataset / "ege")
    sessions = source.read_items("sessions")
    assert {s["status"] for s in sessions} <= {"finished", "absent", "annulled"}
    assert all(s["primary_score"] is None for s in sessions)
    assert {s["result_status"] for s in sessions if s["status"] == "finished"} == {"pending"}
    assert {r["check_status"] for r in source.read_items("expert-reviews")} == {"pending"}
    assert source.read_items("dictionaries/kim"), "после конца экзамена КИМ уже есть"
