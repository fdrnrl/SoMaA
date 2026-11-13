# sma-pipeline

Часть СМА (система мониторинга и анализа результатов экзаменов), роль «Пайплайны» ГИАЦ:
генератор тестовых данных, загрузчик данных платформ, хранилище PostgreSQL, витрины,
проверки качества, резервные копии.

```text
API платформ ЕГЭ, ОГЭ, ВПР, НИКО │ генератор (файлы) │ тестовый API
                 │
                 ↓  загрузчик: забирает, проверяет, пишет с заменой, журналирует
        PostgreSQL: core (данные) · ops (журналы) · analytics · app
                 │
                 ↓  SQL-представления
        mart — витрины для графиков, анализа и живого экрана
```

## Документация

| Кому | Документ |
|---|---|
| Тем, кто смотрит экраны СМА (что значат цифры и отметки аномалий) | [docs/user_guide.md](docs/user_guide.md) |
| Разработчику дашбордов и аналитику аномалий (подключение, витрины, проверка правил по ключу) | [docs/developer_guide.md](docs/developer_guide.md) |
| Им же — как пользоваться витринами: запросы под типичные задачи, соединения, ловушки | [docs/marts_guide.md](docs/marts_guide.md) |
| Тем, кто разворачивает и запускает систему | этот README |
| Требования | [docs/spec.md](docs/spec.md) |
| Принятые решения и их причины | [docs/decisions.md](docs/decisions.md) |
| Схема данных | [docs/er_diagram.md](docs/er_diagram.md) |
| Пример отчета о качестве данных | [docs/quality_report_example.md](docs/quality_report_example.md) |

## Где писать код команде

Каждый пишет в своей папке — так ваши изменения не задевают чужие, а `make test`,
`make lint` и CI сразу проверяют и ваш код.

| Кто | Код | Тесты | Запуск |
|---|---|---|---|
| Аналитик аномалий | `sma/sma_anomalies/` — правила А1–А3, П1–П4, индекс ([README аналитика](sma/sma_anomalies/README.md)) | `tests/anomalies/` | `make rules` |
| Пайплайны: запуск аналитики | `sma/analytics_job.py` — данные из витрин → модуль аналитика → схема `analytics` | `tests/integration/test_analytics_job.py` | `make rules` (и загрузчик по расписанию) |
| Разработчик дашбордов | `dashboard/` — `app.py` (главная), `pages/` (остальные страницы), `data.py` (запросы к витринам), `auth.py` (вход) | `tests/dashboard/` | `make dashboard` |
| Пайплайны | все остальное: `sma/loader`, `sma/generator`, `db/`, … | `tests/unit`, `tests/integration`, `tests/properties` | см. команды ниже |

- Данные — **только из витрин** `mart` (что где лежит — [docs/developer_guide.md](docs/developer_guide.md));
  результаты правил — в схему `analytics`, пользователи дашборда — в схему `app`.
- **Не меняйте** файлы вне своей папки: схемы базы, витрины и формат данных платформ.
  Нужна новая витрина, таблица или столбец — попросите разработчика пайплайнов.
- Новая библиотека (например, `streamlit` для дашборда) — в `requirements.txt` с точной
  версией (`==`), по согласованию с командой.

## Развертывание с нуля (≤ 30 минут)

Проверено на macOS; на Linux команды те же (кроме `sed -i ''` → `sed -i`).
**Windows** — через WSL2, см. раздел [«Windows»](#windows) ниже.

**1. Установить** (если еще нет): Docker Desktop (запустить, дождаться «Engine running»),
Python 3.12, git, make. Проверка:

```bash
docker run --rm hello-world && docker compose version
python3.12 --version && git --version && make --version
```

**2. Получить код и настроить секреты:**

```bash
git clone <адрес репозитория> sma-pipeline
cd sma-pipeline
cp .env.example .env
# Случайные пароли и токены (macOS; на Linux — sed -i без ''):
for name in POSTGRES_PASSWORD SMA_LOADER_PASSWORD SMA_READER_PASSWORD SMA_APP_PASSWORD \
            MOCK_API_TOKEN MOCK_ADMIN_TOKEN; do
  sed -i '' "s/^$name=$/$name=$(openssl rand -hex 16)/" .env
done
```

`.env` в репозиторий не попадает (он в `.gitignore`). Токены реальных платформ
(`EGE_API_TOKEN` и др.) вписываются туда же, когда их выдадут команды.

**3. Собрать окружение, данные и запустить:**

```bash
make install       # окружение .venv и зависимости (точные версии из requirements.txt)
make gen-medium    # синтетический набор: 430 участников на экзамен, ~1 минута
make dev           # база + миграции + загрузчик + тестовый API в Docker
make logs          # видно, как загрузчик забирает данные (Ctrl+C — выйти из журнала)
```

Через минуту данные в базе, через 30 секунд после этого — в витринах.

**4. Посмотреть данные** — в DBeaver: новое соединение PostgreSQL,
host `localhost`, port `5432`, database `sma`, user `sma_reader`,
password — `SMA_READER_PASSWORD` из `.env`. Начать с `mart.region_results` и `mart.live`.
Или в консоли: `make psql`.

**5. Проверить:**

```bash
make test          # все тесты (~1,5 минуты; нужна запущенная база)
make lint          # оформление кода
make quality       # отчет о качестве данных в reports/
```

### Windows

Проект запускается в **WSL2** — настоящем Linux (Ubuntu) внутри Windows. Docker Desktop
в Windows и так работает поверх WSL2, а в Ubuntu все команды этого README работают без изменений.
Нужны Windows 10 (версия 2004 и новее) или Windows 11.

**1. Установить WSL2 и Ubuntu.** PowerShell **от имени администратора**:

```powershell
wsl --install -d Ubuntu-24.04
```

Перезагрузить компьютер. При первом запуске Ubuntu (меню «Пуск» → Ubuntu 24.04) придумать
имя пользователя и пароль Linux — пароль понадобится для `sudo`.

**2. Установить Docker Desktop для Windows** и включить интеграцию с Ubuntu:
Docker Desktop → Settings → Resources → WSL integration → включить **Ubuntu-24.04** →
Apply & restart. Docker Desktop должен быть запущен, когда вы работаете с проектом.

**3. В терминале Ubuntu** установить инструменты (Python 3.12 в Ubuntu 24.04 уже есть):

```bash
sudo apt update
sudo apt install -y make git python3.12-venv openssl
docker run --rm hello-world          # проверка: Docker виден из Ubuntu
```

**4. Клонировать проект в домашнюю папку Linux** — не на диск `C:` (`/mnt/c/...`): там все
работает в разы медленнее, а Windows-программы меняют окончания строк.

```bash
cd ~
git clone <адрес репозитория> sma-pipeline
cd sma-pipeline
cp .env.example .env
for name in POSTGRES_PASSWORD SMA_LOADER_PASSWORD SMA_READER_PASSWORD SMA_APP_PASSWORD \
            MOCK_API_TOKEN MOCK_ADMIN_TOKEN; do
  sed -i "s/^$name=$/$name=$(openssl rand -hex 16)/" .env      # в Linux — sed -i без ''
done
```

Дальше — те же команды, что выше: `make install`, `make gen-medium`, `make dev`, `make test`.

**Редактор.** VS Code: установить расширение **WSL**, затем в терминале Ubuntu в папке проекта
выполнить `code .` — VS Code откроет проект внутри Linux, и его терминал тоже будет терминалом
Ubuntu. Файлы проекта из Проводника Windows: адрес `\\wsl$\Ubuntu-24.04\home\<имя>\sma-pipeline`
(открывать можно, но редактировать лучше в VS Code с расширением WSL).

**DBeaver и браузер** — обычные Windows-программы: `localhost:5432` (база),
`localhost:8000` (тестовый API), `localhost:8501` (дашборд Streamlit) из WSL видны в Windows.

**Частые проблемы в Windows**

| Симптом | Что делать |
|---|---|
| `docker: command not found` в Ubuntu | Docker Desktop не запущен или не включена интеграция с Ubuntu-24.04 (шаг 2) |
| `Makefile: *** missing separator` или `$'\r': command not found` | файлы получили окончания строк Windows (CRLF): проект клонирован в Windows, а не в Ubuntu. Клонировать заново в `~` из терминала Ubuntu (шаг 4) |
| порт 5432 занят | в Windows установлен свой PostgreSQL: остановить службу `postgresql-x64-…` (Win+R → `services.msc`) |
| все очень медленно | проект лежит на `/mnt/c/...` — перенести в `~` |
| `python3.12: command not found` | Ubuntu старше 24.04: `wsl --install -d Ubuntu-24.04` (шаг 1) |
| `make install` падает на `ensurepip` | не установлен `python3.12-venv` (шаг 3) |

## Команды

| Команда | Что делает |
|---|---|
| `make up` | база, миграции и загрузчик по расписанию (источники — `config/platforms.yaml`) |
| `make dev` | то же плюс тестовый API; загрузчик берет данные из него |
| `make db-up` | только база и миграции |
| `make down` | остановить все (данные сохраняются) |
| `make db-reset` | удалить базу и создать заново (только при разработке!) |
| `make logs`, `make psql` | журналы сервисов, консоль SQL |
| `make gen-small` / `gen-medium` / `gen-large` | синтетические наборы в `data/generated/` |
| `make load-files DATA=data/generated/medium` | разовая загрузка набора из файлов |
| `make loader` | загрузчик на этом компьютере (Ctrl+C — остановка) |
| `make loader-mock` | загрузчик на этом компьютере из тестового API на localhost:8000 |
| `make mock-api` | тестовый API на http://localhost:8000 (снимок набора medium) |
| `make mock-replay SPEED=10` | тестовый API «проигрывает» экзамен в 10 раз быстрее |
| `make quality` | проверки качества (раздел 13 спецификации), отчет в `reports/` |
| `make backup` | одна резервная копия в `backups/` |
| `make restore FILE=backups/sma_….dump TARGET=sma_restore` | восстановить копию в новую базу |
| `make backup-service` | копии каждые 30 минут в фоне (на время экзамена) |
| `make test`, `make test-backup`, `make cov` | тесты, тест копий в контейнере, покрытие |
| `make lint`, `make format` | проверить и поправить оформление кода |
| `make migrate` | применить новые миграции |

## Как устроено

| Папка | Что там |
|---|---|
| `db/migrations/` | SQL-схема: `001` схемы и права ролей, `002` core, `003` ops, `004` analytics и app, `005` витрины |
| `sma/contract.py` | описание формата данных платформ (формат v2) — одно на валидатор, генератор и тесты |
| `sma/loader/` | загрузчик: источники (файлы, HTTP), проверка, запись, планировщик |
| `sma/generator/` | генератор: население, модель Раша, версии записей во времени, закладка аномалий |
| `sma/reference/rules.py` | эталонные правила А1–А3, П1–П4 — только для самопроверки генератора |
| `sma/mockapi/` | тестовый API: снимок или воспроизведение экзамена, режимы сбоев |
| `sma/quality/` | проверки качества и отчет |
| `sma/backup/` | резервные копии и восстановление |
| `config/` | платформы для загрузчика, профили генератора, параметры экзаменов |

Роли базы: `sma_owner` — миграции; `sma_loader` — загрузчик (пишет в core и ops, обновляет
витрины); `sma_reader` — только чтение core и mart (аналитики, Streamlit); `sma_app` —
чтение mart, запись analytics и app.

## Синтетические данные

`make gen-medium` создает в `data/generated/medium/`:

- `ege/`, `oge/`, `vpr/`, `niko/` — ответы API платформ в формате v2 (снимок после апелляций);
- `*/timeline/*.jsonl` — все версии записей во времени (для воспроизведения);
- `answer_key.json` — **ключ**: какие аномалии А1–А3, П1–П4 и ловушки заложены, где,
  и что нашли эталонные правила. По нему аналитик проверяет свои правила;
- `manifest.json` — параметры, seed, время экзамена, контрольные суммы.

Данные **синтетические**: шкалы и коды кодификатора условные. Одинаковые профиль и seed
дают побайтно одинаковые файлы.

**Репетиция экзамена:** `make db-reset` и `make gen-medium`, затем в одном терминале
`make mock-replay SPEED=10`, в другом — `make loader-mock`. Экзамен «идет» в 10 раз быстрее:
сессии начинаются, ответы появляются по мере «сдачи», потом приходят оценки экспертов.

## Подключение к реальной платформе

1. В `config/platforms.yaml` у платформы: `source: api`, `base_url:` — адрес платформы
   (без `/api/v1/analytics`), `token_env:` — имя переменной с токеном.
2. В `.env` вписать токен в эту переменную.
3. `make down && make up`, затем `make logs` — ошибки запросов видны в журнале
   и в `mart.data_freshness` (`last_error_reason`: `http_401` — неверный токен,
   `timeout`, `network`, `bad_json`, `no_wrapper`).
4. `make quality` — отклоненные записи с причинами: `ops.rejected_records`.

## Перезапуск во время экзамена (≤ 5 минут)

```bash
make down          # загрузчик дожидается конца текущей страницы и выходит
make up            # продолжает с водяных знаков: ничего не теряется и не дублируется
```

Проверка: `SELECT * FROM mart.data_freshness;` — `last_ok_at` обновляется, `lag_sec` < 120.
Перед экзаменом: `make backup-service` (копии каждые 30 минут), восстановление —
`make restore` (на наборе medium — секунды; норма — 10 минут).

## Если что-то не так

| Симптом | Что делать |
|---|---|
| `failed to connect to the docker API` | запустить Docker Desktop |
| `failed to resolve host 'db'` | команды запускать через `make` (он подставляет `localhost`) |
| порт 5432 занят | остановить другой PostgreSQL на этом компьютере |
| после смены набора данные загрузились не все | `make db-reset`, затем загрузить заново: водяные знаки старого набора отсекают записи нового |
| тесты: «Нет подключения к PostgreSQL» | `make db-up` |
| тест копий пропущен | на компьютере нет pg_dump 16 — `make test-backup` (в контейнере) |
