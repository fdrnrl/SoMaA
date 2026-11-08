"""Модель ответов: модель Раша, уровни, трудности, баллы, время, строки ответов (10.5)."""

import math

import numpy as np
import pytest

from sma.generator import model
from sma.generator.exams import TaskAnswers
from sma.normalize import normalize_answer

INTEGER_TASK = TaskAnswers(
    "12", "integer", ("13", "11", "21"), ("101", "205", "333", "4", "5", "6")
)
DECIMAL_TASK = TaskAnswers("0,25", "decimal", ("0,5", "2,5", "0,2"), ("1,1", "7,3", "9,9"))
WORD_TASK = TaskAnswers("река", "word", ("море", "поле", "лес"), ("дом", "окно", "гора"))


def rng(seed=1):
    return np.random.default_rng(seed)


def test_p_correct_half_when_theta_equals_difficulty():
    p = model.p_correct(np.array([0.7]), np.array([0.7]))
    assert p.shape == (1, 1)
    assert p[0, 0] == pytest.approx(0.5)


def test_p_correct_formula():
    p = model.p_correct(np.array([1.0]), np.array([0.0]))
    assert p[0, 0] == pytest.approx(1 / (1 + math.exp(-1)))


def test_p_correct_shape_and_direction():
    theta = np.array([-1.0, 0.0, 1.0])
    difficulty = np.array([-1.0, 0.0, 1.0, 2.0])
    p = model.p_correct(theta, difficulty)
    assert p.shape == (3, 4)
    assert np.all((p > 0) & (p < 1))
    assert np.all(np.diff(p, axis=0) > 0), "сильнее ученик — выше вероятность"
    assert np.all(np.diff(p, axis=1) < 0), "труднее задание — ниже вероятность"


def test_abilities_shape_and_spread():
    school_index = np.repeat(np.arange(400), 50)
    theta = model.abilities(rng(), school_index, 400)
    assert theta.shape == (20000,)
    assert theta.std() == pytest.approx(math.sqrt(1 + 0.45**2), abs=0.05)
    school_means = theta.reshape(400, 50).mean(axis=1)
    assert school_means.std() == pytest.approx(math.sqrt(0.45**2 + 1 / 50), abs=0.06)


def test_abilities_deterministic():
    index = np.array([0, 0, 1, 2])
    assert np.array_equal(model.abilities(rng(3), index, 3), model.abilities(rng(3), index, 3))


def test_part1_difficulties():
    b = model.part1_difficulties(rng(), 12)
    assert b.shape == (12,)
    assert np.corrcoef(b, np.linspace(-1.8, 1.8, 12))[0, 1] > 0.9
    assert abs(b.mean()) < 0.3


def test_expert_scores_range_and_direction():
    theta = np.concatenate([np.full(500, -2.0), np.full(500, 2.0)])
    max_scores = np.array([2, 3, 4])
    scores = model.expert_scores(rng(), theta, max_scores, np.array([0.3, 0.8, 1.3]))
    assert scores.shape == (1000, 3)
    assert np.issubdtype(scores.dtype, np.integer)
    assert np.all(scores >= 0)
    assert np.all(scores <= max_scores)
    assert scores[500:].mean() > scores[:500].mean()


def test_task_times():
    times = model.task_times(rng(), 12, 3600.0)
    assert times.shape == (12,)
    assert np.all(times > 0)
    assert times.sum() == pytest.approx(3600.0)


def test_wrong_answer_is_never_correct():
    allowed = {""} | set(INTEGER_TASK.typical_wrong) | set(INTEGER_TASK.rare_wrong)
    generator = rng()
    answers = [model.wrong_answer(generator, 0.0, INTEGER_TASK) for _ in range(3000)]
    assert INTEGER_TASK.correct not in answers
    assert set(answers) <= allowed


def test_wrong_answer_typical_shares():
    generator = rng()
    answers = [
        model.wrong_answer(generator, 0.0, INTEGER_TASK, empty_base=0.0) for _ in range(6000)
    ]
    answers = [answer for answer in answers if answer != ""]
    shares = [answers.count(value) / len(answers) for value in INTEGER_TASK.typical_wrong]
    assert shares[0] == pytest.approx(0.5, abs=0.03)
    assert shares[1] == pytest.approx(0.3, abs=0.03)
    assert shares[2] == pytest.approx(0.15, abs=0.03)


@pytest.mark.parametrize("difficulty, expected", [(-2.0, 0.04), (2.0, 0.16)])
def test_wrong_answer_empty_rate(difficulty, expected):
    generator = rng()
    answers = [model.wrong_answer(generator, difficulty, INTEGER_TASK) for _ in range(6000)]
    assert answers.count("") / len(answers) == pytest.approx(expected, abs=0.015)


@pytest.mark.parametrize("task", [INTEGER_TASK, DECIMAL_TASK, WORD_TASK])
def test_typed_correct_normalizes_to_correct(task):
    generator = rng()
    for _ in range(2000):
        assert normalize_answer(model.typed_correct(generator, task)) == task.correct


def test_typed_correct_variations():
    generator = rng()
    decimals = [model.typed_correct(generator, DECIMAL_TASK) for _ in range(3000)]
    words = [model.typed_correct(generator, WORD_TASK) for _ in range(3000)]
    integers = [model.typed_correct(generator, INTEGER_TASK) for _ in range(3000)]
    assert sum(text.startswith(" ") for text in decimals) / 3000 == pytest.approx(0.06, abs=0.015)
    assert sum("." in text for text in decimals) / 3000 == pytest.approx(0.06, abs=0.015)
    assert sum(text.strip()[0].isupper() for text in words) / 3000 == pytest.approx(0.06, abs=0.015)
    assert all(text.strip() == "12" for text in integers), "у целых чисел только пробел"
