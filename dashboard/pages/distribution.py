"""Страница распределения окончательных баллов."""

from dashboard import charts, ui
from dashboard.presentation import distribution_stats, score_text

from ._common import _begin, _filters, _plot, _read


def distribution_page(st, service, mode):
    _begin(st, service, "Распределение", mode)
    selected = _filters(st, service, mode, "distribution", region=True, compact=True)
    if selected is None:
        return
    rows = _read(st, mode, ("distribution", selected), lambda r: r.distribution(*selected),
                 provenance=True)
    if rows is None:
        return
    st.caption("Окончательные результаты, включая апелляции. "
               "Показатели учитывают число участников на каждом балле.")
    stats = distribution_stats(rows)
    if not stats["participants"]:
        st.info("Нет окончательных результатов для выбранных фильтров.")
        return
    modes = stats["modes"]
    mode_value = "; ".join(score_text(score) for score in modes[:3]) if modes else "Нет"
    if len(modes) > 3:
        mode_value += "; …"
    mode_help = ("Наиболее частые баллы: " + "; ".join(score_text(score) for score in modes)
                 if modes else "Все представленные баллы встречаются одинаково часто.")
    metrics = (
        ("Участники", stats["participants"], "Число работ в выбранной группе."),
        ("Средний балл", score_text(stats["mean"]),
         "Сумма баллов всех участников, деленная на их число."),
        ("Медиана", score_text(stats["median"]),
         "Середина упорядоченных результатов. При четном числе работ — среднее двух центральных."),
        ("Мода", mode_value, mode_help),
    )
    for column, (label, value, help_text) in zip(st.columns(4), metrics, strict=True):
        column.metric(label, value, help=help_text)
    view = st.radio("Вид распределения", ("Частоты баллов", "Накопленная доля"),
                    horizontal=True, key="distribution_view", on_change=ui.user_activity,
                    kwargs={"st": st, "service": service})
    if view == "Накопленная доля":
        st.caption("Доля участников, получивших не больше указанного первичного балла.")
        _plot(st, lambda: charts.cumulative_distribution(rows))
    else:
        _plot(st, lambda: charts.distribution(rows))
    st.subheader("Частоты баллов")
    st.dataframe(rows, hide_index=True, column_config={
        "primary_score": "Первичный балл", "participants": "Участники",
        "max_primary_score": "Максимальный балл",
    })
