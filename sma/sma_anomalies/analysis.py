"""Верхний уровень: один запуск анализа по платформе или по всему набору."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import export
from .deep import DeepResult, analyze_exam
from .loader import ExamData, load_dataset, load_exam
from .params import Params
from .prep import final_sessions
from .rules import ANOMALY_COLUMNS, merge_new, run_rules
from .runlog import RunLog

MSK = timezone(timedelta(hours=3))


def deep_by_subject(sessions, answers, subjects, params: Params) -> list[DeepResult]:
    """Углубленный анализ отдельно для каждого сочетания «экзамен, предмет» (раздел 5.5)."""
    out = []
    fin = final_sessions(sessions)
    for (exam, subj), g in fin.groupby(["exam_type", "subject_code"]):
        sess = sessions[(sessions["exam_type"] == exam) & (sessions["subject_code"] == subj)]
        ans = answers[answers["participant_id"].isin(sess["participant_id"])]
        out.append(analyze_exam(sess, ans, subjects, params))
    return out


def analyze_platform(data: ExamData, params: Params = Params(), as_of: str | None = None) -> dict:
    """Правила А1–А3 и углубленный анализ по одной платформе. Возвращает таблицы результатов."""
    anomalies = run_rules(data.sessions, data.answers, data.events, params, as_of)
    results = deep_by_subject(data.sessions, data.answers, data.subjects, params)
    schools = pd.concat([r.schools for r in results], ignore_index=True) if results else pd.DataFrame()
    groups = pd.concat([r.groups for r in results if not r.groups.empty], ignore_index=True) \
        if any(not r.groups.empty for r in results) else pd.DataFrame()
    participants = pd.concat([r.participants for r in results], ignore_index=True) if results else pd.DataFrame()
    pairs = pd.concat([r.pairs for r in results], ignore_index=True) if results else pd.DataFrame()
    return {"anomalies": anomalies, "schools": schools, "groups": groups, "participants": participants, "pairs": pairs}


def _data_time(datasets: dict) -> str:
    """Момент снимка данных: наибольший updated_at сессий всех платформ, в московском времени."""
    ts = max(pd.to_datetime(d.sessions["updated_at"], utc=True).max() for d in datasets.values())
    return ts.tz_convert("Europe/Moscow").isoformat()


def run_dataset(root: str, out_dir: str, params: Params = Params(), now: datetime | None = None,
                as_of: str | None = None) -> dict:
    """Читает набор (small/ или medium/), считает все платформы, пишет CSV и журнал запуска.

    as_of — момент расчета для полей detected_at и calculated_at; по умолчанию момент снимка данных
    (на тестовых наборах это 11.12.2026 12:00 МСК), а не часы компьютера. В журнал запусков
    пишется реальное время начала и конца (NFR-24).

    Файлы в out_dir: anomalies.csv (7.2), schools.csv (7.4), groups.csv (7.3), run-log.jsonl (NFR-24),
    а также details.csv с составом групп А2 (поле participant_ids, которого нет в 7.2).
    """
    os.makedirs(out_dir, exist_ok=True)
    started = now or datetime.now(MSK)
    log = RunLog(os.path.join(out_dir, "run-log.jsonl"))
    rec = log.start(started, "manual", params)
    run_id = rec["run_id"]
    datasets = load_dataset(root)
    calc_at = as_of or _data_time(datasets)

    stored = pd.DataFrame(columns=ANOMALY_COLUMNS)
    all_schools, all_groups = [], []
    for exam, data in datasets.items():
        res = analyze_platform(data, params, as_of=calc_at)
        stored, _ = merge_new(stored, res["anomalies"])
        if not res["schools"].empty:
            all_schools.append(res["schools"].assign(run_id=run_id, calculated_at=calc_at))
        if not res["groups"].empty:
            all_groups.append(res["groups"])
    schools = pd.concat(all_schools, ignore_index=True) if all_schools else pd.DataFrame(columns=export.SCHOOL_COLUMNS)
    groups = pd.concat(all_groups, ignore_index=True) if all_groups else pd.DataFrame(columns=export.GROUP_COLUMNS)
    if not groups.empty:
        groups.insert(0, "group_id", range(1, len(groups) + 1))
        groups["run_id"] = run_id

    export.write_text(os.path.join(out_dir, "anomalies.csv"), export.anomalies_csv(stored))
    export.write_text(os.path.join(out_dir, "schools.csv"), export.schools_csv(schools))
    export.write_text(os.path.join(out_dir, "groups.csv"), export.groups_csv(groups))
    det = stored[stored["rule"] == "A2"]
    if not det.empty:
        export.write_text(os.path.join(out_dir, "details.csv"),
                          export.to_csv_text(det, ["anomaly_id", "exam_type", "school_code", "variant_id", "participant_ids"]))
    log.finish(rec, datetime.now(MSK), anomalies=len(stored), schools=len(schools), groups=len(groups),
               schools_in_list=int(schools["in_list"].sum()) if len(schools) else 0)
    return {"anomalies": stored, "schools": schools, "groups": groups, "run": rec}
