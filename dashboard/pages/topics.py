"""Страница тем кодификатора и вариантов заданий."""

from dashboard import charts, ui
from dashboard.presentation import variant_gaps

from ._common import _begin, _chart_page, _choice, _filters, _plot, _read


def topics_page(st, service, mode):
    _begin(st, service, "Темы и варианты", mode)
    selected = _filters(st, service, mode, "topics")
    if selected is None:
        return
    exam, subject, _ = selected
    result = _read(st, mode, ("topics", exam, subject),
                   lambda r: {"topics": r.topics(exam, subject),
                              "variants": r.variants(exam, subject)}, provenance=True)
    if result is None:
        return
    st.info("Отбор pending для тем и вариантов еще не согласован командой (пункт 09). "
            "Показывается доступный набор; выводы требуют этого согласования.")
    st.subheader("Разделы и коды содержания")
    topics = result["topics"]
    if not topics:
        st.info("Нет сведений о темах: КИМ еще не опубликованы или нет подходящих данных.")
    else:
        years = sorted({r["exam_year"] for r in topics})
        year = _choice(st, service, "Год КИМ", years, "topics_year")
        year_rows = [r for r in topics if r["exam_year"] == year]
        sections = sorted({r["section"] for r in year_rows})
        section = _choice(st, service, "Раздел", sections, "topics_section")
        chosen = [r for r in year_rows if r["section"] == section]
        valid = [r for r in chosen if r["mean_share_score"] is not None]
        if valid:
            _plot(st, lambda: charts.bars(
                [str(r["content_code"]) + " · уровень " + str(r["level"]) for r in valid],
                [r["mean_share_score"] * 100 for r in valid], "Средняя доля баллов, %"))
        else:
            st.info("КИМ опубликованы, но проверенных ответов для выбранного раздела нет.")
        st.dataframe(chosen, hide_index=True)
    st.subheader("Варианты по заданиям")
    variants = result["variants"]
    if not variants:
        st.info("Нет проверенных результатов для сравнения вариантов.")
        return
    gaps = variant_gaps(variants)
    flagged = {r["task_number"] for r in gaps if r["flagged"]}
    if flagged:
        st.warning("Разница между вариантами >15 п. п. в заданиях: "
                   + ", ".join(map(str, sorted(flagged))))
    else:
        st.info("В доступных сравнениях нет разницы >15 п. п. Для сравнения нужны ≥2 варианта.")
    view = st.radio("Сравнение вариантов", ("Тепловая карта", "Столбцы"), horizontal=True,
                    key="topics_view", on_change=ui.user_activity,
                    kwargs={"st": st, "service": service})
    tasks = _chart_page(st, service, sorted({r["task_number"] for r in variants}),
                        "topics_tasks_page", size=20, label="Задания на графике")
    identifiers = _chart_page(st, service, sorted({r["variant_id"] for r in variants}),
                              "topics_variants_page", label="Варианты на графике")
    visible = [r for r in variants if r["task_number"] in tasks
               and r["variant_id"] in identifiers]
    st.caption("Отметка * учитывает все доступные варианты, включая другие части графика.")
    if view == "Тепловая карта":
        st.caption("Ячейка — средняя доля баллов, %. Серый цвет и «—» — нет проверенных ответов.")
        _plot(st, lambda: charts.variants_heatmap(visible, flagged,
                                                 tasks=tasks, variants_=identifiers))
    else:
        if visible:
            _plot(st, lambda: charts.variants(visible, flagged))
        else:
            st.info("Для выбранных заданий и вариантов нет пересекающихся результатов.")
    st.dataframe(gaps, hide_index=True)
    st.dataframe(variants, hide_index=True)
