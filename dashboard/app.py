"""Единая точка входа: доступные страницы определяются проверенным сеансом."""

from dashboard import auth, ui
from dashboard import pages as work_pages

ROLE_NAMES = {"staff": "Сотрудник Рособрнадзора", "admin": "Администратор СМА"}


def login_page(st, service):
    st.title("Вход в СМА")
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
        st.form_submit_button("Войти", on_click=submit)


def home_page(st, service):
    user = ui.require_user(st=st, service=service)
    st.title("СМА")
    st.write(ROLE_NAMES[user["role"]])


def accounts_page(st, service):
    actor = ui.require_user("admin", st=st, service=service)
    st.title("Учетные записи")
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
    st.set_page_config(page_title="СМА", layout="wide")
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

    st.sidebar.text(user["login"] + " · " + ROLE_NAMES[user["role"]])
    st.sidebar.button("Выйти", on_click=auth.logout, args=(st.session_state,))
    mode = st.sidebar.selectbox(
        "Источник данных", ("database", "demo"), key="_sma_data_mode",
        format_func=lambda value: "База данных" if value == "database"
        else "Синтетический снимок", on_change=ui.user_activity,
        kwargs={"st": st, "service": service},
    )
    pages = [st.Page(lambda: home_page(st, service), title="Главная", default=True)]
    for title, path, render in work_pages.WORK_PAGES:
        pages.append(st.Page(lambda render=render: render(st, service, mode),
                             title=title, url_path=path))
    if user["role"] == "admin":
        pages.append(st.Page(lambda: accounts_page(st, service), title="Учетные записи",
                             url_path="accounts"))
    selected = st.navigation(pages)
    previous = st.session_state.get("_sma_previous_page")
    if previous is not None and previous != selected.url_path:
        ui.user_activity(st=st, service=service)
    st.session_state["_sma_previous_page"] = selected.url_path
    ui.session_watchdog(st=st, service=service)
    selected.run()


if __name__ == "__main__":
    main()
