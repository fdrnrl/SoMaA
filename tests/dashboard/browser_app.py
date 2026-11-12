"""Изолированный стенд UI: только синтетика и тестовые аккаунты, не рабочий вход."""

from datetime import datetime

import streamlit as st
from test_auth import MemoryAccounts, account
from test_streamlit_pages import SavedAnalytics

from dashboard import app, auth, data
from dashboard.demo import DemoData


@st.cache_resource
def test_service():
    return auth.AuthService(MemoryAccounts(account(), account("admin", "admin")))


class BrowserData(DemoData):
    anomalies = SavedAnalytics.anomalies
    analysis_runs = SavedAnalytics.analysis_runs
    completed_run = SavedAnalytics.completed_run
    p1_groups = SavedAnalytics.p1_groups
    school_list = SavedAnalytics.school_list
    school_counts = SavedAnalytics.school_counts
    empty = False


def read_test_page(key, loader, *, mode="database", **kwargs):
    if mode != "demo":
        raise data.DataUnavailable("Этот тестовый стенд читает только синтетический снимок.")
    reader = BrowserData(data.DEFAULT_DEMO)
    reader.metadata["note"] += " Аналитические строки — тестовые примеры UI, не результат правил."
    return {"data": loader(reader), "source": reader.metadata}


if __name__ == "__main__":
    data.read_page = read_test_page
    st.session_state.setdefault("_sma_data_mode", "demo")
    app.main(st, test_service())
    st.sidebar.warning("ТЕСТОВЫЙ СТЕНД · синтетика · без подключения к БД")
    st.sidebar.caption("Серверное время: " + datetime.now(data.MSK).isoformat())
