"""Тесты углубленного анализа П1–П4 (T-43–T-54, T-57, T-70)."""
import numpy as np
import pandas as pd

from sma.sma_anomalies import Params, analyze_exam
from sma.sma_anomalies.deep import compute_p2, holm_adjust, robust_z
from sma.sma_anomalies.prep import final_sessions, with_norm_answers
from tests.anomalies.synth import make_answers, make_sessions, set_answers, subjects


def _base(n_schools=12, n=12, seed=3, scores=None):
    rng = np.random.default_rng(seed)
    sch = {f"7{i:04d}": list(rng.integers(5, 20, n)) for i in range(n_schools)}
    s = make_sessions(sch)
    return s, make_answers(s, n_tasks=12, seed=seed)


# --- FR-33, T-43
def test_school_with_9_participants_not_calculated_t43():
    s = make_sessions({"77001": 12, "77002": 12, "77003": 9})
    r = analyze_exam(s, make_answers(s), subjects())
    assert "77003" not in set(r.schools["school_code"])


# --- FR-42, T-50
def test_z_zero_when_mad_zero_t50():
    assert (robust_z(pd.Series([3.0, 3.0, 3.0, 3.0])) == 0).all()
    s = make_sessions({"77001": 12, "77002": 12, "77003": 12})
    r = analyze_exam(s, make_answers(s, p_correct=0.5, seed=1), subjects())
    assert (r.schools["z4a"].abs() <= 10).all() and not r.schools[["z1", "z2", "z3", "z4a", "z4b"]].isna().any().any()


def test_robust_z_value():
    x = pd.Series([1, 2, 3, 4, 100.0])
    assert abs(robust_z(x).iloc[-1] - (100 - 3) / (1.4826 * 1.0)) < 1e-9


# --- FR-38, T-47
def _p2_fixture():
    s = make_sessions({"77001": 40})
    a = make_answers(s, n_tasks=6, seed=5, p_correct=0.5)
    # задания 1..6: делаем трудность строго возрастающей по номеру
    rng = np.random.default_rng(9)
    rows = []
    for pid in s["participant_id"]:
        for t in range(1, 7):
            ok = rng.random() < (0.9 - 0.12 * t)
            rows.append(dict(participant_id=pid, task_number=t, answer="ok" if ok else "w1", is_correct=bool(ok), deleted=False))
    a = pd.DataFrame(rows)
    return s, a


def test_guttman_g_star_zero_and_one_t47():
    s, a = _p2_fixture()
    p_asc, p_hard = s["participant_id"].iloc[0], s["participant_id"].iloc[1]
    a = set_answers(a, p_asc, ["ok", "ok", "ok", "w1", "w1", "w1"])        # строго по возрастанию трудности
    a = set_answers(a, p_hard, ["w1", "w1", "w1", "ok", "ok", "ok"])       # только 3 самых трудных
    res = compute_p2(final_sessions(s), with_norm_answers(a)).set_index("participant_id")
    assert res.loc[p_asc, "g_star"] == 0.0 and res.loc[p_hard, "g_star"] == 1.0


def test_g_star_zero_when_all_or_nothing():
    s, a = _p2_fixture()
    a = set_answers(a, s["participant_id"].iloc[2], ["ok"] * 6)
    a = set_answers(a, s["participant_id"].iloc[3], ["w1"] * 6)
    res = compute_p2(final_sessions(s), with_norm_answers(a)).set_index("participant_id")
    assert res.loc[s["participant_id"].iloc[2], "g_star"] == 0 and res.loc[s["participant_id"].iloc[3], "g_star"] == 0


# --- П4, T-49
def test_p4a_p4b_planted_largest_t49():
    rng = np.random.default_rng(4)
    sch = {f"7{i:04d}": list(rng.integers(3, 25, 12)) for i in range(15)}
    sch["70100"] = [12] * 12                                                  # σ = 0
    sch["70200"] = [8, 9, 10, 8, 9, 10, 8, 9, 10, 8, 9, 22]                   # почти все в окне [8; 10]
    s = make_sessions(sch)
    r = analyze_exam(s, make_answers(s, 8), subjects(threshold=8)).schools.set_index("school_code")
    assert r["z4a"].idxmax() == "70100" and r["z4b"].idxmax() == "70200"


def test_p4b_window_bounds_inclusive():
    s = make_sessions({"77001": [8, 10, 11, 7, 8, 9, 20, 20, 20, 20, 20, 20], "77002": 12, "77003": 12})
    r = analyze_exam(s, make_answers(s, 6), subjects(threshold=8)).schools.set_index("school_code")
    assert abs(r.loc["77001", "p4b"] - 4 / 12) < 1e-9                         # 8, 10, 8, 9; 11 и 7 вне окна


# --- FR-43, T-51
def test_size_correction_makes_small_school_index_smaller_t51():
    """Две школы с одинаковой долей в окне над порогом (П4б = 0,3), 10 и 100 участников."""
    pattern = [8, 9, 10, 20, 20, 20, 20, 20, 20, 20]                  # 3 из 10 в окне [8; 10]
    sch = {"77001": pattern, "77002": pattern * 10}
    for i, k in zip(range(3, 9), [0, 1, 2, 1, 0, 2]):                  # у остальных школ доля в окне разная (MAD > 0)
        sch[f"7700{i}"] = ([8] * k + [20] * (10 - k)) * 3
    s = make_sessions(sch)
    r = analyze_exam(s, make_answers(s, 6, seed=2), subjects(threshold=8)).schools.set_index("school_code")
    assert abs(r.loc["77001", "p4b"] - r.loc["77002", "p4b"]) < 1e-12        # сырые значения одинаковы
    assert 0 < r.loc["77001", "z4b"] < r.loc["77002", "z4b"]                  # z′ малой школы меньше
    assert abs(r.loc["77001", "z4b"] / r.loc["77002", "z4b"] - (10 / 30) / (100 / 120)) < 1e-9


# --- П1, T-44, T-45, T-46
def _p1_fixture(room=False, group_size=4, n_tasks=12):
    rng = np.random.default_rng(11)
    sch = {f"7{i:04d}": list(rng.integers(5, 20, 14)) for i in range(8)}
    kw = {"room": (lambda sc, i: f"R{i % 2}") if room else None}
    s = make_sessions(sch, **kw)
    a = make_answers(s, n_tasks=n_tasks, seed=12, n_wrong_options=40, p_correct=0.3)
    ids = list(s[s["school_code"] == "70000"]["participant_id"])[:group_size]
    rare = [f"zz{t}" for t in range(1, n_tasks + 1)]
    for pid in ids:
        a = set_answers(a, pid, rare[:6] + ["ok"] * (n_tasks - 6))
    return s, a, ids


def test_p1_finds_planted_group_t44():
    s, a, ids = _p1_fixture()
    g = analyze_exam(s, a, subjects()).groups
    assert len(g) == 1 and set(g.iloc[0]["participant_ids"].split(",")) == set(ids)


def test_p1_common_wrong_answers_not_suspicious_t45():
    s = make_sessions({f"7{i:04d}": 14 for i in range(6)})
    a = make_answers(s, n_tasks=12, seed=2, n_wrong_options=1, p_correct=0.4)  # неверный ответ у всех один и тот же
    assert analyze_exam(s, a, subjects()).groups.empty


def test_p1_no_false_pairs_on_clean_data_t46():
    s, a = _base(n_schools=20, n=14, seed=6)
    a = make_answers(s, n_tasks=12, seed=6, n_wrong_options=40, p_correct=0.3)
    assert analyze_exam(s, a, subjects()).pairs["suspicious"].sum() <= 2


def test_p1_group_comparison_by_room_or_school_t54():
    s, a, ids = _p1_fixture(room=True)
    # участники группы лежат в разных аудиториях -> с room_code пары между ними не строятся
    s2 = s.copy()
    s2.loc[s2["participant_id"] == ids[0], "room_code"] = "R0"
    s2.loc[s2["participant_id"].isin(ids[1:]), "room_code"] = "R1"
    with_room = analyze_exam(s2, a, subjects()).pairs
    no_room = analyze_exam(s2.drop(columns="room_code"), a, subjects()).pairs
    assert len(with_room) < len(no_room)
    assert not ((with_room["a"] == ids[0]) & (with_room["b"].isin(ids[1:]))).any()


# --- П3, T-48
def test_p3_planted_school_largest_t48():
    rng = np.random.default_rng(8)
    sch = {f"7{i:04d}": list(rng.integers(5, 20, 14)) for i in range(10)}
    s = make_sessions(sch)
    n_tasks = 12
    rows = []
    for pid, sc in zip(s["participant_id"], s["school_code"]):
        for t in range(1, n_tasks + 1):
            base = 0.9 - 0.07 * t                                            # задания 7..12 — трудные
            p = base + (0.30 if sc == "70003" and t >= 7 else 0)
            ok = rng.random() < p
            rows.append(dict(participant_id=pid, task_number=t, answer="ok" if ok else f"w{rng.integers(30)}",
                             is_correct=bool(ok), deleted=False))
    r = analyze_exam(s, pd.DataFrame(rows), subjects()).schools.set_index("school_code")
    assert r["p3"].idxmax() == "70003"


# --- прочее
def test_pending_results_not_used_t70():
    s, a = _base()
    s.loc[s.index[:30], "result_status"] = "pending"
    r = analyze_exam(s, a, subjects()).schools
    n = final_sessions(s).groupby("school_code").size()
    assert (r.set_index("school_code")["participants"] == n.reindex(r["school_code"]).to_numpy()).all()


def test_deterministic_two_runs_t57():
    s, a = _base()
    r1, r2 = analyze_exam(s, a, subjects()), analyze_exam(s, a, subjects())
    pd.testing.assert_frame_equal(r1.schools, r2.schools)
    pd.testing.assert_frame_equal(r1.groups, r2.groups)


def test_params_change_index_options():
    s, a = _base()
    mean_idx = analyze_exam(s, a, subjects()).schools["index"].max()
    max_idx = analyze_exam(s, a, subjects(), Params(index_agg="max")).schools["index"].max()
    assert max_idx >= mean_idx


def test_holm_known_values():
    assert np.allclose(holm_adjust(np.array([0.01, 0.04, 0.03])), [0.03, 0.06, 0.06])
