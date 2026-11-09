"""Вход пользователей дашборда: таблицы app.users и app.login_log (роль sma_app).

Хранится только хеш пароля; блокировка после неудачных попыток — поля failed_attempts,
locked_until, blocked.
"""
