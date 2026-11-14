"""Единая точка входа: доступные страницы определяются проверенным сеансом."""

from dashboard import auth, ui
from dashboard import pages as work_pages

ROLE_NAMES = {"staff": "Сотрудник Рособрнадзора", "admin": "Администратор СМА"}


def login_page(st, service):
    with st.container(key="sma_login_panel"):
        ui.brand(st)
        _login_form(st, service)


def _login_form(st, service):
    st.title("Вход в СМА")
    st.caption("Войдите, чтобы работать с результатами экзаменов и мониторингом платформ.")
    notice = st.session_state.pop(ui.NOTICE_KEY, None)
    if notice:
        st.info(notice)
    error = st.session_state.pop("_sma_login_error", None)
    if error:
        st.error(error)

    def submit():
        login = st.session_state.get("_sma_login_name", "")
        password = st.session_state.get("_sma_login_password", "")
        try:
            if not service.login(st.session_state, login, password):
                st.session_state["_sma_login_error"] = auth.INVALID_LOGIN
        except auth.AuthError as problem:
            st.session_state["_sma_login_error"] = str(problem)
        finally:
            st.session_state["_sma_login_password"] = ""
            st.session_state["_sma_login_name"] = login

    with st.form("sma_login", clear_on_submit=True):
        st.text_input("Логин", key="_sma_login_name", max_chars=128)
        st.text_input("Пароль", type="password", key="_sma_login_password",
                      max_chars=auth.MAX_PASSWORD_BYTES)
        st.form_submit_button("Войти", on_click=submit, type="primary", width="stretch")


def home_page(st, service, links=()):
    ui.require_user(st=st, service=service)
    ui.page_heading(st, "СМА")
    st.caption("ЕГЭ · ОГЭ · ВПР · НИКО")
    st.write("Результаты экзаменов, ход загрузок и сигналы для дополнительной проверки "
             "в одном рабочем пространстве.")
    st.subheader("С чего начать")
    for start in range(0, len(links), 2):
        for column, (title, page) in zip(st.columns(2), links[start:start + 2], strict=False):
            with column, st.container(border=True, key="sma_home_card_" + page.url_path):
                st.page_link(page, label=title, icon=ui.page_icon(title))
                st.caption(ui.PAGE_DETAILS[title][1])


def accounts_page(st, service):
    actor = ui.require_user("admin", st=st, service=service)
    ui.page_heading(st, "Учетные записи")
    notice = st.session_state.pop("_sma_accounts_notice", None)
    if notice:
        kind, message = notice
        getattr(st, kind)(message)
    try:
        users = service.list_users(st.session_state)
    except auth.AuthError as error:
        st.error(str(error))
        st.stop()
        return
    st.dataframe(users, hide_index=True)

    def create():
        try:
            service.create_user(
                st.session_state, st.session_state.get("_sma_new_login", ""),
                st.session_state.get("_sma_new_password", ""),
                st.session_state.get("_sma_new_role", "staff"),
            )
            st.session_state["_sma_accounts_notice"] = ("success", "Учетная запись создана.")
        except (auth.AuthError, ValueError) as error:
            st.session_state["_sma_accounts_notice"] = ("error", str(error))
        finally:
            st.session_state["_sma_new_password"] = ""

    with st.form("sma_create_user", clear_on_submit=True):
        st.text_input("Новый логин", key="_sma_new_login", max_chars=128)
        st.text_input("Новый пароль", type="password", key="_sma_new_password",
                      max_chars=auth.MAX_PASSWORD_BYTES,
                      help="От 10 символов, буквы и цифры; не более 72 байт UTF-8.")
        st.selectbox("Роль", ("staff", "admin"), format_func=ROLE_NAMES.get, key="_sma_new_role")
        st.form_submit_button("Создать", on_click=create)

    targets = [row["login"] for row in users if row["login"] != actor["login"]]
    if not targets:
        return

    def change_block(blocked):
        try:
            service.set_blocked(
                st.session_state, st.session_state.get("_sma_target_login", ""), blocked
            )
            st.session_state["_sma_accounts_notice"] = ("success", "Состояние записи изменено.")
        except (auth.AuthError, ValueError) as error:
            st.session_state["_sma_accounts_notice"] = ("error", str(error))

    with st.form("sma_block_user"):
        st.selectbox("Пользователь", targets, key="_sma_target_login")
        st.form_submit_button("Заблокировать", on_click=change_block, args=(True,))
        st.form_submit_button("Разблокировать", on_click=change_block, args=(False,))


def main(st=None, service=None):
    if st is None:
        import streamlit as st
    service = service if service is not None else auth.AuthService()
    st.set_page_config(page_title="СМА · Аналитика экзаменов", page_icon=":material/monitoring:",
                       layout="wide", initial_sidebar_state="expanded")
    ui.apply_styles(st)
    user = None
    if auth.SESSION_KEY in st.session_state:
        try:
            user = service.require_user(st.session_state)
        except auth.AuthError as error:
            st.session_state[ui.NOTICE_KEY] = str(error)
    if user is None:
        st.navigation([
            st.Page(lambda: login_page(st, service), title="Вход", default=True)
        ], position="hidden").run()
        return

    links = []
    for title, path, render in work_pages.WORK_PAGES:
        page = st.Page(lambda render=render: render(st, service, mode),
                       title=title, url_path=path, icon=ui.page_icon(title))
        links.append((title, page))
    home = st.Page(lambda: home_page(st, service, links), title="Главная", default=True,
                   icon=":material/space_dashboard:")
    groups = [("Обзор", [("Главная", home)]), ("Результаты экзаменов", links[:4]),
              ("Мониторинг и анализ", links[4:])]
    if user["role"] == "admin":
        accounts = st.Page(lambda: accounts_page(st, service), title="Учетные записи",
                           url_path="accounts", icon=ui.page_icon("Учетные записи"))
        groups.append(("Администрирование", [("Учетные записи", accounts)]))
    selected = st.navigation([page for _, group in groups for _, page in group], position="hidden")
    ui.brand(st.sidebar)
    for heading, group in groups:
        st.sidebar.markdown('<p class="sma-nav-section">' + heading + '</p>',
                            unsafe_allow_html=True)
        for title, page in group:
            st.sidebar.page_link(page, label=title)
    st.sidebar.markdown("---")
    mode = st.sidebar.selectbox(
        "Источник данных", ("database", "demo"), key="_sma_data_mode",
        format_func=lambda value: "База данных" if value == "database"
        else "Синтетический снимок", on_change=ui.user_activity,
        kwargs={"st": st, "service": service},
    )
    st.sidebar.text(user["login"] + " · " + ROLE_NAMES[user["role"]])
    st.sidebar.button("Выйти", on_click=auth.logout, args=(st.session_state,))
    previous = st.session_state.get("_sma_previous_page")
    if previous is not None and previous != selected.url_path:
        ui.user_activity(st=st, service=service)
    st.session_state["_sma_previous_page"] = selected.url_path
    ui.session_watchdog(st=st, service=service)
    selected.run()


if __name__ == "__main__":
    main()
