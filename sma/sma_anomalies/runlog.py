"""Журнал запусков углубленного анализа (NFR-24, T-41) и планировщик запусков (FR-30)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from .params import Params

ENDED_STATUSES = ("finished", "annulled", "absent")


def exam_is_complete(sessions: pd.DataFrame) -> bool:
    """FR-30: все сессии экзамена получили статус finished, annulled или absent,
    а у завершенных result_status не равен pending. Удаленные записи не учитываются."""
    s = sessions
    if "deleted" in s.columns:
        s = s[~s["deleted"].astype(bool)]
    if s.empty:
        return False
    if not s["status"].isin(ENDED_STATUSES).all():
        return False
    return not (s.loc[s["status"] == "finished", "result_status"] == "pending").any()


@dataclass
class Scheduler:
    """FR-30: во время экзамена — запуск каждые 10 минут, и один запуск после завершения экзамена.

    tick(now, sessions) -> причина запуска ("periodic", "final") или None; вызывается загрузчиком/оркестратором.
    """
    interval: timedelta = timedelta(minutes=10)
    last_run: datetime | None = None
    final_done: bool = False

    def tick(self, now: datetime, sessions: pd.DataFrame) -> str | None:
        if self.final_done:
            return None
        if exam_is_complete(sessions):
            self.final_done, self.last_run = True, now
            return "final"
        if self.last_run is None or now - self.last_run >= self.interval:
            self.last_run = now
            return "periodic"
        return None


@dataclass
class RunLog:
    """Журнал запусков в JSON Lines: run_id, начало, конец, причина и все параметры таблицы 13."""
    path: str
    _next: int = field(default=1, init=False)

    def __post_init__(self):
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                ids = [json.loads(line)["run_id"] for line in fh if line.strip()]
            self._next = max(ids, default=0) + 1

    def start(self, started_at: datetime, reason: str, params: Params) -> dict:
        rec = {"run_id": self._next, "started_at": started_at.isoformat(), "finished_at": None,
               "reason": reason, "params": params.as_dict()}
        self._next += 1
        return rec

    def finish(self, rec: dict, finished_at: datetime, **summary) -> dict:
        rec["finished_at"] = finished_at.isoformat()
        rec.update(summary)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def read(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
