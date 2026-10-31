# Спецификация для ИИ-агента: генераторы, загрузчик, хранилище и витрины СМА

Версия спецификации: 1 (26.10.2026). Основание: ТЗ СМА v0.3, «Формат данных для платформ» v2, лекции курса 4 и 5.

Этот файл самодостаточен: в нем есть все, что нужно, чтобы спроектировать, написать, протестировать и развернуть часть системы, за которую отвечает роль «Пайплайны». Читай его целиком перед началом работы. Если что-то в нем противоречит само себе — остановись и спроси пользователя (раздел 2.3).

---

## Оглавление

1. Контекст проекта
2. Как работать агенту
3. Границы задачи
4. Технологии и соглашения
5. Структура репозитория
6. Контракт данных платформ (формат v2)
7. Хранилище: схема PostgreSQL
8. Загрузчик
9. Тестовый API (mock)
10. Генератор тестовых данных
11. Эталонные правила для самопроверки генератора
12. Витрины
13. Качество данных и консистентность витрин
14. Резервное копирование и восстановление
15. Развертывание и воспроизводимость
16. Тестирование
17. Код-ревью и работа с git
18. Этапы работ (milestones) и критерии готовности
19. Трассировка к требованиям ТЗ
20. Допущения и открытые вопросы
21. Приложения: справочники и константы

---

## 1. Контекст проекта

### 1.1 Учебный проект

Университетский курс «Разработка IT-проектов». Группы студентов играют роли организаций:

- **платформы экзаменов** ЕГЭ, ОГЭ, ВПР, НИКО — каждая команда делает свою веб-платформу, на которой ученики сдают экзамен;
- **Рособрнадзор** (Группа 3) — заказчик и контроль. Внутри него команда **ГИАЦ** (Главный информационно-аналитический центр, BI-аналитика) делает **СМА** — систему мониторинга и анализа результатов.

Пользователь, с которым ты работаешь, — разработчик ГИАЦ в роли **«Пайплайны»**: генератор тестовых данных, загрузчик данных из платформ, хранилище PostgreSQL, витрины, качество данных, бэкапы, развертывание. Пользователь знает Python, NumPy, pandas, matplotlib, SQL на базовом уровне; объясняй ему решения простым языком на русском.

### 1.2 Сроки

| Неделя курса | Даты | Что должно быть готово |
| --- | --- | --- |
| 7 | до 31.10.2026 | Репозиторий, docker-compose, схема БД, загрузка тестовых данных из файлов |
| 8 | до 07.11.2026 | Загрузчик по расписанию, тестовый API, первые витрины, unit- и интеграционные тесты |
| 9 | до 14.11.2026 | Генератор с ключом, проверки качества, property-based тесты, покрытие, бэкапы, README |
| 10 | до 21.11.2026 | Подключение к реальным API платформ, витрины на реальных данных |
| 11 | до 28.11.2026 | Аудит, исправление замечаний |
| Demo Day | 05.12.2026 | Живой показ: экзамен идет, данные в реальном времени на экранах |

### 1.3 Путь данных

```text
Слой 1. ИСТОЧНИКИ      API платформ ЕГЭ, ОГЭ, ВПР, НИКО  |  ГЕНЕРАТОР (файлы)  |  ТЕСТОВЫЙ API
            │
            ↓  ЗАГРУЗЧИК: забирает, проверяет, пишет с заменой, журналирует
Слой 2. ХРАНЕНИЕ       PostgreSQL, схема core — нормализованный «склад», источник истины
            │
            ↓  SQL-представления
Слой 3. ВИТРИНЫ        схема mart — готовые наборы под графики и анализ
            │
            ↓
Слой 4. ИЗМЕРЕНИЯ      правила аномалий, графики, живой экран (Streamlit) — НЕ в этой задаче
```

### 1.4 Ключевые числа

- Загрузчик опрашивает каждый адрес раз в 60 с; к одному адресу — не чаще раза в 10 с.
- От события на платформе до появления на экране СМА — не более 120 с.
- На Demo Day — до 200 участников на платформу (допущение), экзамен до 240 минут.
- Персональных данных в хранилище — ноль.

---

## 2. Как работать агенту

### 2.1 Порядок

1. Работай по этапам раздела 18 строго по порядку M0 → M8. Не начинай следующий этап, пока у текущего не выполнены критерии готовности.
2. В начале каждого этапа кратко опиши пользователю, что будешь делать и какие файлы появятся.
3. После каждого этапа: запусти тесты (`make test`), линтер (`make lint`), покажи результат, кратко объясни сделанное.
4. Предлагай пользователю самому написать часть кода, если он хочет учиться: давай заготовку функции, тесты к ней и подсказки. Спрашивай один раз в начале работы, какой режим он предпочитает: «агент пишет все» или «агент пишет каркас и тесты, пользователь — логику».

### 2.2 Правила

- **Не меняй контракт данных** (раздел 6) без согласия пользователя: его уже получили другие команды.
- **Никаких реальных персональных данных** нигде: ни в коде, ни в тестах, ни в примерах. Только псевдонимы вида p_7f3a9c.
- **Никаких секретов в репозитории:** токены, пароли — только в `.env` (в `.gitignore`), в репозитории — `.env.example` с пустыми значениями.
- **Не выдумывай официальные коды и шкалы.** Коды предметов ЕГЭ и ОГЭ — из приложения 21.1. Шкалы перевода и коды кодификатора в генераторе — условные и помечаются как синтетические.
- **Детерминизм:** генератор с одинаковыми параметрами и seed дает побайтно одинаковый результат.
- **Простота важнее изящества:** код читает начинающий разработчик. Явные имена, короткие функции, докстринги на русском, без метапрограммирования.
- **Каждое требование — с тестом.** Добавил поведение — добавь тест.
- **Не удаляй и не переписывай** чужие таблицы (схемы analytics, app) без согласования: их логикой владеют другие участники команды.

### 2.3 Когда остановиться и спросить

- Требование спецификации невозможно выполнить или оно противоречит другому.
- Нужно изменить контракт данных или публичную схему БД (core, mart).
- Нужна новая внешняя зависимость, не перечисленная в разделе 4.
- Тест из раздела 16 не удается сделать зеленым за разумное время.

В остальных случаях выбирай разумное решение сам, записывай его в `docs/decisions.md` (дата, решение, причина) и продолжай.

---

## 3. Границы задачи

### 3.1 Входит

| Компонент | Что сделать |
| --- | --- |
| Хранилище | Схема PostgreSQL: core, ops, mart; заготовки analytics и app по спискам полей (раздел 7) |
| Загрузчик | Опрос API или чтение файлов, проверка записей, идемпотентная запись, журнал, отклоненные записи, водяные знаки since |
| Тестовый API | Сервер, имитирующий платформу: токен, since, страницы, режимы сбоев, режим «воспроизведения» экзамена |
| Генератор | Синтетические данные четырех экзаменов в формате API, с заложенными аномалиями и ключом |
| Эталонные правила | Реализация правил А1–А3, П1–П4 только для самопроверки генератора |
| Витрины | Представления схемы mart под графики и анализ |
| Качество данных | Проверки полноты, уникальности, согласованности, своевременности, точности; отчет |
| Бэкапы | pg_dump по расписанию, восстановление |
| Развертывание | docker-compose, Makefile, README, точные версии |
| Тесты | unit, интеграционные, property-based, покрытие |

### 3.2 Не входит

- Боевой расчет правил аномалий и индекса (делает аналитик аномалий; ему нужны твои витрины и генератор).
- Графики и страницы Streamlit, вход пользователей (делают другие).
- Платформы экзаменов (делают другие команды).

---

## 4. Технологии и соглашения

### 4.1 Стек

| Что | Чем | Версия (фиксировать точно) |
| --- | --- | --- |
| Язык | Python | 3.12.x |
| База | PostgreSQL в Docker | образ postgres:16.4 |
| Драйвер БД | psycopg (v3) | 3.2.x |
| SQL-утилиты | SQLAlchemy Core (только для pandas.read_sql) | 2.0.x |
| Данные | pandas, numpy, scipy | pandas 2.2.x, numpy 2.x, scipy 1.14.x |
| HTTP-клиент | requests | 2.32.x |
| Тестовый API | Flask | 3.0.x |
| Настройки | python-dotenv, PyYAML | — |
| Тесты | pytest, hypothesis, coverage, pytest-cov | — |
| Линтер | ruff | — |
| Контейнеры | Docker, docker compose v2 | — |

Точные версии фиксируются в `requirements.txt` (`==`) на этапе M0. Других зависимостей без согласия пользователя не добавлять.

### 4.2 Соглашения по коду

- Идентификаторы, имена файлов, столбцов, полей — на английском, snake_case.
- Докстринги, комментарии, README, отчеты, сообщения журнала для людей — на русском. Коды причин (reason_code) — на английском.
- Время в коде — только timezone-aware `datetime`. Никаких naive datetime.
- SQL — в файлах `.sql` или в константах модуля `store.py`; только параметризованные запросы, без сборки SQL строками из данных.
- Конфигурация — `config/*.yaml`; секреты — переменные окружения.
- Логи — модуль `logging`, уровень INFO; формат с временем и именем модуля.
- В тексте документов не использовать букву U+0451 («е» с двумя точками) — пишем «е» (принято в команде).

---

## 5. Структура репозитория

```text
sma-pipeline/
  README.md                      развертывание с нуля за 30 минут
  Makefile                       все команды (раздел 15.3)
  docker-compose.yml             db, loader, mock-api, backup
  Dockerfile                     образ для loader, mock-api, backup
  requirements.txt               точные версии
  .env.example                   переменные без значений
  .gitignore                     .env, backups/, data/generated/, __pycache__, .coverage
  config/
    platforms.yaml               какие платформы, источник file|api, адреса, интервалы
    generator/
      small.yaml                 профиль маленького набора
      medium.yaml                профиль среднего набора
      large.yaml                 профиль для T-06
      exams.yaml                 параметры экзаменов (раздел 10.3)
  db/
    migrations/
      001_schemas.sql
      002_core.sql
      003_ops.sql
      004_analytics_app.sql
      005_marts.sql
    migrate.py                   применяет миграции по порядку, ведет ops.schema_migrations
  sma/
    __init__.py
    contract.py                  описание контракта: поля, типы, обязательность, перечисления
    normalize.py                 нормализация ответов
    timeutil.py                  разбор и сравнение времени
    loader/
      sources.py                 FileSource, ApiSource
      validate.py                проверка записей
      store.py                   upsert, журнал, отклоненные записи, водяные знаки
      cycle.py                   один цикл загрузки
      run.py                     планировщик и точка входа
    mockapi/
      server.py                  Flask-приложение
      timeline.py                режим воспроизведения
    generator/
      model.py                   модель ответов (Раш)
      exams.py                   конфигурации экзаменов, шкалы, КИМ
      population.py              регионы, школы, аудитории, участники
      anomalies.py               закладка аномалий и ловушек
      timeline.py                версии записей во времени
      writer.py                  запись файлов в формате API
      generate.py                точка входа
    reference/
      rules.py                   эталонные А1–А3, П1–П4
    quality/
      checks.py                  проверки качества
      report.py                  отчет JSON и Markdown
    backup/
      run.py                     резервное копирование по расписанию
      restore.py                 восстановление
  tests/
    unit/
    integration/
    properties/
    conftest.py
  data/
    samples/                     маленький закоммиченный набор для тестов
    generated/                   сгенерированные наборы (в .gitignore)
  docs/
    decisions.md                 журнал решений
    er_diagram.md                ER-диаграмма (mermaid)
    quality_report_example.md
```

---

## 6. Контракт данных платформ (формат v2)

Это то, что отдают платформы и что должны в точности воспроизводить генератор и тестовый API. Реализуй контракт как данные в `sma/contract.py` (словарь полей с типом, обязательностью присутствия, допустимостью null, перечислением), чтобы валидатор, генератор и тесты использовали одно описание.

### 6.1 Общие правила

- Только GET. Базовый путь `/api/v1/analytics`. Доступ — заголовок `Authorization: Bearer <токен>`; без токена — 401; не GET — 405; неверный параметр — 400.
- Ответ со списком: `{"page": int, "total_pages": int, "items": [ ... ]}`; не более 1000 записей на страницу; параметры `page` (с 1) и `limit` (≤ 1000).
- `since` — ISO 8601 со смещением; вернуть записи с `updated_at > since`. Записи отсортированы по `(updated_at, record_id)` по возрастанию.
- JSON, UTF-8. Время — ISO 8601 со смещением часового пояса (`2026-12-05T10:00:00+03:00`). Разные записи могут иметь разные смещения.
- **Каждое поле контракта присутствует в записи всегда.** Отсутствие значения — `null`, не пропуск поля.
- Числа — числами, строки — строками. `answer` — строка ровно как ввел ученик. `subject_code`, `region_code`, `school_code`, `room_code` — строки.
- Если данных нет — `"items": []`, не ошибка.
- Новые данные — в API не позднее 60 с после события.

### 6.2 Служебные поля (есть в каждой записи каждого списочного адреса)

| Поле | Тип | null | Смысл |
| --- | --- | --- | --- |
| record_id | string, 1–64 символа | нет | Постоянный идентификатор записи в пределах платформы. Не меняется при изменении записи |
| updated_at | datetime | нет | Время последнего изменения записи |
| deleted | boolean | нет | true — запись удалена или отозвана |

Правила идемпотентности: одна запись — всегда один record_id; при изменении — тот же record_id и новый updated_at; запись со старшим updated_at не перезаписывается младшим; удаленные записи приходят с `deleted: true`.

### 6.3 Адреса

| Адрес | Тип ответа | Параметры | Когда опрашивать (по умолчанию) |
| --- | --- | --- | --- |
| /sessions | список | since, page, limit | 60 с |
| /answers | список | since, page, limit | 60 с |
| /expert-reviews | список | since, page, limit | 60 с |
| /proctoring-events | список | since, page, limit | 60 с |
| /live-status | объект | нет | 30 с |
| /dictionaries/regions | список | page, limit | 3600 с |
| /dictionaries/schools | список | page, limit | 3600 с |
| /dictionaries/subjects | список | page, limit | 3600 с |
| /dictionaries/kim | список | since, page, limit | 600 с; до конца экзамена — пустой список |
| /dictionaries/scales | список | since, page, limit | 600 с |

### 6.4 /sessions — сессия ученика

Одна запись = один ученик на одном экзамене (один предмет).

| Поле | Тип | null | Значения и правила |
| --- | --- | --- | --- |
| participant_id | string | нет | Псевдоним ученика, одинаковый во всех адресах платформы |
| exam_type | enum | нет | EGE, OGE, VPR, NIKO |
| subject_code | string `^\d{2}$` | нет | Код предмета (приложение 21.1) |
| grade_level | int 4–11 | нет | Класс |
| variant_id | string | нет | Вариант |
| region_code | string `^\d{2}$` | нет | Код субъекта РФ |
| school_code | string | нет | Код школы |
| room_code | string | да | Код аудитории |
| status | enum | нет | absent, started, in_progress, finished, annulled |
| started_at | datetime | да | null только при absent |
| finished_at | datetime | да | Не null при finished |
| primary_score | int ≥ 0 | да | null, если result_status = pending или status в absent, annulled, started, in_progress |
| max_primary_score | int > 0 | нет | — |
| result_status | enum | нет | pending, final, appeal_changed |
| ege / oge / vpr / niko | object | — | Ровно один блок, соответствующий exam_type; другие блоки отсутствуют |

Блоки экзамена (значения — null, пока result_status = pending или работа не завершена):

| Блок | Поле | Тип | Правила |
| --- | --- | --- | --- |
| ege | test_score | int 0–100 или null | — |
| oge | grade | int 2–5 или null | — |
| oge | block_scores | object string→int или null | Например {"algebra": 12, "geometry": 9} |
| oge | block_failed | boolean или null | Не набран минимум по блоку |
| vpr | grade | int 2–5 или null | — |
| vpr | term_mark | int 2–5 или null | Отметка за четверть по журналу; необязательная по смыслу |
| niko | result_level | enum или null | below_basic, basic, above_basic |
| niko | questionnaire_done | boolean или null | — |

### 6.5 /answers — ответ на задание с кратким ответом

| Поле | Тип | null | Правила |
| --- | --- | --- | --- |
| participant_id | string | нет | — |
| exam_type | enum | нет | — |
| task_number | int ≥ 1 | нет | — |
| answer | string | нет | Как ввел ученик; пустой — "" |
| is_correct | boolean | нет | Для "" — false |
| score | int ≥ 0 | нет | ≤ max_score |
| max_score | int > 0 | нет | — |
| answered_at | datetime | нет | — |
| time_spent_sec | int ≥ 0 | да | — |
| audio_plays | int ≥ 0 | да | Только ОГЭ, задания с аудио; иначе null |

### 6.6 /expert-reviews — оценка задания с развернутым ответом

Запись появляется, как только работа сдана (check_status = pending), и обновляется после проверки (тот же record_id).

| Поле | Тип | null | Правила |
| --- | --- | --- | --- |
| participant_id | string | нет | — |
| exam_type | enum | нет | — |
| task_number | int ≥ 1 | нет | — |
| check_status | enum | нет | pending, checked |
| check_stage | enum | да | first, third_check, appeal; null при pending |
| expert_code | string | да | null при pending |
| score | int ≥ 0 | да | null при pending; ≤ max_score |
| max_score | int > 0 | нет | — |
| reviewed_at | datetime | да | null при pending |

Правило: null — «значения пока нет»; 0 — «проверено, ноль баллов».

### 6.7 /proctoring-events — событие прокторинга

| Поле | Тип | null | Правила |
| --- | --- | --- | --- |
| participant_id | string | нет | — |
| exam_type | enum | нет | — |
| event_type | enum | нет | tab_switch, copy, paste, fullscreen_exit, focus_lost |
| occurred_at | datetime | нет | — |
| task_number | int | да | — |

Если у платформы нет прокторинга (обычно ВПР) — всегда `"items": []`.

### 6.8 /live-status — сводка (объект, не список, без служебных полей)

`{"exam_type": "OGE", "in_progress": 42, "finished": 17, "annulled": 1, "absent": 3, "generated_at": "2026-12-05T10:30:00+03:00"}`

### 6.9 Справочники

| Адрес | Поля (плюс служебные) |
| --- | --- |
| /dictionaries/regions | region_code, name |
| /dictionaries/schools | school_code, region_code, name |
| /dictionaries/subjects | exam_type, subject_code, name, grade_level, max_primary_score, threshold_score |

threshold_score — пороговый первичный балл: ЕГЭ — минимальный; ОГЭ и ВПР — минимум для отметки 3; НИКО — граница базового уровня. В справочниках поле `name` разрешено (это не ФИО).

### 6.10 /dictionaries/kim — структура КИМ

Одна запись = одно задание одного варианта. Тексты заданий и верные ответы не передаются. До окончания экзамена — пустой список.

| Поле | Тип | null | Правила |
| --- | --- | --- | --- |
| exam_type | enum | нет | — |
| subject_code | string | нет | — |
| exam_year | int | нет | — |
| variant_id | string | нет | Как в /sessions |
| task_number | int | нет | Как в /answers, /expert-reviews |
| part | int 1–2 | нет | — |
| check_type | enum | нет | auto, expert |
| max_score | int > 0 | нет | — |
| level | enum | нет | basic, advanced, high |
| content_codes | array of string | нет | Коды элементов содержания по кодификатору; может быть [] |
| skill_codes | array of string | нет | Коды требований (умений); может быть [] |
| section | string | нет | Раздел кодификатора |
| block | string | да | Блок задания, если в предмете есть блоки |

### 6.11 /dictionaries/scales — шкалы перевода

| Поле | Тип | null | Правила |
| --- | --- | --- | --- |
| exam_type | enum | нет | — |
| subject_code | string | нет | — |
| exam_year | int | нет | — |
| scale_type | enum | нет | primary_to_test (ЕГЭ), primary_to_grade (ОГЭ, ВПР), primary_to_level (НИКО) |
| points | array | нет | primary_to_test: [{"primary": int, "test": int}] для каждого балла 0..max; остальные: [{"min": int, "max": int, "result": int или string}] без пропусков и пересечений, покрывают 0..max |
| conditions | array | нет | [{"block": string, "min_score": int, "result_if_not_met": int или string}]; может быть [] |
| scale_version | int ≥ 1 | нет | Новая версия — новая запись; старые не изменяются |
| valid_from | datetime | нет | С какого момента действует версия |

### 6.12 Как отличить состояния

| Ситуация | Признак |
| --- | --- |
| Не явился | sessions.status = absent, started_at и баллы null, ответов и оценок нет |
| Аннулирована | sessions.status = annulled, баллы null |
| Не ответил на задание части 1 | answers.answer = "", score = 0 |
| Не проверено | expert_reviews.check_status = pending, score null |
| Проверено, 0 баллов | check_status = checked, score = 0 |
| Итог не окончательный | sessions.result_status = pending, primary_score null |
| Изменено после апелляции | expert_reviews.check_stage = appeal; sessions.result_status = appeal_changed |

### 6.13 Запрещенные поля (персональные данные)

В любой записи списочных адресов, кроме справочников regions, schools, subjects, на любом уровне вложенности запрещены ключи (без учета регистра): fio, full_name, name, first_name, last_name, middle_name, surname, patronymic, birth_date, birthday, date_of_birth, snils, passport, passport_number, phone, phone_number, email, e_mail, ip, ip_address, address. Запись с таким ключом отклоняется целиком (NFR-12).

---

## 7. Хранилище: схема PostgreSQL

### 7.1 Схемы

| Схема | Что в ней | Владелец логики |
| --- | --- | --- |
| core | Нормализованные данные платформ — источник истины | я (Пайплайны) |
| ops | Журналы загрузок, отклоненные записи, водяные знаки, качество, бэкапы, миграции | я |
| mart | Витрины | я |
| analytics | Результаты правил аномалий и индекса | аналитик аномалий (я создаю таблицы) |
| app | Пользователи и журнал входов | разработчик дашбордов (я создаю таблицы) |

Роли БД: `sma_owner` (миграции), `sma_loader` (запись в core, ops; чтение mart), `sma_reader` (только чтение mart и core — для аналитиков и Streamlit), `sma_app` (чтение mart, запись analytics, app). Пароли — из `.env`.

### 7.2 Принципы

- **Идентичность записи — (exam_type, record_id).** Это первичный ключ всех таблиц core с данными платформ. Upsert идет по нему.
- **Защита от устаревших версий:** обновление применяется только если `EXCLUDED.updated_at >= table.updated_at`.
- **Мягкое удаление:** `deleted boolean`; записи физически не удаляются; витрины берут только `deleted = false`.
- **Естественные ключи** (например exam_type + participant_id + task_number) не ограничиваются в БД уникальным индексом, а проверяются в качестве данных (раздел 13): платформа может прислать дубль с другим record_id, и загрузка не должна падать.
- **Время — TIMESTAMPTZ.** Исходная строка со смещением сохраняется в `payload`.
- **payload JSONB** — исходная запись после удаления неизвестных полей, для прослеживаемости и точности (сверка с источником).
- **Служебные столбцы** в каждой таблице core: `loaded_at timestamptz default now()`, `load_id bigint references ops.load_log(id)`.
- **Нормализация:** названия регионов и школ — только в справочниках; в фактах — коды.
- **Внешние ключи на справочники не ставить:** справочники regions, schools, subjects могут прийти позже фактов, и загрузка не должна падать. Ссылочная целостность проверяется в качестве данных (Q-CONS-07). Это осознанное решение — записать в docs/decisions.md.

### 7.3 Таблицы core

Ниже — обязательный набор столбцов. Типы — PostgreSQL.

**core.sessions** — PK (exam_type, record_id)

| Столбец | Тип |
| --- | --- |
| exam_type | text not null |
| record_id | text not null |
| participant_id | text not null |
| subject_code | text not null |
| grade_level | smallint not null |
| variant_id | text not null |
| region_code | text not null |
| school_code | text not null |
| room_code | text |
| status | text not null, check in (absent, started, in_progress, finished, annulled) |
| started_at | timestamptz |
| finished_at | timestamptz |
| primary_score | smallint |
| max_primary_score | smallint not null |
| result_status | text not null, check in (pending, final, appeal_changed) |
| exam_block | jsonb — содержимое блока ege/oge/vpr/niko |
| updated_at | timestamptz not null |
| deleted | boolean not null |
| payload | jsonb not null |
| loaded_at, load_id | служебные |

Индексы: (exam_type, participant_id), (exam_type, subject_code, region_code, school_code), (updated_at).

**core.answers** — PK (exam_type, record_id): exam_type, record_id, participant_id, task_number smallint, answer text not null, answer_norm text not null (нормализованный ответ, раздел 8.6), is_correct boolean, score smallint, max_score smallint, answered_at timestamptz, time_spent_sec int, audio_plays smallint, updated_at, deleted, payload, служебные. Индексы: (exam_type, participant_id), (exam_type, task_number).

**core.expert_reviews** — PK (exam_type, record_id): participant_id, task_number, check_status, check_stage, expert_code, score, max_score, reviewed_at, updated_at, deleted, payload, служебные. Индекс (exam_type, participant_id).

**core.proctoring_events** — PK (exam_type, record_id): participant_id, event_type, occurred_at, task_number, updated_at, deleted, payload, служебные. Индекс (exam_type, participant_id).

**core.kim_tasks** — PK (exam_type, record_id): subject_code, exam_year, variant_id, task_number, part, check_type, max_score, level, content_codes text[], skill_codes text[], section, block, updated_at, deleted, payload, служебные. Индекс (exam_type, subject_code, exam_year, variant_id, task_number).

**core.scales** — PK (exam_type, record_id): subject_code, exam_year, scale_type, points jsonb, conditions jsonb, scale_version int, valid_from timestamptz, updated_at, deleted, payload, служебные.

**core.regions** — PK region_code: name, updated_at. **core.schools** — PK school_code: region_code, name, updated_at. **core.subjects** — PK (exam_type, subject_code): name, grade_level, max_primary_score, threshold_score, updated_at. Справочники разных платформ сливаются в общие таблицы; расхождения названий между платформами фиксируются в качестве данных (снимки — в ops.dictionary_snapshots).

**core.live_status** — PK exam_type: in_progress, finished, annulled, absent, generated_at, loaded_at. История — ops.live_status_history (все полученные снимки).

### 7.4 Таблицы ops

| Таблица | Назначение | Столбцы |
| --- | --- | --- |
| ops.load_log | Журнал каждого запроса | id bigserial PK, platform, endpoint, started_at, finished_at, status (ok, error, partial), http_status, since_value, pages, received, saved, rejected, skipped_stale, max_updated, error_reason, message |
| ops.rejected_records | Отклоненные записи | id, load_id → load_log, platform, endpoint, record_id (может быть null), reason_code, field, message, payload jsonb (без запрещенных полей: их значения заменяются на "<removed>"), created_at |
| ops.watermarks | Последний успешный since по адресу | PK (platform, endpoint), watermark timestamptz, updated_at |
| ops.dictionary_snapshots | Снимки справочников по платформам | platform, endpoint, received_at, payload jsonb |
| ops.live_status_history | Все снимки /live-status | exam_type, generated_at, payload, received_at |
| ops.quality_runs | Запуски проверок качества | id, started_at, finished_at, issues_total, summary jsonb |
| ops.quality_issues | Найденные нарушения | run_id, check_code, severity (error, warning, info), exam_type, entity, entity_key, details jsonb |
| ops.backup_log | Резервные копии | id, started_at, finished_at, file_path, size_bytes, status, message |
| ops.schema_migrations | Примененные миграции | filename PK, applied_at |

reason_code для отклонений: `missing_field`, `null_not_allowed`, `wrong_type`, `bad_enum`, `bad_format`, `out_of_range`, `pii_field`, `wrong_exam_block`, `exam_type_mismatch` (exam_type записи не совпадает с платформой), `bad_record_id`.

### 7.5 Таблицы analytics и app (только структура)

Создать по ТЗ СМА v0.3, логику не писать:

- analytics.anomalies: anomaly_id bigserial PK, rule (A1, A2, A3), exam_type, subject_code, object_type (school, group, participant), object_id, region_code, school_code, value numeric, threshold numeric, detected_at timestamptz; unique (rule, exam_type, object_type, object_id).
- analytics.analysis_runs: run_id bigserial PK, started_at, finished_at, params jsonb.
- analytics.p1_groups: run_id → analysis_runs, group_id, exam_type, subject_code, school_code, room_code, variant_id, participant_ids text[], size, min_p_value; PK (run_id, group_id).
- analytics.school_index: run_id, exam_type, subject_code, region_code, school_code, participants, p1, p2, p3, p4a, p4b, z1, z2, z3, z4a, z4b, index, in_list; PK (run_id, exam_type, subject_code, school_code).
- app.users: login PK, password_hash, role (staff, admin), blocked boolean, failed_attempts int, locked_until timestamptz, created_at.
- app.login_log: id, login, attempted_at, success boolean.

### 7.6 ER-диаграмма

Сгенерировать `docs/er_diagram.md` в формате mermaid `erDiagram` по фактической схеме core и связям по смыслу (sessions ↔ answers, expert_reviews, proctoring_events по exam_type + participant_id; sessions → schools → regions по кодам; kim_tasks ↔ answers по exam_type + subject_code + variant_id + task_number через sessions).

---

## 8. Загрузчик

### 8.1 Конфигурация `config/platforms.yaml`

```yaml
platforms:
  - exam_type: EGE
    source: file            # file | api
    path: data/generated/medium/ege      # для file
    base_url: null          # для api, например https://ege.example/
    token_env: EGE_API_TOKEN
    enabled: true
  - exam_type: OGE
    source: api
    base_url: http://mock-api:8000/oge
    token_env: MOCK_API_TOKEN
    enabled: true
  # VPR, NIKO аналогично
intervals_sec:
  facts: 60                 # sessions, answers, expert-reviews, proctoring-events
  live_status: 30
  kim_scales: 600
  dictionaries: 3600
min_gap_per_endpoint_sec: 10
request_timeout_sec: 30
since_overlap_sec: 5
initial_since: "1970-01-01T00:00:00+00:00"
```

### 8.2 Источники

Общий интерфейс:

```python
class Source(Protocol):
    def fetch(self, endpoint: str, since: datetime | None, page: int) -> FetchResult: ...
```

FetchResult: `items: list[dict] | dict`, `page`, `total_pages`, `http_status`, `elapsed_sec`.

- **ApiSource** — requests.get с параметрами since (ISO со смещением), page, limit=1000; заголовок Authorization; timeout из конфига. Ошибки сети, таймаут, код ≠ 200, неверный JSON, отсутствие page/total_pages/items → исключение FetchError(reason_code, message).
- **FileSource** — читает файлы генератора (`{endpoint}.json` или `{endpoint}.page-N.json`, где endpoint пишется через дефис, а `/dictionaries/x` → `dictionaries-x`). Фильтрует по since и режет на страницы сам, чтобы вести себя как API.

### 8.3 Один цикл для одного адреса

1. Взять водяной знак W из ops.watermarks (если нет — initial_since). since = W − since_overlap_sec (перекрытие, чтобы не потерять записи с одинаковым временем; дубли безопасны благодаря upsert).
2. Запросить страницу 1, затем 2..total_pages с тем же since. Соблюдать min_gap_per_endpoint_sec между циклами одного адреса (страницы одного цикла — подряд).
3. Каждую запись: проверить (8.4); отклоненные — в ops.rejected_records; годные — нормализовать (8.6) и подготовить строки.
4. Записать годные одной транзакцией на страницу: upsert (8.5).
5. После всех страниц: обновить ops.watermarks значением max(updated_at) по всем полученным записям (включая отклоненные, если у них разбирается updated_at; иначе по принятым), но только если цикл завершен без ошибок FetchError.
6. Записать ops.load_log: status ok или partial (были отклонения), счетчики, max_updated.
7. При FetchError: ops.load_log со status error, error_reason, message; водяной знак не трогать; перейти к следующему адресу.

Для /live-status: без since и страниц; upsert в core.live_status и вставка в ops.live_status_history.

Для справочников regions, schools, subjects: полная выгрузка (без since); upsert в общие таблицы; снимок в ops.dictionary_snapshots.

### 8.4 Проверка записи (validate.py)

Возвращает `(ok: bool, cleaned: dict, problems: list[Problem])`. Порядок:

1. Запрещенные ключи (6.13) на любом уровне → отклонить, reason `pii_field`, field = ключ. Значения в ops.rejected_records не сохранять.
2. record_id: строка 1–64 символа → иначе `bad_record_id`.
3. Каждое поле контракта присутствует → иначе `missing_field`.
4. null там, где нельзя → `null_not_allowed`.
5. Тип → `wrong_type` (bool не считается int; int не принимается вместо string для кодов).
6. Перечисления → `bad_enum`; форматы (subject_code, region_code `^\d{2}$`, datetime со смещением) → `bad_format`; диапазоны → `out_of_range`.
7. exam_type записи равен exam_type платформы из конфига → иначе `exam_type_mismatch`.
8. Блок экзамена: ровно один, соответствующий exam_type → иначе `wrong_exam_block`.
9. Неизвестные поля: удалить из записи перед сохранением, записать предупреждение в лог (не отклонять).

Согласованность между полями (например, primary_score при absent) **не отклоняет** запись — это проверка качества (раздел 13).

### 8.5 Запись (store.py)

```sql
INSERT INTO core.answers (exam_type, record_id, ..., updated_at, deleted, payload, load_id)
VALUES (...)
ON CONFLICT (exam_type, record_id) DO UPDATE SET
    participant_id = EXCLUDED.participant_id,
    ...,
    updated_at = EXCLUDED.updated_at,
    deleted = EXCLUDED.deleted,
    payload = EXCLUDED.payload,
    load_id = EXCLUDED.load_id,
    loaded_at = now()
WHERE EXCLUDED.updated_at >= core.answers.updated_at;
```

Число строк, не обновленных из-за условия WHERE, учитывать как skipped_stale. Использовать пакетную вставку (executemany или COPY во временную таблицу + INSERT ... SELECT). Каждая страница — отдельная транзакция.

После каждого полного цикла по всем платформам — обновить материализованные витрины (раздел 12) командой `REFRESH MATERIALIZED VIEW CONCURRENTLY`.

### 8.6 Нормализация ответа (sma/normalize.py)

`normalize_answer(s) = s` → удалить все пробельные символы → заменить «.» на «,» → привести к нижнему регистру → заменить букву U+0451 (е с двумя точками) на «е». Пустая строка остается пустой. Функция идемпотентна.

### 8.7 Планировщик (run.py)

- Один поток на платформу (ThreadPoolExecutor); внутри платформы адреса по очереди; у каждого адреса свой интервал и время следующего запуска (time.monotonic).
- Отдельное соединение с БД на поток.
- Корректная остановка по SIGINT/SIGTERM: дождаться окончания текущей страницы, закрыть соединения.
- Перезапуск продолжает с водяных знаков: ничего не теряется, дубли не создаются (NFR-14).
- Режимы запуска: `python -m sma.loader.run` (бесконечно), `--once` (один цикл по всем адресам и выход — для тестов и разовой загрузки), `--platform OGE` (только одна).

---

## 9. Тестовый API (mock)

Flask-приложение `sma/mockapi/server.py`, обслуживает одну или несколько платформ: `/{platform}/api/v1/analytics/...`, где platform — ege, oge, vpr, niko.

### 9.1 Поведение

- Данные — из папки генератора (снимок) или из timeline (режим воспроизведения).
- Токен — из переменной MOCK_API_TOKEN; без него или с неверным — 401.
- Не GET — 405. Неверные since, page, limit — 400.
- since, сортировка, страницы, обертка — строго по разделу 6.1.
- /dictionaries/kim — пустой список, пока «текущее время» меньше конца экзамена (exam_end из manifest генератора).

### 9.2 Режим воспроизведения (replay)

Генератор пишет `timeline/{endpoint}.jsonl`: все версии записей с полем `visible_from` (когда версия становится видимой на платформе). Сервер в режиме replay считает «текущее время экзамена» = exam_start + (реальное время − время запуска) × speed. Для каждого record_id отдается последняя версия с visible_from ≤ текущему времени. /live-status рассчитывается по видимым сессиям. Параметры: `--replay --speed 10` (в 10 раз быстрее), `--start-offset 0`.

Так можно прогнать «экзамен» за 25 минут и проверить живой экран, since, переходы pending → checked → appeal.

### 9.3 Режимы сбоев (для тестов)

Управляющие адреса (только при MOCK_ADMIN=1, без токена платформы, с MOCK_ADMIN_TOKEN):

| Адрес | Что делает |
| --- | --- |
| POST /_admin/mode {"platform": "oge", "mode": "normal"} | normal, down (сразу 503), slow (ждать N секунд, параметр delay_sec), broken_json, no_wrapper |
| POST /_admin/inject {"platform", "endpoint", "record"} | добавить или заменить запись (по record_id) — для T-02, T-04 |
| POST /_admin/inject_bad {"platform", "endpoint", "kind"} | missing_field, pii_field, wrong_type, bad_enum — запись с указанным дефектом |
| GET /_admin/requests | журнал полученных запросов (время, адрес, since, page) — для T-01, T-24 |
| POST /_admin/reset | вернуть исходное состояние |

---

## 10. Генератор тестовых данных

### 10.1 Назначение и запуск

Создает синтетические данные экзаменов в точном формате раздела 6, с заложенными аномалиями и ключом ответов. Используется для разработки и проверки всего СМА до появления реальных платформ (FR-06).

```bash
python -m sma.generator.generate --profile config/generator/medium.yaml --out data/generated/medium --seed 2026
```

Параметры профиля (все можно переопределить флагами):

```yaml
exams: [EGE, OGE, VPR, NIKO]
participants_per_exam: 430
variants_per_exam: 2
regions:
  dictionary_count: 20          # сколько регионов в справочнике
  used_count: 6                 # в скольких реально сдают
  big_regions: 4                # регионы с 8–10 школами (нужны для А1)
  schools_per_big_region: 10
schools:
  dictionary_count: 120
  size_min: 10                  # учеников в школе
  size_max: 12
room_size_max: 15
rates:
  absent: 0.03
  annulled: 0.02
  appeal: 0.01                  # доля работ с изменением после апелляции
  empty_answer_base: 0.04
snapshot: after_appeal          # during_exam | after_exam | after_check | after_appeal
anomalies:
  A1: 1                         # школ на экзамен
  A2: 1                         # групп
  A3: 2                         # участников (только если есть прокторинг)
  P1: 1                         # групп в аудитории
  P2: 1                         # участников
  P3: 1                         # школ
  P4a: 1                        # школ
  P4b: 1                        # школ
decoys:
  A2_blank: 1
  A3_focus: 1
self_check: true
max_attempts: 60
```

Профили: small (≈40 на экзамен, 3 региона, 8 школ, без А1 и П3–П4: мало школ), medium (как выше), large (T-06: 400 участников, 20 регионов, 100 школ, 200 аудиторий; аномалии по умолчанию 0).

### 10.2 Выход

```text
out/
  manifest.json                 параметры, seed, версия генератора, время экзамена по платформам, контрольные суммы файлов
  answer_key.json               ключ (10.8)
  README.md                     описание набора, пометка «данные синтетические»
  ege/ oge/ vpr/ niko/
    sessions.json | sessions.page-N.json
    answers...json
    expert-reviews...json
    proctoring-events.json      у ВПР — пустой список
    live-status.json
    dictionaries-regions.json
    dictionaries-schools.json
    dictionaries-subjects.json
    dictionaries-kim.json
    dictionaries-scales.json
    timeline/
      sessions.jsonl            все версии записей с visible_from
      answers.jsonl
      expert-reviews.jsonl
      proctoring-events.jsonl
      dictionaries-kim.jsonl
      dictionaries-scales.jsonl
```

Файлы в папках экзаменов — снимок на момент snapshot. Страницы: если записей больше 1000 — `name.page-1.json`, `name.page-2.json` ...; иначе `name.json`. JSON с отступом 2, ensure_ascii=False, ключи в порядке контракта.

### 10.3 Экзамены (config/generator/exams.yaml)

Значения шкал и коды кодификатора — **условные, синтетические**; в данных и README пометить это явно.

| Параметр | ЕГЭ | ОГЭ | ВПР | НИКО |
| --- | --- | --- | --- | --- |
| subject_code | 02 (математика профильная) | 02 (математика) | 01 (русский язык) | 05 (информатика, код по ОГЭ — допущение) |
| grade_level | 11 | 9 | 5 | 8 |
| Задания части 1 (auto, по 1 баллу) | 1–12 | 1–19 | 1–8 | 1–10 |
| Задания части 2 (expert, макс. балл) | 13:2, 14:3, 15:2, 16:2, 17:3, 18:4, 19:4 | 20–25 по 2 | 9–12 по 3 | 11–12 по 2 |
| max_primary_score | 32 | 31 | 20 | 14 |
| Длительность, мин | 235 | 235 | 45 | 90 |
| Аудитории (room_code) | да | да | нет (null) | да |
| Прокторинг | да | да | нет | да |
| Блоки | нет | algebra: 1–14, 20–22; geometry: 15–19, 23–25 | нет | нет |
| Тип шкалы | primary_to_test | primary_to_grade | primary_to_grade | primary_to_level |
| Шкала (условная) | кусочно-линейная по точкам (0,0), (6,27), (12,64), (20,80), (32,100), округление | 0–7→2, 8–14→3, 15–21→4, 22–31→5; условие geometry ≥ 2 иначе 2 | 0–6→2, 7–11→3, 12–16→4, 17–20→5 | 0–4 below_basic, 5–10 basic, 11–14 above_basic |
| threshold_score | 6 | 8 | 7 | 5 |
| Тип ответов части 1 | целые и десятичные с запятой | целые и десятичные с запятой | слова и наборы цифр | целые и двоичные строки |

Шкалы строятся функциями в `sma/generator/exams.py` (`ege_test_score(primary)`, `oge_grade(primary, geometry)`, `vpr_grade(primary)`, `niko_level(primary)`) и из этих же функций генерируются записи /dictionaries/scales — так данные и шкала гарантированно согласованы.

КИМ (/dictionaries/kim): для каждого варианта и задания — part, check_type, max_score, level (часть 1: первые 60% заданий basic, остальные advanced; часть 2: advanced и high), section и content_codes из синтетического списка разделов предмета (4–6 разделов, коды вида «1.1», «2.3»), skill_codes вида «1.1»; задания с одинаковым номером в разных вариантах имеют одинаковые коды (параллельные варианты). Пометка в README: коды условные, для реальных данных брать из кодификатора ФИПИ.

### 10.4 Население

- Регионы — из приложения 21.2 (код, название, смещение часового пояса). В справочнике — dictionary_count регионов; участники — в used_count, из которых big_regions имеют 8–10 школ.
- school_code = код региона + трехзначный номер: «77001». name = «Школа № N» (N случайное 1–899, уникальное в регионе).
- room_code — четыре цифры, уникальны в пределах платформы; школа делится на аудитории по room_size_max. ВПР — null.
- participant_id = «p_» + 6 hex, уникален в пределах набора (все платформы).
- expert_code — «e_01» ... «e_12».
- Один участник — одна сессия (один предмет на платформу).

### 10.5 Модель ответов

- Уровень ученика θ = эффект школы (N(0, 0,45)) + N(0, 1).
- Трудность заданий части 1 b_t — равномерно от −1,8 до 1,8 по номеру задания + N(0, 0,25).
- Вероятность верного ответа — модель Раша (IRT 1PL): P = 1 / (1 + exp(−(θ − b_t))).
- Неверный ответ: пустой с вероятностью 0,04 + 0,12 × (b_t + 2) / 4; иначе типичная ошибка задания: 3 варианта с вероятностями 0,5 / 0,3 / 0,15, редкая случайная — 0,05.
- Верный ответ печатается «как ввел ученик»: в 6% — с ведущим пробелом, в 6% (для десятичных) — с точкой вместо запятой или (для слов) с заглавной буквы. is_correct определяется по нормализованному сравнению (8.6).
- Баллы части 2: Binomial(max_score, P), где P = 1 / (1 + exp(−(θ − e_t))), e_t — трудность экспертного задания (0,3 + 0,5 × порядковый номер + шум).
- Время: начало — 10:00 местного времени региона + 0–8 мин (ВПР — 0–4); работа длится 55–97% длительности; время на задания части 1 — распределение Дирихле от половины длительности; answered_at — нарастающим итогом; time_spent_sec в 4% — null.
- Прокторинг: у обычного ученика focus_lost ~ Poisson(0,5), засчитываемых событий ~ min(2, Poisson(0,12)); тип засчитываемого — tab_switch 55%, copy, paste, fullscreen_exit по 15%; task_number — задание, над которым работал в момент события, в 20% — null.
- term_mark (ВПР) — round(3,5 + 0,8 × θ_без_закладки), ограничить 2–5; в 10% — null.
- questionnaire_done (НИКО) — true 88%, false 7%, null 5%.

### 10.6 Жизненный цикл записей (timeline)

Генератор моделирует версии записей во времени (для timeline и снимков):

| Запись | Версии |
| --- | --- |
| sessions | started (при старте) → in_progress (первый ответ) → finished, result_status pending (при сдаче) → final (после проверки последнего экспертного задания) → appeal_changed (для доли appeal); absent — одна версия в начале экзамена; annulled — в момент аннулирования |
| answers | одна версия в момент ответа |
| expert_reviews | pending (при сдаче) → checked, first (проверка: 14:30–17:30 МСК) → для appeal: checked, appeal, новый балл (через 2–5 дней) |
| proctoring_events | одна версия в момент события |
| kim | одна версия в момент окончания экзамена |
| scales | версия 1 — за 4 дня до экзамена |

updated_at = visible_from версии. Время платформенных действий (проверка, апелляция, обновления сессий) — по Москве (+03:00); действия ученика — по местному времени.

record_id — детерминированный UUID5 от (namespace проекта, exam_type, endpoint, естественный ключ), чтобы при перегенерации с тем же seed идентификаторы совпадали.

Снимок snapshot: during_exam — середина экзамена; after_exam — сразу после конца, все экспертные pending; after_check — после проверки; after_appeal — после апелляций.

### 10.7 Закладка аномалий и ловушек

| Код | Как заложить | Условия |
| --- | --- | --- |
| A1 | Всем ученикам выбранной школы прибавить к θ величину boost (начать с 1,9) | Школа ≥ 10 учеников, в регионе ≥ 8 школ с ≥ 10 учениками |
| A2 | 5 учеников одной школы с одним вариантом получают одинаковые ответы части 1 — копия работы «источника» средней успеваемости; пустые ответы источника заменить типичной ошибкой | Не меньше половины ответов непустые |
| A3 | Ученику 3–5 засчитываемых событий | Платформа с прокторингом |
| P1 | 3–4 ученика одной аудитории (ВПР — школы) с одним вариантом дают одинаковые редкие неверные ответы (из списка редких, не из типичных) в 5–6 заданиях | Остальные ответы независимы; не пересекаются с A2 |
| P2 | Ученик решает только трудные задания части 1 (верно на последних 40%, неверно на первых 40%) | — |
| P3 | Школа: на трудных заданиях вероятность верного +30 п. п., на легких без изменений | Школа ≥ 10 учеников |
| P4a | Школа: всем ученикам одинаковый первичный балл (подогнать ответы) | Школа ≥ 10 учеников |
| P4b | Школа: 80% учеников получают первичный балл в окне [threshold, threshold + 2] | Школа ≥ 10 учеников |
| Ловушка A2_blank | 5 учеников одной школы и варианта — все ответы части 1 пустые | A2 не должно сработать |
| Ловушка A3_focus | Ученику 5 focus_lost и 2 tab_switch | A3 не должно сработать |

Закладки не пересекаются между собой (разные школы и ученики); заложенные ученики не аннулируются и не absent.

### 10.8 Ключ answer_key.json

```json
{
  "generator_version": "1.0.0",
  "profile": "medium",
  "synthetic": true,
  "note": "Данные синтетические; выводы ограничены свойствами генератора",
  "exams": {
    "OGE": {
      "seed": 2078,
      "planted": [
        {"rule": "A1", "school_code": "54010", "region_code": "54", "subject_code": "02",
         "boost": 1.9, "expected": {"z": 2.19, "school_mean": 20.64, "region_mean": 13.25, "sigma": 3.38}},
        {"rule": "A2", "school_code": "77004", "variant_id": "v2",
         "participant_ids": ["p_...", "p_...", "p_...", "p_...", "p_..."], "source_participant_id": "p_..."},
        {"rule": "A3", "participant_id": "p_...", "counted_events": 4},
        {"rule": "P1", "school_code": "16006", "room_code": "0041", "variant_id": "v1",
         "participant_ids": ["p_..."], "shared_wrong_tasks": [4, 7, 9, 12, 15, 17]}
      ],
      "decoys": [
        {"rule": "A2", "kind": "A2_blank", "participant_ids": ["p_..."], "why": "..."},
        {"rule": "A3", "kind": "A3_focus", "participant_id": "p_...", "why": "..."}
      ],
      "reference_check": {"A1": [...], "A2": [...], "A3": [...], "P1_groups": [...], "P2": [...], "P3": [...], "P4": [...]},
      "extras": 0
    }
  }
}
```

### 10.9 Самопроверка

При self_check: true генератор после создания набора прогоняет эталонные правила (раздел 11) и проверяет:

- все заложенные A1, A2, A3 найдены; P1 — каждая заложенная группа содержится в одной найденной группе;
- для P2 — заложенный ученик имеет наибольший G* среди учеников экзамена; P3 — заложенная школа имеет наибольший П3; P4a — наибольшую z′ П4а; P4b — наибольшую z′ П4б;
- ловушки не срабатывают;
- лишних срабатываний A2 и A3 — 0; лишних A1 и групп P1 (не связанных с A2 и P1) — минимально возможное (extras).

Если условия не выполнены — новый seed (seed + attempt), до max_attempts; выбрать попытку с extras = 0, иначе с наименьшим extras. Итоговый seed — в ключе. Если ни одна попытка не подошла — завершиться с ошибкой и понятным сообщением.

---

## 11. Эталонные правила для самопроверки генератора

Модуль `sma/reference/rules.py`. Используется только генератором и тестами; это не боевая аналитика. Работает на pandas по снимку набора (sessions с status = finished и result_status ≠ pending; answers; proctoring_events). Параметры — из ТЗ СМА v0.3.

| Правило | Определение |
| --- | --- |
| A1 | По каждой паре (exam_type, subject_code): школы с ≥ 10 участниками; регионы с ≥ 3 такими школами; для школы z = (среднее школы − среднее средних по региону) / σ, где σ — выборочное стандартное отклонение (ddof = 1) средних школ региона; аномалия при z > 2. Параметр `sigma_mode`: `include_self` (по ТЗ, по умолчанию) или `leave_one_out` (σ и среднее без самой школы) |
| A2 | Группы по (school_code, variant_id, кортеж нормализованных ответов части 1); аномалия — группа ≥ 5, у которой непустых ответов ≥ половины заданий |
| A3 | Участники с ≥ 3 событиями tab_switch, copy, paste, fullscreen_exit |
| P1 | Для варианта v: p(v, t, a) — доля участников варианта с неверным непустым нормализованным ответом a на задание t; λ(v) = Σ p². Пары участников одной группы сравнения (room_code, иначе school_code) с одним вариантом: k — число заданий с одинаковым неверным непустым ответом; p-value = P(X ≥ k), X ~ Poisson(λ) (scipy.stats.poisson.sf(k − 1, λ)); поправка Холма по всем парам школы; подозрительная пара: k ≥ 3 и скорректированное p < 0,01; группы — связные компоненты графа подозрительных пар |
| P2 | Задания упорядочить по доле верных среди участников варианта; G — число пар (легкое неверно, трудное верно); G* = G / (r × (n − r)), r — решено, n — заданий; при r = 0 или r = n G* = 0 |
| P3 | Задания «трудные» — доля верных по региону ниже медианы; П3 = средняя разность (процент выполнения школы − региона) по трудным − то же по легким |
| P4a | Стандартное отклонение первичных баллов школы; робастная z со сменой знака |
| P4b | Доля участников школы с первичным баллом в [threshold, threshold + 2] |
| Робастная z | z = (x − медиана) / (1,4826 × MAD) по школам пары (exam_type, subject_code); при MAD = 0 z = 0; корректировка z′ = z × n / (n + 20) |

**Важно (записать в docs/decisions.md):** при n школ в регионе z одной школы при `include_self` не может превысить (n − 1) / √n: при 5 школах — 1,79, правило А1 не срабатывает никогда. Поэтому генератор закладывает A1 только в регионы с 8–10 школами.

---

## 12. Витрины

Файл `db/migrations/005_marts.sql`. Все витрины фильтруют `deleted = false`. Если для одного естественного ключа есть несколько record_id — берется запись с наибольшим updated_at (`DISTINCT ON`). Материализованные витрины имеют уникальный индекс (для REFRESH CONCURRENTLY).

| Витрина | Тип | Столбцы | Для кого |
| --- | --- | --- | --- |
| mart.sessions_current | VIEW | актуальные сессии: все столбцы core.sessions + region_name, school_name; grade/test_score/result_level из exam_block отдельными столбцами | все |
| mart.school_results | MATERIALIZED | exam_type, subject_code, region_code, school_code, participants, mean_score, sd_score, median_score, mean_pct (процент выполнения), share_grade2 | А1, визуализация |
| mart.region_results | MATERIALIZED | exam_type, subject_code, region_code, region_name, participants, schools, mean_score, mean_pct, share_grade2, share_absent, share_annulled | страницы «Регионы», «ЕГЭ и ОГЭ», «ОГЭ» |
| mart.answers_flat | VIEW | ответ + subject_code, variant_id, region_code, school_code, room_code, status, result_status ученика; answer_norm | А2, П1 |
| mart.task_stats | MATERIALIZED | exam_type, subject_code, variant_id, task_number, n, share_correct, share_empty, top_wrong (jsonb: 5 частых неверных ответов с долями) | П1–П3 |
| mart.task_stats_region | MATERIALIZED | то же по region_code | П3 |
| mart.kim_stats | MATERIALIZED | exam_type, subject_code, exam_year, section, content_code (unnest), level, tasks, n_answers, mean_share_score (балл / макс.) — для части 1 по answers, для части 2 по expert_reviews со статусом checked | статистика по темам и разделам |
| mart.variant_comparison | MATERIALIZED | exam_type, subject_code, task_number, variant_id, share_score, участников; плюс разность с другими вариантами | сравнение вариантов |
| mart.proctoring_counts | VIEW | exam_type, participant_id, school_code, counted_events, focus_lost, last_event_at | А3, живой экран |
| mart.expert_progress | VIEW | exam_type, subject_code, pending, checked, appeal, share_checked | контроль проверки |
| mart.live | VIEW | exam_type, in_progress, finished, annulled, absent, generated_at, last_successful_load_at (max finished_at из ops.load_log со status ok/partial), anomalies_by_rule (из analytics.anomalies) | живой экран |
| mart.data_freshness | VIEW | platform, endpoint, last_ok_at, last_error_at, last_error_reason, lag_sec | живой экран, мониторинг |

Процент выполнения = primary_score / max_primary_score × 100. Доля «двоек» — по exam_block.grade для ОГЭ и ВПР.

Пример:

```sql
CREATE MATERIALIZED VIEW mart.school_results AS
SELECT s.exam_type, s.subject_code, s.region_code, s.school_code,
       COUNT(*)                                        AS participants,
       AVG(s.primary_score)::numeric(6,2)              AS mean_score,
       STDDEV_SAMP(s.primary_score)::numeric(6,2)      AS sd_score,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY s.primary_score) AS median_score,
       AVG(100.0 * s.primary_score / s.max_primary_score)::numeric(6,2) AS mean_pct,
       AVG(CASE WHEN (s.exam_block->>'grade')::int = 2 THEN 1 ELSE 0 END)::numeric(5,4) AS share_grade2
FROM mart.sessions_current s
WHERE s.status = 'finished' AND s.result_status <> 'pending'
GROUP BY s.exam_type, s.subject_code, s.region_code, s.school_code;

CREATE UNIQUE INDEX ON mart.school_results (exam_type, subject_code, school_code);
```

Время обновления всех материализованных витрин на наборе medium — не более 5 с.

---

## 13. Качество данных и консистентность витрин

Модуль `sma/quality/`. Запуск: `python -m sma.quality.report` (вручную) и автоматически раз в 10 минут из загрузчика. Результат — ops.quality_runs, ops.quality_issues и файл `reports/quality_YYYYMMDD_HHMM.md`.

| Код | Метрика | Проверка | Важность |
| --- | --- | --- | --- |
| Q-COMPL-01 | Полнота | Доля отклоненных записей по платформе и адресу за последний час (из ops.load_log) | warning > 1%, error > 5% |
| Q-COMPL-02 | Полнота | Доля null в полях, которые по 6.12 должны быть заполнены (например primary_score при finished + final) | error > 0 |
| Q-UNIQ-01 | Уникальность | Дубли естественных ключей с разными record_id: sessions (exam_type, participant_id); answers (exam_type, participant_id, task_number); expert_reviews (exam_type, participant_id, task_number); kim (exam_type, subject_code, exam_year, variant_id, task_number); scales (exam_type, subject_code, exam_year, scale_version) | error |
| Q-CONS-01 | Согласованность | Каждый ответ, оценка, событие имеет сессию (exam_type, participant_id) | error |
| Q-CONS-02 | Согласованность | У участника не больше одной сессии на платформе (иначе ответы не привязать однозначно) | error |
| Q-CONS-03 | Согласованность | Для finished + final: primary_score = Σ score из answers + Σ score из expert_reviews (checked) | error |
| Q-CONS-04 | Согласованность | Блок экзамена соответствует шкале из core.scales (действующей на updated_at сессии): test_score, grade, result_level, block_failed | error |
| Q-CONS-05 | Согласованность | finished_at ≥ started_at; answered_at в пределах [started_at, finished_at + 5 мин] | warning |
| Q-CONS-06 | Согласованность | Состояния 6.12: absent без ответов и баллов; pending без primary_score; checked с score | error |
| Q-CONS-07 | Согласованность | Ссылочная целостность справочников: region_code, school_code, subject_code есть в core.regions, core.schools, core.subjects | error |
| Q-CONS-08 | Согласованность | Справочники regions и schools совпадают между платформами (по ops.dictionary_snapshots) | warning |
| Q-CONS-09 | Согласованность | Для каждого (exam_type, subject_code, variant_id) в answers и expert_reviews набор task_number совпадает с kim_tasks (если КИМ уже получен) | warning |
| Q-CONS-10 | Согласованность | Шкалы: points монотонны, покрывают 0..max без пропусков и пересечений | error |
| Q-TIME-01 | Своевременность | lag = now − max(updated_at) по адресу во время экзамена; и время от updated_at записи до loaded_at | warning > 60 с, error > 120 с |
| Q-ACC-01 | Точность | Выборочно 20 записей каждой таблицы: payload совпадает со столбцами | error |
| Q-MART-01 | Консистентность витрин | Σ participants в mart.school_results = число актуальных сессий finished + не pending | error |
| Q-MART-02 | Консистентность витрин | Средние в mart.school_results и mart.region_results совпадают с пересчетом в pandas по core до 0,01 | error |
| Q-MART-03 | Консистентность витрин | mart.task_stats.n по заданию = число ответов в mart.answers_flat | error |
| Q-MISS-01 | Пропуски | Доля null в необязательных полях (room_code, time_spent_sec, term_mark ...) по платформе и школе; флаг, если у одной школы доля в 3 раза выше средней (неслучайные пропуски) | info |
| Q-OUT-01 | Выбросы | Значения вне правдоподобных пределов (time_spent_sec > длительности экзамена, отрицательные) — записать, не удалять | warning |

Отчет содержит: дату, источники (с пометкой «синтетика», если данные из генератора), сводку по метрикам, список нарушений с примерами ключей, ограничения выводов.

---

## 14. Резервное копирование и восстановление

- `python -m sma.backup.run --every 30` — каждые 30 минут `pg_dump -Fc` в `backups/sma_YYYYMMDD_HHMM.dump`; хранить последние 20; запись в ops.backup_log. В docker-compose — сервис backup (профиль `exam`).
- `python -m sma.backup.restore --file backups/... --target-db sma_restore` — `pg_restore` в новую или пустую базу; вывести время и число строк в основных таблицах до и после.
- Требование: восстановление не дольше 10 минут (NFR-15, T-35).

---

## 15. Развертывание и воспроизводимость

### 15.1 docker-compose.yml

| Сервис | Образ | Назначение | Профиль |
| --- | --- | --- | --- |
| db | postgres:16.4 | База; том pgdata; healthcheck pg_isready | всегда |
| migrate | наш Dockerfile | `python db/migrate.py`, затем выход | всегда |
| loader | наш Dockerfile | `python -m sma.loader.run`; depends_on migrate | всегда |
| mock-api | наш Dockerfile | `python -m sma.mockapi.server --data data/generated/medium` | dev |
| backup | наш Dockerfile | `python -m sma.backup.run --every 30` | exam |

Dockerfile: `python:3.12-slim` (точная версия), установка `postgresql-client` той же основной версии (для pg_dump), `pip install -r requirements.txt`.

### 15.2 Переменные окружения (.env.example)

```text
POSTGRES_DB=sma
POSTGRES_USER=sma_owner
POSTGRES_PASSWORD=
SMA_LOADER_PASSWORD=
SMA_READER_PASSWORD=
SMA_APP_PASSWORD=
SMA_DB_HOST=db
SMA_DB_PORT=5432
SMA_PLATFORMS_CONFIG=config/platforms.yaml
EGE_API_TOKEN=
OGE_API_TOKEN=
VPR_API_TOKEN=
NIKO_API_TOKEN=
MOCK_API_TOKEN=
MOCK_ADMIN=0
MOCK_ADMIN_TOKEN=
```

### 15.3 Makefile

| Команда | Что делает |
| --- | --- |
| make up | docker compose up -d (db, migrate, loader) |
| make dev | docker compose --profile dev up -d (плюс mock-api) |
| make down | остановить |
| make migrate | применить миграции |
| make gen-small / gen-medium / gen-large | сгенерировать набор |
| make load-files | загрузчик --once из файлов |
| make quality | отчет о качестве |
| make test | pytest |
| make cov | pytest --cov=sma --cov-report=term-missing --cov-report=html |
| make lint | ruff check . |
| make backup / restore | разовая копия / восстановление |

### 15.4 README

Пошагово, проверяется на чистом компьютере с секундомером (≤ 30 минут, NFR-20, T-39): установить Docker → клонировать → `cp .env.example .env` и заполнить → `make gen-medium` → `make dev` → `make load-files` → открыть DBeaver (параметры подключения) → `make test`. Плюс раздел «Подключение к реальной платформе» (что поменять в platforms.yaml и .env) и «Перезапуск во время экзамена» (≤ 5 минут, NFR-14).

### 15.5 Воспроизводимость

- Все версии — точные (requirements.txt, образы).
- Одна команда сборки и одна команда тестов.
- `make test` дает одинаковый результат на разных компьютерах; генератор детерминирован.

---

## 16. Тестирование

### 16.1 Требования

- Пирамида: около 70% unit, 20% интеграционных, 10% приемочных.
- На каждый модуль (loader, generator, quality, mockapi): не менее 5 unit, 2 интеграционных, 1 property-based.
- Интеграционные тесты используют настоящий PostgreSQL (сервис db или testcontainers не нужен: отдельная база sma_test, создается фикстурой) и тестовый API (Flask test client или поднятый сервер).
- Покрытие — отчет coverage.py; ориентир ≥ 80% для sma/loader и sma/normalize.py; это индикатор, а не цель.
- Тест проверяет результат утверждением, а не просто выполняет код.

### 16.2 Обязательные тесты

**unit (tests/unit):**

| Тест | Проверяет |
| --- | --- |
| test_normalize_* | " 0.25" → "0,25"; "Пришел" → "пришел"; "" → ""; U+0451 → «е» |
| test_validate_missing_field | отсутствующее поле → missing_field с именем поля |
| test_validate_null_not_allowed | null в обязательном поле → null_not_allowed |
| test_validate_pii_nested | ключ email во вложенном объекте → pii_field |
| test_validate_name_allowed_in_dictionaries | name в /dictionaries/schools принимается |
| test_validate_wrong_type_codes | subject_code = 2 (число) → wrong_type |
| test_validate_exam_block | блок oge у VPR → wrong_exam_block |
| test_validate_unknown_field_dropped | неизвестное поле удаляется, запись принимается |
| test_since_with_overlap | since = watermark − 5 с |
| test_file_source_pages | FileSource собирает page-1..N и фильтрует since |
| test_timeutil_offsets | +03:00 и +07:00 сравниваются как моменты времени |
| test_scales_build | шкалы из exams.py покрывают 0..max без пропусков |
| test_generator_counts | число участников, школ, аудиторий = параметрам |
| test_generator_deterministic | одинаковый seed → одинаковые файлы (хеши) |
| test_generator_no_pii | в данных нет запрещенных ключей |
| test_generator_states | absent без ответов; pending без баллов; annulled — null |
| test_reference_a1_samuelson | при 5 школах z ≤ 1,79 при include_self |
| test_quality_checks_* | каждая проверка раздела 13 находит специально испорченную запись |

**интеграционные (tests/integration):**

| Тест | Проверяет | ТЗ |
| --- | --- | --- |
| test_cycle_loads_all_endpoints | загрузка набора small из файлов: все записи в core | T-06 |
| test_api_new_record_within_60s | inject → цикл → запись в core | T-02 |
| test_idempotent_reload | повторная загрузка — число строк не меняется; измененный балл — обновлен; старая версия не перетирает новую | T-04 |
| test_missing_field_rejected | inject_bad missing_field → не сохранено, в ops.rejected_records поле и record_id | T-03 |
| test_pii_rejected | inject_bad pii_field для каждого запрещенного ключа → не сохранено | T-32 |
| test_api_down_and_slow | mode down и slow 40 с → ops.load_log error с причиной; загрузчик продолжает; watermark не сдвинут | T-05 |
| test_request_intervals | в /_admin/requests интервал к одному адресу ≥ 10 с; при интервале 60 с за 10 минут — 10 ± 1 запрос | T-01, T-24 |
| test_restart_resume | остановка после части страниц, запуск — данных столько же, сколько без остановки | T-34 |
| test_replay_transitions | режим replay: pending → checked → appeal_changed доходит до core | — |
| test_marts_consistency | Q-MART-01..03 без нарушений на medium | — |
| test_generator_key_matches_reference | эталонные правила на наборе = ключ | FR-06 |
| test_backup_restore | копия и восстановление; число строк совпадает; время < 10 мин | T-35 |

**property-based (tests/properties, Hypothesis):**

| Свойство |
| --- |
| normalize_answer идемпотентна: n(n(x)) == n(x) для любых строк |
| Любая корректная запись (стратегия, построенная из contract.py) проходит validate; удаление любого обязательного поля дает missing_field |
| Применение одного и того же пакета записей дважды дает то же состояние БД, что и один раз (идемпотентность upsert) |
| Применение версий одной записи в любом порядке дает в БД версию с наибольшим updated_at |
| ОГЭ и ВПР: отметка всегда 2–5 и не убывает с ростом первичного балла; ЕГЭ: тестовый балл 0–100, не убывает |
| Первичный балл сгенерированного участника всегда 0..max_primary_score |
| Запись → JSON → запись: значения равны (сериализация обратима) |

### 16.3 Приемочные тесты (ручные или полуавтоматические)

| Тест | Как проверить |
| --- | --- |
| T-26 | Поиск токенов и паролей: `git grep -nE "(token|password)\s*=\s*['\"][^'\"]+"` — пусто; .env в .gitignore |
| T-38 | В requirements.txt у каждой строки `==` |
| T-39 | Развертывание на чистом компьютере по README — ≤ 30 минут |

---

## 17. Код-ревью и работа с git

- Репозиторий — GitLab CE или Gitea (уточнить у пользователя). Основная ветка main защищена.
- Ветки: `feature/<кратко>`, `fix/<кратко>`. Коммиты на русском в повелительном наклонении: «Добавить проверку запрещенных полей».
- Каждое крупное изменение — merge request. Шаблон описания: что сделано; как проверить; какие требования ТЗ закрывает; чек-лист — тесты добавлены и проходят, нет секретов и персональных данных, docs/decisions.md обновлен.
- Ревьюер — минимум один участник команды, не автор. Замечания закрываются до слияния.
- CI (GitLab CI или Gitea Actions): `make lint`, `make test` с сервисом PostgreSQL 16.4; слияние только при зеленом CI.

---

## 18. Этапы работ (milestones) и критерии готовности

### M0. Каркас (неделя 7)

Сделать: структура репозитория (раздел 5), requirements.txt с точными версиями, Dockerfile, docker-compose (db, migrate), Makefile, .env.example, .gitignore, ruff, pytest, CI, docs/decisions.md.
Готово, когда: `make up` поднимает db; `make test` и `make lint` проходят (пустой тест-набор допустим); в репозитории нет секретов.

### M1. Хранилище (неделя 7)

Сделать: миграции 001–004 (core, ops, analytics, app, роли); db/migrate.py; sma/contract.py; ER-диаграмма.
Готово, когда: миграции применяются на пустую базу и повторно (без ошибок); ER-диаграмма в docs; unit-тесты на contract.py.

### M2. Генератор, часть 1 (неделя 7)

Сделать: население, модель ответов, экзамены и шкалы, КИМ, запись файлов, профиль small, без аномалий и timeline.
Готово, когда: `make gen-small` создает файлы, проходящие validate без единого отклонения; test_generator_* (кроме аномалий) зеленые.

### M3. Загрузчик из файлов (неделя 7)

Сделать: normalize, timeutil, validate, FileSource, store (upsert, журнал, rejected, watermarks), cycle, run --once.
Готово, когда: `make load-files` загружает small и medium; повторный запуск не добавляет строк; unit-тесты validate и store зеленые. **Это минимальный результат недели 7: команда видит данные в базе.**

### M4. Тестовый API и планировщик (неделя 8)

Сделать: mockapi (снимок, режимы сбоев, admin), ApiSource, планировщик с интервалами и потоками, остановка и перезапуск.
Готово, когда: интеграционные T-01–T-05, T-24, T-32, T-34 зеленые; загрузчик 10 минут работает с mock-api без ошибок.

### M5. Витрины (неделя 8)

Сделать: миграция 005_marts.sql, обновление материализованных витрин после цикла.
Готово, когда: все витрины раздела 12 строятся на medium; обновление ≤ 5 с; Q-MART-01..03 без нарушений.

### M6. Генератор, часть 2 (неделя 9)

Сделать: timeline и снимки, record_id UUID5, закладки и ловушки, эталонные правила, самопроверка, ключ, профили medium и large, режим replay в mock-api.
Готово, когда: test_generator_key_matches_reference и test_replay_transitions зеленые; README набора с пометкой о синтетике.

### M7. Качество, бэкапы, развертывание (неделя 9)

Сделать: все проверки раздела 13 и отчет; backup и restore; README; property-based тесты; отчет о покрытии.
Готово, когда: отчет о качестве на medium без error; на испорченных данных каждая проверка срабатывает; T-35 и T-39 пройдены; покрытие выведено.

### M8. Подключение к реальным платформам (недели 10–11)

Сделать: для каждой платформы — platforms.yaml и токен; прогон `--once`; отчет о качестве; список расхождений с контрактом для тимлида (какие поля, какие причины отклонений, сколько записей).
Готово, когда: данные всех доступных платформ в core; задержка ≤ 60 с; отчет о качестве передан тимлиду.

---

## 19. Трассировка к требованиям ТЗ СМА v0.3

| Требование | Где реализуется | Тест |
| --- | --- | --- |
| FR-01 опрос раз в 60 с с since | loader/run.py, cycle.py | T-01 |
| FR-02 сохранение записей с обязательными полями | validate.py, store.py | test_cycle_loads_all_endpoints |
| FR-03 отклонение без обязательного поля + журнал | validate.py, ops.rejected_records | T-03 |
| FR-04 замена по ключу записи (record_id) | store.py upsert | T-04 |
| FR-05 журнал ошибок и таймаутов 30 с | sources.py, ops.load_log | T-05 |
| FR-06 генератор с заданными аномалиями | generator/ | T-06, test_generator_key_matches_reference |
| FR-22 время последней загрузки на живом экране | mart.live, mart.data_freshness | test_marts_consistency |
| NFR-01 задержка ≤ 120 с | интервалы 60 с + обновление витрин | Q-TIME-01 |
| NFR-04 ≥ 10 с к одному адресу | run.py | T-24 |
| NFR-06 секреты вне репозитория | .env, .gitignore | T-26 |
| NFR-12 персональные данные не сохраняются | validate.py | T-32 |
| NFR-14 перезапуск ≤ 5 мин без потерь | run.py, watermarks | T-34 |
| NFR-15 копии раз в 30 мин, восстановление ≤ 10 мин | backup/ | T-35 |
| NFR-19 точные версии | requirements.txt, образы | T-38 |
| NFR-20 развертывание ≤ 30 мин | README, docker-compose | T-39 |
| NFR-23 воспроизводимость | детерминированный генератор, фиксированные версии | test_generator_deterministic |
| Лекция 4: 5 unit / 2 интеграционных / 1 property-based на модуль, покрытие, код-ревью | tests/, CI, раздел 17 | make cov |
| Лекция 5: пайплайн из всех баз, витрина для BI, консистентность витрин, этика синтетики | разделы 8, 12, 13, 10.8 | test_marts_consistency |

---

## 20. Допущения и открытые вопросы

| № | Вопрос | Решение по умолчанию (реализовать настраиваемо) |
| --- | --- | --- |
| 1 | Засчитывается ли API как «доступ к витринам» платформ (лекция 5) | Да; при необходимости добавить источник «прямое чтение БД платформы» — третья реализация Source |
| 2 | Один participant_id может сдавать несколько предметов на одной платформе? В контракте v2 у answers, expert_reviews, proctoring_events нет subject_code | Считать, что одна сессия на участника на платформе; нарушение — Q-CONS-02. Предложить в формате v3 добавить subject_code или session_record_id во все записи участника |
| 3 | Коды предметов ВПР и НИКО | Коды ОГЭ для того же предмета (допущение формата v2) |
| 4 | Коды ОГЭ из справочника 2018/19 | Использовать таблицу приложения 21.1; при расхождении с платформой — принимать код платформы и предупреждать в отчете качества |
| 5 | room_code у платформ | Необязателен; группа сравнения — school_code |
| 6 | Прокторинг ВПР и НИКО | ВПР — нет, НИКО — есть (как в генераторе); реальное поведение — из ответов команд |
| 7 | Число участников на Demo Day | До 200 на платформу |
| 8 | Правило А1: σ с самой школой | Реализовать sigma_mode, по умолчанию include_self (по ТЗ); тимлиду предложено leave_one_out |
| 9 | Строгость since: «после» или «не раньше» | Отдавать updated_at > since; загрузчик запрашивает с перекрытием 5 с |
| 10 | Хостинг репозитория | Спросить пользователя: GitLab CE или Gitea |

---

## 21. Приложения

### 21.1 Коды предметов

ЕГЭ (Правила заполнения бланков ЕГЭ 2026, ФИПИ, с. 13):

| Код | Предмет | Код | Предмет |
| --- | --- | --- | --- |
| 01 | Русский язык | 12 | Обществознание |
| 02 | Математика профильная | 13 | Испанский язык |
| 22 | Математика базовая | 14 | Китайский язык |
| 03 | Физика | 18 | Литература |
| 04 | Химия | 25 | Информатика (КЕГЭ) |
| 06 | Биология | 29 | Английский язык (устный экзамен) |
| 07 | История | 30 | Немецкий язык (устный экзамен) |
| 08 | География | 31 | Французский язык (устный экзамен) |
| 09 | Английский язык | 33 | Испанский язык (устный экзамен) |
| 10 | Немецкий язык | 34 | Китайский язык (устный экзамен) |
| 11 | Французский язык | | |

ОГЭ (коды предметов ОГЭ, РЦОИ Москвы, 2018/19):

| Код | Предмет | Код | Предмет |
| --- | --- | --- | --- |
| 01 | Русский язык | 10 | Немецкий язык |
| 02 | Математика | 11 | Французский язык |
| 03 | Физика | 12 | Обществознание |
| 04 | Химия | 13 | Испанский язык |
| 05 | Информатика и ИКТ | 18 | Литература |
| 06 | Биология | 29 | Английский язык (устный) |
| 07 | История | 30 | Немецкий язык (устный) |
| 08 | География | 31 | Французский язык (устный) |
| 09 | Английский язык | 33 | Испанский язык (устный) |

Источники: https://doc.fipi.ru/ege/normativno-pravovye-dokumenty/Pravila_zapolneniya_blankov_2026.pdf ; https://rcoi.mcko.ru/resources/upload/RichFilemanager/documents/2018-2019/metodicheskie/9/kodi_oge.pdf

ВПР и НИКО — коды ОГЭ для того же предмета (допущение).

### 21.2 Регионы для генератора

| Код | Регион | Смещение | Код | Регион | Смещение |
| --- | --- | --- | --- | --- | --- |
| 77 | Москва | +03:00 | 74 | Челябинская область | +05:00 |
| 78 | Санкт-Петербург | +03:00 | 61 | Ростовская область | +03:00 |
| 50 | Московская область | +03:00 | 24 | Красноярский край | +07:00 |
| 16 | Республика Татарстан | +03:00 | 59 | Пермский край | +05:00 |
| 66 | Свердловская область | +05:00 | 36 | Воронежская область | +03:00 |
| 54 | Новосибирская область | +07:00 | 34 | Волгоградская область | +03:00 |
| 23 | Краснодарский край | +03:00 | 55 | Омская область | +06:00 |
| 02 | Республика Башкортостан | +05:00 | 72 | Тюменская область | +05:00 |
| 63 | Самарская область | +04:00 | 38 | Иркутская область | +08:00 |
| 52 | Нижегородская область | +03:00 | 25 | Приморский край | +10:00 |

Большие регионы (8–10 школ, для А1) по умолчанию: 77, 66, 54, 16.

### 21.3 Константы

| Константа | Значение | Откуда |
| --- | --- | --- |
| Интервал опроса фактов | 60 с | FR-01 |
| Минимальный интервал к адресу | 10 с | NFR-04 |
| Таймаут запроса | 30 с | FR-05 |
| Перекрытие since | 5 с | решение 20.9 |
| Записей на страницу | ≤ 1000 | контракт |
| Порог А1 | z > 2; школа ≥ 10; регион ≥ 3 школ | ТЗ FR-07, FR-08 |
| Порог А2 | ≥ 5 участников; ≥ половины непустых | FR-09 |
| Порог А3 | ≥ 3 событий кроме focus_lost | FR-10 |
| П1 | k_min = 3; α = 0,01; поправка Холма | FR-36 |
| Индекс | N_min = 10; Δ = 2; K = 20; И_min = 2,0 | ТЗ табл. параметров |
| Копии | каждые 30 мин; восстановление ≤ 10 мин | NFR-15 |
| Развертывание | ≤ 30 мин | NFR-20 |
| Перезапуск | ≤ 5 мин | NFR-14 |

### 21.4 Пример записи сессии (эталон для тестов)

```json
{
  "record_id": "3c2a6f0e-1d4b-4a8e-b7f2-9e5d1c0a6b31",
  "participant_id": "p_7f3a9c",
  "exam_type": "OGE",
  "subject_code": "02",
  "grade_level": 9,
  "variant_id": "v12",
  "region_code": "77",
  "school_code": "77003",
  "room_code": "0312",
  "status": "finished",
  "started_at": "2026-12-05T10:00:00+03:00",
  "finished_at": "2026-12-05T13:55:00+03:00",
  "primary_score": 21,
  "max_primary_score": 31,
  "result_status": "final",
  "oge": {"grade": 4, "block_scores": {"algebra": 12, "geometry": 9}, "block_failed": false},
  "updated_at": "2026-12-05T16:02:11+03:00",
  "deleted": false
}
```
