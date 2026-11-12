"""Страница сравнения результатов экзаменов."""

from dashboard import charts, data, ui
from dashboard.presentation import EXAM_NAMES, comparison_rows

from ._common import _begin, _chart_page, _plot, _read


def comparison_page(st, service, mode):
    _begin(st, service, "Сравнение экзаменов", mode)
    exams = st.multiselect("Экзамены", data.EXAMS, default=list(data.EXAMS),
                           format_func=EXAM_NAMES.get, key="comparison_exams",
                           on_change=ui.user_activity, kwargs={"st": st, "service": service})
    if not exams:
        st.info("Выберите хотя бы один экзамен.")
        return
    rows = _read(st, mode, ("comparison", tuple(exams)),
                 lambda r: r.region_results(exams), provenance=True)
    if rows is None:
        return
    if not rows:
        st.info("Нет региональных результатов по выбранным экзаменам.")
        return
    combined = comparison_rows(rows)
    st.caption("Все предметы каждой платформы; средний процент взвешен числом участников. "
               "Пропуск означает отсутствие данных; состав предметов может различаться.")
    view = st.radio("Вид сравнения", ("Тепловая карта", "Столбцы"), horizontal=True,
                    key="comparison_view", on_change=ui.user_activity,
                    kwargs={"st": st, "service": service})
    codes = _chart_page(st, service, sorted({r["region_code"] for r in combined}),
                        "comparison_chart_page", label="Регионы на графике")
    visible = [r for r in combined if r["region_code"] in codes]
    if view == "Тепловая карта":
        st.caption("Цвет и число в ячейке — среднее выполнение, %. Серый цвет и «—» — нет данных.")
        _plot(st, lambda: charts.comparison_heatmap(visible, exams))
    else:
        _plot(st, lambda: charts.comparison(visible, exams))
    st.dataframe(combined, hide_index=True)
