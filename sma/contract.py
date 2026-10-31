"""Контракт данных платформ, формат v2 (раздел 6 спецификации).

Одно описание полей для валидатора, генератора и тестов. Менять только по согласованию:
контракт уже получили команды платформ.
"""

from dataclasses import dataclass

# Типы значений в JSON.
STRING = "string"
INT = "int"
BOOL = "bool"
DATETIME = "datetime"  # строка ISO 8601 со смещением: "2026-12-05T10:00:00+03:00"
OBJECT = "object"
ARRAY = "array"

TWO_DIGITS = r"^\d{2}$"  # subject_code, region_code

EXAM_TYPES = ("EGE", "OGE", "VPR", "NIKO")


@dataclass(frozen=True)
class Field:
    """Одно поле записи.

    Каждое поле контракта присутствует в записи всегда (раздел 6.1);
    nullable говорит, может ли его значение быть null.
    """

    name: str
    type: str
    nullable: bool = False
    enum: tuple[str, ...] | None = None  # допустимые значения
    pattern: str | None = None  # регулярное выражение для строк
    min_value: int | None = None  # для чисел, включительно
    max_value: int | None = None
    min_length: int | None = None  # для строк
    max_length: int | None = None
    item_type: str | None = None  # тип элементов массива


# Служебные поля (6.2): record_id — первым, updated_at и deleted — последними.
RECORD_ID = Field("record_id", STRING, min_length=1, max_length=64)
SERVICE_TAIL = [Field("updated_at", DATETIME), Field("deleted", BOOL)]


# 6.4 /sessions — сессия ученика. Блок экзамена (ege/oge/vpr/niko) описан отдельно ниже.
SESSIONS = [
    Field("participant_id", STRING),
    Field("exam_type", STRING, enum=EXAM_TYPES),
    Field("subject_code", STRING, pattern=TWO_DIGITS),
    Field("grade_level", INT, min_value=4, max_value=11),
    Field("variant_id", STRING),
    Field("region_code", STRING, pattern=TWO_DIGITS),
    Field("school_code", STRING),
    Field("room_code", STRING, nullable=True),
    Field("status", STRING, enum=("absent", "started", "in_progress", "finished", "annulled")),
    Field("started_at", DATETIME, nullable=True),
    Field("finished_at", DATETIME, nullable=True),
    Field("primary_score", INT, nullable=True, min_value=0),
    Field("max_primary_score", INT, min_value=1),  # «int > 0» → min_value=1
    Field("result_status", STRING, enum=("pending", "final", "appeal_changed")),
]

# 6.5 /answers — по таблице 6.5, в том же порядке.
ANSWERS = [
    Field("participant_id", STRING),
    Field("exam_type", STRING, enum=EXAM_TYPES),
    Field("task_number", INT, min_value=1),
    Field("answer", STRING),
    Field("is_correct", BOOL),
    Field("score", INT, min_value=0),
    Field("max_score", INT, min_value=1),
    Field("answered_at", DATETIME),
    Field("time_spent_sec", INT, nullable=True, min_value=0),
    Field("audio_plays", INT, nullable=True, min_value=0),
]

# 6.6 /expert-reviews — по таблице 6.6.
EXPERT_REVIEWS = [
    Field("participant_id", STRING),
    Field("exam_type", STRING, enum=EXAM_TYPES),
    Field("task_number", INT, min_value=1),
    Field("check_status", STRING, enum=("pending", "checked")),
    Field("check_stage", STRING, nullable=True, enum=("first", "third_check", "appeal")),
    Field("expert_code", STRING, nullable=True),
    Field("score", INT, nullable=True, min_value=0),
    Field("max_score", INT, min_value=1),
    Field("reviewed_at", DATETIME, nullable=True),
]

# 6.7 /proctoring-events — по таблице 6.7.
PROCTORING_EVENTS = [
    Field("participant_id", STRING),
    Field("exam_type", STRING, enum=EXAM_TYPES),
    Field(
        "event_type", STRING, enum=("tab_switch", "copy", "paste", "fullscreen_exit", "focus_lost")
    ),
    Field("occurred_at", DATETIME),
    Field("task_number", INT, min_value=1, nullable=True),
]

# 6.9 Справочники. Поле name здесь разрешено (это не ФИО).
REGIONS = [
    Field("region_code", STRING, pattern=TWO_DIGITS),
    Field("name", STRING),
]
SCHOOLS = [
    Field("school_code", STRING),
    Field("region_code", STRING, pattern=TWO_DIGITS),
    Field("name", STRING),
]
SUBJECTS = [
    Field("exam_type", STRING, enum=EXAM_TYPES),
    Field("subject_code", STRING, pattern=TWO_DIGITS),
    Field("name", STRING),
    Field("grade_level", INT, min_value=4, max_value=11),
    Field("max_primary_score", INT, min_value=1),
    Field("threshold_score", INT, min_value=0),
]

# 6.10 /dictionaries/kim — одно задание одного варианта.
KIM = [
    Field("exam_type", STRING, enum=EXAM_TYPES),
    Field("subject_code", STRING, pattern=TWO_DIGITS),
    Field("exam_year", INT),
    Field("variant_id", STRING),
    Field("task_number", INT, min_value=1),
    Field("part", INT, min_value=1, max_value=2),
    Field("check_type", STRING, enum=("auto", "expert")),
    Field("max_score", INT, min_value=1),
    Field("level", STRING, enum=("basic", "advanced", "high")),
    Field("content_codes", ARRAY, item_type=STRING),
    Field("skill_codes", ARRAY, item_type=STRING),
    Field("section", STRING),
    Field("block", STRING, nullable=True),
]

# 6.11 /dictionaries/scales — шкала перевода баллов.
SCALES = [
    Field("exam_type", STRING, enum=EXAM_TYPES),
    Field("subject_code", STRING, pattern=TWO_DIGITS),
    Field("exam_year", INT),
    Field("scale_type", STRING, enum=("primary_to_test", "primary_to_grade", "primary_to_level")),
    Field("points", ARRAY, item_type=OBJECT),
    Field("conditions", ARRAY, item_type=OBJECT),
    Field("scale_version", INT, min_value=1),
    Field("valid_from", DATETIME),
]

# Поля каждого списочного адреса, без служебных. Ключ — путь адреса после /api/v1/analytics/.
ENDPOINT_FIELDS: dict[str, list[Field]] = {
    "sessions": SESSIONS,
    "answers": ANSWERS,
    "expert-reviews": EXPERT_REVIEWS,
    "proctoring-events": PROCTORING_EVENTS,
    "dictionaries/regions": REGIONS,
    "dictionaries/schools": SCHOOLS,
    "dictionaries/subjects": SUBJECTS,
    "dictionaries/kim": KIM,
    "dictionaries/scales": SCALES,
}

# Блок экзамена в /sessions (6.4): в записи ровно один блок — тот, что соответствует exam_type.
EXAM_BLOCK_KEYS = {"EGE": "ege", "OGE": "oge", "VPR": "vpr", "NIKO": "niko"}

# поля каждого блока по второй таблице раздела 6.4.
# Все поля блоков могут быть null (пока result_status = pending).
EXAM_BLOCK_FIELDS: dict[str, list[Field]] = {
    "ege": [Field("test_score", INT, nullable=True, min_value=0, max_value=100)],
    "oge": [
        Field("grade", INT, min_value=2, max_value=5, nullable=True),
        Field("block_scores", OBJECT, nullable=True),
        Field("block_failed", BOOL, nullable=True),
    ],
    "vpr": [
        Field("grade", INT, min_value=2, max_value=5, nullable=True),
        Field("term_mark", INT, min_value=2, max_value=5, nullable=True),
    ],
    "niko": [
        Field("result_level", STRING, enum=("below_basic", "basic", "above_basic"), nullable=True),
        Field("questionnaire_done", BOOL, nullable=True),
    ],
}

# 6.8 /live-status — объект, не список, без служебных полей.
LIVE_STATUS_FIELDS = [
    Field("exam_type", STRING, enum=EXAM_TYPES),
    Field("in_progress", INT, min_value=0),
    Field("finished", INT, min_value=0),
    Field("annulled", INT, min_value=0),
    Field("absent", INT, min_value=0),
    Field("generated_at", DATETIME),
]

# 6.13 запрещенные ключи (персональные данные), строчными буквами.
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "fio",
        "full_name",
        "name",
        "first_name",
        "last_name",
        "middle_name",
        "surname",
        "patronymic",
        "birth_date",
        "birthday",
        "date_of_birth",
        "snils",
        "passport",
        "passport_number",
        "phone",
        "phone_number",
        "email",
        "e_mail",
        "ip",
        "ip_address",
        "address",
    }
)

# Адреса, в которых ключ name разрешен (6.13).
NAME_ALLOWED_ENDPOINTS = frozenset(
    {"dictionaries/regions", "dictionaries/schools", "dictionaries/subjects"}
)


def fields_for(endpoint: str) -> list[Field]:
    """Все поля записи адреса в порядке контракта: record_id, поля адреса, updated_at, deleted.

    Пример: fields_for("answers")[0] — это RECORD_ID.
    """
    fields = [RECORD_ID]
    for field in ENDPOINT_FIELDS[endpoint]:
        fields.append(field)
    fields += SERVICE_TAIL
    return fields


def field_names(endpoint: str) -> list[str]:
    fields = fields_for(endpoint)
    names = [field.name for field in fields]
    return names
