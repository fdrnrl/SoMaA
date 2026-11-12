"""Подготовка данных: окончательные результаты, нормализованные ответы, группа сравнения."""
from __future__ import annotations

import pandas as pd

from .normalize import normalize_answer
from .params import FINAL_RESULTS


def final_sessions(sessions: pd.DataFrame) -> pd.DataFrame:
    """Сессии с окончательным результатом: finished и result_status final / appeal_changed.

    Удаленные записи отбрасываются (FR-48, FR-56); pending не участвует (FR-07, FR-40, T-70).
    """
    s = sessions
    if "deleted" in s.columns:
        s = s[~s["deleted"].astype(bool)]
    return s[(s["status"] == "finished") & s["result_status"].isin(FINAL_RESULTS)].copy()


def with_norm_answers(answers: pd.DataFrame) -> pd.DataFrame:
    a = answers
    if "deleted" in a.columns:
        a = a[~a["deleted"].astype(bool)]
    a = a.copy()
    a["norm"] = a["answer"].map(normalize_answer)
    a["is_correct"] = a["is_correct"].astype(bool)
    return a


def group_key_column(sessions: pd.DataFrame) -> pd.Series:
    """Группа сравнения П1: аудитория, если платформа передает room_code, иначе школа (раздел 5.5, T-54).

    Если room_code есть в таблице, но пуст у части участников, для них группой служит школа.
    """
    if "room_code" not in sessions.columns or sessions["room_code"].isna().all():
        return sessions["school_code"].astype(str)
    room = sessions["room_code"]
    return (sessions["school_code"].astype(str) + "/" + room.astype(str)).where(room.notna(), sessions["school_code"].astype(str))
