"""Выгрузки CSV по разделам 7.2–7.4 ТЗ.

Формат: UTF-8, разделитель полей — точка с запятой, десятичный разделитель — запятая,
время — ISO 8601 с часовым поясом, первая строка — названия полей, списки внутри поля — через запятую.
"""
from __future__ import annotations

import csv
import io
import math

import numpy as np
import pandas as pd

from .rules import ANOMALY_COLUMNS

SCHOOL_COLUMNS = ["run_id", "calculated_at", "exam_type", "subject_code", "region_code", "school_code",
                  "participants", "p1", "p2", "p3", "p4a", "p4b", "z1", "z2", "z3", "z4a", "z4b", "index", "in_list"]
GROUP_COLUMNS = ["group_id", "exam_type", "subject_code", "school_code", "room_code", "variant_id",
                 "participant_ids", "size", "min_p_value", "run_id"]


def fmt_number(x, digits: int = 4) -> str:
    """Число с десятичной запятой, без научной записи и без хвостовых нулей: 21,4; 0,00007."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return ""
    if isinstance(x, (bool, np.bool_)):
        return "true" if x else "false"
    if isinstance(x, (int, np.integer)):
        return str(int(x))
    text = np.format_float_positional(round(float(x), digits), trim="-", precision=digits)
    text = text.rstrip(".") or "0"
    return "0" if text in ("-0", "") else text.replace(".", ",")


def _cell(v, digits):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, (bool, np.bool_, int, np.integer, float, np.floating)):
        return fmt_number(v, digits)
    return str(v)


def to_csv_text(df: pd.DataFrame, columns: list[str], digits: int = 4, float_digits: dict | None = None) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(columns)
    float_digits = float_digits or {}
    for _, r in df.reindex(columns=columns).iterrows():
        w.writerow([_cell(r[c], float_digits.get(c, digits)) for c in columns])
    return buf.getvalue()


def anomalies_csv(anomalies: pd.DataFrame) -> str:
    """Раздел 7.2: поля записи об аномалии (T-13, T-19)."""
    return to_csv_text(anomalies, ANOMALY_COLUMNS, digits=2)


def schools_csv(schools: pd.DataFrame) -> str:
    """Раздел 7.4: перечень школ (FR-47, T-55); порядок — по убыванию индекса."""
    df = schools.sort_values(["index", "school_code"], ascending=[False, True])
    return to_csv_text(df, SCHOOL_COLUMNS, digits=4, float_digits={"index": 2})


def groups_csv(groups: pd.DataFrame) -> str:
    """Раздел 7.3: группы участников П1."""
    return to_csv_text(groups, GROUP_COLUMNS, digits=8)


def write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
