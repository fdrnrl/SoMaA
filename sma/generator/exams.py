"""Экзамены: параметры, шкалы перевода, КИМ и банк ответов (раздел 10.3).

Шкалы и коды кодификатора здесь условные, синтетические: для реальных данных их берут
из документов ФИПИ. Записи /dictionaries/scales строятся из тех же функций шкал, что
и баллы участников, поэтому данные и шкала всегда согласованы.
"""

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import yaml

EXAMS_CONFIG = Path(__file__).resolve().parents[2] / "config" / "generator" / "exams.yaml"


@dataclass(frozen=True)
class ExamConfig:
    """Параметры одного экзамена из config/generator/exams.yaml."""

    exam_type: str
    exam_year: int
    exam_date: date
    subject_code: str
    subject_name: str
    grade_level: int
    part1_tasks: int
    part2_max_scores: dict[int, int]
    max_primary_score: int
    duration_min: int
    start_spread_min: int
    has_rooms: bool
    has_proctoring: bool
    blocks: dict[str, list[int]] | None
    scale_type: str
    threshold_score: int
    answer_kind: str

    @property
    def part1_numbers(self) -> list[int]:
        """Номера заданий части 1: 1..part1_tasks."""
        return list(range(1, self.part1_tasks + 1))

    @property
    def part2_numbers(self) -> list[int]:
        """Номера заданий части 2 по возрастанию."""
        return sorted(self.part2_max_scores)


def load_exams(path: Path = EXAMS_CONFIG) -> dict[str, ExamConfig]:
    """Читает параметры экзаменов и проверяет, что максимальный балл сходится."""
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    exams = {}
    for exam_type, params in config["exams"].items():
        exam = ExamConfig(
            exam_type=exam_type,
            exam_year=config["exam_year"],
            exam_date=date.fromisoformat(config["exam_date"]),
            **params,
        )
        total = exam.part1_tasks + sum(exam.part2_max_scores.values())
        if total != exam.max_primary_score:
            raise ValueError(
                f"{exam_type}: сумма баллов заданий {total} ≠ max_primary_score "
                f"{exam.max_primary_score}"
            )
        exams[exam_type] = exam
    return exams


# --- Шкалы перевода (условные) ---------------------------------------------------------

# ЕГЭ: точки (первичный балл, тестовый балл); между точками — прямая линия.
EGE_SCALE_POINTS = [(0, 0), (6, 27), (12, 64), (20, 80), (32, 100)]

# ОГЭ, ВПР, НИКО: (первичный от, первичный до — включительно, результат).
OGE_GRADE_BOUNDS = [(0, 7, 2), (8, 14, 3), (15, 21, 4), (22, 31, 5)]
OGE_GEOMETRY_MIN = 2  # если по геометрии меньше — отметка 2 при любом первичном балле
VPR_GRADE_BOUNDS = [(0, 6, 2), (7, 11, 3), (12, 16, 4), (17, 20, 5)]
NIKO_LEVEL_BOUNDS = [(0, 4, "below_basic"), (5, 10, "basic"), (11, 14, "above_basic")]


def ege_test_score(primary: int) -> int:
    """Тестовый балл ЕГЭ (0–100) по первичному (0–32).

    Шкала кусочно-линейная по точкам EGE_SCALE_POINTS. Результат округляется до целого,
    половина — вверх: 4,5 → 5. Возвращает обычный int (не numpy).
    Первичный балл вне 0..32 → ValueError.
    """
    if not (0 <= primary <= 32):
        raise ValueError
    return int(
        np.floor(
            np.interp(
                primary,
                [point[0] for point in EGE_SCALE_POINTS],
                [point[1] for point in EGE_SCALE_POINTS],
            )
            + 0.5
        )
    )


def oge_grade(primary: int, geometry: int | None = None) -> int:
    """Отметка ОГЭ (2–5) по первичному баллу (0–31) и таблице OGE_GRADE_BOUNDS.

    Если geometry задан и меньше OGE_GEOMETRY_MIN — отметка 2 при любом первичном балле.
    Если geometry = None — условие по геометрии не проверяется.
    Первичный балл вне 0..31 → ValueError.
    """
    if not (0 <= primary <= 31):
        raise ValueError
    if geometry is not None and (geometry < OGE_GEOMETRY_MIN):
        return 2
    for bound in OGE_GRADE_BOUNDS:
        if bound[0] <= primary <= bound[1]:
            return bound[2]


def vpr_grade(primary: int) -> int:
    """Отметка ВПР (2–5) по первичному баллу (0–20) и таблице VPR_GRADE_BOUNDS.

    Первичный балл вне 0..20 → ValueError.
    """
    if not (0 <= primary <= 20):
        raise ValueError
    for bound in VPR_GRADE_BOUNDS:
        if bound[0] <= primary <= bound[1]:
            return bound[2]


def niko_level(primary: int) -> str:
    """Уровень НИКО (below_basic, basic, above_basic) по первичному баллу (0–14).

    Первичный балл вне 0..14 → ValueError.
    """
    if not (0 <= primary <= 14):
        raise ValueError
    for bound in NIKO_LEVEL_BOUNDS:
        if bound[0] <= primary <= bound[1]:
            return bound[2]


def primary_result(exam_type: str, primary: int) -> int | str:
    """Результат по шкале без условий по блокам: тестовый балл, отметка или уровень."""
    if exam_type == "EGE":
        return ege_test_score(primary)
    if exam_type == "OGE":
        return oge_grade(primary)
    if exam_type == "VPR":
        return vpr_grade(primary)
    if exam_type == "NIKO":
        return niko_level(primary)
    raise ValueError(f"неизвестный экзамен {exam_type}")


def scale_points(exam: ExamConfig) -> list[dict]:
    """Поле points записи /dictionaries/scales (контракт 6.11).

    primary_to_test — точка на каждый первичный балл 0..max; иначе — интервалы
    без пропусков и пересечений, покрывающие 0..max.
    """
    if exam.scale_type == "primary_to_test":
        return [
            {"primary": primary, "test": primary_result(exam.exam_type, primary)}
            for primary in range(exam.max_primary_score + 1)
        ]
    points = []
    for primary in range(exam.max_primary_score + 1):
        result = primary_result(exam.exam_type, primary)
        if points and points[-1]["result"] == result:
            points[-1]["max"] = primary
        else:
            points.append({"min": primary, "max": primary, "result": result})
    return points


def scale_conditions(exam: ExamConfig) -> list[dict]:
    """Поле conditions записи /dictionaries/scales: условия по блокам."""
    if exam.exam_type == "OGE":
        return [{"block": "geometry", "min_score": OGE_GEOMETRY_MIN, "result_if_not_met": 2}]
    return []


# --- КИМ -------------------------------------------------------------------------------


def task_block(exam: ExamConfig, task_number: int) -> str | None:
    """Блок задания (algebra, geometry) или None, если в предмете нет блоков."""
    if exam.blocks is None:
        return None
    for block, numbers in exam.blocks.items():
        if task_number in numbers:
            return block
    raise ValueError(f"{exam.exam_type}: задание {task_number} не входит ни в один блок")


def kim_rows(rng: np.random.Generator, exam: ExamConfig, variant_ids: list[str]) -> list[dict]:
    """Значения записей /dictionaries/kim (без служебных полей): задание × вариант.

    Коды разделов и умений синтетические; у заданий с одним номером в разных вариантах
    они одинаковые (параллельные варианты).
    """
    n_sections = int(rng.integers(4, 7))
    basic_count = round(0.6 * exam.part1_tasks)
    advanced_count = math.ceil(len(exam.part2_numbers) / 2)
    tasks = []
    for task_number in exam.part1_numbers + exam.part2_numbers:
        section = int(rng.integers(1, n_sections + 1))
        if task_number <= exam.part1_tasks:
            part, check_type, max_score = 1, "auto", 1
            level = "basic" if task_number <= basic_count else "advanced"
        else:
            part, check_type = 2, "expert"
            max_score = exam.part2_max_scores[task_number]
            position = exam.part2_numbers.index(task_number)
            level = "advanced" if position < advanced_count else "high"
        tasks.append(
            {
                "task_number": task_number,
                "part": part,
                "check_type": check_type,
                "max_score": max_score,
                "level": level,
                "content_codes": [f"{section}.{int(rng.integers(1, 6))}"],
                "skill_codes": [f"{int(rng.integers(1, 5))}.{int(rng.integers(1, 4))}"],
                "section": f"Раздел {section}",
                "block": task_block(exam, task_number),
            }
        )
    rows = []
    for variant_id in variant_ids:
        for task in tasks:
            rows.append(
                {
                    "exam_type": exam.exam_type,
                    "subject_code": exam.subject_code,
                    "exam_year": exam.exam_year,
                    "variant_id": variant_id,
                    **task,
                }
            )
    return rows


# --- Банк ответов части 1 -----------------------------------------------------------------

WORDS = [
    "дом", "река", "солнце", "пришел", "береза", "звезда", "книга", "окно", "дорога", "ветер",
    "город", "поле", "лес", "море", "гора", "школа", "учитель", "ученик", "задача", "ответ",
]  # fmt: skip

TYPICAL_WRONG_COUNT = 3
RARE_WRONG_COUNT = 6


@dataclass(frozen=True)
class TaskAnswers:
    """Ответы одного задания части 1 одного варианта. Все строки уже нормализованы.

    style — вид ответа: integer, decimal, word, digits, binary.
    """

    correct: str
    style: str
    typical_wrong: tuple[str, ...]
    rare_wrong: tuple[str, ...]


def _draw_answer(rng: np.random.Generator, style: str) -> str:
    """Одна случайная строка ответа заданного вида."""
    if style == "integer":
        return str(int(rng.integers(2, 1000)))
    if style == "decimal":
        tenths = int(rng.integers(11, 1000))
        if tenths % 10 == 0:
            tenths += 1
        return f"{tenths // 10},{tenths % 10}"
    if style == "word":
        return str(rng.choice(WORDS))
    if style == "digits":
        digits = rng.choice(list("123456789"), size=3, replace=False)
        return "".join(sorted(str(d) for d in digits))
    if style == "binary":
        return format(int(rng.integers(8, 256)), "b")
    raise ValueError(f"неизвестный вид ответа {style}")


def _task_answers(rng: np.random.Generator, style: str) -> TaskAnswers:
    """Верный ответ, 3 типичные ошибки и 6 редких — все разные."""
    needed = 1 + TYPICAL_WRONG_COUNT + RARE_WRONG_COUNT
    values: list[str] = []
    while len(values) < needed:
        value = _draw_answer(rng, style)
        if value not in values:
            values.append(value)
    return TaskAnswers(
        correct=values[0],
        style=style,
        typical_wrong=tuple(values[1 : 1 + TYPICAL_WRONG_COUNT]),
        rare_wrong=tuple(values[1 + TYPICAL_WRONG_COUNT :]),
    )


ANSWER_STYLES = {
    "number": ("integer", "decimal"),
    "word": ("word", "digits"),
    "binary": ("integer", "binary"),
}


def make_answer_bank(
    rng: np.random.Generator, exam: ExamConfig, variant_ids: list[str]
) -> dict[tuple[str, int], TaskAnswers]:
    """Банк ответов части 1: (вариант, номер задания) → TaskAnswers.

    Вид ответа у задания одинаков во всех вариантах, сами ответы — разные.
    """
    styles = {task: str(rng.choice(ANSWER_STYLES[exam.answer_kind])) for task in exam.part1_numbers}
    bank = {}
    for variant_id in variant_ids:
        for task in exam.part1_numbers:
            bank[(variant_id, task)] = _task_answers(rng, styles[task])
    return bank
