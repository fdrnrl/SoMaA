"""Проверка записей загрузчиком (раздел 8.4)."""

import copy
import json
from pathlib import Path

import pytest

from sma import contract
from sma.generator.generate import generate
from sma.generator.writer import endpoint_file_stem
from sma.loader.validate import (
    REMOVED,
    remove_forbidden_values,
    validate_live_status,
    validate_record,
)

PROFILE = Path(__file__).resolve().parents[2] / "config" / "generator" / "small.yaml"

# Эталонная запись сессии из приложения 21.4.
SESSION = {
    "record_id": "3c2a6f0e-1d4b-4a8e-b7f2-9e5d1c0a6b31",
    "participant_id": "p_7f3a9c",
    "exam_type": "OGE",
    "subject_code": "02",
    "grade_level": 9,
    "variant_id": "v12",
    "region_code": "77",
    "school_code": "77003",
    "room_code": "0312",
    "status": "finished",
    "started_at": "2026-12-05T10:00:00+03:00",
    "finished_at": "2026-12-05T13:55:00+03:00",
    "primary_score": 21,
    "max_primary_score": 31,
    "result_status": "final",
    "oge": {"grade": 4, "block_scores": {"algebra": 12, "geometry": 9}, "block_failed": False},
    "updated_at": "2026-12-05T16:02:11+03:00",
    "deleted": False,
}

ANSWER = {
    "record_id": "a-1",
    "participant_id": "p_7f3a9c",
    "exam_type": "OGE",
    "task_number": 4,
    "answer": " 0.25",
    "is_correct": True,
    "score": 1,
    "max_score": 1,
    "answered_at": "2026-12-05T10:31:00+03:00",
    "time_spent_sec": None,
    "audio_plays": None,
    "updated_at": "2026-12-05T10:31:00+03:00",
    "deleted": False,
}

SCHOOL = {
    "record_id": "s-1",
    "school_code": "77003",
    "region_code": "77",
    "name": "Школа № 15",
    "updated_at": "2026-11-25T09:00:00+03:00",
    "deleted": False,
}


def changed(record: dict, **changes) -> dict:
    """Копия записи с изменениями."""
    result = copy.deepcopy(record)
    result.update(changes)
    return result


def without(record: dict, name: str) -> dict:
    result = copy.deepcopy(record)
    del result[name]
    return result


def reasons(check) -> list[tuple[str, str | None]]:
    return [(problem.reason_code, problem.field) for problem in check.problems]


def test_sample_session_is_valid():
    check = validate_record(SESSION, "sessions", "OGE")
    assert check.ok, check.problems
    assert check.cleaned == SESSION
    assert check.dropped == []


def test_valid_answer_and_school():
    assert validate_record(ANSWER, "answers", "OGE").ok
    assert validate_record(SCHOOL, "dictionaries/schools", "OGE").ok


@pytest.mark.parametrize("name", contract.field_names("answers"))
def test_validate_missing_field(name):
    check = validate_record(without(ANSWER, name), "answers", "OGE")
    assert not check.ok
    expected = "bad_record_id" if name == "record_id" else "missing_field"
    assert (expected, name) in reasons(check)


@pytest.mark.parametrize("name", ["participant_id", "task_number", "answer", "updated_at"])
def test_validate_null_not_allowed(name):
    check = validate_record(changed(ANSWER, **{name: None}), "answers", "OGE")
    assert reasons(check) == [("null_not_allowed", name)]


def test_nullable_fields_accept_null():
    assert validate_record(changed(ANSWER, time_spent_sec=None), "answers", "OGE").ok
    assert validate_record(changed(SESSION, room_code=None), "sessions", "OGE").ok


@pytest.mark.parametrize("key", sorted(contract.FORBIDDEN_KEYS))
def test_validate_pii_top_level(key):
    check = validate_record(changed(ANSWER, **{key: "x"}), "answers", "OGE")
    assert not check.ok
    assert ("pii_field", key) in reasons(check)


def test_validate_pii_nested():
    record = copy.deepcopy(SESSION)
    record["oge"]["contact"] = {"Email": "x@example.org"}
    check = validate_record(record, "sessions", "OGE")
    assert ("pii_field", "oge.contact.Email") in reasons(check)


def test_validate_name_allowed_in_dictionaries():
    assert validate_record(SCHOOL, "dictionaries/schools", "OGE").ok


def test_validate_name_forbidden_in_facts():
    check = validate_record(changed(ANSWER, name="Иванов"), "answers", "OGE")
    assert ("pii_field", "name") in reasons(check)


def test_pii_values_removed_for_rejected_payload():
    record = changed(ANSWER, phone="+7 900 000-00-00", extra={"fio": "x", "ok": 1})
    cleaned = remove_forbidden_values(record, allow_name=False)
    assert cleaned["phone"] == REMOVED
    assert cleaned["extra"] == {"fio": REMOVED, "ok": 1}
    assert cleaned["answer"] == ANSWER["answer"]


@pytest.mark.parametrize(
    "name, value",
    [
        ("subject_code", 2),
        ("region_code", 77),
        ("grade_level", "9"),
        ("grade_level", 9.0),
        ("grade_level", True),
        ("deleted", 0),
        ("primary_score", "21"),
    ],
)
def test_validate_wrong_type(name, value):
    check = validate_record(changed(SESSION, **{name: value}), "sessions", "OGE")
    assert reasons(check) == [("wrong_type", name)]


def test_validate_wrong_type_codes():
    """subject_code = 2 (число вместо строки) → wrong_type (раздел 16.2)."""
    check = validate_record(changed(SESSION, subject_code=2), "sessions", "OGE")
    assert ("wrong_type", "subject_code") in reasons(check)


@pytest.mark.parametrize(
    "name, value, reason",
    [
        ("status", "done", "bad_enum"),
        ("result_status", "Final", "bad_enum"),
        ("exam_type", "GIA", "bad_enum"),
        ("subject_code", "2", "bad_format"),
        ("region_code", "077", "bad_format"),
        ("started_at", "2026-12-05T10:00:00", "bad_format"),
        ("started_at", "5 декабря", "bad_format"),
        ("grade_level", 3, "out_of_range"),
        ("grade_level", 12, "out_of_range"),
        ("primary_score", -1, "out_of_range"),
        ("max_primary_score", 0, "out_of_range"),
    ],
)
def test_validate_enum_format_range(name, value, reason):
    check = validate_record(changed(SESSION, **{name: value}), "sessions", "OGE")
    assert (reason, name) in reasons(check)


def test_score_above_max_is_out_of_range():
    check = validate_record(changed(ANSWER, score=2, max_score=1), "answers", "OGE")
    assert reasons(check) == [("out_of_range", "score")]


@pytest.mark.parametrize("record_id", ["", "x" * 65, 123, None])
def test_validate_bad_record_id(record_id):
    check = validate_record(changed(ANSWER, record_id=record_id), "answers", "OGE")
    assert ("bad_record_id", "record_id") in reasons(check)


def test_record_id_64_chars_is_ok():
    assert validate_record(changed(ANSWER, record_id="x" * 64), "answers", "OGE").ok


def test_validate_exam_type_mismatch():
    check = validate_record(ANSWER, "answers", "EGE")
    assert reasons(check) == [("exam_type_mismatch", "exam_type")]


def test_validate_exam_block():
    """Блок oge у записи ВПР → wrong_exam_block (раздел 16.2)."""
    record = changed(SESSION, exam_type="VPR")
    check = validate_record(record, "sessions", "VPR")
    assert ("wrong_exam_block", "vpr") in reasons(check)


def test_two_exam_blocks_rejected():
    record = changed(SESSION, ege={"test_score": 70})
    check = validate_record(record, "sessions", "OGE")
    assert ("wrong_exam_block", "oge") in reasons(check)


def test_no_exam_block_rejected():
    check = validate_record(without(SESSION, "oge"), "sessions", "OGE")
    assert ("wrong_exam_block", "oge") in reasons(check)


def test_exam_block_fields_checked():
    record = copy.deepcopy(SESSION)
    record["oge"]["grade"] = 6
    del record["oge"]["block_failed"]
    check = validate_record(record, "sessions", "OGE")
    assert ("out_of_range", "oge.grade") in reasons(check)
    assert ("missing_field", "oge.block_failed") in reasons(check)


def test_validate_unknown_field_dropped():
    record = changed(ANSWER, comment="проверить", extra_score=5)
    check = validate_record(record, "answers", "OGE")
    assert check.ok
    assert "comment" not in check.cleaned and "extra_score" not in check.cleaned
    assert sorted(check.dropped) == ["comment", "extra_score"]
    assert list(check.cleaned) == contract.field_names("answers")


def test_unknown_field_in_block_dropped():
    record = copy.deepcopy(SESSION)
    record["oge"]["note"] = "x"
    check = validate_record(record, "sessions", "OGE")
    assert check.ok
    assert "note" not in check.cleaned["oge"]
    assert check.dropped == ["oge.note"]


def test_consistency_does_not_reject():
    """Баллы у не явившегося — вопрос качества данных (раздел 13), а не проверки."""
    record = changed(SESSION, status="absent", started_at=None)
    assert validate_record(record, "sessions", "OGE").ok


def test_not_a_dict_rejected():
    check = validate_record(["not", "a", "record"], "answers", "OGE")
    assert not check.ok


def test_live_status():
    status = {
        "exam_type": "OGE",
        "in_progress": 42,
        "finished": 17,
        "annulled": 1,
        "absent": 3,
        "generated_at": "2026-12-05T10:30:00+03:00",
    }
    assert validate_live_status(status, "OGE").ok
    assert not validate_live_status({**status, "finished": -1}, "OGE").ok
    assert not validate_live_status(status, "EGE").ok


@pytest.fixture(scope="module")
def small_dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("small")
    generate(PROFILE, out, 2026)
    return out


@pytest.mark.parametrize("exam_type", ["EGE", "OGE", "VPR", "NIKO"])
def test_generated_small_has_no_rejections(small_dataset, exam_type):
    """Критерий M2: файлы генератора проходят проверку без единого отклонения."""
    for endpoint in contract.ENDPOINT_FIELDS:
        path = small_dataset / exam_type.lower() / f"{endpoint_file_stem(endpoint)}.json"
        items = json.loads(path.read_text(encoding="utf-8"))["items"]
        for item in items:
            check = validate_record(item, endpoint, exam_type)
            assert check.ok, (endpoint, check.problems)
            assert check.dropped == []
