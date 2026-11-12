"""Property-based тесты (NFR-21): инварианты проверяются на многих случайных входах.

Здесь они написаны на numpy со случайными зернами (запускаются без внешних зависимостей).
При наличии Hypothesis те же инварианты можно оформить через @given — набор свойств не изменится.
"""
import numpy as np
import pandas as pd

from sma_anomalies import Params, merge_new, rule_a1
from sma_anomalies.deep import compute_p2, holm_adjust, robust_z
from sma_anomalies.normalize import normalize_answer
from sma_anomalies.prep import final_sessions, with_norm_answers
from tests.synth import make_answers, make_sessions

N = 60


def test_prop_holm_monotone_bounded_and_not_smaller():
    for seed in range(N):
        p = np.random.default_rng(seed).random(np.random.default_rng(seed).integers(1, 40))
        adj = holm_adjust(p)
        assert ((adj >= p - 1e-12) & (adj <= 1.0)).all()
        order = np.argsort(p)
        assert (np.diff(adj[order]) >= -1e-12).all()


def test_prop_robust_z_shift_scale_invariant():
    for seed in range(N):
        x = pd.Series(np.random.default_rng(seed).normal(size=25))
        z = robust_z(x)
        assert np.allclose(robust_z(x * 7.5 + 3.0), z)
        assert abs(z.median()) < 1e-9


def test_prop_normalize_idempotent_and_blank_stable():
    alphabet = list("0123456789., aAбБ-")
    for seed in range(N):
        rng = np.random.default_rng(seed)
        text = "".join(rng.choice(alphabet, rng.integers(0, 10)))
        n1 = normalize_answer(text)
        assert normalize_answer(n1) == n1 and " " not in n1 and n1 == n1.lower()


def test_prop_g_star_in_unit_interval():
    for seed in range(20):
        s = make_sessions({"77001": 30})
        a = make_answers(s, n_tasks=9, seed=seed, p_correct=np.random.default_rng(seed).uniform(0.1, 0.9))
        g = compute_p2(final_sessions(s), with_norm_answers(a))
        assert g["g_star"].between(0, 1).all() and g["G"].ge(0).all()


def test_prop_a1_never_fires_with_fewer_than_three_big_schools():
    for seed in range(N):
        rng = np.random.default_rng(seed)
        s = make_sessions({"77001": list(rng.integers(0, 40, 10)), "77002": list(rng.integers(0, 40, 12))})
        assert rule_a1(s).empty


def test_prop_a1_all_sigma_bound():
    """С σ по всем школам (по выборке, ddof = 1) z школы не может превышать (n − 1)/√n (раздел 10 ТЗ)."""
    for seed in range(N):
        rng = np.random.default_rng(seed)
        k = int(rng.integers(3, 12))
        sch = {f"7{i:04d}": list(rng.integers(0, 40, 10)) for i in range(k)}
        sch["79999"] = [int(rng.integers(40, 200))] * 10
        r = rule_a1(make_sessions(sch))
        if len(r):
            n = k + 1
            assert r["z"].max() <= (n - 1) / n ** 0.5 + 1e-6


def test_prop_merge_new_idempotent_and_ids_unique():
    for seed in range(N):
        rng = np.random.default_rng(seed)
        found = pd.DataFrame({"rule": rng.choice(["A1", "A3"], 8), "exam_type": "OGE", "object_type": "x",
                              "object_id": rng.choice(list("abcde"), 8), "value": 1, "threshold": 1})
        stored, _ = merge_new(None, found)
        again, new = merge_new(stored, found)
        assert len(again) == len(stored) and new.empty and stored["anomaly_id"].is_unique
