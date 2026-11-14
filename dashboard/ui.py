"""Общее оформление, проверка доступа и явные действия пользователя."""

from pathlib import Path

from dashboard.auth import AuthError, AuthService

NOTICE_KEY = "_sma_auth_notice"

PAGE_DETAILS = {
    "Распределение": ("bar_chart", "Как распределились баллы и где находится середина выборки."),
    "Сравнение экзаменов": ("compare_arrows",
                           "Сопоставьте среднее выполнение по регионам и экзаменам."),
    "Отметки": ("school", "Доля отметки «2» по регионам для ОГЭ и ВПР."),
    "Регионы": ("map", "География результатов и сравнение регионов выбранного экзамена."),
    "Темы и варианты": ("grid_view",
                       "Освоение тем и различия результатов между вариантами заданий."),
    "Живой экран": ("monitoring", "Ход экзамена, свежесть загрузок и новые сигналы для проверки."),
    "Аномалии": ("flag", "Сохраненные сигналы оперативных правил и выгрузка для проверки."),
    "Углубленный анализ": ("query_stats", "Перечень школ для проверки и группы участников П1."),
    "Учетные записи": ("manage_accounts",
                      "Создание пользователей и управление доступом к системе."),
}

ACCESSIBLE_STYLES = """
<style>
[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] :is(p, span, code) {
    color: inherit !important;
    opacity: 1 !important;
}
[data-testid="stMarkdownContainer"] a {
    color: inherit !important;
    text-decoration: underline;
}
</style>
"""


def apply_styles(st):
    """Единый стиль с сохранением цветов выбранной пользователем темы."""
    styles = Path(__file__).with_name("theme.css").read_text(encoding="utf-8")
    st.markdown("<style>" + styles + "</style>", unsafe_allow_html=True)
    st.markdown(ACCESSIBLE_STYLES, unsafe_allow_html=True)


def brand(st):
    st.markdown(
        '<div class="sma-brand"><div class="sma-brand-mark" aria-hidden="true">'
        '<i></i><i></i><i></i></div><div><strong>СМА</strong>'
        '<span>Аналитика экзаменов</span></div></div>', unsafe_allow_html=True,
    )


def page_heading(st, title):
    st.markdown('<p class="sma-eyebrow">Мониторинг и анализ результатов</p>',
                unsafe_allow_html=True)
    st.title(title)
    if title in PAGE_DETAILS:
        st.caption(PAGE_DETAILS[title][1])


def page_icon(title):
    return ":material/" + PAGE_DETAILS[title][0] + ":"


def _streamlit(st):
    if st is None:
        import streamlit

        return streamlit
    return st


def require_user(role=None, *, st=None, service=None):
    """Вызывать до вывода данных страницы, в том числе при прямом переходе по URL."""
    st = _streamlit(st)
    service = service if service is not None else AuthService()
    try:
        return service.require_user(st.session_state, role=role)
    except AuthError as error:
        st.error(str(error))
        st.stop()
        raise


def user_activity(*, st=None, service=None):
    """Только для on_change/on_click; автоматические перезапуски сюда не направлять."""
    st = _streamlit(st)
    service = service if service is not None else AuthService()
    try:
        service.touch(st.session_state)
    except AuthError as error:
        st.session_state[NOTICE_KEY] = str(error)
        st.rerun()


def session_watchdog(*, st=None, service=None):
    """Проверяет тайм-аут без продления активности и без ручного обновления страницы."""
    st = _streamlit(st)
    service = service if service is not None else AuthService()

    @st.fragment(run_every="15s")
    def check():
        try:
            service.require_user(st.session_state)
        except AuthError as error:
            st.session_state[NOTICE_KEY] = str(error)
            st.rerun()

    check()
