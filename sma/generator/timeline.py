"""Версии записей во времени и снимки набора (раздел 10.6).

Версия — полная запись контракта; ее updated_at — момент, когда версия становится
видимой на платформе (visible_from). Снимок на момент T — последняя версия каждой
записи с updated_at ≤ T.
"""

from datetime import datetime, time, timedelta

from sma.timeutil import parse_datetime

SNAPSHOTS = ("during_exam", "after_exam", "after_check", "after_appeal")
IN_PROGRESS = ("started", "in_progress")


def snapshot_moments(exam_date, exam_start: datetime, exam_end: datetime, msk) -> dict:
    """Моменты снимков: середина экзамена, сразу после него, после проверки, после апелляций."""
    return {
        "during_exam": exam_start + (exam_end - exam_start) / 2,
        "after_exam": exam_end + timedelta(minutes=1),
        "after_check": datetime.combine(exam_date, time(18, 30), tzinfo=msk),
        "after_appeal": datetime.combine(exam_date + timedelta(days=6), time(12, 0), tzinfo=msk),
    }


def version_order(record: dict) -> tuple[datetime, str]:
    return parse_datetime(record["updated_at"]), record["record_id"]


def snapshot(versions: list[dict], moment: datetime | None = None) -> list[dict]:
    """Последняя версия каждой записи на момент moment (None — самая последняя)."""
    latest: dict[str, dict] = {}
    for record in sorted(versions, key=version_order):
        if moment is not None and parse_datetime(record["updated_at"]) > moment:
            break
        latest[record["record_id"]] = record
    return list(latest.values())


def live_status(exam_type: str, sessions: list[dict], moment: datetime) -> dict:
    """Сводка /live-status по видимым сессиям: started и in_progress — «идет»."""
    statuses = [session["status"] for session in sessions]
    return {
        "exam_type": exam_type,
        "in_progress": sum(status in IN_PROGRESS for status in statuses),
        "finished": statuses.count("finished"),
        "annulled": statuses.count("annulled"),
        "absent": statuses.count("absent"),
        "generated_at": moment.isoformat(timespec="seconds"),
    }
