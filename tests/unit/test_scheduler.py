"""Планировщик загрузчика на «быстрых часах»: 10 минут проходят за доли секунды (T-01, T-24)."""

import threading
from pathlib import Path

import pytest

from sma.loader.cycle import ALL_ENDPOINTS, FACT_ENDPOINTS, EndpointResult, LoaderSettings
from sma.loader.run import PlatformJob, endpoint_intervals, load_config, run_platform

CONFIG = load_config(Path(__file__).resolve().parents[2] / "config" / "platforms.yaml")
MINUTES_10 = 600


class FakeClock:
    """Часы, которые идут только когда планировщик «спит» или загрузка «работает»."""

    def __init__(self, end: float):
        self.t = 0.0
        self.end = end

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float, stop: threading.Event) -> None:
        self.t += seconds
        if self.t >= self.end:
            stop.set()


def recording_load(clock: FakeClock, calls: list, failing: set[str] = frozenset()):
    """Вместо настоящей загрузки — запись (время, адрес); каждый запрос «длится» 0,5 с."""

    def load(conn, source, platform, endpoint, settings, should_stop):
        calls.append((clock.now(), endpoint))
        assert callable(should_stop)
        clock.t += 0.5
        status = "error" if endpoint in failing else "ok"
        return EndpointResult(platform, endpoint, status=status)

    return load


def run(intervals: dict[str, float], seconds: float, failing: set[str] = frozenset()) -> list:
    clock = FakeClock(seconds)
    calls: list = []
    job = PlatformJob("OGE", source=None, intervals=intervals, settings=LoaderSettings())
    stop = threading.Event()
    run_platform(job, None, stop, clock, recording_load(clock, calls, failing))
    return calls


def times_of(calls: list, endpoint: str) -> list[float]:
    return [moment for moment, name in calls if name == endpoint]


@pytest.mark.parametrize("endpoint", FACT_ENDPOINTS)
def test_request_intervals(endpoint):
    """T-01: при интервале 60 с за 10 минут — 10 ± 1 запрос к адресу."""
    calls = run(endpoint_intervals(CONFIG), MINUTES_10)
    assert 9 <= len(times_of(calls, endpoint)) <= 11


def test_other_intervals():
    calls = run(endpoint_intervals(CONFIG), MINUTES_10)
    assert 19 <= len(times_of(calls, "live-status")) <= 21, "раз в 30 с"
    assert len(times_of(calls, "dictionaries/kim")) == 1, "раз в 600 с"
    assert len(times_of(calls, "dictionaries/regions")) == 1, "раз в 3600 с"


def test_every_endpoint_is_polled_at_start():
    calls = run(endpoint_intervals(CONFIG), 1)
    assert [name for _, name in calls] == list(ALL_ENDPOINTS)


def test_min_gap_per_endpoint():
    """T-24 (NFR-04): даже при интервале 1 с — к одному адресу не чаще раза в 10 с."""
    config = {**CONFIG, "intervals_sec": {key: 1 for key in CONFIG["intervals_sec"]}}
    intervals = endpoint_intervals(config)
    assert set(intervals.values()) == {10.0}
    calls = run(intervals, MINUTES_10)
    for endpoint in ALL_ENDPOINTS:
        moments = times_of(calls, endpoint)
        gaps = [later - earlier for earlier, later in zip(moments, moments[1:], strict=False)]
        assert gaps and min(gaps) >= 10, endpoint


def test_error_is_retried_after_min_gap():
    """Справочник с ошибкой повторяется через 10 с, а не через час."""
    calls = run(endpoint_intervals(CONFIG), 60, failing={"dictionaries/regions"})
    assert len(times_of(calls, "dictionaries/regions")) >= 5
    assert len(times_of(calls, "dictionaries/schools")) == 1


def test_stop_before_start_does_nothing():
    stop = threading.Event()
    stop.set()
    calls: list = []
    clock = FakeClock(100)
    job = PlatformJob("OGE", None, endpoint_intervals(CONFIG), LoaderSettings())
    run_platform(job, None, stop, clock, recording_load(clock, calls))
    assert calls == []


def test_stop_during_cycle_finishes_current_and_exits():
    stop = threading.Event()
    calls: list = []
    clock = FakeClock(10_000)

    def load(conn, source, platform, endpoint, settings, should_stop):
        calls.append(endpoint)
        if endpoint == "sessions":
            stop.set()  # «сигнал» пришел во время загрузки sessions
        return EndpointResult(platform, endpoint)

    job = PlatformJob("OGE", None, endpoint_intervals(CONFIG), LoaderSettings())
    run_platform(job, None, stop, clock, load)
    assert calls[-1] == "sessions"
