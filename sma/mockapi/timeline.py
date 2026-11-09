"""Режим воспроизведения тестового API (раздел 9.2).

Генератор пишет timeline/{адрес}.jsonl — все версии записей с полем visible_from.
Сервер считает «текущее время экзамена» = начало экзамена + смещение +
(реальное время с запуска) × скорость и для каждой записи отдает последнюю версию
с visible_from ≤ текущему времени.
"""

import bisect
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from sma.generator.writer import endpoint_file_stem
from sma.timeutil import parse_datetime

TIMELINE_ENDPOINTS = (
    "sessions",
    "answers",
    "expert-reviews",
    "proctoring-events",
    "dictionaries/kim",
    "dictionaries/scales",
)


class ReplayClock:
    """Часы экзамена: идут в speed раз быстрее настоящих, начиная с exam_start + offset."""

    def __init__(self, exam_start: datetime, speed: float = 1.0, start_offset_sec: float = 0):
        self.exam_start = exam_start
        self.speed = speed
        self.start_offset_sec = start_offset_sec
        self.started = time.monotonic()

    def now(self) -> datetime:
        elapsed = (time.monotonic() - self.started) * self.speed
        return self.exam_start + timedelta(seconds=self.start_offset_sec + elapsed)


class Timeline:
    """Версии записей одного адреса, упорядоченные по visible_from."""

    def __init__(self, versions: list[dict]):
        pairs = sorted(
            ((parse_datetime(v["visible_from"]), v["record_id"], v) for v in versions),
            key=lambda pair: (pair[0], pair[1]),
        )
        self.moments = [moment for moment, _, _ in pairs]
        self.records = [
            {key: value for key, value in version.items() if key != "visible_from"}
            for _, _, version in pairs
        ]

    def visible(self, moment: datetime) -> dict[str, dict]:
        """record_id → последняя версия с visible_from ≤ moment."""
        latest: dict[str, dict] = {}
        for record in self.records[: bisect.bisect_right(self.moments, moment)]:
            latest[record["record_id"]] = record
        return latest


def load_timelines(folder: Path) -> dict[str, Timeline]:
    """Все timeline/*.jsonl одной платформы."""
    timelines = {}
    for endpoint in TIMELINE_ENDPOINTS:
        path = Path(folder) / "timeline" / f"{endpoint_file_stem(endpoint)}.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        timelines[endpoint] = Timeline([json.loads(line) for line in lines if line])
    return timelines
