"""Записи API (раздел 6) и их версии во времени для одного экзамена (разделы 10.5–10.7).

Каждая запись — несколько версий с одним record_id; updated_at версии — момент, когда
она становится видимой на платформе. Жизненный цикл (раздел 10.6):
  sessions        started → in_progress → finished (pending) → final → appeal_changed;
                  absent — одна версия; annulled — в момент аннулирования
  answers         одна версия в момент ответа
  expert-reviews  pending (при сдаче) → checked, first → для апелляции: checked, appeal
  proctoring      одна версия в момент события

Время действий ученика — по местному времени его региона; время действий платформы
(отметка о неявке, итоги, проверка, апелляция, справочники) — по Москве (+03:00).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import numpy as np

from sma import contract
from sma.generator import anomalies, model
from sma.generator.exams import (
    OGE_GEOMETRY_MIN,
    ExamConfig,
    TaskAnswers,
    ege_test_score,
    niko_level,
    oge_grade,
    scale_conditions,
    scale_points,
    vpr_grade,
)
from sma.generator.population import Participant, Region, School
from sma.generator.timeline import snapshot_moments
from sma.normalize import normalize_answer

MSK = timezone(timedelta(hours=3))
RECORD_NAMESPACE = uuid.UUID("6f1d3c52-8a7e-4b0e-9a51-2f6c0d9e4b17")  # постоянный для проекта

EXAM_START = time(10, 0)  # начало по местному времени
CHECK_START = time(14, 30)  # проверка экспертами по Москве: 14:30–17:30
CHECK_WINDOW_SEC = 3 * 3600
APPEAL_DAYS = (2, 5)  # апелляция — через 2–5 дней после проверки
DICTIONARY_DAYS_BEFORE = 10
SCALE_DAYS_BEFORE = 4

EXPERT_CODES = [f"e_{n:02d}" for n in range(1, 13)]
COUNTED_EVENTS = ("tab_switch", "copy", "paste", "fullscreen_exit")
COUNTED_EVENT_PROBS = (0.55, 0.15, 0.15, 0.15)
TIME_SPENT_NULL_RATE = 0.04
PROCTORING_TASK_NULL_RATE = 0.2
TERM_MARK_NULL_RATE = 0.10
FACT_ENDPOINTS = ("sessions", "answers", "expert-reviews", "proctoring-events")


@dataclass
class ExamResult:
    """Все версии записей одного экзамена и его время."""

    versions: dict[str, list[dict]]  # адрес → версии всех записей
    exam_start: datetime
    exam_end: datetime
    moments: dict[str, datetime]  # моменты снимков (timeline.SNAPSHOTS)
    appeals: list[str]  # участники с изменением после апелляции


def make_record_id(exam_type: str, endpoint: str, key: str) -> str:
    """Детерминированный record_id: UUID5 от (exam_type, адрес, естественный ключ)."""
    return str(uuid.uuid5(RECORD_NAMESPACE, f"{exam_type}/{endpoint}/{key}"))


def iso(moment: datetime) -> str:
    """Время в формате контракта: "2026-12-05T10:00:00+03:00"."""
    return moment.isoformat(timespec="seconds")


def build_record(
    endpoint: str,
    record_id: str,
    values: dict,
    updated_at: datetime,
    block: tuple[str, dict] | None = None,
) -> dict:
    """Запись адреса с ключами в порядке контракта; время переводится в строки ISO 8601.

    values должен содержать ровно поля адреса из contract.ENDPOINT_FIELDS — иначе ValueError.
    block — пара (ключ блока, значения) для /sessions.
    """
    names = [field.name for field in contract.ENDPOINT_FIELDS[endpoint]]
    if set(values) != set(names):
        raise ValueError(f"{endpoint}: поля {sorted(values)} не совпадают с контрактом {names}")
    record = {"record_id": record_id}
    for name in names:
        value = values[name]
        record[name] = iso(value) if isinstance(value, datetime) else value
    if block is not None:
        key, block_values = block
        record[key] = dict(block_values)
    record["updated_at"] = iso(updated_at)
    record["deleted"] = False
    return record


def local_zone(region: Region) -> timezone:
    return timezone(timedelta(hours=region.utc_offset))


def scheduled_start(exam: ExamConfig, region: Region) -> datetime:
    """Плановое начало экзамена в регионе: 10:00 местного времени."""
    return datetime.combine(exam.exam_date, EXAM_START, tzinfo=local_zone(region))


def at_msk(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment, tzinfo=MSK)


def choose_statuses(
    rng: np.random.Generator, n: int, rates: dict, protected: set[int]
) -> list[str]:
    """Ровно round(доля × n) absent и annulled среди незащищенных участников."""
    n_absent = round(rates["absent"] * n)
    n_annulled = round(rates["annulled"] * n)
    candidates = [index for index in range(n) if index not in protected]
    if n_absent + n_annulled > len(candidates):
        raise anomalies.PlantingError("слишком мало участников без закладок")
    statuses = ["finished"] * n
    chosen = rng.choice(candidates, size=n_absent + n_annulled, replace=False)
    for k, index in enumerate(chosen):
        statuses[int(index)] = "absent" if k < n_absent else "annulled"
    return statuses


def empty_block(exam: ExamConfig) -> dict:
    """Блок экзамена, пока результата нет: все значения null."""
    key = contract.EXAM_BLOCK_KEYS[exam.exam_type]
    return {field.name: None for field in contract.EXAM_BLOCK_FIELDS[key]}


def block_extras(rng: np.random.Generator, exam: ExamConfig, theta: float) -> dict:
    """Значения блока, не зависящие от баллов: отметка за четверть ВПР, анкета НИКО."""
    if exam.exam_type == "VPR":
        if rng.random() < TERM_MARK_NULL_RATE:
            return {"term_mark": None}
        return {"term_mark": int(np.clip(round(3.5 + 0.8 * theta), 2, 5))}
    if exam.exam_type == "NIKO":
        u = rng.random()
        return {"questionnaire_done": True if u < 0.88 else (False if u < 0.95 else None)}
    return {}


def result_block(exam: ExamConfig, primary: int, task_scores: dict[int, int], extras: dict) -> dict:
    """Блок экзамена с итогами по шкале (ключи — в порядке контракта)."""
    values = dict(extras)
    if exam.exam_type == "EGE":
        values["test_score"] = ege_test_score(primary)
    elif exam.exam_type == "OGE":
        sums = {block: sum(task_scores[t] for t in tasks) for block, tasks in exam.blocks.items()}
        values.update(
            grade=oge_grade(primary, sums["geometry"]),
            block_scores=sums,
            block_failed=sums["geometry"] < OGE_GEOMETRY_MIN,
        )
    elif exam.exam_type == "VPR":
        values["grade"] = vpr_grade(primary)
    else:
        values["result_level"] = niko_level(primary)
    return {name: values[name] for name in empty_block(exam)}


def answer_texts(
    rng: np.random.Generator,
    exam: ExamConfig,
    participants: list[Participant],
    correct: np.ndarray,
    difficulty: np.ndarray,
    bank: dict[tuple[str, int], TaskAnswers],
    empty_base: float,
) -> list[list[str]]:
    """Строки ответов части 1 «как ввел ученик» для всех участников и заданий."""
    texts = []
    for i, participant in enumerate(participants):
        row = []
        for k, task in enumerate(exam.part1_numbers):
            answers = bank[(participant.variant_id, task)]
            if correct[i, k]:
                row.append(model.typed_correct(rng, answers))
            else:
                row.append(model.wrong_answer(rng, float(difficulty[k]), answers, empty_base))
        texts.append(row)
    return texts


def proctoring_types(rng: np.random.Generator, counted: int | None, decoy: bool) -> list[str]:
    """Типы событий участника: обычно focus_lost ~ Poisson(0,5) и засчитываемых ≤ 2.

    counted — заложенное число засчитываемых (А3); decoy — ловушка (5 focus_lost, 2 tab_switch).
    """
    if decoy:
        return ["focus_lost"] * 5 + ["tab_switch"] * 2
    n_focus = int(rng.poisson(0.5))
    n_counted = counted if counted is not None else min(2, int(rng.poisson(0.12)))
    chosen = rng.choice(COUNTED_EVENTS, size=n_counted, p=COUNTED_EVENT_PROBS)
    return ["focus_lost"] * n_focus + [str(event) for event in chosen]


def participant_versions(
    rng: np.random.Generator,
    exam: ExamConfig,
    participant: Participant,
    region: Region,
    status: str,
    texts: list[str],
    part2_scores: np.ndarray,
    extras: dict,
    bank: dict[tuple[str, int], TaskAnswers],
    appeal: bool,
    counted_events: int | None,
    focus_decoy: bool,
) -> dict[str, list[dict]]:
    """Все версии записей одного участника: сессия, ответы, оценки, события."""
    pid = participant.participant_id
    out: dict[str, list[dict]] = {endpoint: [] for endpoint in FACT_ENDPOINTS}
    block_key = contract.EXAM_BLOCK_KEYS[exam.exam_type]
    session_id = make_record_id(exam.exam_type, "sessions", pid)
    session = {
        "participant_id": pid,
        "exam_type": exam.exam_type,
        "subject_code": exam.subject_code,
        "grade_level": exam.grade_level,
        "variant_id": participant.variant_id,
        "region_code": participant.region_code,
        "school_code": participant.school_code,
        "room_code": participant.room_code,
        "status": status,
        "started_at": None,
        "finished_at": None,
        "primary_score": None,
        "max_primary_score": exam.max_primary_score,
        "result_status": "pending",
    }

    def session_version(moment: datetime, block: dict | None = None, **changes) -> None:
        session.update(changes)
        values = (block_key, block if block is not None else empty_block(exam))
        out["sessions"].append(
            build_record("sessions", session_id, session, moment.astimezone(MSK), values)
        )

    if status == "absent":
        marked = scheduled_start(exam, region) + timedelta(minutes=int(rng.integers(1, 31)))
        session_version(marked, status="absent", result_status="final")
        return out

    start = scheduled_start(exam, region)
    start += timedelta(seconds=int(rng.integers(0, exam.start_spread_min * 60 + 1)))
    work_sec = int(rng.uniform(0.55, 0.97) * exam.duration_min * 60)
    part1_sec = model.task_times(rng, exam.part1_tasks, 0.5 * work_sec)
    offsets = np.rint(np.cumsum(part1_sec)).astype(int)
    stop_sec = work_sec if status == "finished" else int(rng.uniform(0.2, 0.8) * work_sec)

    # Ответы части 1.
    task_scores: dict[int, int] = {}
    for k, task in enumerate(exam.part1_numbers):
        if offsets[k] > stop_sec:
            break
        text = texts[k]
        answers = bank[(participant.variant_id, task)]
        is_correct = text != "" and normalize_answer(text) == answers.correct
        task_scores[task] = int(is_correct)
        spent = None if rng.random() < TIME_SPENT_NULL_RATE else int(round(float(part1_sec[k])))
        answered_at = start + timedelta(seconds=int(offsets[k]))
        values = {
            "participant_id": pid,
            "exam_type": exam.exam_type,
            "task_number": task,
            "answer": text,
            "is_correct": is_correct,
            "score": int(is_correct),
            "max_score": 1,
            "answered_at": answered_at,
            "time_spent_sec": spent,
            "audio_plays": None,
        }
        record_id = make_record_id(exam.exam_type, "answers", f"{pid}/{task}")
        out["answers"].append(build_record("answers", record_id, values, answered_at))

    # События прокторинга.
    if exam.has_proctoring:
        event_types = proctoring_types(rng, counted_events, focus_decoy)
        seconds = rng.integers(0, stop_sec + 1, size=len(event_types))
        for number, k in enumerate(np.argsort(seconds, kind="stable")):
            moment_sec = int(seconds[k])
            position = int(np.searchsorted(offsets, moment_sec))
            if position < len(exam.part1_numbers):
                task_number = exam.part1_numbers[position]
            else:
                task_number = int(rng.choice(exam.part2_numbers))
            if rng.random() < PROCTORING_TASK_NULL_RATE:
                task_number = None
            occurred_at = start + timedelta(seconds=moment_sec)
            values = {
                "participant_id": pid,
                "exam_type": exam.exam_type,
                "event_type": event_types[k],
                "occurred_at": occurred_at,
                "task_number": task_number,
            }
            record_id = make_record_id(exam.exam_type, "proctoring-events", f"{pid}/{number}")
            out["proctoring-events"].append(
                build_record("proctoring-events", record_id, values, occurred_at)
            )

    # Сессия: начало, первый ответ, конец.
    session_version(start, status="started", started_at=start)
    first_answer = start + timedelta(seconds=int(offsets[0]) if task_scores else 60)
    if first_answer <= start + timedelta(seconds=stop_sec):
        session_version(first_answer, status="in_progress")
    if status == "annulled":
        session_version(
            start + timedelta(seconds=stop_sec), status="annulled", result_status="final"
        )
        return out
    finished_at = start + timedelta(seconds=work_sec)
    session_version(finished_at, status="finished", finished_at=finished_at)

    # Оценки экспертов: pending при сдаче → checked после проверки.
    check_start = at_msk(exam.exam_date, CHECK_START)
    reviews = []
    for k, task in enumerate(exam.part2_numbers):
        record_id = make_record_id(exam.exam_type, "expert-reviews", f"{pid}/{task}")
        review = {
            "participant_id": pid,
            "exam_type": exam.exam_type,
            "task_number": task,
            "check_status": "pending",
            "check_stage": None,
            "expert_code": None,
            "score": None,
            "max_score": exam.part2_max_scores[task],
            "reviewed_at": None,
        }
        out["expert-reviews"].append(
            build_record("expert-reviews", record_id, review, finished_at.astimezone(MSK))
        )
        reviewed_at = check_start + timedelta(seconds=int(rng.integers(0, CHECK_WINDOW_SEC + 1)))
        review.update(
            check_status="checked",
            check_stage="first",
            expert_code=str(rng.choice(EXPERT_CODES)),
            score=int(part2_scores[k]),
            reviewed_at=reviewed_at,
        )
        out["expert-reviews"].append(build_record("expert-reviews", record_id, review, reviewed_at))
        reviews.append((record_id, review))
        task_scores[task] = int(part2_scores[k])

    primary = sum(task_scores.values())
    final_at = max(review["reviewed_at"] for _, review in reviews)
    final_at += timedelta(minutes=int(rng.integers(1, 31)))
    session_version(
        final_at,
        result_block(exam, primary, task_scores, extras),
        primary_score=primary,
        result_status="final",
    )
    if not appeal:
        return out

    # Апелляция: балл за одно задание части 2 меняется на 1.
    raisable = [k for k, task in enumerate(exam.part2_numbers)
                if task_scores[task] < exam.part2_max_scores[task]]  # fmt: skip
    k = int(rng.choice(raisable)) if raisable else 0
    task = exam.part2_numbers[k]
    task_scores[task] += 1 if raisable else -1
    record_id, review = reviews[k]
    days = rng.uniform(*APPEAL_DAYS)
    appeal_at = (review["reviewed_at"] + timedelta(days=float(days))).replace(microsecond=0)
    review.update(
        check_stage="appeal",
        expert_code=str(rng.choice(EXPERT_CODES)),
        score=task_scores[task],
        reviewed_at=appeal_at,
    )
    out["expert-reviews"].append(build_record("expert-reviews", record_id, review, appeal_at))
    primary = sum(task_scores.values())
    session_version(
        appeal_at + timedelta(minutes=int(rng.integers(1, 31))),
        result_block(exam, primary, task_scores, extras),
        primary_score=primary,
        result_status="appeal_changed",
    )
    return out


def simulate_exam(
    rng: np.random.Generator,
    exam: ExamConfig,
    plan: anomalies.Plan,
    n_schools: int,
    regions: dict[str, Region],
    bank: dict[tuple[str, int], TaskAnswers],
    rates: dict,
) -> ExamResult:
    """Моделирует один экзамен с закладками из plan и собирает версии записей
    /sessions, /answers, /expert-reviews, /proctoring-events.
    """
    participants = plan.participants
    n = len(participants)
    statuses = choose_statuses(rng, n, rates, plan.protected)
    school_index = np.array([p.school_index for p in participants])
    theta = model.abilities(rng, school_index, n_schools)
    anomalies.apply_boost(theta, plan)
    part1_difficulty = model.part1_difficulties(rng, exam.part1_tasks)
    probabilities = model.p_correct(theta, part1_difficulty)
    anomalies.apply_p3(probabilities, part1_difficulty, plan)
    correct = rng.random((n, exam.part1_tasks)) < probabilities
    max2 = np.array([exam.part2_max_scores[task] for task in exam.part2_numbers])
    part2_difficulty = 0.3 + 0.5 * np.arange(len(max2)) + rng.normal(0, 0.2, len(max2))
    part2_scores = model.expert_scores(rng, theta, max2, part2_difficulty)
    anomalies.apply_p2(correct, part1_difficulty, plan)
    anomalies.apply_p4(rng, correct, part2_scores, part1_difficulty, max2, exam, plan)
    texts = answer_texts(
        rng, exam, participants, correct, part1_difficulty, bank, rates["empty_answer_base"]
    )
    anomalies.apply_texts(rng, texts, theta, exam, bank, plan)

    finished = [i for i in range(n) if statuses[i] == "finished" and i not in plan.protected]
    n_appeals = min(len(finished), round(rates.get("appeal", 0) * len(finished)))
    appealed = {int(i) for i in rng.choice(finished, size=n_appeals, replace=False)}

    versions: dict[str, list[dict]] = {endpoint: [] for endpoint in FACT_ENDPOINTS}
    for i, participant in enumerate(participants):
        one = participant_versions(
            rng,
            exam,
            participant,
            regions[participant.region_code],
            statuses[i],
            texts[i],
            part2_scores[i],
            block_extras(rng, exam, float(theta[i])),
            bank,
            appeal=i in appealed,
            counted_events=plan.a3_counts.get(i),
            focus_decoy=i in plan.focus_decoys,
        )
        for endpoint, items in one.items():
            versions[endpoint].extend(items)

    starts = [scheduled_start(exam, regions[p.region_code]) for p in participants]
    exam_start = min(starts).astimezone(MSK)
    exam_end = max(starts) + timedelta(minutes=exam.start_spread_min + exam.duration_min)
    exam_end = exam_end.astimezone(MSK)
    moments = snapshot_moments(exam.exam_date, exam_start, exam_end, MSK)
    appeals = sorted(participants[i].participant_id for i in appealed)
    return ExamResult(versions, exam_start, exam_end, moments, appeals)


def dictionary_records(
    exam: ExamConfig,
    regions: list[Region],
    schools: list[School],
    kim: list[dict],
    exam_end: datetime,
) -> dict[str, list[dict]]:
    """Записи справочников одной платформы: regions, schools, subjects (одна версия, видны
    всегда), kim (видна с конца экзамена) и scales (версия 1 — за 4 дня до экзамена).
    """
    exam_type = exam.exam_type
    published = at_msk(exam.exam_date - timedelta(days=DICTIONARY_DAYS_BEFORE), time(9, 0))
    scale_from = at_msk(exam.exam_date - timedelta(days=SCALE_DAYS_BEFORE), time(9, 0))

    def rid(endpoint: str, key: str) -> str:
        return make_record_id(exam_type, endpoint, key)

    return {
        "dictionaries/regions": [
            build_record(
                "dictionaries/regions",
                rid("dictionaries/regions", region.code),
                {"region_code": region.code, "name": region.name},
                published,
            )
            for region in regions
        ],
        "dictionaries/schools": [
            build_record(
                "dictionaries/schools",
                rid("dictionaries/schools", school.school_code),
                {
                    "school_code": school.school_code,
                    "region_code": school.region_code,
                    "name": school.name,
                },
                published,
            )
            for school in schools
        ],
        "dictionaries/subjects": [
            build_record(
                "dictionaries/subjects",
                rid("dictionaries/subjects", exam.subject_code),
                {
                    "exam_type": exam_type,
                    "subject_code": exam.subject_code,
                    "name": exam.subject_name,
                    "grade_level": exam.grade_level,
                    "max_primary_score": exam.max_primary_score,
                    "threshold_score": exam.threshold_score,
                },
                published,
            )
        ],
        "dictionaries/kim": [
            build_record(
                "dictionaries/kim",
                rid("dictionaries/kim", f"{row['variant_id']}/{row['task_number']}"),
                row,
                exam_end,
            )
            for row in kim
        ],
        "dictionaries/scales": [
            build_record(
                "dictionaries/scales",
                rid("dictionaries/scales", f"{exam.subject_code}/{exam.exam_year}/1"),
                {
                    "exam_type": exam_type,
                    "subject_code": exam.subject_code,
                    "exam_year": exam.exam_year,
                    "scale_type": exam.scale_type,
                    "points": scale_points(exam),
                    "conditions": scale_conditions(exam),
                    "scale_version": 1,
                    "valid_from": scale_from,
                },
                scale_from,
            )
        ],
    }
