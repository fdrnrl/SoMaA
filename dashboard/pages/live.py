"""Живой экран платформ, загрузок и ленты аномалий."""

from datetime import datetime

from dashboard import charts, data, ui
from dashboard.presentation import EXAM_NAMES, RULE_NAMES, anomaly_feed, is_stale, time_text

from ._common import _begin, _chart_page, _optional, _plot, _read, _run_choice


def _live_read(reader, run_id):
    result = {"live": reader.live(), "freshness": reader.freshness(),
              "feed": _optional(reader.anomalies), "runs": _optional(reader.analysis_runs),
              "schools": None, "run": None}
    if run_id is not None:
        result["schools"] = _optional(lambda: reader.school_counts(run_id))
        if result["schools"]["error"] is None:
            result["run"] = reader.completed_run(run_id)
    return result


def _feed(st, mode, result):
    st.subheader("Лента аномалий")
    if result["error"]:
        st.info(result["error"])
        return
    feed = anomaly_feed(st.session_state, result["rows"], mode)
    if not feed:
        st.info("Сохраненных аномалий нет.")
        return
    for row, new in feed[:20]:
        text = ("НОВАЯ · " if new else "") + RULE_NAMES.get(row["rule"], row["rule"])
        text += " · " + row["exam_type"] + " · объект " + str(row["object_id"])
        text += " · " + time_text(row["detected_at"])
        (st.warning if new else st.info)(text)
    st.caption("Последние 20 записей; полный журнал — на странице «Аномалии». "
               "Новизна определяется по ID после первого чтения в текущем сеансе.")


def _live_body(st, service, mode):
    ui.require_user(st=st, service=service)
    before = st.session_state.get("live_run")
    result = _read(st, mode, ("live", before), lambda r: _live_read(r, before), provenance=True)
    if result is None:
        return
    run = (_run_choice(st, service, result["runs"], "live_run")
           if not result["runs"]["error"] else None)
    if run != before:
        result = _read(st, mode, ("live", run), lambda r: _live_read(r, run))
        if result is None:
            return
    if result["run"]:
        st.caption("Расчет школ " + str(run) + " · "
                   + time_text(result["run"]["finished_at"]))
        with st.expander("Параметры расчета школ", expanded=False):
            st.write(result["run"]["params"])
    st.subheader("Платформы")
    now = datetime.now(data.MSK)
    st.caption("Экран обновлен: " + time_text(now) + " · автоматически каждые 30 с.")
    live = {r["exam_type"]: r for r in result["live"]}
    schools = result["schools"]
    counts = None if schools is None or schools["error"] else {
        r["exam_type"]: r["schools"] for r in schools["rows"]}
    st.caption("Абсолютные количества из сводки. При неполной сводке платформа не сравнивается.")
    _plot(st, lambda: charts.platform_statuses(result["live"], data.EXAMS))
    for exam, column in zip(data.EXAMS, st.columns(4), strict=True):
        with column, st.container(border=True, key="sma_platform_" + exam):
            st.subheader(EXAM_NAMES[exam])
            row = live.get(exam)
            if row is None:
                st.info("Нет сводки платформы.")
                continue
            for key, label in (("in_progress", "Пишут"), ("finished", "Сдали"),
                               ("annulled", "Аннулированы"), ("absent", "Не явились")):
                st.metric(label, row[key] if row[key] is not None else "—")
            rules = row["anomalies_by_rule"]
            for rule in RULE_NAMES:
                value = rules.get(rule, 0) if rules is not None else "—"
                st.write(RULE_NAMES[rule] + ": " + str(value))
            st.metric("Школы в перечне", counts.get(exam, "—") if counts is not None else "—")
            st.caption("Сводка: " + time_text(row["generated_at"]))
            st.caption("Загрузка: " + time_text(row["last_successful_load_at"]))
            if mode == "database":
                if row["last_successful_load_at"] is None:
                    st.warning("Нет времени последней успешной загрузки.")
                elif is_stale(row["last_successful_load_at"], now):
                    st.warning("Загрузка задерживается более чем на 120 с.")
                if is_stale(row["generated_at"], now):
                    st.warning("Сводка старше 120 с.")
    st.caption("Все времена — московские; «—» означает нет сведений. "
               "Число школ относится к выбранному расчету. Отсутствие платформы в его "
               "результатах не означает нулевой счетчик.")
    if result["runs"]["error"]:
        st.info(result["runs"]["error"])
    if schools and schools["error"]:
        st.info(schools["error"])
    _feed(st, mode, result["feed"])
    st.subheader("Свежесть загрузок")
    fresh = result["freshness"]
    if not fresh:
        st.info("Нет сведений о загрузках. Статический снимок не подтверждает работу live.")
    else:
        delayed = [r for r in fresh if (r["lag_sec"] is not None and r["lag_sec"] > 120)
                   or is_stale(r["last_ok_at"], now)]
        if mode == "database" and delayed:
            st.warning("Есть источники с задержкой более 120 с; подробности в таблице.")
        ordered = sorted(fresh, key=lambda r: (r["lag_sec"] is None,
                                               -(r["lag_sec"] or 0),
                                               r["platform"], r["endpoint"]))
        visible = _chart_page(st, service, ordered, "live_freshness_page",
                              label="Источники на графике")
        st.caption("Задержки больше 120 с выделены цветом. Неизвестное время не означает ноль.")
        _plot(st, lambda: charts.freshness_delays(visible))
        st.dataframe(fresh, hide_index=True)


def live_page(st, service, mode):
    _begin(st, service, "Живой экран", mode)

    @st.fragment(run_every="30s")
    def refresh():
        _live_body(st, service, mode)

    refresh()
