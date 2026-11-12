"""Страница долей отметки два по регионам."""

from dashboard import charts

from ._common import _begin, _filters, _plot, _read


def grades_page(st, service, mode):
    _begin(st, service, "Отметки", mode)
    selected = _filters(st, service, mode, "grades", exams=("OGE", "VPR"))
    if selected is None:
        return
    exam, subject, _ = selected
    rows = _read(st, mode, ("grades", exam, subject),
                 lambda r: r.region_results([exam], subject), provenance=True)
    if rows is None:
        return
    valid = [r for r in rows if r["share_grade2"] is not None]
    if not valid:
        st.info("Нет сведений об отметке «2» для выбранных фильтров.")
        return
    st.caption("Доля отметки «2» среди окончательно сдавших. Отметки получены из источника.")
    _plot(st, lambda: charts.bars(
        [r["region_code"] + " · " + (r["region_name"] or "") for r in valid],
        [r["share_grade2"] * 100 for r in valid], "Отметка «2», % участников"))
    st.dataframe(rows, hide_index=True)
