"""Интеграционные тесты на тестовых наборах (T-07, T-13, T-19, T-30-подобные, T-41, T-52, T-55, T-57, T-63, T-70).

Путь к набору — переменная окружения SMA_DATA (папка с small/ и medium/), по умолчанию
./Тестовые_данные_СМА_v2. Без набора тесты пропускаются.
"""
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

import pandas as pd

from sma.sma_anomalies import Params, run_dataset
from sma.sma_anomalies.analysis import analyze_platform
from sma.sma_anomalies.check_key import check_dataset
from sma.sma_anomalies.loader import load_exam
from sma.sma_anomalies.runlog import Scheduler, exam_is_complete

DATA = os.environ.get("SMA_DATA", "Тестовые_данные_СМА_v2")


class Skip(Exception):
    pass


def _need(path):
    if not os.path.isdir(path):
        raise Skip(f"нет набора данных: {path}")
    return path


def test_key_small_all_planted_found_t63():
    rows = check_dataset(_need(os.path.join(DATA, "small")))
    assert [r for r in rows if not r[1]] == []


def test_key_medium_all_planted_found_t63_t07():
    rows = check_dataset(_need(os.path.join(DATA, "medium")))
    assert len(rows) > 40 and [r for r in rows if not r[1]] == []


def test_matches_reference_values_t52():
    """П1–П4, z′ и индексы совпадают с reference_check ключа с точностью 0,011 (T-52)."""
    root = _need(os.path.join(DATA, "medium"))
    key = json.load(open(os.path.join(root, "answer_key.json"), encoding="utf-8"))
    for exam, sub in [("EGE", "ege"), ("OGE", "oge"), ("VPR", "vpr"), ("NIKO", "niko")]:
        res = analyze_platform(load_exam(os.path.join(root, sub), exam))
        refs = {x["school_code"]: x for x in key["exams"][exam]["reference_check"]["schools"]}
        assert set(res["schools"]["school_code"]) == set(refs)
        for _, r in res["schools"].iterrows():
            x = refs[r["school_code"]]
            pairs = [(r.p1, x["P1"]), (r.p2, x["P2"]), (r.p3, x["P3"]), (r.p4a, x["P4a"]), (r.p4b, x["P4b"]),
                     (r.z1, x["z_adj"]["P1"]), (r.z2, x["z_adj"]["P2"]), (r.z3, x["z_adj"]["P3"]),
                     (r.z4a, x["z_adj"]["P4a"]), (r.z4b, x["z_adj"]["P4b"]), (r["index"], x["index"])]
            assert all(abs(a - b) <= 0.011 for a, b in pairs), (exam, r["school_code"])
        got = {frozenset(g.split(",")) for g in res["groups"]["participant_ids"]}
        assert got == {frozenset(g["participant_ids"]) for g in key["exams"][exam]["reference_check"]["P1_groups"]}
        assert int(res["pairs"]["suspicious"].sum()) == key["exams"][exam]["reference_check"]["P1_suspicious_pairs"]


def test_csv_formats_t13_t55():
    root = _need(os.path.join(DATA, "medium"))
    with tempfile.TemporaryDirectory() as out:
        run_dataset(root, out)
        for name, n_cols in [("anomalies.csv", 11), ("schools.csv", 19), ("groups.csv", 10)]:
            raw = open(os.path.join(out, name), encoding="utf-8").read()
            rows = list(csv.reader(io.StringIO(raw), delimiter=";"))
            assert all(len(r) == n_cols for r in rows) and len(rows) > 1
            assert "e-" not in raw                                      # без научной записи чисел
        sch = pd.read_csv(os.path.join(out, "schools.csv"), sep=";", decimal=",")
        assert list(sch["index"]) == sorted(sch["index"], reverse=True)  # перечень — по убыванию индекса
        an = pd.read_csv(os.path.join(out, "anomalies.csv"), sep=";", decimal=",", dtype={"subject_code": str})
        assert an[["rule", "exam_type", "subject_code", "object_type", "object_id", "detected_at"]].notna().all().all()
        assert set(an["rule"]) == {"A1", "A2", "A3"} and an["subject_code"].str.len().eq(2).all()


def test_two_runs_identical_t57():
    root = _need(os.path.join(DATA, "medium"))
    with tempfile.TemporaryDirectory() as o1, tempfile.TemporaryDirectory() as o2:
        run_dataset(root, o1)
        run_dataset(root, o2)
        for f in ["anomalies.csv", "schools.csv", "groups.csv"]:
            assert open(os.path.join(o1, f), encoding="utf-8").read() == open(os.path.join(o2, f), encoding="utf-8").read()


def test_run_log_has_all_parameters_t41():
    root = _need(os.path.join(DATA, "small"))
    with tempfile.TemporaryDirectory() as out:
        run_dataset(root, out, Params())
        rec = json.loads(open(os.path.join(out, "run-log.jsonl"), encoding="utf-8").readline())
        for k in ["n_min", "k_min", "alpha", "delta", "k_adj", "i_min"]:
            assert k in rec["params"]
        assert rec["started_at"] and rec["finished_at"]


def test_scheduler_ten_minute_runs_and_final_run_t41():
    sess = pd.DataFrame({"status": ["in_progress", "finished"], "result_status": ["pending", "pending"], "deleted": False})
    done = pd.DataFrame({"status": ["finished", "annulled", "absent"], "result_status": ["final", "final", "final"], "deleted": False})
    sch, t0, runs = Scheduler(), datetime(2026, 12, 5, 10, 0), []
    for m in range(0, 61):
        now = t0 + timedelta(minutes=m)
        why = sch.tick(now, sess if m < 60 else done)
        if why:
            runs.append((m, why))
    assert [m for m, _ in runs if _ == "periodic"] == [0, 10, 20, 30, 40, 50]
    assert runs[-1] == (60, "final")
    assert sch.tick(t0 + timedelta(minutes=70), done) is None          # после финального запуска — больше не запускаем
    assert exam_is_complete(done) and not exam_is_complete(sess)


def test_pending_snapshot_after_exam_has_no_final_results_t70():
    """Снимок «сразу после экзамена» (все оценки pending): итоговых результатов нет, анализ их не использует."""
    root = _need(os.path.join(DATA, "medium", "oge"))
    script = os.path.join(DATA, "make_snapshot.py")
    if not os.path.exists(script):
        raise Skip("нет make_snapshot.py")
    with tempfile.TemporaryDirectory() as snap:
        subprocess.run([sys.executable, script, root, "2026-12-05T14:10:00+03:00", snap], check=True,
                       capture_output=True)
        d = load_exam(snap, "OGE")
        assert (d.sessions["result_status"] == "pending").any()
        res = analyze_platform(d)
        n_final = ((d.sessions["status"] == "finished") & d.sessions["result_status"].isin(["final", "appeal_changed"])).sum()
        assert res["schools"].empty or res["schools"]["participants"].sum() <= n_final
        assert res["anomalies"].empty or "A1" not in set(res["anomalies"]["rule"])
