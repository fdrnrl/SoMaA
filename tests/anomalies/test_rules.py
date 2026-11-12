"""Тесты оперативных правил А1–А3 (T-07–T-14, T-42, T-70)."""
import numpy as np
import pandas as pd

from sma_anomalies import Params, merge_new, rule_a1, rule_a2, rule_a3, run_rules
from sma_anomalies.normalize import normalize_answer
from tests.synth import events, make_answers, make_sessions, set_answers


def _region_with_outlier(n_schools=10):
    schools = {f"7700{i}": [8, 9, 10, 11, 9, 10, 9, 10, 8, 11] for i in range(n_schools - 1)}
    schools["77099"] = [25] * 10
    return make_sessions(schools)


# --- нормализация (T-42, FR-31, FR-32)
def test_normalize_decimal_and_spaces():
    assert normalize_answer("0.5") == normalize_answer(" 0,5 ") == normalize_answer("0,5") == "0,5"


def test_normalize_case():
    assert normalize_answer("Да") == normalize_answer("да")


def test_blank_answers_normalize_to_empty():
    assert normalize_answer("") == normalize_answer("   ") == normalize_answer(None) == ""


# --- А1
def test_a1_fires_on_outlier_school():
    r = rule_a1(_region_with_outlier())
    assert list(r["object_id"]) == ["77099"]
    assert r.iloc[0]["value"] > r.iloc[0]["threshold"]


def test_a1_not_applied_with_two_schools_t09():
    s = make_sessions({"77001": [5] * 10, "77002": [30] * 10})
    assert rule_a1(s).empty


def test_a1_ignores_schools_below_n_min():
    schools = {f"7700{i}": [8, 9, 10, 11, 9, 10, 9, 10, 8, 11] for i in range(5)}
    schools["77099"] = [30] * 9                                  # 9 участников < N_min
    assert rule_a1(make_sessions(schools)).empty


def test_a1_per_subject_t10():
    a = _region_with_outlier()
    flat = make_sessions({f"7700{i}": [8, 9, 10, 11, 9, 10, 9, 10, 8, 11] for i in range(10)}, subject="03")
    flat["participant_id"] = flat["participant_id"] + "_b"
    r = rule_a1(pd.concat([a, flat], ignore_index=True))
    assert set(r["subject_code"]) == {"02"}


def test_a1_pending_results_excluded_t70():
    s = _region_with_outlier()
    s.loc[s["school_code"] == "77099", "result_status"] = "pending"
    assert rule_a1(s).empty


def test_a1_leave_one_out_option_finds_more_than_all_mode():
    # 5 школ: при σ по всем школам z <= (n-1)/√n = 1,79 < 2 — А1 не сработает никогда (раздел 10 ТЗ)
    def sch(shift):
        return [8 + shift, 9 + shift, 10 + shift, 11 + shift, 9 + shift, 10 + shift, 9 + shift, 10 + shift, 8 + shift, 11 + shift]
    s = make_sessions({"54001": sch(-0.5), "54002": sch(0), "54003": sch(0.5), "54004": sch(1), "54099": sch(4.5)})
    assert rule_a1(s).empty
    loo = rule_a1(s, Params(a1_sigma_mode="leave_one_out"))
    assert list(loo["object_id"]) == ["54099"]


# --- А2
def _group_fixture(variants=None, blank=False):
    s = make_sessions({"77001": 8}, variant="v1")
    if variants:
        s["variant_id"] = variants
    a = make_answers(s, n_tasks=6, seed=1)
    same = ["ok", "w1", "", "ok", "w2", "ok"] if not blank else [""] * 6
    for pid in s["participant_id"][:5]:
        a = set_answers(a, pid, same)
    return s, a


def test_a2_five_identical_found():
    s, a = _group_fixture()
    r = rule_a2(s, a)
    assert len(r) == 1 and r.iloc[0]["value"] == 5


def test_a2_different_variants_not_flagged_t11():
    s, a = _group_fixture(variants=["v1", "v2", "v1", "v2", "v1", "v2", "v1", "v2"])
    assert rule_a2(s, a).empty


def test_a2_blank_group_not_flagged_t11():
    s, a = _group_fixture(blank=True)
    assert rule_a2(s, a).empty


def test_a2_matches_after_normalization():
    s, a = _group_fixture()
    a = set_answers(a, s["participant_id"][0], [" ok", "W1", "", "ok", "w2", "ok"])
    assert len(rule_a2(s, a)) == 1                                # пробелы и регистр не мешают совпадению


# --- А3
def test_a3_focus_lost_not_counted_t12():
    s = make_sessions({"77001": 3})
    p = list(s["participant_id"])
    ev = events([(p[0], "focus_lost", False)] * 5 + [(p[0], "tab_switch", False)] * 2 +
                [(p[1], "copy", False)] * 3)
    r = rule_a3(s, ev)
    assert list(r["object_id"]) == [p[1]]


def test_a3_revoked_event_not_counted():
    s = make_sessions({"77001": 2})
    p = s["participant_id"].iloc[0]
    ev = events([(p, "copy", False), (p, "paste", False), (p, "tab_switch", True)])
    assert rule_a3(s, ev).empty


def test_a3_empty_events_ok():
    assert rule_a3(make_sessions({"77001": 2}), events([])).empty


# --- хранение (FR-13, T-14)
def test_merge_new_is_idempotent_t14():
    s = make_sessions({"77001": 3})
    p = s["participant_id"].iloc[0]
    found = run_rules(s, make_answers(s), events([(p, "copy", False)] * 3))
    stored, new1 = merge_new(None, found)
    for _ in range(3):
        stored, _ = merge_new(stored, found)
    assert len(new1) == 1 and len(stored) == 1


def test_all_fields_present_t13():
    s = _region_with_outlier()
    found = run_rules(s, make_answers(s), events([]))
    stored, _ = merge_new(None, found)
    cols = ["anomaly_id", "rule", "exam_type", "subject_code", "object_type", "object_id",
            "region_code", "school_code", "value", "threshold", "detected_at"]
    assert stored[cols].notna().all().all()
