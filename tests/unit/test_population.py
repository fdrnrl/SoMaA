"""Население: псевдонимы, размеры школ и аудиторий, коды аудиторий (раздел 10.4)."""

import math
import re

import numpy as np
import pytest

from sma.generator.population import (
    make_participant_ids,
    make_room_codes,
    room_sizes,
    split_sizes,
)


def rng(seed=1):
    return np.random.default_rng(seed)


def test_participant_ids_format_and_unique():
    ids = make_participant_ids(rng(), 2000)
    assert len(ids) == 2000
    assert len(set(ids)) == 2000
    for pid in ids:
        assert type(pid) is str
        assert re.fullmatch(r"p_[0-9a-f]{6}", pid), pid


def test_participant_ids_deterministic():
    assert make_participant_ids(rng(5), 50) == make_participant_ids(rng(5), 50)
    assert make_participant_ids(rng(5), 50) != make_participant_ids(rng(6), 50)


@pytest.mark.parametrize(
    "total, n_groups, size_min, size_max",
    [(40, 8, 4, 6), (430, 40, 10, 12), (8, 8, 1, 1), (60, 10, 4, 6)],
)
def test_split_sizes(total, n_groups, size_min, size_max):
    sizes = split_sizes(rng(), total, n_groups, size_min, size_max)
    assert len(sizes) == n_groups
    assert sum(sizes) == total
    assert all(size_min <= size <= size_max for size in sizes)
    assert all(type(size) is int for size in sizes)


def test_split_sizes_is_random():
    results = {tuple(split_sizes(rng(seed), 40, 8, 4, 6)) for seed in range(10)}
    assert len(results) > 1


@pytest.mark.parametrize("total", [10, 100])
def test_split_sizes_impossible(total):
    with pytest.raises(ValueError):
        split_sizes(rng(), total, 8, 4, 6)


@pytest.mark.parametrize(
    "n_students, expected",
    [(0, []), (10, [10]), (15, [15]), (16, [8, 8]), (31, [11, 10, 10]), (45, [15, 15, 15])],
)
def test_room_sizes_examples(n_students, expected):
    assert sorted(room_sizes(n_students, 15), reverse=True) == expected


def test_room_sizes_rules():
    for n_students in range(1, 101):
        sizes = room_sizes(n_students, 15)
        assert sum(sizes) == n_students
        assert len(sizes) == math.ceil(n_students / 15)
        assert max(sizes) <= 15
        assert max(sizes) - min(sizes) <= 1


def test_room_codes():
    codes = make_room_codes(rng(), 300)
    assert len(codes) == 300
    assert len(set(codes)) == 300
    for code in codes:
        assert type(code) is str
        assert re.fullmatch(r"\d{4}", code), code
