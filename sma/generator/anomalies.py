"""Закладка аномалий и ловушек (раздел 10.7) и самопроверка набора (раздел 10.9).

План закладок составляется до моделирования: кто из участников и какие школы получат
закладку. Затем функции apply_* меняют модель (уровни, вероятности, верность ответов,
строки ответов). Закладки не пересекаются: у каждой — своя школа; заложенные ученики
не бывают absent или annulled.
"""

from dataclasses import dataclass, field, replace

import numpy as np

from sma.generator.exams import ExamConfig, TaskAnswers
from sma.generator.population import Participant
from sma.reference import rules

A1_BOOST = 1.9  # начальная прибавка к уровню учеников школы А1 (раздел 10.7)
A1_BOOST_STEP = 0.05  # с каждой неудачной попыткой прибавка растет
A1_BOOST_MAX = 2.4  # больше — у школы почти все ответы верные, и срабатывает А2
P1_TASKS = 6  # П1: одинаковые редкие ответы в 6 заданиях (верхняя граница «5–6»)
BIG_SCHOOL = 10  # школа для А1, П3, П4 — не меньше 10 учеников
A1_REGION_SCHOOLS = 8  # регион А1 — не меньше 8 таких школ (иначе z не превысит 2)
A2_GROUP = 5
P3_BONUS = 0.30  # П3: +30 п. п. к вероятности верного на трудных заданиях
P2_SHARE = 0.4  # П2: неверно на легких 40%, верно на трудных 40%
P4B_SHARE = 0.8


class PlantingError(ValueError):
    """Закладку нельзя сделать в этом населении (мало школ или учеников)."""


@dataclass
class Plan:
    """Кто и что получает. Индексы — номера участников в списке participants."""

    participants: list[Participant]
    protected: set[int] = field(default_factory=set)
    boost: dict[int, float] = field(default_factory=dict)
    a1_schools: list[str] = field(default_factory=list)
    a2_groups: list[list[int]] = field(default_factory=list)
    a2_sources: list[int] = field(default_factory=list)
    a2_blank_groups: list[list[int]] = field(default_factory=list)
    a3_counts: dict[int, int] = field(default_factory=dict)
    focus_decoys: list[int] = field(default_factory=list)
    p1_groups: list[tuple[list[int], list[int]]] = field(default_factory=list)
    p2: list[int] = field(default_factory=list)
    p3_schools: list[str] = field(default_factory=list)
    p4a_schools: dict[str, int] = field(default_factory=dict)  # школа → целевой балл
    p4b_schools: list[str] = field(default_factory=list)
    a1_boost: float = A1_BOOST

    def members(self, school_code: str) -> list[int]:
        return [i for i, p in enumerate(self.participants) if p.school_code == school_code]

    @property
    def is_empty(self) -> bool:
        return not (
            self.a1_schools or self.a2_groups or self.a2_blank_groups or self.a3_counts
            or self.focus_decoys or self.p1_groups or self.p2 or self.p3_schools
            or self.p4a_schools or self.p4b_schools
        )  # fmt: skip


# --- План ----------------------------------------------------------------------------------


def plan_anomalies(
    rng: np.random.Generator,
    exam: ExamConfig,
    participants: list[Participant],
    anomalies: dict,
    decoys: dict,
    a1_boost: float = 1.9,
) -> Plan:
    """Выбирает школы и участников для закладок. Если закладку сделать нельзя — PlantingError.

    a1_boost — прибавка к уровню учеников школы А1 (генератор увеличивает ее с попытками).
    """
    plan = Plan(list(participants))
    schools: dict[str, list[int]] = {}
    for index, participant in enumerate(participants):
        schools.setdefault(participant.school_code, []).append(index)
    region_of = {p.school_code: p.region_code for p in participants}
    big = {code for code, members in schools.items() if len(members) >= BIG_SCHOOL}
    big_per_region: dict[str, int] = {}
    for code in big:
        big_per_region[region_of[code]] = big_per_region.get(region_of[code], 0) + 1
    used: set[str] = set()

    def pick_school(what: str, allowed) -> str:
        candidates = sorted(code for code in schools if code not in used and allowed(code))
        if not candidates:
            raise PlantingError(f"{exam.exam_type}: нет подходящей школы для {what}")
        code = candidates[int(rng.integers(len(candidates)))]
        used.add(code)
        return code

    def protect_school(code: str) -> list[int]:
        plan.protected.update(schools[code])
        return schools[code]

    def same_variant(members: list[int]) -> None:
        variant = plan.participants[members[0]].variant_id
        for index in members:
            plan.participants[index] = replace(plan.participants[index], variant_id=variant)

    def pick_people(code: str, size: int) -> list[int]:
        chosen = sorted(int(i) for i in rng.choice(schools[code], size=size, replace=False))
        plan.protected.update(chosen)
        return chosen

    for _ in range(anomalies.get("A1", 0)):
        code = pick_school(
            "A1", lambda c: c in big and big_per_region[region_of[c]] >= A1_REGION_SCHOOLS
        )
        plan.a1_schools.append(code)
        plan.a1_boost = a1_boost
        for index in protect_school(code):
            plan.boost[index] = a1_boost
        # Все большие школы региона остаются с ≥ 10 участниками: иначе они выпадают из А1,
        # и при n школах z не может превысить (n − 1) / √n (при 6 школах — 2,04).
        for other in big:
            if region_of[other] == region_of[code]:
                plan.protected.update(schools[other])
    for _ in range(anomalies.get("P3", 0)):
        code = pick_school("P3", lambda c: c in big)
        plan.p3_schools.append(code)
        protect_school(code)
    for _ in range(anomalies.get("P4a", 0)):
        code = pick_school("P4a", lambda c: c in big)
        target = round(exam.max_primary_score * 0.5) + int(rng.integers(-2, 3))
        window = range(exam.threshold_score, exam.threshold_score + 3)
        if target in window:  # не попадать в окно П4б, иначе школа П4а «станет» и П4б
            target = exam.threshold_score + 3
        plan.p4a_schools[code] = target
        protect_school(code)
    for _ in range(anomalies.get("P4b", 0)):
        code = pick_school("P4b", lambda c: c in big)
        plan.p4b_schools.append(code)
        protect_school(code)

    for _ in range(anomalies.get("A2", 0)):
        code = pick_school("A2", lambda c: len(schools[c]) >= A2_GROUP)
        group = pick_people(code, A2_GROUP)
        same_variant(group)
        plan.a2_groups.append(group)
    for _ in range(decoys.get("A2_blank", 0)):
        code = pick_school("A2_blank", lambda c: len(schools[c]) >= A2_GROUP)
        group = pick_people(code, A2_GROUP)
        same_variant(group)
        plan.a2_blank_groups.append(group)

    for _ in range(anomalies.get("P1", 0)):
        size = int(rng.integers(3, 5))
        rooms: dict[tuple[str, str | None], list[int]] = {}
        for index, p in enumerate(participants):
            rooms.setdefault((p.school_code, p.room_code), []).append(index)
        fitting = {code for (code, _), members in rooms.items() if len(members) >= size}
        code = pick_school("P1", lambda c, fitting=fitting: c in fitting)
        room = max((members for (c, _), members in rooms.items() if c == code), key=len)
        group = sorted(int(i) for i in rng.choice(room, size=size, replace=False))
        plan.protected.update(group)
        same_variant(group)
        n_tasks = P1_TASKS
        tasks = sorted(int(t) for t in rng.choice(exam.part1_numbers, size=n_tasks, replace=False))
        plan.p1_groups.append((group, tasks))

    for _ in range(anomalies.get("P2", 0)):
        plan.p2 += pick_people(pick_school("P2", lambda c: True), 1)
    if exam.has_proctoring:
        for _ in range(anomalies.get("A3", 0)):
            (index,) = pick_people(pick_school("A3", lambda c: True), 1)
            plan.a3_counts[index] = int(rng.integers(3, 6))
        for _ in range(decoys.get("A3_focus", 0)):
            plan.focus_decoys += pick_people(pick_school("A3_focus", lambda c: True), 1)
    return plan


# --- Изменения модели --------------------------------------------------------------------


def apply_boost(theta: np.ndarray, plan: Plan) -> None:
    """А1: прибавка к уровню учеников школы."""
    for index, boost in plan.boost.items():
        theta[index] += boost


def apply_p3(p: np.ndarray, difficulty: np.ndarray, plan: Plan) -> None:
    """П3: на трудных заданиях (трудность выше медианы) +30 п. п. к вероятности верного."""
    hard = difficulty > np.median(difficulty)
    for code in plan.p3_schools:
        rows = plan.members(code)
        p[np.ix_(rows, hard)] = np.clip(p[np.ix_(rows, hard)] + P3_BONUS, 0, 0.99)


def apply_p2(correct: np.ndarray, difficulty: np.ndarray, plan: Plan) -> None:
    """П2: неверно на легкой половине заданий, верно на трудной.

    Это включает требование раздела 10.7 (неверно на первых 40%, верно на последних 40%),
    а средние 20% тоже идут «наоборот», и G* получается около 1. Иначе в коротком КИМ
    (НИКО — 10 заданий) обычный слабый ученик, случайно решивший одно самое трудное
    задание, получает G* = 1 и обгоняет закладку.
    """
    order = np.argsort(difficulty)
    half = len(difficulty) // 2
    for index in plan.p2:
        correct[index, order[:half]] = False
        correct[index, order[half:]] = True


def fit_score(
    rng: np.random.Generator, target: int, difficulty: np.ndarray, max2: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Верность заданий части 1 и баллы части 2 с суммой ровно target.

    Баллы части 2 раскладываются случайно; часть 1 — решены самые легкие задания.
    """
    n1 = len(difficulty)
    low, high = max(0, target - n1), min(target, int(max2.sum()))
    part2_total = int(rng.integers(low, high + 1))
    scores = np.zeros(len(max2), dtype=int)
    for _ in range(part2_total):
        open_tasks = np.flatnonzero(scores < max2)
        scores[open_tasks[int(rng.integers(len(open_tasks)))]] += 1
    # Решенные задания — случайные, легкие вероятнее. Одинаковый набор у всей школы
    # дал бы одинаковые ответы, и сработало бы правило А2.
    weights = np.exp(-difficulty)
    solved = rng.choice(n1, size=target - part2_total, replace=False, p=weights / weights.sum())
    correct = np.zeros(n1, dtype=bool)
    correct[solved] = True
    return correct, scores


def apply_p4(
    rng: np.random.Generator,
    correct: np.ndarray,
    scores2: np.ndarray,
    difficulty: np.ndarray,
    max2: np.ndarray,
    exam: ExamConfig,
    plan: Plan,
) -> None:
    """П4а: всем ученикам школы одинаковый балл. П4б: 80% — балл в [порог, порог + 2]."""
    for code, target in plan.p4a_schools.items():
        for index in plan.members(code):
            correct[index], scores2[index] = fit_score(rng, target, difficulty, max2)
    for code in plan.p4b_schools:
        members = plan.members(code)
        chosen = rng.choice(members, size=round(P4B_SHARE * len(members)), replace=False)
        for index in chosen:
            target = exam.threshold_score + int(rng.integers(0, 3))
            correct[index], scores2[index] = fit_score(rng, target, difficulty, max2)


def apply_texts(
    rng: np.random.Generator,
    texts: list[list[str]],
    theta: np.ndarray,
    exam: ExamConfig,
    bank: dict[tuple[str, int], TaskAnswers],
    plan: Plan,
) -> None:
    """Строки ответов: А2 — копия работы источника, ловушка — пустые ответы,
    П1 — одинаковые редкие неверные ответы в 5–6 заданиях.
    """
    plan.a2_sources = []
    for group in plan.a2_groups:
        source = sorted(group, key=lambda index: theta[index])[len(group) // 2]
        variant = plan.participants[source].variant_id
        copy = [
            text if text != "" else bank[(variant, task)].typical_wrong[0]
            for text, task in zip(texts[source], exam.part1_numbers, strict=True)
        ]
        for index in group:
            texts[index] = list(copy)
        plan.a2_sources.append(source)
    for group in plan.a2_blank_groups:
        for index in group:
            texts[index] = [""] * exam.part1_tasks
    for group, tasks in plan.p1_groups:
        variant = plan.participants[group[0]].variant_id
        for task in tasks:
            shared = str(rng.choice(bank[(variant, task)].rare_wrong))
            for index in group:
                texts[index][exam.part1_numbers.index(task)] = shared


# --- Самопроверка и ключ ---------------------------------------------------------------------


def pid(plan: Plan, index: int) -> str:
    return plan.participants[index].participant_id


def self_check(records: dict[str, list[dict]], exam: ExamConfig, plan: Plan) -> dict:
    """Прогоняет эталонные правила и сверяет с планом (раздел 10.9).

    Возвращает {"passed", "extras", "problems", "planted", "decoys", "reference_check"}.
    """
    frames = rules.to_frames(records)
    sessions, answers = frames["sessions"], frames["answers"]
    problems: list[str] = []

    a1 = rules.rule_a1(sessions)
    a1_found = set(a1.loc[a1["anomaly"], "school_code"])
    a2_found = [set(group["participant_ids"]) for group in rules.rule_a2(answers, sessions)]
    a3_found = set(rules.rule_a3(frames["proctoring-events"])["participant_id"])
    p1_found = [set(group["participant_ids"]) for group in rules.rule_p1(answers, sessions)]
    p2 = rules.rule_p2(answers, sessions)
    p3 = rules.rule_p3(answers, sessions)
    p4 = rules.rule_p4(sessions, {exam.exam_type: exam.threshold_score})

    planted: list[dict] = []
    for code in plan.a1_schools:
        row = a1[a1["school_code"] == code]
        if code not in a1_found:
            problems.append(f"A1 {code} не найдена")
        expected = {}
        if not row.empty:
            first = row.iloc[0]
            expected = {key: round(float(first[key]), 2)
                        for key in ("z", "school_mean", "region_mean", "sigma")}  # fmt: skip
        members = plan.members(code)
        planted.append({"rule": "A1", "school_code": code,
                        "region_code": plan.participants[members[0]].region_code,
                        "subject_code": exam.subject_code, "boost": round(plan.a1_boost, 2),
                        "expected": expected})  # fmt: skip

    a2_planted = [{pid(plan, i) for i in group} for group in plan.a2_groups]
    for group, source, ids in zip(plan.a2_groups, plan.a2_sources, a2_planted, strict=True):
        if ids not in a2_found:
            problems.append(f"A2 {sorted(ids)} не найдена")
        planted.append({"rule": "A2", "school_code": plan.participants[group[0]].school_code,
                        "variant_id": plan.participants[group[0]].variant_id,
                        "participant_ids": sorted(ids),
                        "source_participant_id": pid(plan, source)})  # fmt: skip
    for index, count in plan.a3_counts.items():
        if pid(plan, index) not in a3_found:
            problems.append(f"A3 {pid(plan, index)} не найден")
        planted.append({"rule": "A3", "participant_id": pid(plan, index), "counted_events": count})

    p1_planted = []
    for group, tasks in plan.p1_groups:
        ids = {pid(plan, i) for i in group}
        p1_planted.append(ids)
        if not any(ids <= found for found in p1_found):
            problems.append(f"P1 {sorted(ids)} не найдена")
        first = plan.participants[group[0]]
        planted.append({"rule": "P1", "school_code": first.school_code,
                        "room_code": first.room_code, "variant_id": first.variant_id,
                        "participant_ids": sorted(ids), "shared_wrong_tasks": tasks})  # fmt: skip

    def top(frame, column, key):
        exam_rows = frame[frame["exam_type"] == exam.exam_type]
        if exam_rows.empty:
            return None
        return exam_rows.loc[exam_rows[column].idxmax(), key]

    # «Наибольший» — среди обычных школ и участников: другая закладка (например, школа А1,
    # которая лучше и на трудных заданиях) — тоже аномалия, а не фон. Равенство допускается.
    planted_schools = set(plan.a1_schools) | set(plan.p3_schools) | set(plan.p4a_schools)
    planted_schools |= set(plan.p4b_schools)
    for group in plan.a2_groups + plan.a2_blank_groups + [g for g, _ in plan.p1_groups]:
        planted_schools.add(plan.participants[group[0]].school_code)
    planted_indices = set(plan.p2) | set(plan.a3_counts) | set(plan.focus_decoys)
    for group in plan.a2_groups + plan.a2_blank_groups + [g for g, _ in plan.p1_groups]:
        planted_indices.update(group)
    for code in set(plan.a1_schools) | set(plan.p3_schools) | set(plan.p4a_schools):
        planted_indices.update(plan.members(code))
    for code in plan.p4b_schools:
        planted_indices.update(plan.members(code))
    planted_people = {pid(plan, index) for index in planted_indices}

    def leads(frame, column, key, value, excluded) -> bool:
        rows = frame[frame["exam_type"] == exam.exam_type]
        own = rows.loc[rows[key] == value, column]
        others = rows.loc[(rows[key] != value) & ~rows[key].isin(excluded), column]
        return not own.empty and (others.empty or float(own.iloc[0]) >= float(others.max()))

    for index in plan.p2:
        if not leads(p2, "g_star", "participant_id", pid(plan, index), planted_people):
            problems.append(f"P2 {pid(plan, index)} не наибольший G*")
        planted.append({"rule": "P2", "participant_id": pid(plan, index)})
    for code in plan.p3_schools:
        if not leads(p3, "p3", "school_code", code, planted_schools):
            problems.append(f"P3 {code} не наибольший П3")
        planted.append({"rule": "P3", "school_code": code})
    for code, target in plan.p4a_schools.items():
        if not leads(p4, "z4a", "school_code", code, planted_schools):
            problems.append(f"P4a {code} не наибольший z′")
        planted.append({"rule": "P4a", "school_code": code, "target_score": target})
    for code in plan.p4b_schools:
        if not leads(p4, "z4b", "school_code", code, planted_schools):
            problems.append(f"P4b {code} не наибольший z′")
        planted.append({"rule": "P4b", "school_code": code})

    decoys: list[dict] = []
    for group in plan.a2_blank_groups:
        ids = {pid(plan, i) for i in group}
        if any(ids & found for found in a2_found):
            problems.append(f"ловушка A2_blank {sorted(ids)} сработала")
        decoys.append({"rule": "A2", "kind": "A2_blank", "participant_ids": sorted(ids),
                       "why": "все ответы части 1 пустые: непустых меньше половины"})  # fmt: skip
    for index in plan.focus_decoys:
        if pid(plan, index) in a3_found:
            problems.append(f"ловушка A3_focus {pid(plan, index)} сработала")
        why = "5 focus_lost и 2 tab_switch: засчитываемых событий меньше 3"
        decoys.append(
            {"rule": "A3", "kind": "A3_focus", "participant_id": pid(plan, index), "why": why}
        )

    a2_extra = [found for found in a2_found if found not in a2_planted]
    a3_extra = a3_found - {pid(plan, i) for i in plan.a3_counts}
    if a2_extra:
        problems.append(f"лишние срабатывания A2: {len(a2_extra)}")
    if a3_extra:
        problems.append(f"лишние срабатывания A3: {len(a3_extra)}")
    planted_people = set().union(*a2_planted, *p1_planted) if (a2_planted or p1_planted) else set()
    p1_extra = [found for found in p1_found if not found & planted_people]
    extras = len(a1_found - set(plan.a1_schools)) + len(p1_extra)

    reference_check = {
        "A1": sorted(a1_found),
        "A2": [sorted(found) for found in a2_found],
        "A3": sorted(a3_found),
        "P1_groups": [sorted(found) for found in p1_found],
        "P2": top(p2, "g_star", "participant_id"),
        "P3": top(p3, "p3", "school_code"),
        "P4": {"a": top(p4, "z4a", "school_code"), "b": top(p4, "z4b", "school_code")},
    }
    return {
        "passed": not problems,
        "extras": extras,
        "problems": problems,
        "planted": planted,
        "decoys": decoys,
        "reference_check": reference_check,
    }
