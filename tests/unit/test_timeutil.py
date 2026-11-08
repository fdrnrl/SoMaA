"""Разбор и сравнение времени со смещением (sma/timeutil.py)."""

from datetime import timedelta

import pytest

from sma.timeutil import parse_datetime, try_parse_datetime, utc_now


def test_timeutil_offsets():
    """+03:00 и +07:00 сравниваются как моменты времени, а не как строки."""
    moscow = parse_datetime("2026-12-05T10:00:00+03:00")
    novosibirsk = parse_datetime("2026-12-05T14:00:00+07:00")
    assert moscow == novosibirsk
    later = parse_datetime("2026-12-05T13:00:00+07:00") + timedelta(hours=2)
    assert later > moscow


def test_utc_suffix_z():
    assert parse_datetime("2026-12-05T07:00:00Z") == parse_datetime("2026-12-05T10:00:00+03:00")


@pytest.mark.parametrize(
    "value", ["2026-12-05T10:00:00", "2026-12-05", "вчера", "", 1733389200, None]
)
def test_bad_values_raise(value):
    with pytest.raises(ValueError):
        parse_datetime(value)
    assert try_parse_datetime(value) is None


def test_utc_now_is_aware():
    assert utc_now().utcoffset() == timedelta(0)
