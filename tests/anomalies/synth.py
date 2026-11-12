"""Маленький генератор синтетических таблиц для тестов (не заменяет генератор разработчика пайплайнов)."""
import numpy as np
import pandas as pd

T0 = "2026-12-05T12:00:00+03:00"


def make_sessions(schools: dict, exam="OGE", subject="02", region="77", variant="v1", scores=None,
                  status="finished", result="final", room=None):
    """schools: {school_code: n_participants} или {school_code: [scores]}; регион одинаковый."""
    rows = []
    for sc, spec in schools.items():
        sc_scores = spec if isinstance(spec, (list, tuple, np.ndarray)) else [10] * spec
        for i, sco in enumerate(sc_scores):
            rows.append(dict(participant_id=f"p_{sc}_{i:03d}", exam_type=exam, subject_code=subject,
                             variant_id=variant, region_code=region, school_code=str(sc),
                             room_code=(room(sc, i) if room else None), status=status, result_status=result,
                             primary_score=int(sco), updated_at=T0, deleted=False))
    return pd.DataFrame(rows)


def make_answers(sessions, n_tasks=10, seed=0, n_wrong_options=25, p_correct=0.5):
    """Случайные ответы: верный — "ok", неверный — один из n_wrong_options вариантов "w<k>"."""
    rng = np.random.default_rng(seed)
    rows = []
    for pid in sessions["participant_id"]:
        for t in range(1, n_tasks + 1):
            ok = rng.random() < p_correct
            rows.append(dict(participant_id=pid, task_number=t, answer="ok" if ok else f"w{rng.integers(n_wrong_options)}",
                             is_correct=bool(ok), deleted=False))
    return pd.DataFrame(rows)


def set_answers(answers, pid, values):
    """Задать ответы участника списком; "ok" — верный, иначе неверный (пустая строка — пустой)."""
    a = answers.copy()
    for t, v in enumerate(values, start=1):
        m = (a["participant_id"] == pid) & (a["task_number"] == t)
        a.loc[m, "answer"] = v
        a.loc[m, "is_correct"] = (v == "ok")
    return a


def subjects(exam="OGE", subject="02", threshold=8):
    return pd.DataFrame([dict(exam_type=exam, subject_code=subject, threshold_score=threshold)])


def events(rows):
    """rows: [(participant_id, event_type, deleted)]"""
    return pd.DataFrame([dict(participant_id=p, event_type=e, deleted=d,
                              occurred_at=T0) for p, e, d in rows],
                        columns=["participant_id", "event_type", "deleted", "occurred_at"])
