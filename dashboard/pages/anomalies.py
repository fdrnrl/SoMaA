"""Страница сохраненных аномалий и их выгрузки."""

from dashboard import data, export

from ._common import _begin, _download, _read


def anomalies_page(st, service, mode):
    _begin(st, service, "Аномалии", mode)
    rows = _read(st, mode, "anomalies", lambda r: r.anomalies(), provenance=True)
    if rows is None:
        return
    st.caption("Все сохраненные аномалии, новые записи первыми. Поля раздела 7.2 ТЗ.")
    if not rows:
        st.info("Сохраненных аномалий нет.")
    st.dataframe([{name: row[name] for name in data.ANOMALY_COLUMNS} for row in rows],
                 hide_index=True)
    _download(st, service, mode, rows, export.anomalies_csv, "anomalies.csv", "anomalies_csv")
