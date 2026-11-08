"""Шкалы перевода баллов (раздел 10.3) и записи /dictionaries/scales."""

import pytest

from sma.generator.exams import (
    ege_test_score,
    load_exams,
    niko_level,
    oge_grade,
    scale_points,
    vpr_grade,
)


@pytest.mark.parametrize(
    "primary, expected",
    [(0, 0), (6, 27), (12, 64), (20, 80), (32, 100), (1, 5), (3, 14), (7, 33), (26, 90)],
)
def test_ege_test_score(primary, expected):
    assert ege_test_score(primary) == expected


def test_ege_monotonic_int_in_range():
    values = [ege_test_score(primary) for primary in range(33)]
    assert all(type(value) is int for value in values), "нужен обычный int, не numpy"
    assert values == sorted(values)
    assert min(values) == 0 and max(values) == 100


@pytest.mark.parametrize(
    "primary, expected",
    [(0, 2), (7, 2), (8, 3), (14, 3), (15, 4), (21, 4), (22, 5), (31, 5)],
)
def test_oge_grade(primary, expected):
    assert oge_grade(primary) == expected


def test_oge_geometry_condition():
    assert oge_grade(25, geometry=1) == 2
    assert oge_grade(25, geometry=0) == 2
    assert oge_grade(25, geometry=2) == 5
    assert oge_grade(25, geometry=None) == 5


@pytest.mark.parametrize(
    "primary, expected",
    [(0, 2), (6, 2), (7, 3), (11, 3), (12, 4), (16, 4), (17, 5), (20, 5)],
)
def test_vpr_grade(primary, expected):
    assert vpr_grade(primary) == expected


@pytest.mark.parametrize(
    "primary, expected",
    [
        (0, "below_basic"),
        (4, "below_basic"),
        (5, "basic"),
        (10, "basic"),
        (11, "above_basic"),
        (14, "above_basic"),
    ],
)
def test_niko_level(primary, expected):
    assert niko_level(primary) == expected


@pytest.mark.parametrize(
    "function, bad",
    [
        (ege_test_score, -1),
        (ege_test_score, 33),
        (oge_grade, -1),
        (oge_grade, 32),
        (vpr_grade, -1),
        (vpr_grade, 21),
        (niko_level, -1),
        (niko_level, 15),
    ],
)
def test_out_of_range_raises(function, bad):
    with pytest.raises(ValueError):
        function(bad)


@pytest.mark.parametrize("exam_type", ["EGE", "OGE", "VPR", "NIKO"])
def test_scales_build(exam_type):
    """Шкала покрывает 0..max без пропусков и пересечений (Q-CONS-10)."""
    exam = load_exams()[exam_type]
    points = scale_points(exam)
    if exam.scale_type == "primary_to_test":
        assert [point["primary"] for point in points] == list(range(exam.max_primary_score + 1))
        return
    assert points[0]["min"] == 0
    assert points[-1]["max"] == exam.max_primary_score
    for previous, current in zip(points, points[1:], strict=False):
        assert current["min"] == previous["max"] + 1


def test_exam_max_scores_add_up():
    for exam in load_exams().values():
        assert exam.part1_tasks + sum(exam.part2_max_scores.values()) == exam.max_primary_score
