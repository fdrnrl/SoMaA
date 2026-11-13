"""Страница «Команды» для Streamlit: контрольные точки и обновления репозиториев GitHub.

Запуск из корня проекта:  streamlit run visualization/teams_app.py
Токен GitHub (нужен только закрытым репозиториям) — в переменной окружения GITHUB_TOKEN.
"""

import os
from pathlib import Path

import streamlit as st

from visualization import teams

FOLDER = Path(__file__).parent
CACHE = str(FOLDER / "cache" / "github_cache.json")

st.set_page_config(page_title="СМА — команды", layout="wide")
st.title("Команды: контрольные точки и обновления")
table = teams.load_teams(str(FOLDER / "teams.csv"))
if st.button("Обновить с GitHub") or "commits" not in st.session_state:
    st.session_state["commits"] = teams.collect(table, CACHE, os.environ.get("GITHUB_TOKEN"))
commits = st.session_state["commits"]
st.pyplot(teams.fig_timeline(table, commits))
st.dataframe(teams.summary(table, commits), hide_index=True)
st.caption("Нет связи с GitHub — показаны последние сохраненные данные (колонка source). "
           "Статус «сдано» берется из teams.csv, а не из GitHub.")
