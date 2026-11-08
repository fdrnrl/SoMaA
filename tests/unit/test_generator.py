"""Генератор целиком на профиле small: файлы, количества, контракт, состояния, детерминизм."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from sma import contract
from sma.generator.exams import ege_test_score, load_exams, niko_level, oge_grade, vpr_grade
from sma.generator.generate import generate
from sma.generator.writer import endpoint_file_stem, write_endpoint
from tests.contract_check import contract_problems, value_problems

PROFILE = Path(__file__).resolve().parents[2] / "config" / "generator" / "small.yaml"
SEED = 2026
EXAMS = ["EGE", "OGE", "VPR", "NIKO"]
LIST_ENDPOINTS = list(contract.ENDPOINT_FIELDS)
PER_EXAM = 40
USED_SCHOOLS = 8


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("small")
    generate(PROFILE, out, SEED)
    return out


def load_items(dataset: Path, exam_type: str, endpoint: str) -> list[dict]:
    """Все записи адреса из файлов набора (одна или несколько страниц)."""
    folder = dataset / exam_type.lower()
    stem = endpoint_file_stem(endpoint)
    paths = sorted(folder.glob(f"{stem}.page-*.json")) or [folder / f"{stem}.json"]
    items = []
    for path in paths:
        items += json.loads(path.read_text(encoding="utf-8"))["items"]
    return items


def by_participant(items: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for item in items:
        grouped.setdefault(item["participant_id"], []).append(item)
    return grouped


def test_files_exist(dataset):
    assert (dataset / "manifest.json").exists()
    assert "синтетические" in (dataset / "README.md").read_text(encoding="utf-8")
    for exam_type in EXAMS:
        folder = dataset / exam_type.lower()
        assert (folder / "live-status.json").exists()
        for endpoint in LIST_ENDPOINTS:
            assert (folder / f"{endpoint_file_stem(endpoint)}.json").exists(), endpoint


@pytest.mark.parametrize("exam_type", EXAMS)
def test_generator_counts(dataset, exam_type):
    sessions = load_items(dataset, exam_type, "sessions")
    assert len(sessions) == PER_EXAM
    assert len({s["school_code"] for s in sessions}) == USED_SCHOOLS
    assert len(load_items(dataset, exam_type, "dictionaries/regions")) == 5
    assert len(load_items(dataset, exam_type, "dictionaries/schools")) == 12
    assert {s["variant_id"] for s in sessions} <= {"v1", "v2"}
    rooms = {s["room_code"] for s in sessions}
    if exam_type == "VPR":
        assert rooms == {None}
    else:
        assert len(rooms) == USED_SCHOOLS, "в small каждая школа помещается в одну аудиторию"


def test_participant_ids_unique_across_exams(dataset):
    ids = [s["participant_id"] for e in EXAMS for s in load_items(dataset, e, "sessions")]
    assert len(ids) == len(set(ids)) == PER_EXAM * len(EXAMS)


@pytest.mark.parametrize("exam_type", EXAMS)
@pytest.mark.parametrize("endpoint", LIST_ENDPOINTS)
def test_records_match_contract(dataset, exam_type, endpoint):
    items = load_items(dataset, exam_type, endpoint)
    problems = [p for item in items for p in contract_problems(item, endpoint)]
    assert problems == [], problems[:10]
    ids = [item["record_id"] for item in items]
    assert len(ids) == len(set(ids)), "record_id повторяется"


@pytest.mark.parametrize("exam_type", EXAMS)
def test_live_status(dataset, exam_type):
    path = dataset / exam_type.lower() / "live-status.json"
    status = json.loads(path.read_text(encoding="utf-8"))
    assert list(status) == [field.name for field in contract.LIVE_STATUS_FIELDS]
    for field in contract.LIVE_STATUS_FIELDS:
        assert value_problems(field.name, field, status[field.name]) == []
    sessions = load_items(dataset, exam_type, "sessions")
    for name in ("finished", "annulled", "absent"):
        assert status[name] == sum(s["status"] == name for s in sessions)


def all_keys(value):
    """Все ключи словарей на любом уровне вложенности."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from all_keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from all_keys(inner)


@pytest.mark.parametrize("exam_type", EXAMS)
def test_generator_no_pii(dataset, exam_type):
    for endpoint in LIST_ENDPOINTS:
        allowed = {"name"} if endpoint in contract.NAME_ALLOWED_ENDPOINTS else set()
        keys = {key.lower() for key in all_keys(load_items(dataset, exam_type, endpoint))}
        assert not (keys & (contract.FORBIDDEN_KEYS - allowed)), endpoint


@pytest.mark.parametrize("exam_type", EXAMS)
def test_generator_states(dataset, exam_type):
    """Состояния 6.12: absent — без ответов и баллов; annulled — без баллов;
    finished + final — первичный балл равен сумме баллов за задания.
    """
    sessions = load_items(dataset, exam_type, "sessions")
    answers = by_participant(load_items(dataset, exam_type, "answers"))
    reviews = by_participant(load_items(dataset, exam_type, "expert-reviews"))
    events = by_participant(load_items(dataset, exam_type, "proctoring-events"))
    block_key = contract.EXAM_BLOCK_KEYS[exam_type]
    for s in sessions:
        pid = s["participant_id"]
        if s["status"] == "absent":
            assert s["started_at"] is None and s["primary_score"] is None
            assert pid not in answers and pid not in reviews and pid not in events
            assert set(s[block_key].values()) == {None}
        elif s["status"] == "annulled":
            assert s["started_at"] is not None and s["primary_score"] is None
            assert pid not in reviews
            assert set(s[block_key].values()) == {None}
        else:
            assert s["status"] == "finished" and s["result_status"] == "final"
            total = sum(a["score"] for a in answers.get(pid, []))
            total += sum(r["score"] for r in reviews[pid])
            assert s["primary_score"] == total
            assert 0 <= total <= s["max_primary_score"]
            started = datetime.fromisoformat(s["started_at"])
            assert datetime.fromisoformat(s["finished_at"]) > started
            for a in answers.get(pid, []):
                assert datetime.fromisoformat(a["answered_at"]) >= started


def test_generator_has_absent_and_annulled(dataset):
    statuses = [s["status"] for e in EXAMS for s in load_items(dataset, e, "sessions")]
    assert "absent" in statuses and "annulled" in statuses


@pytest.mark.parametrize("exam_type", EXAMS)
def test_answers_consistent(dataset, exam_type):
    for a in load_items(dataset, exam_type, "answers"):
        assert a["score"] == int(a["is_correct"])
        assert a["max_score"] == 1
        if a["answer"] == "":
            assert a["is_correct"] is False


def test_exam_blocks_match_scales(dataset):
    for exam_type in EXAMS:
        for s in load_items(dataset, exam_type, "sessions"):
            if s["status"] != "finished":
                continue
            primary = s["primary_score"]
            if exam_type == "EGE":
                assert s["ege"]["test_score"] == ege_test_score(primary)
            elif exam_type == "OGE":
                block = s["oge"]
                assert sum(block["block_scores"].values()) == primary
                geometry = block["block_scores"]["geometry"]
                assert block["grade"] == oge_grade(primary, geometry)
                assert block["block_failed"] == (geometry < 2)
            elif exam_type == "VPR":
                assert s["vpr"]["grade"] == vpr_grade(primary)
            else:
                assert s["niko"]["result_level"] == niko_level(primary)


def test_vpr_has_no_proctoring(dataset):
    assert load_items(dataset, "VPR", "proctoring-events") == []


@pytest.mark.parametrize("exam_type", EXAMS)
def test_kim_covers_all_tasks(dataset, exam_type):
    exam = load_exams()[exam_type]
    rows = load_items(dataset, exam_type, "dictionaries/kim")
    tasks = exam.part1_numbers + exam.part2_numbers
    for variant in ("v1", "v2"):
        numbers = sorted(r["task_number"] for r in rows if r["variant_id"] == variant)
        assert numbers == tasks
    codes = {}
    for r in rows:
        codes.setdefault(r["task_number"], set()).add(tuple(r["content_codes"]))
    assert all(len(variants) == 1 for variants in codes.values()), "параллельные варианты"


def test_manifest(dataset):
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["synthetic"] is True
    assert manifest["seed"] == SEED
    assert set(manifest["platforms"]) == set(EXAMS)
    for platform in manifest["platforms"].values():
        start = datetime.fromisoformat(platform["exam_start"])
        assert start < datetime.fromisoformat(platform["exam_end"])
    assert "oge/sessions.json" in manifest["files"]


def test_generator_deterministic(dataset, tmp_path):
    """Одинаковый seed → побайтно одинаковые файлы; другой seed → другие данные."""
    again = tmp_path / "again"
    generate(PROFILE, again, SEED)
    for path in sorted(dataset.rglob("*")):
        if path.is_file():
            assert (again / path.relative_to(dataset)).read_bytes() == path.read_bytes(), path
    other = tmp_path / "other"
    generate(PROFILE, other, SEED + 1)
    same = (other / "oge" / "sessions.json").read_bytes() == (
        dataset / "oge" / "sessions.json"
    ).read_bytes()
    assert not same


def test_writer_pages(tmp_path):
    items = [
        {"record_id": f"r{k:05d}", "updated_at": "2026-12-05T10:00:00+03:00"} for k in range(2500)
    ]
    paths = write_endpoint(tmp_path, "answers", items)
    assert [path.name for path in paths] == [f"answers.page-{n}.json" for n in (1, 2, 3)]
    pages = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    assert [page["page"] for page in pages] == [1, 2, 3]
    assert all(page["total_pages"] == 3 for page in pages)
    assert all(len(page["items"]) <= 1000 for page in pages)
    assert [item for page in pages for item in page["items"]] == items


def test_writer_empty_list(tmp_path):
    write_endpoint(tmp_path, "proctoring-events", [])
    page = json.loads((tmp_path / "proctoring-events.json").read_text(encoding="utf-8"))
    assert page == {"page": 1, "total_pages": 1, "items": []}
