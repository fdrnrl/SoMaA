"""Страница перечня школ и групп углубленного анализа."""

from dashboard import data, export
from dashboard.presentation import EXAM_NAMES, time_text

from ._common import _begin, _choice, _download, _optional, _read, _run_choice


def _school_bundle(reader, run, exam, subject, school):
    result = {"run": reader.completed_run(run),
              "schools": reader.school_list(run, exam, subject), "groups": None}
    keys = {(r["exam_type"], r["subject_code"], r["school_code"])
            for r in result["schools"]}
    if school in keys:
        result["groups"] = _optional(lambda: reader.p1_groups(run, *school))
    return result


def analysis_page(st, service, mode):
    _begin(st, service, "Углубленный анализ", mode)
    runs = _read(st, mode, "analysis_runs", lambda r: _optional(r.analysis_runs), provenance=True)
    if runs is None:
        return
    run = _run_choice(st, service, runs, "analysis_run")
    if run is None:
        return
    available = _read(st, mode, ("analysis_options", run),
                      lambda r: r.school_list(run))
    if available is None:
        return
    exam = _choice(st, service, "Экзамен", [None, *data.EXAMS], "analysis_exam",
                   format_func=lambda e: EXAM_NAMES.get(e, "Все экзамены"))
    subjects = sorted({r["subject_code"] for r in available
                       if exam is None or r["exam_type"] == exam})
    subject = _choice(st, service, "Код предмета", [None, *subjects], "analysis_subject",
                      format_func=lambda s: s or "Все предметы")
    before = st.session_state.get("analysis_school")
    result = _read(st, mode, ("analysis", run, exam, subject, before),
                   lambda r: _school_bundle(r, run, exam, subject, before))
    if result is None:
        return
    schools = result["schools"]
    choices = list(dict.fromkeys((r["exam_type"], r["subject_code"], r["school_code"])
                                for r in schools))
    school = None
    if choices:
        school = _choice(st, service, "Школа · экзамен / предмет / код", choices,
                         "analysis_school", format_func=lambda c: " / ".join(c))
        if school != before:
            result = _read(st, mode, ("analysis", run, exam, subject, school),
                           lambda r: _school_bundle(r, run, exam, subject, school))
            if result is None:
                return
            schools = result["schools"]
    st.write("Запуск:", run)
    st.caption("Начат: " + time_text(result["run"]["started_at"]) + " · Завершен: "
               + time_text(result["run"]["finished_at"]))
    st.write("Параметры:", result["run"]["params"])
    st.caption("Индекс по убыванию; z1…z4b — скорректированные значения z′. "
               "Расчетное время каждой строки — calculated_at.")
    filename = f"schools_run_{run}_exam_{exam or 'all'}_subject_{subject or 'all'}.csv"
    _download(st, service, mode, schools, export.schools_csv, filename, "schools_csv")
    if not schools:
        st.info("Расчет завершен. Перечень школ для выбранных фильтров пуст.")
        return
    st.dataframe([{name: row[name] for name in data.SCHOOL_COLUMNS} for row in schools],
                 hide_index=True)
    if school not in {(r["exam_type"], r["subject_code"], r["school_code"]) for r in schools}:
        st.info("Выбранной школы больше нет в этом перечне. Выберите школу повторно.")
        return
    st.subheader("Группы П1 выбранной школы")
    groups = result["groups"]
    if groups is None:
        st.info("Выберите школу для просмотра групп.")
    elif groups["error"]:
        st.info(groups["error"])
    elif not groups["rows"]:
        st.info("В выбранном завершенном расчете у школы нет сохраненных групп П1.")
    else:
        st.dataframe([{name: row[name] for name in data.GROUP_COLUMNS}
                      for row in groups["rows"]], hide_index=True)
