"""normalize_answer (раздел 8.6)."""

import pytest

from sma.normalize import normalize_answer

YO = "ё"  # е с двумя точками, строчная
YO_UPPER = "Ё"


@pytest.mark.parametrize(
    "answer, expected",
    [
        (" 0.25", "0,25"),
        ("0,25", "0,25"),
        ("Пришел", "пришел"),
        ("", ""),
        ("   ", ""),
        (f"{YO}лка", "елка"),
        (f"{YO_UPPER}ЛКА", "елка"),
        ("1 2\t3\n", "123"),
        ("ABC", "abc"),
        ("3.5.1", "3,5,1"),
    ],
)
def test_normalize_examples(answer, expected):
    assert normalize_answer(answer) == expected


@pytest.mark.parametrize("answer", [" 0.25", "Пришел", f"{YO_UPPER}лка", "", " a b. C "])
def test_normalize_idempotent(answer):
    once = normalize_answer(answer)
    assert normalize_answer(once) == once


def test_normalize_returns_str():
    assert type(normalize_answer(" 7")) is str
