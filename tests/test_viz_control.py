"""Сверка чисел дашборда с независимым расчетом на pandas (T-15, T-16, T-68).

Источник дашборда — DemoData (то, что страницы показывают), источник контроля —
visualization.calc, который читает те же сырые файлы своим кодом.
Допуски: средние баллы 0,01; проценты и доли — 0,1 п. п. (здесь строже: по округлению витрины).
"""

from pathlib import Path

import pytest

from dashboard.demo import DemoData
from visualization import calc, data

FIXTURE = Path(__file__).parents[1] / "dashboard" / "fixtures" / "small.zip"
pytestmark = pytest.mark.skipif(not FIXTURE.exists(), reason="нет tests/dashboard/fixtures")


@pytest.fixture(scope="module")
def shown():
    return DemoData(FIXTURE)


@pytest.fixture(scope="module")
def tables():
    loaded = data.load_set(str(FIXTURE))
    loaded["region_results"] = calc.region_results(loaded["sessions"], loaded["regions"])
    loaded["scores"] = calc.part_scores(loaded["sessions"], loaded["answers"], loaded["reviews"])
    return loaded


def test_region_results_match(shown, tables):
    control = tables["region_results"].set_index(["exam_type", "subject_code", "region_code"])
    rows = shown.region_results()
    assert len(rows) == len(control)
    for row in rows:
        ref = control.loc[(row["exam_type"], row["subject_code"], row["region_code"])]
        assert row["participants"] == ref.n
        assert row["schools"] == ref.schools
        assert abs(row["mean_score"] - ref.mean_score) <= 0.01
        assert abs(row["mean_pct"] - ref.pct_exec) <= 0.01
        assert abs(row["share_absent"] - ref.share_absent / 100) <= 0.0001
        assert abs(row["share_annulled"] - ref.share_annulled / 100) <= 0.0001
        if row["share_grade2"] is not None and ref.n_graded:
            assert abs(row["share_grade2"] - ref.share_grade2 / 100) <= 0.0001


def test_every_filter_combination_of_distribution_matches(shown, tables):
    final = calc.final_sessions(tables["sessions"])
    combos = final[["exam_type", "subject_code", "region_code"]].drop_duplicates()
    for exam, subject, region in combos.itertuples(index=False):
        for region_filter in (None, region):
            hist = calc.score_histogram(tables["sessions"], exam, subject, region_filter)
            expected = {int(s): int(c) for s, c in zip(hist.score, hist["count"], strict=True) if c}
            rows = shown.distribution(exam, subject, region_filter)
            assert {r["primary_score"]: r["participants"] for r in rows} == expected


def test_topics_match(shown, tables):
    for exam, subject in tables["region_results"][["exam_type", "subject_code"]].drop_duplicates(
    ).itertuples(index=False):
        control = calc.topic_stats(tables["scores"], tables["kim"], exam, subject).set_index(
            ["section", "content_code", "level"])
        rows = shown.topics(exam, subject)
        for row in rows:
            ref = control.loc[(row["section"], row["content_code"], row["level"])]
            assert row["tasks"] == ref.tasks
            assert row["n_answers"] == ref.n_answers
            if row["mean_share_score"] is not None:
                assert abs(row["mean_share_score"] - ref.share_pct / 100) <= 0.0001


def test_variants_match(shown, tables):
    for exam, subject in tables["region_results"][["exam_type", "subject_code"]].drop_duplicates(
    ).itertuples(index=False):
        control = calc.variant_comparison(tables["scores"], exam, subject).set_index(
            ["task_number", "variant_id"])
        for row in shown.variants(exam, subject):
            ref = control.loc[(row["task_number"], row["variant_id"])]
            assert row["participants"] == ref.n
            assert abs(row["share_score"] - ref.share_pct / 100) <= 0.0001
            if row["diff_from_others"] is not None:
                assert abs(row["diff_from_others"] - ref.diff_to_others_pp / 100) <= 0.0002
