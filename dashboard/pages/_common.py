"""Общие элементы отображения и чтения данных страниц."""

from contextlib import nullcontext

from dashboard import data, ui
from dashboard.presentation import EXAM_NAMES, time_text


def _begin(st, service, title, mode):
    ui.require_user(st=st, service=service)
    st.title(title)
    if mode == "demo":
        st.warning("Синтетические данные · статический снимок. Выводы ограничены генератором.")
    elif data.database_is_synthetic():
        st.warning("Синтетические данные в БД. Выводы ограничены свойствами генератора.")
    else:
        st.caption("Источник: база данных. Происхождение набора уточняется у команды.")


def _read(st, mode, key, loader, *, provenance=False):
    try:
        page = data.read_page(key, loader, mode=mode)
    except data.DataUnavailable as error:
        st.warning(str(error))
        return None
    if provenance:
        source = page["source"]
        st.caption(source["note"])
        if source.get("snapshot"):
            st.caption("Снимок: " + str(source["snapshot"]))
    return page["data"]


def _choice(st, service, label, options, key, *, format_func=str):
    if st.session_state.get(key) not in options:
        st.session_state[key] = options[0]
    return st.selectbox(label, options, key=key, format_func=format_func,
                        on_change=ui.user_activity, kwargs={"st": st, "service": service})


def _filters(st, service, mode, page, *, exams=data.EXAMS, region=False, compact=False):
    columns = st.columns(3 if region else 2) if compact else None
    with columns[0] if columns else nullcontext():
        exam = _choice(st, service, "Экзамен", list(exams), page + "_exam",
                       format_func=EXAM_NAMES.get)
    options = _read(st, mode, (page, "options", exam), lambda r: r.filters(exam))
    if options is None:
        return None
    if not options["subject_codes"]:
        st.info("Нет данных по выбранному экзамену.")
        return None
    with columns[1] if columns else nullcontext():
        subject = _choice(st, service, "Код предмета", options["subject_codes"], page + "_subject")
    code = None
    if region:
        options = _read(st, mode, (page, "regions", exam, subject),
                        lambda r: r.filters(exam, subject))
        if options is None:
            return None
        names = {r["region_code"]: r["region_name"] for r in options["regions"]}
        with columns[2] if columns else nullcontext():
            code = _choice(st, service, "Регион", [None, *names], page + "_region",
                           format_func=lambda c: "Все регионы" if c is None else
                           c + " · " + (names[c] or c))
    return exam, subject, code


def _plot(st, create):
    try:
        st.pyplot(create())
    except ImportError:
        st.warning("Для графика необходим matplotlib. Таблица данных доступна ниже.")


def _chart_page(st, service, items, key, *, size=20, label="Часть графика"):
    if len(items) <= size:
        return items
    pages = list(range((len(items) + size - 1) // size))
    page = _choice(st, service, label, pages, key,
                   format_func=lambda p: f"{p * size + 1}–{min((p + 1) * size, len(items))} "
                   f"из {len(items)}")
    st.caption("На графике показана выбранная часть. Полная выборка сохранена в таблице.")
    return items[page * size:(page + 1) * size]


def _optional(call):
    try:
        return {"rows": call(), "error": None}
    except data.DataUnavailable as error:
        if error.code not in ("source_not_configured", "analysis_missing"):
            raise
        return {"rows": None, "error": str(error)}


def _run_choice(st, service, result, key):
    if result["error"]:
        st.info(result["error"])
        return None
    runs = result["rows"]
    if not runs:
        st.info("Нет завершенных расчетов. Незавершенный расчет не считается пустым перечнем.")
        return None
    lookup = {r["run_id"]: r for r in runs}
    return _choice(st, service, "Завершенный расчет", [None, *lookup], key,
                   format_func=lambda value: "Выберите расчет с нужной областью данных"
                   if value is None else str(value) + " · "
                   + time_text(lookup[value]["finished_at"]))


def _download(st, service, mode, rows, serialize, filename, key):
    try:
        content = serialize(rows)
    except (ValueError, TypeError):
        st.error("Выгрузка недоступна: некорректные данные источника.")
        return
    st.download_button(
        "Выгрузить", data=content, file_name=("demo_" if mode == "demo" else "") + filename,
        mime="text/csv; charset=utf-8", key=key, on_click=ui.user_activity,
        kwargs={"st": st, "service": service},
    )
