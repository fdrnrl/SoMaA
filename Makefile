# Все команды проекта. Запуск: make <команда>, например make test.

PYTHON ?= .venv/bin/python

# Команды на этом компьютере подключаются к базе через открытый порт localhost:5432.
# В контейнерах адрес базы — db (из .env); в CI — postgres (из .gitlab-ci.yml).
export SMA_DB_HOST ?= localhost

.PHONY: install up down logs psql build migrate db-reset test lint format cov

## Создать виртуальное окружение и установить зависимости
install:
	python3.12 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

## Поднять базу и применить миграции (сервис migrate)
up:
	docker compose up -d --build --wait

## Остановить сервисы (данные базы сохраняются в томе pgdata)
down:
	docker compose down

## Журнал сервисов
logs:
	docker compose logs --tail=100 -f

## Открыть консоль SQL внутри контейнера базы
psql:
	docker compose exec db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

## Собрать образ из Dockerfile
build:
	docker build -t sma-pipeline .

## Применить новые миграции к рабочей базе sma
migrate:
	$(PYTHON) db/migrate.py

## Удалить рабочую базу со всеми данными и создать заново (только при разработке!)
db-reset:
	docker compose down -v
	docker compose up -d --build --wait

## Тесты. Можно выбрать часть: make test ARGS="tests/unit/test_contract.py -x"
test:
	$(PYTHON) -m pytest $(ARGS)

lint:
	$(PYTHON) -m ruff check .

## Автоматически поправить оформление кода (пробелы, кавычки, переносы)
format:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

cov:
	$(PYTHON) -m pytest --cov=sma --cov-report=term-missing --cov-report=html
