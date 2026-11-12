"""Первый администратор: python -m dashboard.manage_users bootstrap-admin --login admin."""

import argparse
import getpass
import hmac
import sys

from dashboard.auth import AuthError, AuthService


class Arguments(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Ошибка аргументов. Используйте --help; пароль вводится отдельно.\n")


def main(argv=None, *, service=None, prompt=None, output=print):
    parser = Arguments(description="Безопасное создание первого администратора СМА")
    parser.add_argument("command", choices=["bootstrap-admin"])
    parser.add_argument("--login", required=True)
    args = parser.parse_args(argv)
    if prompt is None:
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            output("Запустите команду в интерактивном терминале для скрытого ввода пароля.")
            return 2
        prompt = getpass.getpass
    try:
        password = prompt("Новый пароль: ")
        confirmation = prompt("Повторите пароль: ")
        if not hmac.compare_digest(password.encode("utf-8"), confirmation.encode("utf-8")):
            output("Пароли не совпадают.")
            return 1
        service = service if service is not None else AuthService()
        service.bootstrap_admin(args.login, password)
    except (AuthError, ValueError, EOFError) as error:
        output(str(error))
        return 1
    except KeyboardInterrupt:
        output("Создание отменено.")
        return 1
    output("Администратор создан.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
