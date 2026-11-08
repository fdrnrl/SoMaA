"""Время: разбор строк ISO 8601 со смещением и текущий момент.

В коде — только datetime с часовым поясом (раздел 4.2).
"""

from datetime import UTC, datetime


def parse_datetime(value: str) -> datetime:
    """Строка ISO 8601 со смещением → datetime с часовым поясом.

    "2026-12-05T10:00:00+03:00" и "2026-12-05T14:00:00+07:00" — один и тот же момент,
    и сравниваются как равные. Строка без смещения или не время → ValueError.
    """
    if not isinstance(value, str):
        raise ValueError(f"ожидалась строка времени, получено {value!r}")
    moment = datetime.fromisoformat(value)
    if moment.utcoffset() is None:
        raise ValueError(f"время без смещения часового пояса: {value!r}")
    return moment


def try_parse_datetime(value: object) -> datetime | None:
    """Как parse_datetime, но вместо ошибки возвращает None."""
    try:
        return parse_datetime(value)
    except ValueError:
        return None


def utc_now() -> datetime:
    """Текущий момент в UTC."""
    return datetime.now(UTC)
