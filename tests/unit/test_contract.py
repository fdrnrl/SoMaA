"""Проверки описания контракта (раздел 6) в sma/contract.py."""

import pytest

from sma import contract
from sma.contract import ARRAY, BOOL, DATETIME, INT, OBJECT, STRING, TWO_DIGITS

LIST_ENDPOINTS = [
    "sessions",
    "answers",
    "expert-reviews",
    "proctoring-events",
    "dictionaries/regions",
    "dictionaries/schools",
    "dictionaries/subjects",
    "dictionaries/kim",
    "dictionaries/scales",
]

EXAMS = ("EGE", "OGE", "VPR", "NIKO")

# (имя, тип, может ли быть null) по таблицам раздела 6 — без служебных полей.
EXPECTED_FIELDS = {
    "sessions": [
        ("participant_id", STRING, False),
        ("exam_type", STRING, False),
        ("subject_code", STRING, False),
        ("grade_level", INT, False),
        ("variant_id", STRING, False),
        ("region_code", STRING, False),
        ("school_code", STRING, False),
        ("room_code", STRING, True),
        ("status", STRING, False),
        ("started_at", DATETIME, True),
        ("finished_at", DATETIME, True),
        ("primary_score", INT, True),
        ("max_primary_score", INT, False),
        ("result_status", STRING, False),
    ],
    "answers": [
        ("participant_id", STRING, False),
        ("exam_type", STRING, False),
        ("task_number", INT, False),
        ("answer", STRING, False),
        ("is_correct", BOOL, False),
        ("score", INT, False),
        ("max_score", INT, False),
        ("answered_at", DATETIME, False),
        ("time_spent_sec", INT, True),
        ("audio_plays", INT, True),
    ],
    "expert-reviews": [
        ("participant_id", STRING, False),
        ("exam_type", STRING, False),
        ("task_number", INT, False),
        ("check_status", STRING, False),
        ("check_stage", STRING, True),
        ("expert_code", STRING, True),
        ("score", INT, True),
        ("max_score", INT, False),
        ("reviewed_at", DATETIME, True),
    ],
    "proctoring-events": [
        ("participant_id", STRING, False),
        ("exam_type", STRING, False),
        ("event_type", STRING, False),
        ("occurred_at", DATETIME, False),
        ("task_number", INT, True),
    ],
    "dictionaries/regions": [
        ("region_code", STRING, False),
        ("name", STRING, False),
    ],
    "dictionaries/schools": [
        ("school_code", STRING, False),
        ("region_code", STRING, False),
        ("name", STRING, False),
    ],
    "dictionaries/subjects": [
        ("exam_type", STRING, False),
        ("subject_code", STRING, False),
        ("name", STRING, False),
        ("grade_level", INT, False),
        ("max_primary_score", INT, False),
        ("threshold_score", INT, False),
    ],
    "dictionaries/kim": [
        ("exam_type", STRING, False),
        ("subject_code", STRING, False),
        ("exam_year", INT, False),
        ("variant_id", STRING, False),
        ("task_number", INT, False),
        ("part", INT, False),
        ("check_type", STRING, False),
        ("max_score", INT, False),
        ("level", STRING, False),
        ("content_codes", ARRAY, False),
        ("skill_codes", ARRAY, False),
        ("section", STRING, False),
        ("block", STRING, True),
    ],
    "dictionaries/scales": [
        ("exam_type", STRING, False),
        ("subject_code", STRING, False),
        ("exam_year", INT, False),
        ("scale_type", STRING, False),
        ("points", ARRAY, False),
        ("conditions", ARRAY, False),
        ("scale_version", INT, False),
        ("valid_from", DATETIME, False),
    ],
}

# (адрес, поле, допустимые значения)
EXPECTED_ENUMS = [
    ("sessions", "exam_type", EXAMS),
    ("sessions", "status", ("absent", "started", "in_progress", "finished", "annulled")),
    ("sessions", "result_status", ("pending", "final", "appeal_changed")),
    ("answers", "exam_type", EXAMS),
    ("expert-reviews", "exam_type", EXAMS),
    ("expert-reviews", "check_status", ("pending", "checked")),
    ("expert-reviews", "check_stage", ("first", "third_check", "appeal")),
    ("proctoring-events", "exam_type", EXAMS),
    (
        "proctoring-events",
        "event_type",
        ("tab_switch", "copy", "paste", "fullscreen_exit", "focus_lost"),
    ),
    ("dictionaries/subjects", "exam_type", EXAMS),
    ("dictionaries/kim", "exam_type", EXAMS),
    ("dictionaries/kim", "check_type", ("auto", "expert")),
    ("dictionaries/kim", "level", ("basic", "advanced", "high")),
    ("dictionaries/scales", "exam_type", EXAMS),
    (
        "dictionaries/scales",
        "scale_type",
        ("primary_to_test", "primary_to_grade", "primary_to_level"),
    ),
]

# (адрес, поле, min_value, max_value). «int > 0» означает min_value = 1.
EXPECTED_RANGES = [
    ("sessions", "grade_level", 4, 11),
    ("sessions", "primary_score", 0, None),
    ("sessions", "max_primary_score", 1, None),
    ("answers", "task_number", 1, None),
    ("answers", "score", 0, None),
    ("answers", "max_score", 1, None),
    ("answers", "time_spent_sec", 0, None),
    ("answers", "audio_plays", 0, None),
    ("expert-reviews", "task_number", 1, None),
    ("expert-reviews", "score", 0, None),
    ("expert-reviews", "max_score", 1, None),
    ("dictionaries/kim", "part", 1, 2),
]

# Поля с форматом «две цифры».
EXPECTED_TWO_DIGITS = [
    ("sessions", "subject_code"),
    ("sessions", "region_code"),
    ("dictionaries/regions", "region_code"),
    ("dictionaries/schools", "region_code"),
    ("dictionaries/subjects", "subject_code"),
    ("dictionaries/kim", "subject_code"),
    ("dictionaries/scales", "subject_code"),
]

EXPECTED_BLOCKS = {
    "ege": [("test_score", INT, True)],
    "oge": [("grade", INT, True), ("block_scores", OBJECT, True), ("block_failed", BOOL, True)],
    "vpr": [("grade", INT, True), ("term_mark", INT, True)],
    "niko": [("result_level", STRING, True), ("questionnaire_done", BOOL, True)],
}

EXPECTED_BLOCK_RANGES = [
    ("ege", "test_score", 0, 100),
    ("oge", "grade", 2, 5),
    ("vpr", "grade", 2, 5),
    ("vpr", "term_mark", 2, 5),
]

# Эталонная запись сессии из приложения 21.4.
SAMPLE_SESSION = {
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


def by_name(fields):
    """Словарь имя → Field."""
    return {field.name: field for field in fields}


def test_list_endpoints():
    assert list(contract.ENDPOINT_FIELDS) == LIST_ENDPOINTS


@pytest.mark.parametrize("endpoint", LIST_ENDPOINTS)
def test_fields_name_type_nullable(endpoint):
    actual = [(f.name, f.type, f.nullable) for f in contract.ENDPOINT_FIELDS[endpoint]]
    assert actual == EXPECTED_FIELDS[endpoint]


@pytest.mark.parametrize("endpoint", LIST_ENDPOINTS)
def test_every_field_is_a_field_object(endpoint):
    for field in contract.ENDPOINT_FIELDS[endpoint]:
        assert isinstance(field, contract.Field)


@pytest.mark.parametrize("endpoint, name, values", EXPECTED_ENUMS)
def test_enum_values(endpoint, name, values):
    field = by_name(contract.ENDPOINT_FIELDS[endpoint])[name]
    assert field.enum is not None, f"{endpoint}.{name}: не задан enum"
    assert set(field.enum) == set(values)


@pytest.mark.parametrize("endpoint, name, low, high", EXPECTED_RANGES)
def test_ranges(endpoint, name, low, high):
    field = by_name(contract.ENDPOINT_FIELDS[endpoint])[name]
    assert (field.min_value, field.max_value) == (low, high)


@pytest.mark.parametrize("endpoint, name", EXPECTED_TWO_DIGITS)
def test_two_digit_codes(endpoint, name):
    assert by_name(contract.ENDPOINT_FIELDS[endpoint])[name].pattern == TWO_DIGITS


def test_record_id_length():
    assert contract.RECORD_ID.name == "record_id"
    assert (contract.RECORD_ID.min_length, contract.RECORD_ID.max_length) == (1, 64)


def test_fields_for_adds_service_fields():
    fields = contract.fields_for("answers")
    assert fields[0] is contract.RECORD_ID
    assert fields[1:-2] == contract.ANSWERS
    assert [f.name for f in fields[-2:]] == ["updated_at", "deleted"]


@pytest.mark.parametrize("endpoint", LIST_ENDPOINTS)
def test_field_names_match_fields_for(endpoint):
    names = contract.field_names(endpoint)
    assert names == [f.name for f in contract.fields_for(endpoint)]
    assert len(names) == len(set(names)), "имена полей повторяются"


def test_exam_block_keys():
    assert contract.EXAM_BLOCK_KEYS == {"EGE": "ege", "OGE": "oge", "VPR": "vpr", "NIKO": "niko"}


@pytest.mark.parametrize("block", EXPECTED_BLOCKS)
def test_exam_block_fields(block):
    actual = [(f.name, f.type, f.nullable) for f in contract.EXAM_BLOCK_FIELDS[block]]
    assert actual == EXPECTED_BLOCKS[block]


@pytest.mark.parametrize("block, name, low, high", EXPECTED_BLOCK_RANGES)
def test_exam_block_ranges(block, name, low, high):
    field = by_name(contract.EXAM_BLOCK_FIELDS[block])[name]
    assert (field.min_value, field.max_value) == (low, high)


def test_niko_result_level_enum():
    field = by_name(contract.EXAM_BLOCK_FIELDS["niko"])["result_level"]
    assert field.enum is not None
    assert set(field.enum) == {"below_basic", "basic", "above_basic"}


def test_live_status_fields():
    names = [f.name for f in contract.LIVE_STATUS_FIELDS]
    assert names == ["exam_type", "in_progress", "finished", "annulled", "absent", "generated_at"]


def test_forbidden_keys():
    assert contract.FORBIDDEN_KEYS == {
        "fio", "full_name", "name", "first_name", "last_name", "middle_name", "surname",
        "patronymic", "birth_date", "birthday", "date_of_birth", "snils", "passport",
        "passport_number", "phone", "phone_number", "email", "e_mail", "ip", "ip_address",
        "address",
    }  # fmt: skip


def test_name_allowed_only_in_dictionaries():
    assert contract.NAME_ALLOWED_ENDPOINTS == {
        "dictionaries/regions",
        "dictionaries/schools",
        "dictionaries/subjects",
    }


def test_sample_session_matches_contract():
    """Эталонная запись 21.4 содержит ровно поля контракта плюс блок oge."""
    assert set(SAMPLE_SESSION) == set(contract.field_names("sessions")) | {"oge"}
    oge_names = {f.name for f in contract.EXAM_BLOCK_FIELDS["oge"]}
    assert set(SAMPLE_SESSION["oge"]) == oge_names
