"""Страница карты и показателей регионов."""

from dashboard import charts, data, geo, ui

from ._common import _begin, _chart_page, _filters, _plot, _read


def regions_page(st, service, mode):
    _begin(st, service, "Регионы", mode)
    selected = _filters(st, service, mode, "regions")
    if selected is None:
        return
    exam, subject, _ = selected
    rows = _read(st, mode, ("regions", exam, subject),
                 lambda r: r.region_results([exam], subject), provenance=True)
    if rows is None:
        return
    if not rows:
        st.info("Нет окончательных региональных результатов для выбранных фильтров.")
    view = st.radio("Представление регионов", ("Карта", "Рейтинг"), horizontal=True,
                    key="regions_view", on_change=ui.user_activity,
                    kwargs={"st": st, "service": service})
    if view == "Карта":
        st.caption(geo.MAP_NOTE)
        st.markdown("[Источник и лицензия контуров](https://www.openstreetmap.org/copyright)")
        try:
            features = geo.load_regions()
            missing = geo.unmapped_codes(rows, features)
            if missing:
                st.warning("Не сопоставлены с геометрией коды: " + ", ".join(missing)
                           + ". Значения сохранены в таблице.")
            _plot(st, lambda: charts.regions(rows, features))
        except data.DataUnavailable as error:
            st.warning(str(error))
    else:
        valid = sorted((r for r in rows if r["mean_pct"] is not None and r["participants"] > 0),
                       key=lambda r: (-r["mean_pct"], r["region_code"]))
        if valid:
            visible = _chart_page(st, service, valid, "regions_chart_page",
                                  label="Регионы в рейтинге")
            st.caption("n — число участников. Пунктир — среднее по всей выбранной группе "
                       "с весами по числу участников. Нет значения — регион не ранжируется.")
            _plot(st, lambda: charts.region_ranking(visible, valid))
        else:
            st.info("Нет результатов для рейтинга регионов.")
    st.dataframe(rows, hide_index=True)
