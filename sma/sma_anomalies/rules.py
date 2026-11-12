"""Оперативные правила А1–А3 (FR-07–FR-13), запись об аномалии по разделу 7.2."""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from .params import COUNTED_EVENTS, Params
from .prep import final_sessions, with_norm_answers

ANOMALY_COLUMNS = ["anomaly_id", "rule", "exam_type", "subject_code", "object_type", "object_id",
                   "region_code", "school_code", "value", "threshold", "detected_at"]
KEY_COLUMNS = ["rule", "exam_type", "object_type", "object_id"]   # ключ записи, таблица 22


def _row(rule, exam, subject, otype, oid, region, school, value, threshold, detected_at, **extra):
    r = {"rule": rule, "exam_type": exam, "subject_code": subject, "object_type": otype,
         "object_id": str(oid), "region_code": region, "school_code": school,
         "value": value, "threshold": threshold, "detected_at": detected_at}
    r.update(extra)
    return r


def _default_detected_at(sessions: pd.DataFrame) -> str:
    """Момент расчета по умолчанию — наибольший updated_at данных, в московском времени."""
    ts = pd.to_datetime(sessions["updated_at"], utc=True).max()
    return ts.tz_convert("Europe/Moscow").isoformat()


# ----------------------------------------------------------------------------- А1
def school_results(sessions: pd.DataFrame) -> pd.DataFrame:
    """Витрина «Результаты школ» для А1: участники, средний балл по школе (окончательные результаты)."""
    fin = final_sessions(sessions)
    g = fin.groupby(["exam_type", "subject_code", "region_code", "school_code"])["primary_score"]
    return g.agg(participants="size", mean_score="mean").reset_index()


def rule_a1(sessions: pd.DataFrame, params: Params = Params(), detected_at: str | None = None) -> pd.DataFrame:
    """А1 (FR-07, FR-08): средний балл школы выше среднего значения средних баллов школ региона
    более чем на 2σ, σ — стандартное отклонение средних баллов школ региона (по выборке, ddof = 1,
    как у pandas .std() и в эталонной реализации набора; параметр a1_ddof).

    Учитываются школы с не менее чем N_min участников с окончательным результатом; если таких школ
    в регионе меньше трех — правило в регионе не применяется. Для каждого сочетания «экзамен, предмет».
    """
    detected_at = detected_at or _default_detected_at(sessions)
    sr = school_results(sessions)
    sr = sr[sr["participants"] >= params.n_min]
    rows = []
    for (exam, subj, region), g in sr.groupby(["exam_type", "subject_code", "region_code"]):
        if len(g) < params.a1_min_schools:                       # FR-08
            continue
        means = g["mean_score"].to_numpy(float)
        mu = means.mean()
        for i, (school, m) in enumerate(zip(g["school_code"], means)):
            if params.a1_sigma_mode == "leave_one_out":
                others = np.delete(means, i)
                base, sigma = others.mean(), others.std(ddof=params.a1_ddof)
            else:
                base, sigma = mu, means.std(ddof=params.a1_ddof)
            if sigma == 0:
                continue
            threshold = base + params.a1_sigma_mult * sigma
            if m > threshold + 1e-12:
                rows.append(_row("A1", exam, subj, "school", school, region, school,
                                 round(float(m), 4), round(float(threshold), 4), detected_at,
                                 z=round(float((m - base) / sigma), 3), region_mean=round(float(base), 4),
                                 sigma=round(float(sigma), 4), schools_in_region=len(g)))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- А2
def _vector_hash(vec: tuple) -> str:
    return hashlib.sha1("\x1f".join(vec).encode("utf-8")).hexdigest()[:8]


def rule_a2(sessions: pd.DataFrame, answers: pd.DataFrame, params: Params = Params(),
            detected_at: str | None = None) -> pd.DataFrame:
    """А2 (FR-09): 5+ участников одной школы с одинаковыми экзаменом, предметом и вариантом, у которых
    совпадают нормализованные ответы на ВСЕ задания части 1 и не менее половины ответов непустые.

    Берутся завершенные (finished) сессии; у аннулированных ответы неполные. Пустые ответы
    не считаются совпадением: группа из пустых работ не отмечается (T-11).
    """
    detected_at = detected_at or _default_detected_at(sessions)
    s = sessions[(sessions["status"] == "finished")]
    if "deleted" in s.columns:
        s = s[~s["deleted"].astype(bool)]
    a = with_norm_answers(answers)
    a = a[a["participant_id"].isin(s["participant_id"])]
    if a.empty:
        return pd.DataFrame()
    n_tasks = a["task_number"].nunique()
    vec = (a.sort_values("task_number").groupby("participant_id")["norm"].apply(tuple))
    meta = s.set_index("participant_id")[["exam_type", "subject_code", "variant_id", "region_code", "school_code"]]
    df = meta.join(vec.rename("vec"), how="inner")
    df = df[df["vec"].map(len) == n_tasks]                 # работа с полным набором ответов части 1
    df["nonempty"] = df["vec"].map(lambda v: sum(x != "" for x in v) / len(v))
    df = df[df["nonempty"] >= params.a2_min_nonempty]      # не менее половины непустые
    rows = []
    for (exam, subj, school, variant, vec_t), g in df.groupby(
            ["exam_type", "subject_code", "school_code", "variant_id", "vec"], sort=True):
        if len(g) < params.a2_min_group:
            continue
        ids = sorted(g.index)
        rows.append(_row("A2", exam, subj, "group", f"{school}:{variant}:{_vector_hash(vec_t)}",
                         g["region_code"].iloc[0], school, len(ids), params.a2_min_group, detected_at,
                         variant_id=variant, participant_ids=",".join(ids)))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- А3
def rule_a3(sessions: pd.DataFrame, events: pd.DataFrame, params: Params = Params(),
            detected_at: str | None = None) -> pd.DataFrame:
    """А3 (FR-10): у участника 3+ действующих событий tab_switch, copy, paste, fullscreen_exit.

    focus_lost не считается; события с deleted = true (отозванные платформой) не считаются (FR-48, T-12).
    """
    if events is None or events.empty:
        return pd.DataFrame()
    detected_at = detected_at or _default_detected_at(sessions)
    ev = events
    if "deleted" in ev.columns:
        ev = ev[~ev["deleted"].astype(bool)]
    ev = ev[ev["event_type"].isin(COUNTED_EVENTS)]
    cnt = ev.groupby("participant_id").size()
    cnt = cnt[cnt >= params.a3_min_events]
    s = sessions
    if "deleted" in s.columns:
        s = s[~s["deleted"].astype(bool)]
    meta = s.set_index("participant_id")[["exam_type", "subject_code", "region_code", "school_code"]]
    rows = []
    for pid, n in cnt.items():
        if pid not in meta.index:           # событие без сессии — не наше дело, это нарушение качества данных
            continue
        m = meta.loc[pid]
        rows.append(_row("A3", m["exam_type"], m["subject_code"], "participant", pid, m["region_code"],
                         m["school_code"], int(n), params.a3_min_events, detected_at))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- сборка и хранение
def run_rules(sessions, answers, events, params: Params = Params(), detected_at: str | None = None) -> pd.DataFrame:
    """Рассчитывает А1–А3 заново (FR-12) и возвращает записи без anomaly_id."""
    parts = [rule_a1(sessions, params, detected_at), rule_a2(sessions, answers, params, detected_at),
             rule_a3(sessions, events, params, detected_at)]
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=ANOMALY_COLUMNS)


def merge_new(stored: pd.DataFrame | None, found: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """FR-13: аномалию с тем же (rule, exam_type, object_type, object_id) повторно не сохраняем.

    Возвращает (хранилище после слияния, только новые записи). anomaly_id выдаются по возрастанию.
    """
    if stored is None or stored.empty:
        stored = pd.DataFrame(columns=ANOMALY_COLUMNS)
    if found.empty:
        return stored, found
    have = set(map(tuple, stored[KEY_COLUMNS].astype(str).to_numpy()))
    keep = [tuple(map(str, r)) not in have for r in found[KEY_COLUMNS].to_numpy()]
    new = found[keep].drop_duplicates(KEY_COLUMNS).copy()
    start = int(stored["anomaly_id"].max()) + 1 if len(stored) else 1
    new.insert(0, "anomaly_id", range(start, start + len(new)))
    return pd.concat([stored, new], ignore_index=True), new
