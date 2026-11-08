# Все команды проекта. Запуск: make <команда>, например make test.

PYTHON ?= .venv/bin/python

# Команды на этом компьютере подключаются к базе через открытый порт localhost:5432.
# В контейнерах адрес базы — db (из .env); в CI — postgres (из .gitlab-ci.yml).
export SMA_DB_HOST ?= localhost

# Набор генератора для make load-files; другой: make load-files DATA=data/generated/medium
DATA ?= data/generated/small

.PHONY: install up down logs psql build migrate db-reset gen-small gen-medium load-files \
	test lint format cov

## Создать виртуальное окружение и установить зависимости
install:
	python3.12 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

## Поднять базу и применить миграции (сервис migrate)
up:
	docker compose up -d --build --wait db
	docker compose run --rm --build migrate

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
	$(MAKE) up

## Сгенерировать маленький набор синтетических данных в data/generated/small
gen-small:
	$(PYTHON) -m sma.generator.generate --profile config/generator/small.yaml \
		--out data/generated/small --seed 2026

## Сгенерировать средний набор (430 участников на экзамен) в data/generated/medium
gen-medium:
	$(PYTHON) -m sma.generator.generate --profile config/generator/medium.yaml \
		--out data/generated/medium --seed 2026

## Загрузить набор генератора в базу один раз (по умолчанию small)
load-files:
	$(PYTHON) -m sma.loader.run --once --data-root $(DATA)

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
