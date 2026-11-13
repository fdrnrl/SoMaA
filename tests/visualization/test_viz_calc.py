"""Модульные тесты контрольного расчета на маленьких таблицах (без файлов и базы)."""

import numpy as np
import pandas as pd
from viz_helpers import frame, sess

from visualization import calc


def test_deleted_pending_annulled_absent_excluded():
    rows = [sess("a", 10), sess("b", 20, deleted=True), sess("c", 30, rs="pending"),
            sess("d", None, status="annulled"), sess("e", None, status="absent")]
    final = calc.final_sessions(frame(rows))
    assert list(final.participant_id) == ["a"]


def test_latest_version_wins_and_deleted_version_removes_record():
    a1 = sess("a", 10, upd="2026-12-05T10:00:00+03:00")
    a2 = sess("a", 12, upd="2026-12-05T12:00:00+03:00")
    b1 = sess("b", 9, upd="2026-12-05T10:00:00+03:00")
    b2 = dict(sess("b", 9, upd="2026-12-05T11:00:00+03:00"), deleted=True)
    for row in (a1, a2):
        row["record_id"] = "ra"  # версии одной записи платформы
    for row in (b1, b2):
        row["record_id"] = "rb"  # последняя версия удалена: запись исчезает целиком
    final = calc.final_sessions(frame([a1, a2, b1, b2]))
    assert dict(zip(final.participant_id, final.primary_score, strict=True)) == {"a": 12}


def test_timezones_compared_in_utc():
    # 12:00+07:00 = 05:00 UTC — раньше, чем 10:00+03:00 = 07:00 UTC
    first = sess("a", 5, upd="2026-12-05T12:00:00+07:00")
    second = sess("a", 7, upd="2026-12-05T10:00:00+03:00")
    first["record_id"], second["record_id"] = "x1", "x2"
    final = calc.final_sessions(frame([first, second]))
    assert final.primary_score.iloc[0] == 7


def test_zero_score_counts_but_null_does_not():
    result = calc.region_results(frame([sess("a", 0), sess("b", 10), sess("c", None)]))
    assert result.n.iloc[0] == 2
    assert result.mean_score.iloc[0] == 5.0


def test_pct_exec():
    result = calc.region_results(frame([sess("a", 31, mx=31), sess("b", 0, mx=31)]))
    assert abs(result.pct_exec.iloc[0] - 50.0) < 1e-9


def test_grade2_share_uses_platform_grade_not_recomputed():
    # балл 20 «по шкале» дал бы 4, но платформа прислала 2 (условие геометрии) — берем 2
    rows = [sess("a", 20, grade=2), sess("b", 20, grade=4), sess("c", 3, grade=2),
            sess("d", 25, grade=5)]
    assert calc.region_results(frame(rows)).share_grade2.iloc[0] == 50.0


def test_grade_missing_gives_nan_not_zero():
    result = calc.region_results(frame([sess("a", 10), sess("b", 12)]))
    assert np.isnan(result.share_grade2.iloc[0])


def test_histogram_has_all_bins_and_counts_sum():
    rows = [sess("a", 0), sess("b", 2), sess("c", 2), sess("d", 31)]
    hist = calc.score_histogram(frame(rows), "OGE", "02")
    assert list(hist.score) == list(range(32))
    assert hist["count"].sum() == 4
    assert hist.set_index("score")["count"][2] == 2
    assert hist.set_index("score")["count"][1] == 0


def test_histogram_region_filter_and_empty():
    rows = frame([sess("a", 5, region="16"), sess("b", 6, region="54")])
    assert calc.score_histogram(rows, "OGE", "02", "54")["count"].sum() == 1
    assert calc.score_histogram(rows, "OGE", "02", "99")["count"].sum() == 0


def _scores(rows):
    result = pd.DataFrame(rows, columns=["exam_type", "subject_code", "task_number",
                                         "variant_id", "score", "max_score"])
    result["share"] = result["score"] / result["max_score"]
    return result


def test_variant_flag_is_strictly_greater_than_15():
    rows = []
    for task, (first, second) in {1: (60, 45), 2: (60, 44)}.items():
        rows += [("OGE", "02", task, "v1", 1, 1)] * first
        rows += [("OGE", "02", task, "v1", 0, 1)] * (100 - first)
        rows += [("OGE", "02", task, "v2", 1, 1)] * second
        rows += [("OGE", "02", task, "v2", 0, 1)] * (100 - second)
    result = calc.variant_comparison(_scores(rows), "OGE", "02")
    assert not result[result.task_number == 1].flagged.any()
    assert result[result.task_number == 2].flagged.all()


def test_variant_min_n_drops_small_variant_from_spread():
    rows = [("OGE", "02", 1, "v1", 1, 1)] * 50 + [("OGE", "02", 1, "v2", 0, 1)] * 3
    assert calc.variant_comparison(_scores(rows), "OGE", "02").flagged.any()
    assert not calc.variant_comparison(_scores(rows), "OGE", "02", min_n=10).flagged.any()


def test_unchecked_part2_not_counted_but_checked_zero_is():
    sessions = frame([sess("a", 3)])
    stamp = "2026-12-05T10:00:00+03:00"
    answers = frame([{"record_id": "1", "exam_type": "OGE", "participant_id": "a",
                      "task_number": 1, "score": 1, "max_score": 1, "updated_at": stamp,
                      "deleted": False}])
    reviews = frame([
        {"record_id": "2", "exam_type": "OGE", "participant_id": "a", "task_number": 23,
         "score": None, "max_score": 2, "check_status": "pending", "updated_at": stamp,
         "deleted": False},
        {"record_id": "3", "exam_type": "OGE", "participant_id": "a", "task_number": 24,
         "score": 0, "max_score": 2, "check_status": "checked", "updated_at": stamp,
         "deleted": False},
    ])
    scores = calc.part_scores(sessions, answers, reviews)
    assert sorted(scores.task_number) == [1, 24]


def test_property_histogram_invariants_random():
    """Сумма столбцов = числу участников; среднее по гистограмме = среднему по данным."""
    rng = np.random.default_rng(7)
    for _ in range(50):
        count = int(rng.integers(1, 80))
        values = rng.integers(0, 32, count)
        rows = [sess(f"p{i}", int(v)) for i, v in enumerate(values)]
        hist = calc.score_histogram(frame(rows), "OGE", "02")
        assert hist["count"].sum() == count
        assert abs((hist.score * hist["count"]).sum() / count - values.mean()) < 1e-9
