"""Дашборд команд: контрольные точки (ТЗ, прототип) и даты обновлений репозиториев GitHub.

Команды описаны в visualization/teams.csv: team, repo_url, tz_date, tz_done,
prototype_date, prototype_done (даты ГГГГ-ММ-ДД, *_done — «да» или «нет»).
Коммиты берутся через публичный API GitHub; при ошибке сети используется последняя сохраненная
копия (cache_path). Токен — только из переменной окружения GITHUB_TOKEN.
"""

import json
import os
import re
from datetime import UTC, datetime

import numpy as np
import pandas as pd

DEFENSE_DATES = {"защита 28.11": "2026-11-28", "защита 05.12": "2026-12-05"}
MSK = "Europe/Moscow"
INK = "#1a1a1a"
BLUE = "#145DA0"
GREEN = "#287A48"
RED = "#B00020"


def today_msk() -> pd.Timestamp:
    """Сегодняшний день по Москве, без времени и без часового пояса."""
    return pd.Timestamp.now(tz=MSK).tz_localize(None).normalize()


def load_teams(path: str) -> pd.DataFrame:
    teams = pd.read_csv(path, dtype=str).fillna("")
    for column in ("tz_date", "prototype_date"):
        teams[column] = pd.to_datetime(teams[column], errors="coerce")
    return teams


def parse_repo(url: str) -> tuple[str, str] | None:
    found = re.match(r"^https?://github\.com/([^/\s]+)/([^/\s#?]+?)(?:\.git)?/?$", url.strip())
    return (found.group(1), found.group(2)) if found else None


def fetch_commits(repo_url: str, token: str | None = None, session=None,
                  max_pages: int = 5) -> list[str]:
    """Даты коммитов (ISO, UTC). При сбое бросает RuntimeError с понятным текстом."""
    repo = parse_repo(repo_url)
    if not repo:
        raise RuntimeError("ссылка не похожа на https://github.com/владелец/репозиторий")
    import requests

    client = session or requests
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    dates: list[str] = []
    for page in range(1, max_pages + 1):
        try:
            reply = client.get(f"https://api.github.com/repos/{repo[0]}/{repo[1]}/commits",
                               params={"per_page": 100, "page": page}, headers=headers,
                               timeout=10)
        except Exception as error:
            raise RuntimeError(f"нет связи с GitHub: {type(error).__name__}") from error
        if reply.status_code == 404:
            raise RuntimeError("репозиторий не найден или закрыт (нужен токен)")
        if reply.status_code in (401, 403):
            raise RuntimeError("доступ запрещен или исчерпан лимит запросов (задайте GITHUB_TOKEN)")
        if reply.status_code != 200:
            raise RuntimeError(f"GitHub ответил {reply.status_code}")
        items = reply.json()
        dates += [item["commit"]["committer"]["date"] for item in items]
        if len(items) < 100:
            break
    return dates


def collect(teams: pd.DataFrame, cache_path: str, token: str | None = None,
            session=None) -> pd.DataFrame:
    """Коммиты всех команд: team, commit_at, source, error.

    Успешный ответ пишется в кэш; при ошибке берется запись команды из кэша (source «кэш от …»).
    """
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as handle:
            cache = json.load(handle)
    updated = dict(cache)
    rows = []
    for _, team in teams.iterrows():
        name = team["team"]
        try:
            dates = fetch_commits(team["repo_url"], token, session)
            updated[name] = {"fetched_at": datetime.now(UTC).isoformat(), "commits": dates}
            rows += [(name, d, "GitHub", "") for d in dates] or [(name, None, "GitHub", "")]
        except RuntimeError as error:
            saved = cache.get(name)
            if saved:
                source = f"кэш от {saved['fetched_at'][:16]}"
                rows += [(name, d, source, str(error)) for d in saved["commits"]]
            else:
                rows.append((name, None, "нет данных", str(error)))
    if updated != cache:
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as handle:
            json.dump(updated, handle, ensure_ascii=False)
    commits = pd.DataFrame(rows, columns=["team", "commit_at", "source", "error"])
    commits["commit_at"] = pd.to_datetime(commits["commit_at"], utc=True, errors="coerce")
    return commits


def checkpoint_status(date, done: str, today) -> str:
    if pd.isna(date):
        return "дата не задана"
    if str(done).strip().lower() == "да":
        return "сдано"
    days = (pd.Timestamp(date).normalize() - pd.Timestamp(today).normalize()).days
    return f"впереди, через {days} дн." if days >= 0 else f"просрочено на {-days} дн."


def _msk_naive(stamps: pd.Series) -> pd.Series:
    return stamps.dt.tz_convert(MSK).dt.tz_localize(None)


def summary(teams: pd.DataFrame, commits: pd.DataFrame, today=None) -> pd.DataFrame:
    """Строка на команду: последнее обновление (МСК), дней с него, коммитов, статусы точек."""
    today = pd.Timestamp(today) if today is not None else today_msk()
    rows = []
    for _, team in teams.iterrows():
        own = commits[commits["team"] == team["team"]]
        dated = own.dropna(subset=["commit_at"])
        last = _msk_naive(dated["commit_at"]).max() if len(dated) else pd.NaT
        rows.append({
            "team": team["team"],
            "last_commit": last,
            "days_since": (today.normalize() - last.normalize()).days if pd.notna(last) else np.nan,
            "commits": len(dated),
            "tz": checkpoint_status(team["tz_date"], team["tz_done"], today),
            "prototype": checkpoint_status(team["prototype_date"], team["prototype_done"], today),
            "source": own["source"].iloc[0] if len(own) else "нет данных",
            "error": own["error"].iloc[0] if len(own) else "",
        })
    return pd.DataFrame(rows)


def fig_timeline(teams: pd.DataFrame, commits: pd.DataFrame, today=None,
                 title: str = "Контрольные точки и обновления репозиториев"):
    """Лента: строка — команда; точки — коммиты, ромб — срок ТЗ, квадрат — срок прототипа."""
    import matplotlib.dates as mdates
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D

    today = pd.Timestamp(today) if today is not None else today_msk()
    fig = Figure(figsize=(11, max(3.2, 0.9 * len(teams) + 1.6)))
    ax = fig.subplots()
    names = list(teams["team"])
    for _, team in teams.iterrows():
        row = names.index(team["team"])
        own = commits[commits["team"] == team["team"]].dropna(subset=["commit_at"])
        if len(own):
            ax.scatter(_msk_naive(own["commit_at"]), [row] * len(own), s=22, color=BLUE,
                       alpha=0.55, zorder=3)
        for column, marker, done in (("tz_date", "D", team["tz_done"]),
                                     ("prototype_date", "s", team["prototype_done"])):
            if pd.notna(team[column]):
                ready = str(done).strip().lower() == "да"
                ax.scatter([team[column]], [row], marker=marker, s=110, zorder=4,
                           edgecolor=INK, linewidth=1.4, color=GREEN if ready else "white")
    for label, day in DEFENSE_DATES.items():
        ax.axvline(pd.Timestamp(day), color=RED, linestyle="--", linewidth=1.4)
        ax.text(pd.Timestamp(day), len(names) - 0.45, label, color=RED, fontsize=8,
                rotation=90, va="top", ha="right")
    ax.axvline(today, color=INK, linewidth=1.2)
    ax.text(today, -0.7, "сегодня", fontsize=8, ha="center", va="top")
    ax.set_yticks(range(len(names)), names)
    ax.set_ylim(-0.9, len(names) - 0.3)
    ax.invert_yaxis()
    ax.set_title(title, loc="left", fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=3))
    ax.tick_params(axis="x", labelsize=8)
    handles = [
        Line2D([], [], marker="o", linestyle="", color=BLUE, label="коммит"),
        Line2D([], [], marker="D", linestyle="", mfc="white", mec=INK, ms=9, label="срок ТЗ"),
        Line2D([], [], marker="s", linestyle="", mfc="white", mec=INK, ms=9,
               label="срок прототипа"),
        Line2D([], [], marker="D", linestyle="", mfc=GREEN, mec=INK, ms=9,
               label="точка сдана (залита зеленым)"),
    ]
    fig.legend(handles=handles, loc="lower center", ncols=4, frameon=False, fontsize=8)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    return fig
