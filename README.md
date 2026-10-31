# sma-pipeline

Генератор тестовых данных, загрузчик, хранилище PostgreSQL и витрины СМА (роль «Пайплайны», ГИАЦ).
Требования — в [docs/spec.md](docs/spec.md), принятые решения — в [docs/decisions.md](docs/decisions.md).

Подробная инструкция по развертыванию появится на этапе M7.

## Быстрый старт

Нужны: Docker Desktop (запущенный), Python 3.12, make.

```bash
cp .env.example .env      # заполнить пароли
make install              # окружение .venv и зависимости
make up                   # поднять базу
make test                 # тесты
make lint                 # линтер
make psql                 # консоль SQL
```
