"""Проверка записи по описанию контракта из sma/contract.py — для тестов генератора.

Возвращает список проблем человеческим языком; пустой список — запись соответствует контракту.
Это не загрузчик: коды причин и очистка записи — задача sma/loader/validate.py (M3).
"""

import re
from datetime import datetime

from sma import contract
from sma.contract import ARRAY, BOOL, DATETIME, INT, OBJECT, STRING


def value_problems(where: str, field: contract.Field, value: object) -> list[str]:
    """Проблемы одного значения поля."""
    if value is None:
        return [] if field.nullable else [f"{where}: null не разрешен"]
    if field.type == STRING:
        ok = isinstance(value, str)
    elif field.type == INT:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif field.type == BOOL:
        ok = isinstance(value, bool)
    elif field.type == OBJECT:
        ok = isinstance(value, dict)
    elif field.type == ARRAY:
        ok = isinstance(value, list)
    elif field.type == DATETIME:
        ok = isinstance(value, str)
        if ok:
            try:
                ok = datetime.fromisoformat(value).tzinfo is not None
            except ValueError:
                ok = False
    else:
        return [f"{where}: неизвестный тип {field.type}"]
    if not ok:
        return [f"{where}: значение {value!r} не типа {field.type}"]

    problems = []
    if field.enum is not None and value not in field.enum:
        problems.append(f"{where}: {value!r} не из {field.enum}")
    if field.pattern is not None and not re.fullmatch(field.pattern, value):
        problems.append(f"{where}: {value!r} не по формату {field.pattern}")
    if field.min_value is not None and value < field.min_value:
        problems.append(f"{where}: {value} < {field.min_value}")
    if field.max_value is not None and value > field.max_value:
        problems.append(f"{where}: {value} > {field.max_value}")
    if field.min_length is not None and len(value) < field.min_length:
        problems.append(f"{where}: длина меньше {field.min_length}")
    if field.max_length is not None and len(value) > field.max_length:
        problems.append(f"{where}: длина больше {field.max_length}")
    if field.item_type == STRING and not all(isinstance(item, str) for item in value):
        problems.append(f"{where}: элементы не строки")
    if field.item_type == OBJECT and not all(isinstance(item, dict) for item in value):
        problems.append(f"{where}: элементы не объекты")
    return problems


def fields_problems(
    where: str, record: dict, fields: list[contract.Field], extra: set
) -> list[str]:
    """Набор ключей ровно как в контракте (плюс extra) и значения каждого поля."""
    names = [field.name for field in fields]
    problems = []
    missing = set(names) - set(record)
    unknown = set(record) - set(names) - extra
    if missing:
        problems.append(f"{where}: нет полей {sorted(missing)}")
    if unknown:
        problems.append(f"{where}: лишние поля {sorted(unknown)}")
    for field in fields:
        if field.name in record:
            problems += value_problems(f"{where}.{field.name}", field, record[field.name])
    return problems


def contract_problems(record: dict, endpoint: str) -> list[str]:
    """Все проблемы записи списочного адреса."""
    where = f"{endpoint}[{record.get('record_id')}]"
    if endpoint != "sessions":
        return fields_problems(where, record, contract.fields_for(endpoint), set())

    block_key = contract.EXAM_BLOCK_KEYS.get(record.get("exam_type"))
    problems = fields_problems(where, record, contract.fields_for(endpoint), {block_key})
    other_blocks = set(contract.EXAM_BLOCK_KEYS.values()) - {block_key}
    if other_blocks & set(record):
        problems.append(f"{where}: лишние блоки {sorted(other_blocks & set(record))}")
    block = record.get(block_key)
    if not isinstance(block, dict):
        problems.append(f"{where}: нет блока {block_key}")
    else:
        fields = contract.EXAM_BLOCK_FIELDS[block_key]
        problems += fields_problems(f"{where}.{block_key}", block, fields, set())
    return problems
