"""Модель ответов учеников: модель Раша (IRT 1PL) и выбор строки ответа (раздел 10.5).

Все функции получают генератор случайных чисел rng (numpy.random.Generator) аргументом:
одинаковый seed → одинаковые данные. Создавать свой генератор внутри функций нельзя.
"""

import numpy as np

from sma.generator.exams import TaskAnswers

SCHOOL_EFFECT_SD = 0.45  # разброс эффекта школы
DIFFICULTY_MIN = -1.8  # трудность первого задания части 1
DIFFICULTY_MAX = 1.8  # трудность последнего задания части 1
DIFFICULTY_NOISE_SD = 0.25
EMPTY_ANSWER_SLOPE = 0.12  # прибавка к вероятности пустого ответа на трудных заданиях
TYPICAL_WRONG_PROBS = (0.5, 0.3, 0.15)  # три типичные ошибки; оставшиеся 0,05 — редкая
LEADING_SPACE_RATE = 0.06  # верный ответ с пробелом в начале
VARIATION_RATE = 0.06  # точка вместо запятой (decimal) или заглавная буква (word)


def p_correct(theta: np.ndarray, difficulty: np.ndarray) -> np.ndarray:
    """Вероятность верного ответа по модели Раша: P = 1 / (1 + exp(−(θ − b))).

    theta — уровни учеников, форма (n,); difficulty — трудности заданий, форма (t,).
    Результат — матрица (n, t): строка — ученик, столбец — задание.
    """
    # theta[:, None] — столбец (n, 1), difficulty[None, :] — строка (1, t);
    # их разность — сразу вся таблица (n, t), без циклов (broadcasting).
    return 1 / (1 + np.exp(-(theta[:, None] - difficulty[None, :])))


def abilities(rng: np.random.Generator, school_index: np.ndarray, n_schools: int) -> np.ndarray:
    """Уровень каждого ученика: θ = эффект его школы + N(0, 1).

    Эффекты школ — n_schools чисел из N(0, SCHOOL_EFFECT_SD). school_index[i] — номер школы
    ученика i. Результат — форма (len(school_index),).
    """
    school_effects = rng.normal(0, SCHOOL_EFFECT_SD, n_schools)
    # Индексация массивом номеров «раздает» каждому ученику эффект его школы.
    return school_effects[school_index] + rng.normal(0, 1, len(school_index))


def part1_difficulties(rng: np.random.Generator, n_tasks: int) -> np.ndarray:
    """Трудности заданий части 1: равномерно от DIFFICULTY_MIN до DIFFICULTY_MAX по номеру
    задания плюс шум N(0, DIFFICULTY_NOISE_SD). Форма (n_tasks,).
    """
    trend = np.linspace(DIFFICULTY_MIN, DIFFICULTY_MAX, n_tasks)
    return trend + rng.normal(0, DIFFICULTY_NOISE_SD, n_tasks)


def expert_scores(
    rng: np.random.Generator,
    theta: np.ndarray,
    max_scores: np.ndarray,
    difficulty: np.ndarray,
) -> np.ndarray:
    """Баллы за задания части 2: Binomial(max_score, P), P — по модели Раша.

    theta — форма (n,); max_scores и difficulty — форма (k,). Результат — целые, форма (n, k),
    каждый балл от 0 до max_score своего задания.
    """
    probabilities = p_correct(theta, difficulty)
    return rng.binomial(max_scores[None, :], probabilities)


def task_times(rng: np.random.Generator, n_tasks: int, total_sec: float) -> np.ndarray:
    """Время на каждое из n_tasks заданий в секундах: доли из распределения Дирихле
    (все параметры = 1), умноженные на total_sec. Сумма — total_sec. Форма (n_tasks,).
    """
    shares = rng.dirichlet(np.ones(n_tasks))
    return shares * total_sec


def wrong_answer(
    rng: np.random.Generator, difficulty: float, task: TaskAnswers, empty_base: float = 0.04
) -> str:
    """Строка неверного ответа.

    С вероятностью empty_base + EMPTY_ANSWER_SLOPE × (difficulty + 2) / 4 — пустая строка "".
    Иначе: одна из трех типичных ошибок task.typical_wrong с вероятностями
    TYPICAL_WRONG_PROBS (0,5 / 0,3 / 0,15), а в остальных 0,05 — случайная редкая
    из task.rare_wrong.
    """
    empty_probability = empty_base + EMPTY_ANSWER_SLOPE * (difficulty + 2) / 4
    if rng.random() < empty_probability:
        return ""
    u = rng.random()
    threshold = 0.0
    for answer, probability in zip(task.typical_wrong, TYPICAL_WRONG_PROBS, strict=True):
        threshold += probability
        if u < threshold:
            return answer
    return str(rng.choice(task.rare_wrong))


def typed_correct(rng: np.random.Generator, task: TaskAnswers) -> str:
    """Верный ответ так, как его мог напечатать ученик.

    1. Для task.style == "decimal" с вероятностью VARIATION_RATE запятая заменяется точкой
       ("0,25" → "0.25"); для "word" — первая буква заглавная ("река" → "Река").
    2. Затем с вероятностью LEADING_SPACE_RATE в начало добавляется пробел.
    После normalize_answer результат всегда равен task.correct.
    """
    text = task.correct
    if task.style == "decimal" and rng.random() < VARIATION_RATE:
        text = text.replace(",", ".")
    elif task.style == "word" and rng.random() < VARIATION_RATE:
        text = text[0].upper() + text[1:]
    if rng.random() < LEADING_SPACE_RATE:
        text = " " + text
    return text
