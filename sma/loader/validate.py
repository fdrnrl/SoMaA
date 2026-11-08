"""Проверка записей платформ по контракту (раздел 8.4).

Порядок проверок: запрещенные поля → record_id → наличие полей → null → тип →
перечисления, форматы, диапазоны → exam_type платформы → блок экзамена.
Неизвестные поля удаляются из записи (запись не отклоняется).

Согласованность между полями (например, баллы при status = absent) запись не отклоняет —
это проверка качества данных (раздел 13). Исключение — правило контракта 6.5
«score ≤ max_score»: нарушение отклоняется как out_of_range (см. docs/decisions.md).
"""

import re
from dataclasses import dataclass
from typing import NamedTuple

from sma import contract
from sma.contract import ARRAY, BOOL, DATETIME, INT, OBJECT, STRING
from sma.timeutil import try_parse_datetime

REMOVED = "<removed>"
SCORE_ENDPOINTS = ("answers", "expert-reviews")


@dataclass(frozen=True)
class Problem:
    """Одна причина отклонения записи."""

    reason_code: str  # missing_field, null_not_allowed, wrong_type, ... (раздел 7.4)
    field: str | None
    message: str


class Validation(NamedTuple):
    """Результат проверки: ok, очищенная запись (или None), проблемы, удаленные поля."""

    ok: bool
    cleaned: dict | None
    problems: list[Problem]
    dropped: list[str]


# --- Запрещенные поля ------------------------------------------------------------------------


def is_forbidden(key: object, allow_name: bool) -> bool:
    """Ключ — персональные данные (без учета регистра)? name разрешен в справочниках."""
    lowered = str(key).lower()
    if allow_name and lowered == "name":
        return False
    return lowered in contract.FORBIDDEN_KEYS


def find_forbidden_keys(value: object, allow_name: bool, path: str = "") -> list[str]:
    """Пути всех запрещенных ключей на любом уровне вложенности: ["email", "oge.phone"]."""
    found = []
    if isinstance(value, dict):
        for key, inner in value.items():
            full = f"{path}.{key}" if path else str(key)
            if is_forbidden(key, allow_name):
                found.append(full)
            found += find_forbidden_keys(inner, allow_name, full)
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            found += find_forbidden_keys(inner, allow_name, f"{path}[{index}]")
    return found


def remove_forbidden_values(value: object, allow_name: bool) -> object:
    """Копия значения, где у запрещенных ключей значение заменено на "<removed>".

    Так отклоненную запись можно сохранить в ops.rejected_records без персональных данных.
    """
    if isinstance(value, dict):
        return {
            key: REMOVED
            if is_forbidden(key, allow_name)
            else remove_forbidden_values(inner, allow_name)
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [remove_forbidden_values(inner, allow_name) for inner in value]
    return value


# --- Проверка значений -----------------------------------------------------------------------


def type_ok(field: contract.Field, value: object) -> bool:
    """Значение нужного JSON-типа? bool не считается числом, число — строкой."""
    if field.type == STRING or field.type == DATETIME:
        return isinstance(value, str)
    if field.type == INT:
        return isinstance(value, int) and not isinstance(value, bool)
    if field.type == BOOL:
        return isinstance(value, bool)
    if field.type == OBJECT:
        return isinstance(value, dict)
    if field.type == ARRAY:
        return isinstance(value, list)
    raise ValueError(f"неизвестный тип в контракте: {field.type}")


def check_value(field: contract.Field, value: object, where: str) -> list[Problem]:
    """Проблемы одного значения: null, тип, перечисление, формат, диапазон."""
    if value is None:
        if field.nullable:
            return []
        return [Problem("null_not_allowed", where, f"{where}: null не разрешен")]
    if not type_ok(field, value):
        return [Problem("wrong_type", where, f"{where}: {value!r} — не {field.type}")]

    problems = []
    if field.type == DATETIME and try_parse_datetime(value) is None:
        problems.append(Problem("bad_format", where, f"{where}: {value!r} — не время со смещением"))
    if field.enum is not None and value not in field.enum:
        problems.append(Problem("bad_enum", where, f"{where}: {value!r} не из {list(field.enum)}"))
    if field.pattern is not None and not re.fullmatch(field.pattern, value):
        problems.append(Problem("bad_format", where, f"{where}: {value!r} не по формату"))
    if field.min_value is not None and value < field.min_value:
        problems.append(Problem("out_of_range", where, f"{where}: {value} < {field.min_value}"))
    if field.max_value is not None and value > field.max_value:
        problems.append(Problem("out_of_range", where, f"{where}: {value} > {field.max_value}"))
    if field.min_length is not None and len(value) < field.min_length:
        problems.append(Problem("out_of_range", where, f"{where}: короче {field.min_length}"))
    if field.max_length is not None and len(value) > field.max_length:
        problems.append(Problem("out_of_range", where, f"{where}: длиннее {field.max_length}"))
    if field.item_type is not None:
        item = contract.Field("item", field.item_type)
        if not all(type_ok(item, element) for element in value):
            problems.append(Problem("wrong_type", where, f"{where}: элементы не {field.item_type}"))
    return problems


def check_fields(
    record: dict, fields: list[contract.Field], prefix: str = ""
) -> tuple[list[Problem], dict]:
    """Проверяет поля из списка. Возвращает проблемы и словарь только этих полей."""
    problems = []
    cleaned = {}
    for field in fields:
        where = f"{prefix}{field.name}"
        if field.name not in record:
            problems.append(Problem("missing_field", where, f"нет поля {where}"))
            continue
        problems += check_value(field, record[field.name], where)
        cleaned[field.name] = record[field.name]
    return problems, cleaned


def check_record_id(record: dict) -> list[Problem]:
    """record_id: строка от 1 до 64 символов."""
    record_id = record.get("record_id")
    if isinstance(record_id, str) and 1 <= len(record_id) <= 64:
        return []
    return [Problem("bad_record_id", "record_id", f"record_id {record_id!r}: нужна строка 1–64")]


def check_exam_type(cleaned: dict, platform: str) -> list[Problem]:
    """exam_type записи совпадает с платформой из конфига."""
    exam_type = cleaned.get("exam_type")
    if exam_type in contract.EXAM_TYPES and exam_type != platform:
        message = f"exam_type {exam_type} у записи платформы {platform}"
        return [Problem("exam_type_mismatch", "exam_type", message)]
    return []


def check_score(cleaned: dict) -> list[Problem]:
    """score ≤ max_score (контракт 6.5, 6.6)."""
    score, max_score = cleaned.get("score"), cleaned.get("max_score")
    both_int = all(isinstance(v, int) and not isinstance(v, bool) for v in (score, max_score))
    if both_int and score > max_score:
        return [Problem("out_of_range", "score", f"score {score} > max_score {max_score}")]
    return []


def check_exam_block(record: dict, cleaned: dict) -> list[Problem]:
    """Ровно один блок экзамена, соответствующий exam_type.

    Проверенный блок (только поля контракта) добавляется в cleaned.
    """
    expected = contract.EXAM_BLOCK_KEYS.get(cleaned.get("exam_type"))
    if expected is None:
        return []  # неверный exam_type уже отмечен другой проверкой
    present = [key for key in contract.EXAM_BLOCK_KEYS.values() if key in record]
    if present != [expected]:
        message = f"нужен ровно один блок {expected}, есть {present}"
        return [Problem("wrong_exam_block", expected, message)]
    block = record[expected]
    if not isinstance(block, dict):
        return [Problem("wrong_type", expected, f"{expected}: блок должен быть объектом")]
    fields = contract.EXAM_BLOCK_FIELDS[expected]
    problems, cleaned_block = check_fields(block, fields, f"{expected}.")
    cleaned[expected] = cleaned_block
    return problems


# --- Проверка записи целиком -----------------------------------------------------------------


def validate_record(record: object, endpoint: str, platform: str) -> Validation:
    """Проверяет запись списочного адреса платформы platform (EGE, OGE, VPR, NIKO).

    cleaned — запись только с полями контракта (в порядке контракта); None, если отклонена.
    dropped — имена удаленных неизвестных полей.
    """
    if not isinstance(record, dict):
        return Validation(False, None, [Problem("wrong_type", None, "запись не объект")], [])
    allow_name = endpoint in contract.NAME_ALLOWED_ENDPOINTS

    problems = [
        Problem("pii_field", path, f"запрещенное поле {path}")
        for path in find_forbidden_keys(record, allow_name)
    ]
    problems += check_record_id(record)

    field_problems, fields = check_fields(record, contract.ENDPOINT_FIELDS[endpoint])
    problems += field_problems
    tail_problems, tail = check_fields(record, contract.SERVICE_TAIL)
    problems += tail_problems

    cleaned = {"record_id": record.get("record_id"), **fields}
    problems += check_exam_type(cleaned, platform)
    if endpoint in SCORE_ENDPOINTS:
        problems += check_score(cleaned)

    known = {"record_id"} | set(fields) | set(tail)
    if endpoint == "sessions":
        problems += check_exam_block(record, cleaned)
        known |= set(contract.EXAM_BLOCK_KEYS.values())
    cleaned.update(tail)

    dropped = [key for key in record if key not in known and not is_forbidden(key, allow_name)]
    if endpoint == "sessions":
        for key in contract.EXAM_BLOCK_KEYS.values():
            block = record.get(key)
            if isinstance(block, dict) and isinstance(cleaned.get(key), dict):
                dropped += [f"{key}.{name}" for name in block if name not in cleaned[key]]

    if problems:
        return Validation(False, None, problems, dropped)
    return Validation(True, cleaned, [], dropped)


def validate_live_status(status: object, platform: str) -> Validation:
    """Проверяет объект /live-status (без служебных полей)."""
    if not isinstance(status, dict):
        return Validation(False, None, [Problem("wrong_type", None, "сводка не объект")], [])
    problems = [
        Problem("pii_field", path, f"запрещенное поле {path}")
        for path in find_forbidden_keys(status, allow_name=False)
    ]
    field_problems, cleaned = check_fields(status, contract.LIVE_STATUS_FIELDS)
    problems += field_problems + check_exam_type(cleaned, platform)
    dropped = [key for key in status if key not in cleaned and not is_forbidden(key, False)]
    if problems:
        return Validation(False, None, problems, dropped)
    return Validation(True, cleaned, [], dropped)
