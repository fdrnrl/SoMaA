"""Сборка записей API (раздел 6) из смоделированных данных одного экзамена.

Этап M2: один снимок «после проверки» (after_check) — без версий записей во времени
и без закладки аномалий (они появятся на этапе M6).

Время действий ученика — по местному времени его региона; время действий платформы
(проверка, обновление итогов, справочники) — по Москве (+03:00).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import numpy as np

from sma import contract
from sma.generator import model
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
from sma.normalize import normalize_answer

MSK = timezone(timedelta(hours=3))
RECORD_NAMESPACE = uuid.UUID("6f1d3c52-8a7e-4b0e-9a51-2f6c0d9e4b17")  # постоянный для проекта

EXAM_START = time(10, 0)  # начало по местному времени
CHECK_START = time(14, 30)  # проверка экспертами по Москве: 14:30–17:30
CHECK_WINDOW_SEC = 3 * 3600
SNAPSHOT_TIME = time(18, 30)  # момент снимка after_check по Москве
DICTIONARY_DAYS_BEFORE = 10
SCALE_DAYS_BEFORE = 4

EXPERT_CODES = [f"e_{n:02d}" for n in range(1, 13)]
COUNTED_EVENTS = ("tab_switch", "copy", "paste", "fullscreen_exit")
COUNTED_EVENT_PROBS = (0.55, 0.15, 0.15, 0.15)
TIME_SPENT_NULL_RATE = 0.04
PROCTORING_TASK_NULL_RATE = 0.2
TERM_MARK_NULL_RATE = 0.10


@dataclass
class ExamResult:
    """Все записи одного экзамена и его время."""

    records: dict[str, list[dict]]  # адрес → записи
    live_status: dict
    exam_start: datetime
    exam_end: datetime
    snapshot_at: datetime


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
        record[key] = block_values
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


def choose_statuses(rng: np.random.Generator, n: int, rates: dict) -> list[str]:
    """Статус каждого участника: ровно round(доля × n) absent и annulled, остальные finished."""
    n_absent = round(rates["absent"] * n)
    n_annulled = round(rates["annulled"] * n)
    statuses = ["finished"] * n
    chosen = rng.choice(n, size=n_absent + n_annulled, replace=False)
    for k, index in enumerate(chosen):
        statuses[int(index)] = "absent" if k < n_absent else "annulled"
    return statuses


def empty_block(exam: ExamConfig) -> dict:
    """Блок экзамена, пока результата нет: все значения null."""
    key = contract.EXAM_BLOCK_KEYS[exam.exam_type]
    return {field.name: None for field in contract.EXAM_BLOCK_FIELDS[key]}


def result_block(
    rng: np.random.Generator,
    exam: ExamConfig,
    primary: int,
    task_scores: dict[int, int],
    theta: float,
) -> dict:
    """Блок экзамена с итогами по шкале (ключи — в порядке контракта)."""
    if exam.exam_type == "EGE":
        values = {"test_score": ege_test_score(primary)}
    elif exam.exam_type == "OGE":
        sums = {block: sum(task_scores[t] for t in tasks) for block, tasks in exam.blocks.items()}
        values = {
            "grade": oge_grade(primary, sums["geometry"]),
            "block_scores": sums,
            "block_failed": sums["geometry"] < OGE_GEOMETRY_MIN,
        }
    elif exam.exam_type == "VPR":
        term_mark = None
        if rng.random() >= TERM_MARK_NULL_RATE:
            term_mark = int(np.clip(round(3.5 + 0.8 * theta), 2, 5))
        values = {"grade": vpr_grade(primary), "term_mark": term_mark}
    else:
        u = rng.random()
        done = True if u < 0.88 else (False if u < 0.95 else None)
        values = {"result_level": niko_level(primary), "questionnaire_done": done}
    return {name: values[name] for name in empty_block(exam)}


def participant_records(
    rng: np.random.Generator,
    exam: ExamConfig,
    participant: Participant,
    region: Region,
    status: str,
    theta: float,
    correct: np.ndarray,
    part2_scores: np.ndarray,
    part1_difficulty: np.ndarray,
    bank: dict[tuple[str, int], TaskAnswers],
    empty_base: float,
) -> dict[str, list[dict]]:
    """Записи одного участника: сессия, ответы, оценки экспертов, события прокторинга."""
    pid = participant.participant_id
    out: dict[str, list[dict]] = {
        "sessions": [],
        "answers": [],
        "expert-reviews": [],
        "proctoring-events": [],
    }
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
        "result_status": "final",
    }
    block_key = contract.EXAM_BLOCK_KEYS[exam.exam_type]
    session_id = make_record_id(exam.exam_type, "sessions", pid)

    if status == "absent":
        marked = scheduled_start(exam, region).astimezone(MSK)
        marked += timedelta(minutes=int(rng.integers(1, 31)))
        out["sessions"].append(
            build_record("sessions", session_id, session, marked, (block_key, empty_block(exam)))
        )
        return out

    start = scheduled_start(exam, region)
    start += timedelta(seconds=int(rng.integers(0, exam.start_spread_min * 60 + 1)))
    work_sec = int(rng.uniform(0.55, 0.97) * exam.duration_min * 60)
    part1_sec = model.task_times(rng, exam.part1_tasks, 0.5 * work_sec)
    offsets = np.rint(np.cumsum(part1_sec)).astype(int)
    stop_sec = work_sec if status == "finished" else int(rng.uniform(0.2, 0.8) * work_sec)

    # Ответы части 1.
    task_scores = {}
    for k, task in enumerate(exam.part1_numbers):
        if offsets[k] > stop_sec:
            break
        answers = bank[(participant.variant_id, task)]
        if correct[k]:
            text = model.typed_correct(rng, answers)
        else:
            text = model.wrong_answer(rng, float(part1_difficulty[k]), answers, empty_base)
        is_correct = text != "" and normalize_answer(text) == answers.correct
        task_scores[task] = int(is_correct)
        spent = None if rng.random() < TIME_SPENT_NULL_RATE else int(round(float(part1_sec[k])))
        values = {
            "participant_id": pid,
            "exam_type": exam.exam_type,
            "task_number": task,
            "answer": text,
            "is_correct": is_correct,
            "score": int(is_correct),
            "max_score": 1,
            "answered_at": start + timedelta(seconds=int(offsets[k])),
            "time_spent_sec": spent,
            "audio_plays": None,
        }
        answered_at = values["answered_at"]
        record_id = make_record_id(exam.exam_type, "answers", f"{pid}/{task}")
        out["answers"].append(build_record("answers", record_id, values, answered_at))

    # События прокторинга.
    if exam.has_proctoring:
        n_focus = int(rng.poisson(0.5))
        n_counted = min(2, int(rng.poisson(0.12)))
        counted = rng.choice(COUNTED_EVENTS, size=n_counted, p=COUNTED_EVENT_PROBS)
        event_types = ["focus_lost"] * n_focus + [str(event) for event in counted]
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

    if status == "annulled":
        session["started_at"] = start
        annulled_at = (start + timedelta(seconds=stop_sec)).astimezone(MSK)
        out["sessions"].append(
            build_record(
                "sessions", session_id, session, annulled_at, (block_key, empty_block(exam))
            )
        )
        return out

    # Оценки экспертов (часть 2) — только для сданных работ.
    check_start = at_msk(exam.exam_date, CHECK_START)
    reviewed_times = []
    for k, task in enumerate(exam.part2_numbers):
        reviewed_at = check_start + timedelta(seconds=int(rng.integers(0, CHECK_WINDOW_SEC + 1)))
        reviewed_times.append(reviewed_at)
        task_scores[task] = int(part2_scores[k])
        values = {
            "participant_id": pid,
            "exam_type": exam.exam_type,
            "task_number": task,
            "check_status": "checked",
            "check_stage": "first",
            "expert_code": str(rng.choice(EXPERT_CODES)),
            "score": int(part2_scores[k]),
            "max_score": exam.part2_max_scores[task],
            "reviewed_at": reviewed_at,
        }
        record_id = make_record_id(exam.exam_type, "expert-reviews", f"{pid}/{task}")
        out["expert-reviews"].append(build_record("expert-reviews", record_id, values, reviewed_at))

    primary = sum(task_scores.values())
    session["started_at"] = start
    session["finished_at"] = start + timedelta(seconds=work_sec)
    session["primary_score"] = primary
    block = result_block(rng, exam, primary, task_scores, theta)
    final_at = max(reviewed_times) + timedelta(minutes=int(rng.integers(1, 31)))
    out["sessions"].append(
        build_record("sessions", session_id, session, final_at, (block_key, block))
    )
    return out


def simulate_exam(
    rng: np.random.Generator,
    exam: ExamConfig,
    participants: list[Participant],
    n_schools: int,
    regions: dict[str, Region],
    bank: dict[tuple[str, int], TaskAnswers],
    rates: dict,
) -> ExamResult:
    """Моделирует один экзамен и собирает записи /sessions, /answers, /expert-reviews,
    /proctoring-events и сводку /live-status.
    """
    n = len(participants)
    statuses = choose_statuses(rng, n, rates)
    school_index = np.array([p.school_index for p in participants])
    theta = model.abilities(rng, school_index, n_schools)
    part1_difficulty = model.part1_difficulties(rng, exam.part1_tasks)
    correct = rng.random((n, exam.part1_tasks)) < model.p_correct(theta, part1_difficulty)
    max2 = np.array([exam.part2_max_scores[task] for task in exam.part2_numbers])
    part2_difficulty = 0.3 + 0.5 * np.arange(len(max2)) + rng.normal(0, 0.2, len(max2))
    part2_scores = model.expert_scores(rng, theta, max2, part2_difficulty)

    records: dict[str, list[dict]] = {
        "sessions": [],
        "answers": [],
        "expert-reviews": [],
        "proctoring-events": [],
    }
    for i, participant in enumerate(participants):
        one = participant_records(
            rng,
            exam,
            participant,
            regions[participant.region_code],
            statuses[i],
            float(theta[i]),
            correct[i],
            part2_scores[i],
            part1_difficulty,
            bank,
            rates["empty_answer_base"],
        )
        for endpoint, items in one.items():
            records[endpoint].extend(items)

    starts = [scheduled_start(exam, regions[p.region_code]) for p in participants]
    exam_start = min(starts)
    exam_end = max(starts) + timedelta(minutes=exam.start_spread_min + exam.duration_min)
    snapshot_at = at_msk(exam.exam_date, SNAPSHOT_TIME)
    live_status = {
        "exam_type": exam.exam_type,
        "in_progress": 0,
        "finished": statuses.count("finished"),
        "annulled": statuses.count("annulled"),
        "absent": statuses.count("absent"),
        "generated_at": iso(snapshot_at),
    }
    return ExamResult(records, live_status, exam_start, exam_end, snapshot_at)


def dictionary_records(
    exam: ExamConfig,
    regions: list[Region],
    schools: list[School],
    kim: list[dict],
    exam_end: datetime,
) -> dict[str, list[dict]]:
    """Записи справочников одной платформы: regions, schools, subjects, kim, scales."""
    exam_type = exam.exam_type
    published = at_msk(exam.exam_date - timedelta(days=DICTIONARY_DAYS_BEFORE), time(9, 0))
    scale_from = at_msk(exam.exam_date - timedelta(days=SCALE_DAYS_BEFORE), time(9, 0))

    def rid(endpoint: str, key: str) -> str:
        return make_record_id(exam_type, endpoint, key)

    out: dict[str, list[dict]] = {
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
    return out
