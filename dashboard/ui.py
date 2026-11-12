"""Общая проверка доступа для каждой страницы и явных действий пользователя."""

from dashboard.auth import AuthError, AuthService

NOTICE_KEY = "_sma_auth_notice"

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
    """Сохраняет читаемость подписей в светлой и темной теме без смены цветов темы."""
    st.markdown(ACCESSIBLE_STYLES, unsafe_allow_html=True)


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
