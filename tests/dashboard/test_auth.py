"""Соль, блокировки, роли, журнал и сеанс без настоящих учетных записей и БД."""

import copy
import io
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext, redirect_stderr
from datetime import datetime, timedelta
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bcrypt

from dashboard import app, auth, manage_users, ui

TEST_PASSWORD = "UnitOnlyPass12345"
NOW = datetime.fromisoformat("2026-12-05T10:00:00+03:00")
TEST_HASH = auth.hash_password(TEST_PASSWORD)


def account(login="staff", role="staff"):
    return {"login": login, "password_hash": TEST_HASH, "role": role, "blocked": False,
            "failed_attempts": 0, "locked_until": None, "created_at": NOW}


def session(login="staff", role="staff", moment=NOW):
    value = auth.Session(login, role, auth._stamp(TEST_HASH), moment, "unit-token")
    return {auth.SESSION_KEY: value}


class MemoryAccounts:
    """Только тестовый двойник; приложение не использует его для входа."""

    def __init__(self, *users):
        self.users = {row["login"]: dict(row) for row in users}
        self.logs = []
        self.lock = RLock()
        self.fail_log = False
        self.admin_calls = 0

    @contextmanager
    def attempt(self, login):
        with self.lock:
            before = copy.deepcopy(self.users), list(self.logs)
            store = self

            class Attempt:
                user = store.users.get(login)

                def set_failures(self, count, until):
                    self.user.update(failed_attempts=count, locked_until=until)

                def log(self, name, moment, success):
                    if store.fail_log:
                        raise auth.AuthUnavailable("Journal unavailable")
                    store.logs.append((name, moment, success))

            try:
                yield Attempt()
            except Exception:
                self.users, self.logs = before
                raise

    def get_user(self, login):
        row = self.users.get(login)
        return dict(row) if row else None

    def create_user(self, actor, login, encoded, role, now):
        self.admin_calls += 1
        if self.users[actor]["role"] != "admin" or self.users[actor]["blocked"]:
            raise auth.AccessDenied("Administrator required")
        if login in self.users:
            raise auth.AuthError("Duplicate login")
        self.users[login] = {**account(login, role), "password_hash": encoded, "created_at": now}

    def set_blocked(self, actor, login, blocked, now):
        self.admin_calls += 1
        if self.users[actor]["role"] != "admin" or self.users[actor]["blocked"]:
            raise auth.AccessDenied("Administrator required")
        if actor == login and blocked:
            raise ValueError("Cannot block own account")
        self.users[login]["blocked"] = blocked
        if not blocked:
            self.users[login].update(failed_attempts=0, locked_until=None)

    def list_users(self, actor, now):
        self.admin_calls += 1
        if self.users[actor]["role"] != "admin":
            raise auth.AccessDenied("Administrator required")
        return [{k: v for k, v in row.items() if k != "password_hash"}
                for row in self.users.values()]

    def bootstrap_admin(self, login, encoded, now):
        if any(row["role"] == "admin" for row in self.users.values()):
            raise auth.AuthError("Administrator already exists")
        self.users[login] = {**account(login, "admin"), "password_hash": encoded}


class PasswordAndLoginTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryAccounts(account())
        self.now = NOW
        self.service = auth.AuthService(self.store, lambda: self.now)
        self.state = {}

    def test_equal_passwords_have_different_salted_hashes(self):
        second = auth.hash_password(TEST_PASSWORD)
        self.assertTrue(second.startswith("$2b$12$"))
        self.assertTrue(bcrypt.checkpw(TEST_PASSWORD.encode("utf-8"), second.encode("ascii")))
        self.assertNotEqual(TEST_HASH, second)
        self.assertTrue(auth.verify_password(TEST_PASSWORD, second))
        self.assertFalse(auth.verify_password("WrongValue123", second))
        self.assertNotIn(TEST_PASSWORD, second)

    def test_password_policy_and_invalid_hashes(self):
        for password in ("Short1234", "NoDigitsHere", "12345678901"):
            with self.subTest(password=password), self.assertRaises(ValueError):
                auth.hash_password(password)
        for encoded in ("", "not-a-hash", "pbkdf2_sha256$1$00$00", None):
            self.assertFalse(auth.verify_password(TEST_PASSWORD, encoded))

    def test_password_length_is_measured_in_utf8_bytes_without_truncation(self):
        for password in ("A1" + "a" * 70, "Я" * 35 + "A1"):
            with self.subTest(password=password):
                encoded = auth.hash_password(password)
                self.assertTrue(auth.verify_password(password, encoded))
                self.assertFalse(auth.verify_password(password + "x", encoded))
                with self.assertRaises(ValueError):
                    auth.hash_password(password + "x")
        for password in ("Я" * 36 + "A1", "Invalid\ud800Pass123"):
            with self.subTest(password=password):
                with self.assertRaises(ValueError):
                    auth.hash_password(password)
                self.assertFalse(auth.verify_password(password, TEST_HASH))

    def test_standard_bcrypt_hashes_are_accepted_and_legacy_hashes_are_rejected(self):
        for prefix in ("$2a$", "$2b$", "$2y$"):
            self.assertTrue(auth.verify_password(TEST_PASSWORD, prefix + TEST_HASH[4:]))
        legacy = "pbkdf2_sha256$600000$" + "00" * 32 + "$" + "00" * 32
        with patch.object(auth.bcrypt, "checkpw") as check:
            for encoded in (legacy, TEST_HASH[:4] + "31" + TEST_HASH[6:],
                            TEST_HASH[:-1] + "!", "$2b$12$" + "a" * 54):
                self.assertFalse(auth.verify_password(TEST_PASSWORD, encoded))
            check.assert_not_called()
        self.store.users["staff"]["password_hash"] = legacy
        self.assertFalse(self.service.login(self.state, "staff", TEST_PASSWORD))
        self.assertNotIn(auth.SESSION_KEY, self.state)

    def test_each_attempt_including_unknown_login_is_logged(self):
        self.assertFalse(self.service.login(self.state, "unknown", TEST_PASSWORD))
        self.assertFalse(self.service.login(self.state, "staff", "WrongValue123"))
        self.assertTrue(self.service.login(self.state, "staff", TEST_PASSWORD))
        self.assertEqual([row[2] for row in self.store.logs], [False, False, True])
        self.assertEqual([row[0] for row in self.store.logs], ["unknown", "staff", "staff"])
        self.assertEqual(self.store.users["staff"]["failed_attempts"], 0)
        self.assertNotIn(TEST_PASSWORD, repr(self.state))

    def test_fifth_error_locks_for_fifteen_minutes(self):
        for _ in range(5):
            self.assertFalse(self.service.login(self.state, "staff", "WrongValue123"))
        self.assertEqual(self.store.users["staff"]["locked_until"], NOW + timedelta(minutes=15))
        self.now = NOW + timedelta(minutes=14, seconds=59)
        self.assertFalse(self.service.login(self.state, "staff", TEST_PASSWORD))
        self.now = NOW + timedelta(minutes=15)
        self.assertTrue(self.service.login(self.state, "staff", TEST_PASSWORD))
        self.assertIsNone(self.store.users["staff"]["locked_until"])

    def test_expired_lock_starts_a_new_attempt_counter(self):
        self.store.users["staff"].update(failed_attempts=5, locked_until=NOW)
        self.assertFalse(self.service.login(self.state, "staff", "WrongValue123"))
        self.assertEqual(self.store.users["staff"]["failed_attempts"], 1)
        self.assertIsNone(self.store.users["staff"]["locked_until"])

    def test_blocked_account_does_not_log_in(self):
        self.store.users["staff"]["blocked"] = True
        self.assertFalse(self.service.login(self.state, "staff", TEST_PASSWORD))
        self.assertEqual(self.store.logs[-1][2], False)
        self.assertNotIn(auth.SESSION_KEY, self.state)

    def test_failed_journal_write_cannot_create_a_session_or_commit_counter(self):
        self.store.fail_log = True
        with self.assertRaises(auth.AuthUnavailable):
            self.service.login(self.state, "staff", "WrongValue123")
        self.assertEqual(self.store.users["staff"]["failed_attempts"], 0)
        self.assertEqual(self.store.logs, [])
        self.assertNotIn(auth.SESSION_KEY, self.state)

    def test_parallel_failed_attempts_stop_counter_at_five(self):
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(
                lambda _: self.service.login({}, "staff", "WrongValue123"), range(6)
            ))
        self.assertFalse(any(results))
        self.assertEqual(self.store.users["staff"]["failed_attempts"], 5)
        self.assertEqual(len(self.store.logs), 6)

    def test_invalid_login_still_has_a_safe_journal_entry(self):
        self.assertFalse(self.service.login(self.state, "bad\x00name", TEST_PASSWORD))
        self.assertEqual(self.store.logs[-1][0], "bad?name")


class SessionAndRoleTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryAccounts(account(), account("admin", "admin"))
        self.now = NOW
        self.service = auth.AuthService(self.store, lambda: self.now)
        self.state = session()

    def test_no_session_and_a_forged_dictionary_are_rejected(self):
        for state in ({}, {auth.SESSION_KEY: {"login": "admin", "role": "admin"}}):
            with self.assertRaises(auth.AccessDenied):
                self.service.require_user(state)
            self.assertEqual(state, {})

    def test_automatic_refresh_does_not_extend_thirty_minute_session(self):
        self.now = NOW + timedelta(minutes=29)
        self.service.require_user(self.state)
        self.assertEqual(self.state[auth.SESSION_KEY].last_activity, NOW)
        self.now = NOW + timedelta(minutes=30)
        with self.assertRaises(auth.AccessDenied):
            self.service.require_user(self.state)
        self.assertEqual(self.state, {})

    def test_real_action_extends_only_a_valid_session(self):
        self.now = NOW + timedelta(minutes=29)
        self.service.touch(self.state)
        self.now = NOW + timedelta(minutes=31)
        self.assertEqual(self.service.require_user(self.state)["login"], "staff")
        self.now = NOW + timedelta(minutes=59)
        with self.assertRaises(auth.AccessDenied):
            self.service.touch(self.state)

    def test_block_or_password_change_invalidates_existing_session(self):
        self.store.users["staff"]["blocked"] = True
        with self.assertRaises(auth.AccessDenied):
            self.service.require_user(self.state)
        self.state = session()
        self.store.users["staff"].update(blocked=False, password_hash="changed-hash")
        with self.assertRaises(auth.AccessDenied):
            self.service.require_user(self.state)

    def test_changed_role_is_read_from_store_not_trusted_from_session(self):
        state = session("staff", "admin")
        with self.assertRaises(auth.AccessDenied):
            self.service.require_user(state, role="admin")
        self.assertEqual(self.service.require_user(state)["role"], "staff")

    def test_staff_cannot_create_block_or_list_users(self):
        actions = [lambda: self.service.create_user(self.state, "new", TEST_PASSWORD),
                   lambda: self.service.set_blocked(self.state, "admin", True),
                   lambda: self.service.list_users(self.state)]
        for action in actions:
            with self.assertRaises(auth.AccessDenied):
                action()
        self.assertEqual(self.store.admin_calls, 0)

    def test_admin_creates_blocks_and_unblocks_user(self):
        state = session("admin", "admin")
        self.service.create_user(state, "new", TEST_PASSWORD)
        self.assertTrue(auth.verify_password(
            TEST_PASSWORD, self.store.users["new"]["password_hash"]
        ))
        self.assertNotIn("password_hash", self.service.list_users(state)[0])
        self.service.set_blocked(state, "new", True)
        self.assertFalse(self.service.login({}, "new", TEST_PASSWORD))
        self.service.set_blocked(state, "new", False)
        self.assertTrue(self.service.login({}, "new", TEST_PASSWORD))
        with self.assertRaises(ValueError):
            self.service.set_blocked(state, "admin", True)

    def test_auth_storage_failure_ends_session(self):
        self.store.get_user = Mock(side_effect=auth.AuthUnavailable("Unavailable"))
        with self.assertRaises(auth.AuthUnavailable):
            self.service.require_user(self.state)
        self.assertEqual(self.state, {})

    def test_logout_clears_session_and_widget_values(self):
        self.state["_sma_new_password"] = "unit-only"
        auth.logout(self.state)
        self.assertEqual(self.state, {})


class SqlCursor:
    def __init__(self, records):
        names = list(records[0]) if records else []
        self.description = [SimpleNamespace(name=name) for name in names]
        self.records = [tuple(row[name] for name in names) for row in records]

    def fetchone(self):
        return self.records[0] if self.records else None

    def fetchall(self):
        return self.records


class SqlConnection:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.queries = []
        self.closed = False

    def execute(self, sql, params=()):
        self.queries.append((sql, params))
        return SqlCursor(self.responses.pop(0) if self.responses else [])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


class RepositoryTests(unittest.TestCase):
    def test_counter_and_journal_use_one_locked_transaction(self):
        connection = SqlConnection([account()], [], [])
        factory = Mock(return_value=connection)
        service = auth.AuthService(auth.DatabaseAccounts(factory), lambda: NOW)
        self.assertFalse(service.login({}, "staff", "WrongValue123"))
        factory.assert_called_once()
        self.assertIn("FOR UPDATE", connection.queries[0][0])
        self.assertIn("UPDATE app.users", connection.queries[1][0])
        self.assertIn("INSERT INTO app.login_log", connection.queries[2][0])
        self.assertTrue(connection.closed)
        self.assertTrue(all("core." not in sql for sql, _ in connection.queries))

    def test_repository_rechecks_admin_before_writing(self):
        connection = SqlConnection([account()])
        with self.assertRaises(auth.AccessDenied):
            auth.DatabaseAccounts(lambda: connection).create_user(
                "staff", "new", TEST_HASH, "staff", NOW
            )
        self.assertEqual(len(connection.queries), 1)

    def test_bootstrap_locks_table_and_refuses_an_existing_admin(self):
        connection = SqlConnection([], [{"exists": 1}])
        with self.assertRaises(auth.AuthError):
            auth.DatabaseAccounts(lambda: connection).bootstrap_admin("admin", TEST_HASH, NOW)
        self.assertIn("LOCK TABLE app.users", connection.queries[0][0])
        self.assertEqual(len(connection.queries), 2)

    def test_app_role_is_explicit_and_database_errors_are_redacted(self):
        settings, connect = Mock(return_value={"user": "sma_app", "password": ""}), Mock()
        with patch.dict("sys.modules", {
            "psycopg": SimpleNamespace(connect=connect),
            "sma.db": SimpleNamespace(connection_settings=settings),
        }), patch.dict("os.environ", {"SMA_APP_PASSWORD": "unit-only"}):
            auth._connect_app()
        settings.assert_called_once_with(user="sma_app", password="")
        self.assertEqual(connect.call_args.kwargs["user"], "sma_app")
        for exception in (RuntimeError("PRIVATE_DATABASE_DETAIL"),
                          ValueError("PRIVATE_DATABASE_DETAIL")):
            store = auth.DatabaseAccounts(Mock(side_effect=exception))
            with self.assertRaises(auth.AuthUnavailable) as caught:
                store.get_user("staff")
            self.assertNotIn("PRIVATE_DATABASE_DETAIL", str(caught.exception))


class StopPage(Exception):
    pass


class RerunPage(Exception):
    pass


class FakeStreamlit:
    def __init__(self, state=None, url="accounts"):
        self.session_state = state if state is not None else {}
        self.url = url
        self.page_titles = []
        self.sidebar = self
        self.tables = []
        self.messages = []
        self.callbacks = {}

    def set_page_config(self, **kwargs):
        pass

    def markdown(self, *args, **kwargs):
        pass

    def Page(self, render, title, default=False, url_path=None):
        return SimpleNamespace(run=render, title=title, url_path=url_path or "", default=default)

    def navigation(self, pages, **kwargs):
        self.page_titles = [page.title for page in pages]
        return next((page for page in pages if page.url_path == self.url), pages[0])

    def fragment(self, **kwargs):
        return lambda function: function

    def form(self, *args, **kwargs):
        return nullcontext()

    def text_input(self, label, key, **kwargs):
        self.session_state.setdefault(key, "")

    def form_submit_button(self, label, on_click=None, args=()):
        self.callbacks[label] = lambda: on_click(*args)

    def selectbox(self, label, options, key, **kwargs):
        self.session_state.setdefault(key, options[0])
        return self.session_state[key]

    def button(self, *args, **kwargs):
        pass

    def text(self, *args):
        pass

    def title(self, *args):
        pass

    def write(self, *args):
        pass

    def info(self, message):
        self.messages.append(message)

    error = info

    def dataframe(self, rows, **kwargs):
        self.tables.append(rows)

    def stop(self):
        raise StopPage()

    def rerun(self):
        raise RerunPage()


class NavigationAndCliTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryAccounts(account(), account("admin", "admin"))
        self.service = auth.AuthService(self.store, lambda: NOW)

    def test_direct_admin_url_before_login_shows_only_login(self):
        st = FakeStreamlit(url="accounts")
        app.main(st, self.service)
        self.assertEqual(st.page_titles, ["Вход"])
        self.assertEqual(st.tables, [])
        self.assertNotIn("_sma_new_login", st.session_state)

    def test_staff_navigation_does_not_expose_admin_page(self):
        st = FakeStreamlit(session(), url="accounts")
        app.main(st, self.service)
        self.assertEqual(st.page_titles, ["Главная"] + [p[0] for p in app.work_pages.WORK_PAGES])
        with self.assertRaises(StopPage):
            app.accounts_page(st, self.service)
        self.assertEqual(st.tables, [])

    def test_admin_navigation_contains_account_management(self):
        st = FakeStreamlit(session("admin", "admin"), url="accounts")
        app.main(st, self.service)
        self.assertEqual(st.page_titles, ["Главная"] + [p[0] for p in app.work_pages.WORK_PAGES]
                         + ["Учетные записи"])
        self.assertEqual(len(st.tables), 1)
        self.assertTrue(all("password_hash" not in row for row in st.tables[0]))

    def test_login_form_clears_password_after_success(self):
        st = FakeStreamlit()
        app.main(st, self.service)
        st.session_state.update(_sma_login_name="staff", _sma_login_password=TEST_PASSWORD)
        st.callbacks["Войти"]()
        self.assertIn(auth.SESSION_KEY, st.session_state)
        self.assertEqual(st.session_state["_sma_login_password"], "")

    def test_admin_form_callbacks_create_and_block_user(self):
        st = FakeStreamlit(session("admin", "admin"), url="accounts")
        app.main(st, self.service)
        st.session_state.update(_sma_new_login="new", _sma_new_password=TEST_PASSWORD)
        st.callbacks["Создать"]()
        self.assertIn("new", self.store.users)
        self.assertEqual(st.session_state["_sma_new_password"], "")
        st.session_state["_sma_target_login"] = "new"
        st.callbacks["Заблокировать"]()
        self.assertTrue(self.store.users["new"]["blocked"])

    def test_watchdog_expires_session_without_counting_a_user_action(self):
        state = session(moment=NOW - timedelta(minutes=30))
        st = FakeStreamlit(state)
        with self.assertRaises(RerunPage):
            ui.session_watchdog(st=st, service=self.service)
        self.assertNotIn(auth.SESSION_KEY, state)

    def test_bootstrap_uses_prompt_and_runs_only_once(self):
        store = MemoryAccounts()
        service = auth.AuthService(store, lambda: NOW)
        prompt = Mock(side_effect=[TEST_PASSWORD, TEST_PASSWORD])
        self.assertEqual(manage_users.main(
            ["bootstrap-admin", "--login", "admin"], service=service, prompt=prompt, output=Mock()
        ), 0)
        self.assertEqual(store.users["admin"]["role"], "admin")
        with self.assertRaises(auth.AuthError):
            service.bootstrap_admin("another", TEST_PASSWORD)

    def test_bootstrap_mismatch_and_noninteractive_input_do_not_create_users(self):
        service = Mock()
        self.assertEqual(manage_users.main(
            ["bootstrap-admin", "--login", "admin"], service=service,
            prompt=Mock(side_effect=[TEST_PASSWORD, "different"]), output=Mock()
        ), 1)
        service.bootstrap_admin.assert_not_called()
        with patch("sys.stdin.isatty", return_value=False):
            self.assertEqual(manage_users.main(
                ["bootstrap-admin", "--login", "admin"], service=service, output=Mock()
            ), 2)

    def test_unsupported_cli_arguments_are_not_echoed(self):
        stream = io.StringIO()
        with redirect_stderr(stream), self.assertRaises(SystemExit):
            manage_users.main(["bootstrap-admin", "--login", "admin", "--password", "PRIVATE"])
        self.assertNotIn("PRIVATE", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
