# Все команды проекта. Запуск: make <команда>, например make test.

PYTHON ?= .venv/bin/python

# Команды на этом компьютере подключаются к базе через открытый порт localhost:5432.
# В контейнерах адрес базы — db (из .env); в CI — postgres (из .gitlab-ci.yml).
export SMA_DB_HOST ?= localhost

# Набор генератора для make load-files; другой: make load-files DATA=data/generated/medium
DATA ?= data/generated/small
# Скорость воспроизведения экзамена для make mock-replay
SPEED ?= 10

.PHONY: install up dev db-up down logs psql build migrate db-reset gen-small gen-medium gen-large \
	load-files loader mock-api mock-replay test lint format cov

## Создать виртуальное окружение и установить зависимости
install:
	python3.12 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

## База + миграции + загрузчик по расписанию (источники из config/platforms.yaml)
up:
	docker compose up -d --build loader
	docker compose ps

## То же плюс тестовый API; загрузчик берет данные из mock-api (сначала make gen-medium)
dev:
	SMA_LOADER_CONFIG=config/platforms.mock.yaml docker compose --profile dev up -d --build \
		loader mock-api
	docker compose --profile dev ps

## Только база и миграции, без загрузчика (для тестов и make load-files)
db-up:
	docker compose up -d --build --wait db
	docker compose run --rm --build migrate

## Остановить все сервисы (данные базы сохраняются в томе pgdata)
down:
	docker compose --profile dev down

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
	docker compose --profile dev down -v
	$(MAKE) db-up

## Сгенерировать маленький набор синтетических данных в data/generated/small
gen-small:
	$(PYTHON) -m sma.generator.generate --profile config/generator/small.yaml \
		--out data/generated/small --seed 2026

## Сгенерировать средний набор (430 участников на экзамен) в data/generated/medium
gen-medium:
	$(PYTHON) -m sma.generator.generate --profile config/generator/medium.yaml \
		--out data/generated/medium --seed 2026

## Сгенерировать большой набор для нагрузочного теста T-06 в data/generated/large
gen-large:
	$(PYTHON) -m sma.generator.generate --profile config/generator/large.yaml \
		--out data/generated/large --seed 2026

## Загрузить набор генератора в базу один раз (по умолчанию small)
load-files:
	$(PYTHON) -m sma.loader.run --once --data-root $(DATA)

## Загрузчик по расписанию на этом компьютере (Ctrl+C — остановка)
loader:
	$(PYTHON) -m sma.loader.run

## Тестовый API на этом компьютере: http://localhost:8000 (сначала make gen-medium)
mock-api:
	MOCK_ADMIN=1 $(PYTHON) -m sma.mockapi.server --data data/generated/medium --port 8000

## Тестовый API в режиме воспроизведения экзамена: make mock-replay SPEED=10
mock-replay:
	MOCK_ADMIN=1 $(PYTHON) -m sma.mockapi.server --data data/generated/medium --port 8000 \
		--replay --speed $(SPEED)

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
