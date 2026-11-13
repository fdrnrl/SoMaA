"""Сверка результатов с ключом набора (answer_key.json): заложенное найдено, ловушки не сработали (T-63, FR-53).

Запуск:  python -m sma.sma_anomalies.check_key <папка набора, например medium>
FR-66 (различие вариантов) — витрина и страница «Темы и варианты», к модулю аномалий не относится.
"""
from __future__ import annotations

import json
import os
import sys

from .analysis import analyze_platform
from .loader import load_dataset
from .params import Params


def check_exam(exam: str, key: dict, res: dict, data) -> list[tuple[str, bool, str]]:
    out = []
    an, sch, grp, p2 = res["anomalies"], res["schools"], res["groups"], res["participants"]
    a = lambda rule: an[an["rule"] == rule] if len(an) else an

    for p in key["planted"]:
        r = p["rule"]
        if r == "A1":
            ok = len(a("A1")) and (a("A1")["object_id"] == p["school_code"]).any()
            out.append((f"{exam} А1 школа {p['school_code']}", bool(ok), "найдена" if ok else "НЕ найдена"))
        elif r == "A2":
            found = [set(x.split(",")) for x in a("A2")["participant_ids"]] if len(a("A2")) else []
            ok = set(p["participant_ids"]) in found
            out.append((f"{exam} А2 школа {p['school_code']} {p['variant_id']}", ok, "группа совпала" if ok else "НЕ найдена"))
        elif r == "A3":
            ok = len(a("A3")) and (a("A3")["object_id"] == p["participant_id"]).any()
            out.append((f"{exam} А3 {p['participant_id']}", bool(ok), "найден" if ok else "НЕ найден"))
        elif r == "P1":
            want = set(p["participant_ids"])
            ok = len(grp) and any(want <= set(x.split(",")) for x in grp["participant_ids"])
            out.append((f"{exam} П1 школа {p['school_code']}", bool(ok), "группа найдена" if ok else "НЕ найдена"))
        elif r == "P2":
            top = p2.sort_values("g_star", ascending=False).iloc[0]["participant_id"]
            ok = top == p["participant_id"]
            out.append((f"{exam} П2 {p['participant_id']}", ok, "наибольший G*" if ok else f"наибольший у {top}"))
        elif r in ("P3", "P4a", "P4b"):
            col = {"P3": "z3", "P4a": "z4a", "P4b": "z4b"}[r]
            top = sch.sort_values(col, ascending=False).iloc[0]["school_code"]
            ok = top == p["school_code"]
            out.append((f"{exam} {r} школа {p['school_code']}", ok, f"наибольшая z′ ({col})" if ok else f"наибольшая у {top}"))
    for d in key["decoys"]:
        k = d["kind"]
        if k == "A2_blank":
            bad = len(a("A2")) and (a("A2")["school_code"] == d["school_code"]).any() and \
                any(set(d["participant_ids"]) & set(x.split(",")) for x in a("A2")["participant_ids"])
            out.append((f"{exam} ловушка {k} школа {d['school_code']}", not bad, "не сработала" if not bad else "СРАБОТАЛА"))
        elif k in ("A3_focus", "A3_deleted"):
            bad = len(a("A3")) and (a("A3")["object_id"] == d["participant_id"]).any()
            out.append((f"{exam} ловушка {k} {d['participant_id']}", not bad, "не сработала" if not bad else "СРАБОТАЛА"))
        elif k == "deleted_session":
            pid = d["participant_id"]
            seen = pid in set(data.sessions["participant_id"])        # удаленная сессия отброшена загрузчиком
            in_res = len(p2) and (p2["participant_id"] == pid).any()
            ok = (not seen) and (not in_res)
            out.append((f"{exam} ловушка {k} {pid}", ok, "в расчетах нет" if ok else "ПОПАЛА в расчеты"))
    # лишние срабатывания
    planted_a3 = {p["participant_id"] for p in key["planted"] if p["rule"] == "A3"}
    extra_a3 = set(a("A3")["object_id"]) - planted_a3 if len(a("A3")) else set()
    out.append((f"{exam} лишние А3", not extra_a3, "нет" if not extra_a3 else str(sorted(extra_a3))))
    planted_a1 = {p["school_code"] for p in key["planted"] if p["rule"] == "A1"}
    extra_a1 = set(a("A1")["object_id"]) - planted_a1 if len(a("A1")) else set()
    out.append((f"{exam} лишние А1", not extra_a1, "нет" if not extra_a1 else str(sorted(extra_a1))))
    return out


def check_dataset(root: str, params: Params = Params()) -> list[tuple[str, bool, str]]:
    with open(os.path.join(root, "answer_key.json"), encoding="utf-8") as fh:
        key = json.load(fh)
    rows = []
    for exam, data in load_dataset(root).items():
        res = analyze_platform(data, params)
        rows += check_exam(exam, key["exams"][exam], res, data)
    return rows


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    rows = check_dataset(argv[0])
    for name, ok, msg in rows:
        print(("OK   " if ok else "FAIL ") + name + " — " + msg)
    bad = [r for r in rows if not r[1]]
    print(f"\nПроверок: {len(rows)}, не пройдено: {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
