"""Чтение тестового набора (формат данных v2) в pandas.

Модуль читает файлы папки платформы так же, как это будет делать загрузчик:
страницы answers.page-N.json склеиваются, блок экзамена (oge.grade ...) разворачивается
в отдельные столбцы, удаленные записи (deleted = true) отбрасываются (FR-48).

В боевой СМА те же таблицы приходят из витрин хранилища (раздел 7.6);
контракт столбцов описан в README.
"""
from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass

import pandas as pd

EXAM_FOLDERS = {"EGE": "ege", "OGE": "oge", "VPR": "vpr", "NIKO": "niko"}


@dataclass
class ExamData:
    """Данные одной платформы, подготовленные для правил и углубленного анализа."""

    exam_type: str
    sessions: pd.DataFrame   # действующие (deleted = false) сессии
    answers: pd.DataFrame    # ответы части 1
    events: pd.DataFrame     # события прокторинга (может быть пустой)
    subjects: pd.DataFrame   # справочник предметов (threshold_score)
    schools: pd.DataFrame    # справочник школ (school_code -> region_code)


def _read_pages(folder: str, endpoint: str) -> list[dict]:
    files = glob.glob(os.path.join(folder, f"{endpoint}.json")) + glob.glob(
        os.path.join(folder, f"{endpoint}.page-*.json"))
    files.sort(key=lambda f: int(f.rsplit("page-", 1)[1].split(".")[0]) if "page-" in f else 0)
    items: list[dict] = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            items += json.load(fh)["items"]
    return items


def _frame(folder: str, endpoint: str) -> pd.DataFrame:
    items = _read_pages(folder, endpoint)
    return pd.json_normalize(items) if items else pd.DataFrame()


def _alive(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "deleted" not in df.columns:
        return df
    return df[~df["deleted"].astype(bool)].reset_index(drop=True)


def load_exam(folder: str, exam_type: str | None = None) -> ExamData:
    """Читает папку платформы (например medium/oge)."""
    sessions = _alive(_frame(folder, "sessions"))
    answers = _alive(_frame(folder, "answers"))
    events = _alive(_frame(folder, "proctoring-events"))
    subjects = _alive(_frame(folder, "dictionaries-subjects"))
    schools = _alive(_frame(folder, "dictionaries-schools"))
    if exam_type is None:
        exam_type = str(sessions["exam_type"].iloc[0])
    return ExamData(exam_type, sessions, answers, events, subjects, schools)


def load_dataset(root: str) -> dict[str, ExamData]:
    """Читает все четыре платформы набора (например medium/)."""
    out = {}
    for exam, sub in EXAM_FOLDERS.items():
        path = os.path.join(root, sub)
        if os.path.isdir(path):
            out[exam] = load_exam(path, exam)
    return out
