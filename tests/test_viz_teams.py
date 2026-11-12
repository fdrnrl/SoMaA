"""Тесты страницы «Команды» без сети: подставной ответ GitHub."""

import tempfile
from pathlib import Path

import pandas as pd

from visualization import teams


class Reply:
    def __init__(self, code, payload):
        self.status_code = code
        self._payload = payload

    def json(self):
        return self._payload


class Good:
    @staticmethod
    def get(url, **kwargs):
        return Reply(200, [{"commit": {"committer": {"date": "2026-11-08T10:00:00Z"}}}])


class Down:
    @staticmethod
    def get(url, **kwargs):
        raise ConnectionError


def _teams():
    return pd.DataFrame([{
        "team": "A", "repo_url": "https://github.com/o/r", "tz_date": pd.Timestamp("2026-11-16"),
        "tz_done": "нет", "prototype_date": pd.Timestamp("2026-11-23"), "prototype_done": "да",
    }])


def _cache():
    return str(Path(tempfile.mkdtemp()) / "cache.json")


def test_parse_repo():
    assert teams.parse_repo("https://github.com/o/r.git") == ("o", "r")
    assert teams.parse_repo("https://example.com/o/r") is None


def test_checkpoint_status():
    date = pd.Timestamp("2026-11-16")
    assert teams.checkpoint_status(date, "да", "2026-11-20") == "сдано"
    assert teams.checkpoint_status(date, "нет", "2026-11-09") == "впереди, через 7 дн."
    assert teams.checkpoint_status(date, "нет", "2026-11-18") == "просрочено на 2 дн."
    assert teams.checkpoint_status(pd.NaT, "", "2026-11-18") == "дата не задана"


def test_cache_used_when_offline():
    path = _cache()
    teams.collect(_teams(), path, session=Good)
    result = teams.collect(_teams(), path, session=Down)
    assert result.source.iloc[0].startswith("кэш")
    assert result.commit_at.notna().all()


def test_no_cache_and_offline_gives_empty_row_with_error():
    result = teams.collect(_teams(), _cache(), session=Down)
    assert result.source.iloc[0] == "нет данных"
    assert "нет связи" in result.error.iloc[0]
    assert teams.summary(_teams(), result, today="2026-11-09").commits.iloc[0] == 0


def test_summary_days_since_uses_moscow_time():
    commits = teams.collect(_teams(), _cache(), session=Good)
    row = teams.summary(_teams(), commits, today="2026-11-09").iloc[0]
    assert row.days_since == 1


def test_timeline_builds():
    commits = teams.collect(_teams(), _cache(), session=Good)
    assert teams.fig_timeline(_teams(), commits, today="2026-11-09").axes
