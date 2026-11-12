"""Вход, роли и сеанс; запись только в существующие app.users/app.login_log."""

import hashlib
import hmac
import os
import re
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import bcrypt

BCRYPT_ROUNDS = 12
MAX_PASSWORD_BYTES = 72
LOCK_TIME = timedelta(minutes=15)
IDLE_TIME = timedelta(minutes=30)
SESSION_KEY = "_sma_auth_session"
INVALID_LOGIN = "Неверный логин или пароль, либо вход временно недоступен."
USER_COLUMNS = (
    "login, password_hash, role, blocked, failed_attempts, locked_until, created_at"
)


class AuthError(RuntimeError):
    """Безопасная ошибка доступа для интерфейса."""


class AuthUnavailable(AuthError):
    """Хранилище доступа недоступно; вход запрещен."""


class AccessDenied(AuthError):
    """Нет действующего сеанса или требуемой роли."""


def utc_now():
    return datetime.now(UTC)


def _aware(moment):
    if moment.utcoffset() is None:
        raise ValueError("Время должно содержать часовой пояс")
    return moment.astimezone(UTC)


def validate_password(password):
    message = "Пароль: от 10 символов и не более 72 байт UTF-8."
    if not isinstance(password, str) or not 10 <= len(password) <= MAX_PASSWORD_BYTES:
        raise ValueError(message)
    try:
        size = len(password.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("Пароль содержит недопустимые символы.") from None
    if size > MAX_PASSWORD_BYTES:
        raise ValueError(message)
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        raise ValueError("Пароль должен содержать буквы и цифры.")


def validate_login(login):
    if not isinstance(login, str):
        raise ValueError("Укажите логин.")
    login = login.strip()
    if not 1 <= len(login) <= 128 or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in login):
        raise ValueError("Логин должен содержать от 1 до 128 печатных символов.")
    return login


def hash_password(password):
    validate_password(password)
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("ascii")


def verify_password(password, encoded):
    if (not isinstance(password, str) or len(password) > MAX_PASSWORD_BYTES
            or not isinstance(encoded, str) or len(encoded) != 60):
        return False
    if not re.fullmatch(r"\$2[aby]\$(?:0[4-9]|1[0-6])\$[./A-Za-z0-9]{53}", encoded):
        return False
    try:
        raw = password.encode("utf-8")
        if len(raw) > MAX_PASSWORD_BYTES:
            return False
        return bcrypt.checkpw(raw, encoded.encode("ascii"))
    except (ValueError, UnicodeError):
        return False


@lru_cache(maxsize=1)
def _dummy_hash():
    return hash_password(secrets.token_hex(32) + "a1")


def _stamp(encoded):
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _audit_login(login):
    text = login if isinstance(login, str) else ""
    return text.encode("utf-8", errors="replace").decode("utf-8").replace("\x00", "?")[:128]


def _connect_app():
    try:
        import psycopg

        from sma.db import connection_settings
    except ImportError:
        raise AuthUnavailable("Не установлены зависимости подключения к БД.") from None
    settings = connection_settings(user="sma_app", password="")
    password = os.environ.get("SMA_APP_PASSWORD")
    if not password:
        raise AuthUnavailable("Не настроен доступ модуля входа к БД.")
    settings["password"] = password
    return psycopg.connect(**settings, connect_timeout=3)


def _row(cursor):
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip((column.name for column in cursor.description), row, strict=True))


def _active_admin(user, now):
    if (not user or user["role"] != "admin" or user["blocked"]
            or (user["locked_until"] is not None and user["locked_until"] > now)):
        raise AccessDenied("Требуется действующий сеанс администратора.")


class LoginAttempt:
    """Изменение счетчика и запись журнала в одной транзакции с блокировкой строки."""

    def __init__(self, connection, user):
        self.connection, self.user = connection, user

    def set_failures(self, count, locked_until):
        self.connection.execute(
            "UPDATE app.users SET failed_attempts = %s, locked_until = %s WHERE login = %s",
            (count, locked_until, self.user["login"]),
        )

    def log(self, login, moment, success):
        self.connection.execute(
            "INSERT INTO app.login_log (login, attempted_at, success) VALUES (%s, %s, %s)",
            (login, moment, success),
        )


class DatabaseAccounts:
    """Хранилище существующей схемы app; подключения и ошибки не кэшируются."""

    def __init__(self, connection_factory=None):
        self.connection_factory = connection_factory or _connect_app

    @contextmanager
    def _transaction(self):
        try:
            with self.connection_factory() as connection:
                yield connection
        except AuthError:
            raise
        except Exception:
            raise AuthUnavailable("Хранилище учетных записей недоступно.") from None

    @staticmethod
    def _user(connection, login, lock=False):
        return _row(connection.execute(
            "SELECT " + USER_COLUMNS + " FROM app.users WHERE login = %s"
            + (" FOR UPDATE" if lock else ""), (login,),
        ))

    @contextmanager
    def attempt(self, login):
        with self._transaction() as connection:
            yield LoginAttempt(connection, self._user(connection, login, lock=True))

    def get_user(self, login):
        with self._transaction() as connection:
            return self._user(connection, login)

    def create_user(self, actor_login, login, encoded, role, now):
        with self._transaction() as connection:
            _active_admin(self._user(connection, actor_login, lock=True), now)
            cursor = connection.execute(
                "INSERT INTO app.users (login, password_hash, role) VALUES (%s, %s, %s) "
                "ON CONFLICT (login) DO NOTHING RETURNING login", (login, encoded, role),
            )
            if cursor.fetchone() is None:
                raise AuthError("Логин уже занят.")

    def set_blocked(self, actor_login, login, blocked, now):
        if login == actor_login and blocked:
            raise ValueError("Нельзя блокировать собственную учетную запись.")
        with self._transaction() as connection:
            _active_admin(self._user(connection, actor_login, lock=True), now)
            sql = "UPDATE app.users SET blocked = true" if blocked else (
                "UPDATE app.users SET blocked = false, failed_attempts = 0, locked_until = NULL"
            )
            if connection.execute(sql + " WHERE login = %s RETURNING login", (login,)
                                  ).fetchone() is None:
                raise AuthError("Учетная запись не найдена.")

    def list_users(self, actor_login, now):
        with self._transaction() as connection:
            _active_admin(self._user(connection, actor_login), now)
            cursor = connection.execute(
                "SELECT login, role, blocked, failed_attempts, locked_until, created_at "
                "FROM app.users ORDER BY login"
            )
            names = [column.name for column in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def bootstrap_admin(self, login, encoded, now):
        with self._transaction() as connection:
            connection.execute("LOCK TABLE app.users IN EXCLUSIVE MODE")
            if connection.execute(
                "SELECT 1 FROM app.users WHERE role = 'admin' LIMIT 1"
            ).fetchone() is not None:
                raise AuthError("Первый администратор уже создан.")
            if connection.execute(
                "INSERT INTO app.users (login, password_hash, role, created_at) "
                "VALUES (%s, %s, 'admin', %s) ON CONFLICT (login) DO NOTHING RETURNING login",
                (login, encoded, now),
            ).fetchone() is None:
                raise AuthError("Логин уже занят.")


@dataclass
class Session:
    login: str
    role: str
    stamp: str
    last_activity: datetime
    token: str


def logout(state):
    state.clear()


class AuthService:
    """Проверяет сеанс по актуальному пользователю; активность отмечается явно."""

    def __init__(self, repository=None, clock=utc_now):
        self.repository = repository if repository is not None else DatabaseAccounts()
        self.clock = clock

    def login(self, state, login, password):
        logout(state)
        now = _aware(self.clock())
        audit = _audit_login(login)
        try:
            normalized = validate_login(login)
        except ValueError:
            normalized = ""
        principal = None
        with self.repository.attempt(normalized) as attempt:
            user = attempt.user
            matches = verify_password(password, user["password_hash"] if user else _dummy_hash())
            allowed = bool(user and not user["blocked"] and user["role"] in ("staff", "admin"))
            locked = bool(user and user["locked_until"] and user["locked_until"] > now)
            success = allowed and not locked and matches
            if allowed and not locked:
                failures = 0 if user["locked_until"] else user["failed_attempts"]
                failures = 0 if success else failures + 1
                until = now + LOCK_TIME if failures >= 5 else None
                attempt.set_failures(failures, until)
            attempt.log(audit, now, success)
            if success:
                principal = {"login": user["login"], "role": user["role"]}
                stamp = _stamp(user["password_hash"])
        if principal is None:
            return False
        state[SESSION_KEY] = Session(
            principal["login"], principal["role"], stamp, now, secrets.token_urlsafe(32)
        )
        return True

    def require_user(self, state, role=None, activity=False):
        session = state.get(SESSION_KEY)
        now = _aware(self.clock())
        if not isinstance(session, Session):
            logout(state)
            raise AccessDenied("Войдите в систему.")
        elapsed = now - _aware(session.last_activity)
        if elapsed < timedelta(0) or elapsed >= IDLE_TIME:
            logout(state)
            raise AccessDenied("Сеанс завершен. Войдите снова.")
        try:
            user = self.repository.get_user(session.login)
        except AuthError:
            logout(state)
            raise
        if (not user or user["blocked"] or user["role"] not in ("staff", "admin")
                or (user["locked_until"] is not None and user["locked_until"] > now)
                or not hmac.compare_digest(_stamp(user["password_hash"]), session.stamp)):
            logout(state)
            raise AccessDenied("Сеанс завершен. Войдите снова.")
        session.role = user["role"]
        if role is not None and session.role != role:
            raise AccessDenied("Недостаточно прав.")
        if activity:
            session.last_activity = now
        return {"login": session.login, "role": session.role}

    def touch(self, state):
        return self.require_user(state, activity=True)

    def create_user(self, state, login, password, role="staff"):
        actor = self.require_user(state, role="admin", activity=True)
        login = validate_login(login)
        if role not in ("staff", "admin"):
            raise ValueError("Неизвестная роль.")
        encoded = hash_password(password)
        self.repository.create_user(actor["login"], login, encoded, role, _aware(self.clock()))

    def set_blocked(self, state, login, blocked):
        actor = self.require_user(state, role="admin", activity=True)
        if not isinstance(blocked, bool):
            raise ValueError("Нужно логическое значение блокировки.")
        self.repository.set_blocked(
            actor["login"], validate_login(login), blocked, _aware(self.clock())
        )

    def list_users(self, state):
        actor = self.require_user(state, role="admin")
        return self.repository.list_users(actor["login"], _aware(self.clock()))

    def bootstrap_admin(self, login, password):
        self.repository.bootstrap_admin(
            validate_login(login), hash_password(password), _aware(self.clock())
        )
