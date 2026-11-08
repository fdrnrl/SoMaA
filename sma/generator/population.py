"""Население: регионы, школы, аудитории, участники (раздел 10.4)."""

import math
from collections import Counter
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Region:
    """Регион: код, название, смещение часового пояса от UTC в часах."""

    code: str
    name: str
    utc_offset: int


@dataclass(frozen=True)
class School:
    """Школа из справочника."""

    school_code: str
    region_code: str
    name: str


@dataclass(frozen=True)
class Participant:
    """Участник одного экзамена (одна сессия)."""

    participant_id: str
    region_code: str
    school_code: str
    school_index: int  # номер школы в списке используемых школ (для эффекта школы)
    room_code: str | None
    variant_id: str


# Приложение 21.2.
REGIONS = [
    Region("77", "Москва", 3),
    Region("78", "Санкт-Петербург", 3),
    Region("50", "Московская область", 3),
    Region("16", "Республика Татарстан", 3),
    Region("66", "Свердловская область", 5),
    Region("54", "Новосибирская область", 7),
    Region("23", "Краснодарский край", 3),
    Region("02", "Республика Башкортостан", 5),
    Region("63", "Самарская область", 4),
    Region("52", "Нижегородская область", 3),
    Region("74", "Челябинская область", 5),
    Region("61", "Ростовская область", 3),
    Region("24", "Красноярский край", 7),
    Region("59", "Пермский край", 5),
    Region("36", "Воронежская область", 3),
    Region("34", "Волгоградская область", 3),
    Region("55", "Омская область", 6),
    Region("72", "Тюменская область", 5),
    Region("38", "Иркутская область", 8),
    Region("25", "Приморский край", 10),
]
BIG_REGION_CODES = ("77", "66", "54", "16")
SCHOOL_NUMBER_MAX = 899


# --- Псевдонимы, школы, аудитории ----------------------------------------------------------


def make_participant_ids(rng: np.random.Generator, count: int) -> list[str]:
    """count разных псевдонимов вида «p_» + 6 шестнадцатеричных цифр строчными: "p_7f3a9c"."""
    numbers = rng.choice(16**6, size=count, replace=False)
    return [f"p_{int(number):06x}" for number in numbers]


def split_sizes(
    rng: np.random.Generator, total: int, n_groups: int, size_min: int, size_max: int
) -> list[int]:
    """Случайно делит total участников на n_groups школ.

    Каждое число — от size_min до size_max включительно, сумма — ровно total.
    Если так разделить нельзя — ValueError с понятным сообщением.
    Пример: split_sizes(rng, 40, 8, 4, 6) → [5, 6, 4, 5, 5, 6, 4, 5].
    """
    if not n_groups * size_min <= total <= n_groups * size_max:
        raise ValueError(
            f"нельзя разделить {total} участников на {n_groups} школ "
            f"по {size_min}–{size_max} человек"
        )
    sizes = [size_min] * n_groups
    remaining = total - sum(sizes)
    while remaining > 0:
        not_full = [k for k in range(n_groups) if sizes[k] < size_max]
        k = not_full[int(rng.integers(len(not_full)))]
        sizes[k] += 1
        remaining -= 1
    return sizes


def room_sizes(n_students: int, room_size_max: int) -> list[int]:
    """Размеры аудиторий школы: как можно меньше аудиторий, не больше room_size_max человек
    в каждой, и размеры отличаются не больше чем на 1.

    Примеры: (16, 15) → [8, 8]; (31, 15) → [11, 10, 10]; (10, 15) → [10]; (0, 15) → [].
    Случайности нет.
    """
    if n_students == 0:
        return []
    n_rooms = math.ceil(n_students / room_size_max)
    base, extra = divmod(n_students, n_rooms)
    return [base + 1] * extra + [base] * (n_rooms - extra)


def make_room_codes(rng: np.random.Generator, count: int) -> list[str]:
    """count разных кодов аудиторий из четырех цифр: "0041", "3120"."""
    numbers = rng.choice(np.arange(1, 10000), size=count, replace=False)
    return [f"{int(number):04d}" for number in numbers]


# --- Сборка населения ----------------------------------------------------------------------


def build_regions(rng: np.random.Generator, profile: dict) -> tuple[list[Region], list[Region]]:
    """Регионы справочника и регионы, где реально сдают (большие — всегда среди них)."""
    config = profile["regions"]
    dictionary = REGIONS[: config["dictionary_count"]]
    big_codes = BIG_REGION_CODES[: config["big_regions"]]
    big = [region for region in dictionary if region.code in big_codes]
    if len(big) != len(big_codes):
        raise ValueError("большие регионы должны входить в справочник: увеличьте dictionary_count")
    others = [region for region in dictionary if region not in big]
    picked = rng.choice(len(others), size=config["used_count"] - len(big), replace=False)
    chosen = big + [others[int(k)] for k in picked]
    used = [region for region in dictionary if region in chosen]
    return dictionary, used


def school_counts(used_regions: list[Region], profile: dict) -> dict[str, int]:
    """Сколько школ с участниками в каждом используемом регионе."""
    big_codes = BIG_REGION_CODES[: profile["regions"]["big_regions"]]
    per_big = profile["regions"]["schools_per_big_region"]
    counts = {code: per_big for code in big_codes}
    normal = [region.code for region in used_regions if region.code not in big_codes]
    rest = profile["schools"]["used_count"] - per_big * len(big_codes)
    if rest < len(normal) or (not normal and rest != 0):
        raise ValueError("schools.used_count не сходится с числом регионов и больших регионов")
    for k, code in enumerate(normal):
        counts[code] = rest // len(normal) + (1 if k < rest % len(normal) else 0)
    return counts


def build_schools(
    rng: np.random.Generator,
    dictionary_regions: list[Region],
    used_regions: list[Region],
    profile: dict,
) -> tuple[list[School], list[School]]:
    """Школы справочника и школы с участниками.

    school_code = код региона + номер по порядку из трех цифр; name = «Школа № N»,
    где N случайный и не повторяется внутри региона.
    """
    used_counts = school_counts(used_regions, profile)
    extra = profile["schools"]["dictionary_count"] - profile["schools"]["used_count"]
    if extra < 0:
        raise ValueError("schools.dictionary_count меньше schools.used_count")
    extra_regions = rng.choice([region.code for region in dictionary_regions], size=extra)
    extra_counts = Counter(str(code) for code in extra_regions)

    dictionary, used = [], []
    for region in dictionary_regions:
        n_used = used_counts.get(region.code, 0)
        n_total = n_used + extra_counts.get(region.code, 0)
        if n_total == 0:
            continue
        numbers = rng.choice(np.arange(1, SCHOOL_NUMBER_MAX + 1), size=n_total, replace=False)
        for k in range(n_total):
            school = School(f"{region.code}{k + 1:03d}", region.code, f"Школа № {numbers[k]}")
            dictionary.append(school)
            if k < n_used:
                used.append(school)
    return dictionary, used


def build_participants(
    rng: np.random.Generator,
    has_rooms: bool,
    used_schools: list[School],
    participant_ids: list[str],
    variant_ids: list[str],
    profile: dict,
) -> list[Participant]:
    """Участники одного экзамена: школа, аудитория, вариант."""
    sizes = split_sizes(
        rng,
        len(participant_ids),
        len(used_schools),
        profile["schools"]["size_min"],
        profile["schools"]["size_max"],
    )
    rooms_per_school = [room_sizes(size, profile["room_size_max"]) for size in sizes]
    codes = make_room_codes(rng, sum(len(rooms) for rooms in rooms_per_school)) if has_rooms else []

    participants = []
    next_id = 0
    next_room = 0
    for school_index, school in enumerate(used_schools):
        seats: list[str | None] = []
        for room_size in rooms_per_school[school_index]:
            seats += [codes[next_room] if has_rooms else None] * room_size
            next_room += 1
        for room_code in seats:
            participants.append(
                Participant(
                    participant_id=participant_ids[next_id],
                    region_code=school.region_code,
                    school_code=school.school_code,
                    school_index=school_index,
                    room_code=room_code,
                    variant_id=str(rng.choice(variant_ids)),
                )
            )
            next_id += 1
    return participants
