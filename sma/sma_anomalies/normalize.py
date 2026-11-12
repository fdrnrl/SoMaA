"""Нормализация ответов (FR-31, FR-32, T-42)."""
from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def normalize_answer(value) -> str:
    """Убирает пробелы, меняет точку на запятую, приводит к нижнему регистру.

    Пустой ответ (None, "", только пробелы) возвращается как "" — он учитывается
    отдельно и никогда не считается совпадением (FR-32).
    """
    if value is None:
        return ""
    text = _WS.sub("", str(value))
    return text.replace(".", ",").lower()


def is_blank(norm: str) -> bool:
    return norm == ""
