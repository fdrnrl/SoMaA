"""Эталонные правила А1–А3, П1–П4 на маленьких таблицах с известным ответом (раздел 11)."""

import math

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from sma.reference import rules


def sessions_frame(rows: list[tuple]) -> pd.DataFrame:
    """(participant_id, school_code, region_code, primary_score, variant_id, room_code)."""
    return pd.DataFrame(
        [
            {
                "exam_type": "OGE",
                "subject_code": "02",
                "participant_id": pid,
                "school_code": school,
                "region_code": region,
                "room_code": room,
                "variant_id": variant,
                "status": "finished",
                "result_status": "final",
                "primary_score": score,
                "deleted": False,
            }
            for pid, school, region, score, variant, room in rows
        ]
    )


def answers_frame(rows: list[tuple]) -> pd.DataFrame:
    """(participant_id, task_number, answer, is_correct)."""
    return pd.DataFrame(
        [
            {"exam_type": "OGE", "participant_id": pid, "task_number": task, "answer": answer,
             "is_correct": correct, "deleted": False}
            for pid, task, answer, correct in rows
        ]
    )  # fmt: skip


def schools_with_means(means: list[float], size: int = 10) -> pd.DataFrame:
    rows = []
    for k, mean in enumerate(means):
        for j in range(size):
            rows.append((f"p{k}_{j}", f"77{k:03d}", "77", mean + (j % 2) - 0.5, "v1", None))
    return sessions_frame(rows)


def test_reference_a1_samuelson():
    """При 5 школах z одной школы с σ «вместе с ней» не превышает 1,79 — А1 не срабатывает."""
    assert rules.max_z_include_self(5) == pytest.approx(1.789, abs=0.001)
    result = rules.rule_a1(schools_with_means([10, 11, 12, 11, 1000]))
    assert result["z"].max() <= 1.79
    assert not result["anomaly"].any()


@given(st.lists(st.floats(0, 32), min_size=3, max_size=12))
def test_a1_include_self_bound(means):
    """Свойство: z ≤ (n − 1) / √n для любых средних школ."""
    result = rules.rule_a1(schools_with_means(means))
    bound = rules.max_z_include_self(len(means)) + 1e-9
    assert (result["z"] <= bound).all()


def test_a1_leave_one_out_finds_outlier():
    result = rules.rule_a1(schools_with_means([10, 11, 12, 11, 20]), sigma_mode="leave_one_out")
    assert set(result.loc[result["anomaly"], "school_code"]) == {"77004"}


def test_a1_needs_three_big_schools():
    small_school = rules.rule_a1(schools_with_means([10, 30], size=10))
    assert small_school.empty
    too_few = rules.rule_a1(schools_with_means([10, 11, 30], size=9))
    assert too_few.empty


def test_a2_identical_group_found_blank_group_not():
    people = [(f"c{i}", "77001", "77", 5, "v1", None) for i in range(5)]
    people += [(f"b{i}", "77002", "77", 0, "v1", None) for i in range(5)]
    people += [("x", "77001", "77", 5, "v1", None)]
    rows = []
    for task in range(1, 7):
        rows += [(f"c{i}", task, f"a{task}", False) for i in range(5)]
        rows += [(f"b{i}", task, "", False) for i in range(5)]
        rows.append(("x", task, f"z{task}", False))
    groups = rules.rule_a2(answers_frame(rows), sessions_frame(people))
    assert [group["participant_ids"] for group in groups] == [[f"c{i}" for i in range(5)]]


def test_a2_needs_five():
    people = [(f"c{i}", "77001", "77", 5, "v1", None) for i in range(4)]
    rows = [(f"c{i}", 1, "7", True) for i in range(4)]
    assert rules.rule_a2(answers_frame(rows), sessions_frame(people)) == []


def test_a3_ignores_focus_lost():
    events = pd.DataFrame(
        [{"exam_type": "OGE", "participant_id": "a", "event_type": kind}
         for kind in ("tab_switch", "copy", "paste")]
        + [{"exam_type": "OGE", "participant_id": "b", "event_type": kind}
           for kind in ["focus_lost"] * 5 + ["tab_switch"] * 2]
    )  # fmt: skip
    assert list(rules.rule_a3(events)["participant_id"]) == ["a"]


def test_holm():
    assert rules.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert rules.holm([0.5]) == [0.5]


def test_connected_groups():
    groups = rules.connected_groups([("a", "b"), ("b", "c"), ("x", "y")])
    assert sorted(groups) == [["a", "b", "c"], ["x", "y"]]


def test_p1_finds_shared_rare_answers():
    """Трое в одной аудитории с одинаковыми редкими неверными ответами в 6 заданиях."""
    people = [(f"p{i}", "77001", "77", 0, "v1", "0001") for i in range(30)]
    rows = []
    for i in range(30):
        for task in range(1, 13):
            if i < 3 and task <= 6:
                rows.append((f"p{i}", task, f"rare{task}", False))
            else:
                rows.append((f"p{i}", task, str(task), True))
    groups = rules.rule_p1(answers_frame(rows), sessions_frame(people))
    assert [group["participant_ids"] for group in groups] == [["p0", "p1", "p2"]]


def test_p2_reversed_pattern_has_g_star_one():
    """Решены только трудные задания (их мало кто решил) → G* = 1; «по порядку» → 0."""
    people = [(f"p{i}", "77001", "77", 0, "v1", None) for i in range(10)]
    rows = []
    for i in range(10):
        for task in range(1, 7):
            normal = task <= 3  # обычные ученики решают легкие 1–3
            reversed_ = task > 3
            rows.append((f"p{i}", task, "x", reversed_ if i == 0 else normal))
    result = rules.rule_p2(answers_frame(rows), sessions_frame(people)).set_index("participant_id")
    assert result.loc["p0", "g_star"] == pytest.approx(1.0)
    assert result.loc["p1", "g_star"] == 0


def test_robust_z():
    assert list(rules.robust_z(pd.Series([5.0, 5.0, 5.0]))) == [0, 0, 0]
    z = rules.robust_z(pd.Series([1.0, 2.0, 3.0, 4.0, 100.0]))
    assert z.iloc[-1] == max(z)
    assert z.iloc[2] == 0


def test_p4_identical_scores_and_threshold_window():
    rows = []
    for k in range(6):
        for j in range(10):
            # Обычные школы различаются: у школы k в окне [8, 10] ровно k − 2 ученика
            # (доли 0; 0,1; 0,2; 0,3), иначе MAD = 0 и все z = 0.
            if k == 0:
                score = 15
            elif k == 1:
                score = 8 if j < 8 else 20
            else:
                score = 9 if j < k - 2 else 12 + j + k
            rows.append((f"p{k}_{j}", f"77{k:03d}", "77", score, "v1", None))
    result = rules.rule_p4(sessions_frame(rows), {"OGE": 8}).set_index("school_code")
    assert result["z4a"].idxmax() == "77000", "все баллы одинаковые — наименьший разброс"
    assert result["z4b"].idxmax() == "77001", "80% у порога"
    assert result.loc["77000", "sd"] == 0
    assert math.isclose(result.loc["77001", "share_window"], 0.8)
