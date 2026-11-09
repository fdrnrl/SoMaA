# Руководство разработчика: дашборды и правила аномалий

Для участников команды ГИАЦ, которые делают свою часть СМА поверх данных этого проекта:

- **разработчик дашбордов** — графики, страницы Streamlit, живой экран, вход пользователей;
- **аналитик аномалий** — правила А1–А3, П1–П4 и индекс школы.

Проект открывать и менять не нужно: вы читаете готовые **витрины** (схема `mart`) и пишете
результаты в свои схемы (`analytics`, `app`). Ниже — как за 15 минут получить базу с данными
у себя на компьютере, что лежит в витринах и как проверить свои правила по ключу.

Термины: **витрина** — готовая таблица или представление в схеме `mart` под конкретную задачу;
**сессия** — один ученик на одном экзамене; **платформа** — сайт экзамена (ЕГЭ, ОГЭ, ВПР, НИКО),
в данных — `exam_type` (`EGE`, `OGE`, `VPR`, `NIKO`).

---

## 0. Где писать свой код

| Кто | Код | Тесты | Запуск |
|---|---|---|---|
| Аналитик аномалий | `sma/rules/anomalies.py` (А1–А3), `sma/rules/school_index.py` (П1–П4, индекс), `sma/rules/run.py` (точка входа) | `tests/rules/` | `make rules` |
| Разработчик дашбордов | `dashboard/app.py` (главная), `dashboard/pages/` (страницы), `dashboard/data.py` (запросы), `dashboard/auth.py` (вход) | `tests/dashboard/` | `make dashboard` |

Файлы уже созданы — в каждом сверху написано, что в нем должно быть. Пишите только
в своих папках; файлы вне их меняет разработчик пайплайнов (попросите его).

Свой код можно запускать и проверять теми же командами, что и весь проект:
`make test` (все тесты, в том числе ваши), `make lint` и `make format` (оформление).
Из своего кода можно пользоваться готовым: `sma.db.connect(...)` — подключение к базе
по настройкам из `.env`; `sma.normalize.normalize_answer` — нормализация ответа.

**Дашборду нужен Streamlit**, его пока нет в `requirements.txt`: добавьте строку
`streamlit==<версия>` (точная версия обязательна, тест T-38), выполните `make install`
и согласуйте с командой в merge request. `make dashboard` запускает приложение так,
что из `dashboard/` доступны пакеты проекта: `from dashboard import data`, `from sma.db import connect`.

## 1. База с данными у себя за 15 минут

Нужны: Docker Desktop (запущенный), Python 3.12, git, make.
**На Windows** все делается в WSL2 (Ubuntu) — пошагово в разделе «Windows» в README.

```bash
git clone <адрес репозитория> sma-pipeline
cd sma-pipeline
cp .env.example .env
for name in POSTGRES_PASSWORD SMA_LOADER_PASSWORD SMA_READER_PASSWORD SMA_APP_PASSWORD \
            MOCK_API_TOKEN MOCK_ADMIN_TOKEN; do
  sed -i '' "s/^$name=$/$name=$(openssl rand -hex 16)/" .env    # на Linux: sed -i без ''
done
make install         # окружение Python
make gen-medium      # синтетические данные: 4 экзамена по 430 участников, ~1 минута
make db-up           # база PostgreSQL в Docker и все таблицы
make load-files DATA=data/generated/medium   # загрузить набор в базу (~5 секунд)
```

Готово: в базе `sma` на `localhost:5432` около 31 000 записей, витрины заполнены.
Пароли — в вашем `.env` (у каждого свои; база у каждого своя, на его компьютере).

Остановить базу — `make down` (данные сохраняются), поднять снова — `make db-up`.
Начать с чистой базы — `make db-reset`, затем снова `make load-files ...`.

**Если нужно, чтобы данные шли «вживую»** (для живого экрана): `make dev` — база, загрузчик
и тестовый API в Docker. Чтобы «проиграть» экзамен с начала: `make db-reset`, затем в одном
терминале `make mock-replay SPEED=10`, в другом `make loader-mock`. Экзамен идет в 10 раз
быстрее: сессии начинаются, ответы появляются по ходу работы, потом приходят оценки экспертов.

## 2. Подключение и права

Подключайтесь под ролью своей задачи — у нее ровно те права, которые нужны.

| Роль | Пароль в `.env` | Читает | Пишет | Для кого |
|---|---|---|---|---|
| `sma_reader` | `SMA_READER_PASSWORD` | `mart`, `core` | — | графики, разовый анализ |
| `sma_app` | `SMA_APP_PASSWORD` | `mart` | `analytics`, `app` | правила аномалий, приложение Streamlit (вход пользователей) |

`sma_app` не читает `core` — это намеренно: все, что нужно правилам, есть в витринах.
Схемы `ops` (журналы загрузчика) не видит ни одна из двух ролей, а то, что из них нужно
экрану (время последней загрузки), уже есть в `mart.live` и `mart.data_freshness`.

Из Python (pandas и SQLAlchemy уже есть в `requirements.txt`):

```python
import os

import pandas as pd
from dotenv import find_dotenv, load_dotenv
from sqlalchemy import create_engine

# .env ищется от текущей папки вверх — так работает и в скрипте, и в Jupyter
load_dotenv(find_dotenv(usecwd=True))
engine = create_engine(
    f"postgresql+psycopg://sma_reader:{os.environ['SMA_READER_PASSWORD']}@localhost:5432/sma"
)
regions = pd.read_sql("SELECT * FROM mart.region_results", engine)
```

DBeaver: PostgreSQL, host `localhost`, port `5432`, database `sma`, user и пароль — из таблицы выше.

Для Streamlit: кэшируйте запросы не дольше 30 секунд (`@st.cache_data(ttl=30)`) — витрины
обновляются раз в 30 секунд, если пришли новые данные.

## 3. Что важно знать о данных

1. **Персональных данных нет.** Ученик — псевдоним вида `p_7f3a9c`. ФИО, телефоны, email
   отсекаются при загрузке. Не добавляйте их и в свои таблицы.
2. **Ученик связан со своими ответами через пару `(exam_type, participant_id)`**, а не через
   один `participant_id`: псевдонимы уникальны только в пределах платформы.
3. **Итоги — только у сданных работ с окончательным результатом:**
   `status = 'finished' AND result_status <> 'pending'`. Витрины с итогами (`school_results`,
   `region_results`, `task_stats` и др.) уже отфильтрованы. В `sessions_current` и `answers_flat`
   есть все сессии — фильтруйте сами.

   | Ситуация | Признак |
   |---|---|
   | идет экзамен | `status` = `started` или `in_progress` |
   | не явился | `status = 'absent'`, баллов и ответов нет |
   | аннулирована | `status = 'annulled'`, баллов нет |
   | сдал, итог еще не окончательный (часть 2 не проверена) | `result_status = 'pending'`, `primary_score` пуст |
   | итог окончательный | `result_status = 'final'` |
   | итог изменен после апелляции | `result_status = 'appeal_changed'` |

4. **Записи обновляются.** Одна и та же запись приходит несколько раз (сдал → проверено →
   апелляция); в базе всегда последняя версия. Удаленные платформой записи в витринах не видны.
   Поэтому число строк и баллы во время экзамена меняются — это нормально.
5. **Время хранится с часовым поясом** (`timestamptz`): ученики сдают в 10:00 по своему местному
   времени (Москва +3, Новосибирск +7, Владивосток +10). Показывайте пользователям время
   **по Москве**: `AT TIME ZONE 'Europe/Moscow'` в SQL или `.dt.tz_convert("Europe/Moscow")` в pandas.
6. **Доли — от 0 до 1** (`share_*`, `mean_share_score`); **проценты — от 0 до 100** (`mean_pct`).
   Числа в витринах — тип `numeric`: в pandas приводите к `float` (`df["mean_score"].astype(float)`).
7. **Части работы:** часть 1 — задания с кратким ответом (проверяются автоматически,
   в `answers_flat`, по 1 баллу); часть 2 — развернутые ответы (оценки экспертов,
   в `core.expert_reviews`, до 2–4 баллов).
8. **Названия** регионов и школ — только в справочниках; в витринах `sessions_current`
   и `region_results` они уже подставлены (`region_name`, `school_name`).

## 4. Витрины

Каждая строка таблицы — «одна строка = ...» (зерно витрины). **М** — материализованная:
обновляется раз в 30 секунд при новых данных; остальные считаются при каждом запросе.

| Витрина | Одна строка = | Столбцы |
|---|---|---|
| `mart.sessions_current` | сессия ученика (последняя версия) | все поля сессии + `region_name`, `school_name`; итог отдельными столбцами: `grade` (ОГЭ, ВПР), `test_score` (ЕГЭ), `result_level` (НИКО); `exam_block` — весь блок экзамена (jsonb) |
| `mart.answers_flat` | ответ на задание части 1 | `answer` (как ввел ученик), `answer_norm` (без пробелов, «.» → «,», строчными), `is_correct`, `score`, `answered_at`, `time_spent_sec` + данные сессии: `subject_code`, `variant_id`, `region_code`, `school_code`, `room_code`, `status`, `result_status` |
| `mart.school_results` **М** | школа × экзамен × предмет | `participants`, `mean_score`, `sd_score`, `median_score` (первичный балл), `mean_pct` (% выполнения), `share_grade2` (доля «двоек», только ОГЭ и ВПР) |
| `mart.region_results` **М** | регион × экзамен × предмет | `region_name`, `participants`, `schools`, `mean_score`, `mean_pct`, `share_grade2`, `share_absent`, `share_annulled` (доли от всех сессий региона) |
| `mart.task_stats` **М** | задание × вариант | `n` (ответов), `share_correct`, `share_empty`, `top_wrong` — до 5 самых частых неверных ответов: `[{"answer": "12", "share": 0.18}, ...]` |
| `mart.task_stats_region` **М** | задание × вариант × регион | то же, что `task_stats` |
| `mart.kim_stats` **М** | раздел × элемент содержания × уровень | `tasks`, `n_answers`, `mean_share_score` — средняя доля балла по заданиям темы (часть 1 и 2) |
| `mart.variant_comparison` **М** | задание × вариант | `share_score`, `participants`, `diff_from_others` — насколько вариант легче (+) или труднее (−) остальных |
| `mart.proctoring_counts` | ученик с событиями прокторинга | `counted_events` (переключение вкладки, копирование, вставка, выход из полноэкранного режима), `focus_lost` (не засчитывается), `last_event_at` |
| `mart.expert_progress` | экзамен × предмет | `pending`, `checked`, `appeal`, `share_checked` — ход проверки части 2 |
| `mart.live` | платформа | `in_progress`, `finished`, `annulled`, `absent` — сводка платформы; `generated_at` — когда платформа ее составила; `last_successful_load_at` — последняя успешная загрузка; `anomalies_by_rule` — `{"A1": 1, "A3": 2}` из `analytics.anomalies` |
| `mart.data_freshness` | платформа × адрес API | `last_ok_at`, `last_error_at`, `last_error_reason`, `lag_sec` — секунд с последней успешной загрузки |

**Готовые запросы** к каждой витрине, соединения, данные для правил и чтение из Python —
в [marts_guide.md](marts_guide.md).

Процент выполнения = первичный балл / максимальный × 100. Максимальный первичный балл
в синтетических данных: ЕГЭ 32, ОГЭ 31, ВПР 20, НИКО 14.

## 5. Разработчику дашбордов

| Страница | Откуда брать |
|---|---|
| Живой экран | `mart.live` (счетчики, аномалии), `mart.data_freshness` (свежесть) |
| Регионы | `mart.region_results` |
| Школы | `mart.school_results` (+ `region_name` из `mart.region_results` или `sessions_current`) |
| ЕГЭ и ОГЭ: распределение баллов | `mart.sessions_current` с фильтром «итог окончательный» |
| Задания, частые ошибки | `mart.task_stats`, `mart.task_stats_region` |
| Темы кодификатора | `mart.kim_stats` |
| Варианты | `mart.variant_comparison` |
| Проверка экспертами | `mart.expert_progress` |

Живой экран:

- **Свежесть.** Если `now() - last_successful_load_at` больше 2 минут — показывайте
  предупреждение «данные платформы не обновляются», причину — из `mart.data_freshness.last_error_reason`
  (`http_401` — неверный токен, `timeout` / `network` — платформа недоступна, `bad_json` — платформа
  прислала испорченный ответ).
- **КИМ** (структура заданий, темы) появляется только после конца экзамена — до этого
  `kim_stats` пуст, это не ошибка.
- **Пока идет экзамен,** итогов нет: показывайте счетчики `mart.live`, ход сдачи
  (`sessions_current.status`), события прокторинга.

Пользователи и вход: таблицы `app.users` (`login`, `password_hash`, `role` — `staff` или
`admin`, `blocked`, `failed_attempts`, `locked_until`) и `app.login_log` уже созданы; логика
входа — ваша. Храните только хеш пароля.

## 6. Аналитику аномалий

### Откуда брать данные для правил

| Правило | Что значит | Витрины |
|---|---|---|
| А1 | средний балл школы аномально выше школ своего региона | `mart.school_results` (`mean_score`, `participants`) |
| А2 | ≥ 5 учеников школы с одним вариантом дали одинаковые ответы части 1 | `mart.answers_flat` (`answer_norm`, `school_code`, `variant_id`) |
| А3 | ≥ 3 засчитываемых события прокторинга | `mart.proctoring_counts` (`counted_events`) |
| П1 | пары учеников одной аудитории с одинаковыми редкими неверными ответами | `mart.answers_flat` (`room_code`, иначе `school_code`; `answer_norm`, `is_correct`) |
| П2 | решены трудные задания при нерешенных легких | `mart.answers_flat` + `mart.task_stats` (`share_correct` — трудность) |
| П3 | школа лучше региона именно на трудных заданиях | `mart.answers_flat` + `mart.task_stats_region` |
| П4а, П4б | одинаковые баллы в школе; много баллов у порога | `mart.sessions_current` (`primary_score`) + порог из спецификации |

Сравнивайте ответы по **`answer_norm`**, а не по `answer`: ученики пишут «0.5», «0,5» и « 0,5».
Считайте правила по сданным работам с итогом:
`WHERE status = 'finished' AND result_status <> 'pending'`.

### Куда писать результаты

Схема `analytics` (роль `sma_app`). Таблицы созданы по ТЗ:

- `analytics.anomalies` — срабатывания А1–А3: `rule` (`A1`, `A2`, `A3`), `exam_type`, `subject_code`,
  `object_type` (`school`, `group`, `participant`), `object_id`, `region_code`, `school_code`,
  `value`, `threshold`, `detected_at`. Ключ — `(rule, exam_type, object_type, object_id)`,
  поэтому повторный расчет пишите «с заменой»:

  ```sql
  INSERT INTO analytics.anomalies
      (rule, exam_type, subject_code, object_type, object_id, region_code, school_code, value, threshold)
  VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
  ON CONFLICT (rule, exam_type, object_type, object_id)
  DO UPDATE SET value = EXCLUDED.value, threshold = EXCLUDED.threshold, detected_at = now();
  ```

  Для группы А2 `object_id` — устойчивый идентификатор группы, например
  `школа/вариант/первые 8 знаков md5 отсортированных участников`.
  Живой экран сразу покажет число срабатываний (`mart.live.anomalies_by_rule`).
- `analytics.analysis_runs` — запуск расчета П1–П4 и индекса (`params` — параметры в jsonb);
- `analytics.p1_groups` — группы П1 запуска (`participant_ids` — массив, `min_p_value`);
- `analytics.school_index` — индекс школ запуска: `p1`…`p4b`, `z1`…`z4b`, `index`, `in_list`.

Нужна новая таблица или столбец — скажите разработчику пайплайнов: структуру схем
меняет только он (файлом миграции в `db/migrations/`), иначе ваша таблица пропадет
при пересоздании базы.

### Как проверить свои правила

В наборе генератора заложены аномалии, и есть **ключ** — `data/generated/medium/answer_key.json`:

```json
"OGE": {
  "planted": [
    {"rule": "A1", "school_code": "54010", "expected": {"z": 2.19, ...}},
    {"rule": "A2", "participant_ids": ["p_...", ...]},
    {"rule": "P1", "participant_ids": [...], "shared_wrong_tasks": [4, 7, 9, 12, 15, 17]}
  ],
  "decoys": [
    {"rule": "A2", "kind": "A2_blank", "why": "все ответы части 1 пустые"},
    {"rule": "A3", "kind": "A3_focus", "why": "5 focus_lost и 2 tab_switch"}
  ]
}
```

Ваше правило должно найти все `planted` своего типа и **не** сработать на `decoys` (ловушки
сделаны так, чтобы правило, написанное по ТЗ, на них не срабатывало). Пример:

```python
import json

key = json.load(open("data/generated/medium/answer_key.json", encoding="utf-8"))
expected = {item["school_code"] for item in key["exams"]["OGE"]["planted"] if item["rule"] == "A1"}
found = set(my_rule_a1(...)["school_code"])          # ваше правило
print("пропущено:", expected - found, "лишние:", found - expected)
```

**Эталонная реализация** всех правил по разделу 11 спецификации — `sma/reference/rules.py`.
Она нужна генератору для самопроверки и работает на pandas; можно смотреть, как посчитаны
параметры (z, Холм, MAD), но боевые правила — ваши.

Другие наборы: `make gen-small` (40 участников, быстро, без А1 и П3–П4 — мало школ),
`make gen-large` (400 участников, 100 школ, без аномалий — для проверки ложных срабатываний).
Одинаковый профиль и seed дают одинаковые данные, поэтому результаты можно сравнивать
между собой.

### Что мы уже узнали о правилах (на синтетических данных)

1. **А1 при малом числе школ не срабатывает никогда.** Если σ считать вместе с самой школой,
   z не превышает (n − 1) / √n: при 5 школах — 1,79 < 2. Вариант «σ без самой школы»
   (`sigma_mode="leave_one_out"` в эталоне) этого недостатка лишен.
2. **А2 срабатывает на полностью верных работах.** В сильной школе 5–7 учеников одного варианта
   отвечают все верно — ответы одинаковы, и А2 видит «списывание». Стоит не считать группы,
   где все ответы верные.
3. **П1 с поправкой Холма по всем парам школы осторожен.** В ОГЭ неверные ответы собираются
   в несколько типичных ошибок, и случайная пара уже совпадает в ~1,5 неверных ответа; группа
   с 6 общими редкими ответами находится примерно в трети случаев.

Подробности — в `docs/decisions.md` (раздел M6).

## 7. Правила команды

- **Не меняйте** схемы `core`, `ops`, `mart` и формат данных платформ (`sma/contract.py`) —
  от них зависят загрузчик, другие команды и тесты. Нужна новая витрина или поле — попросите.
- **Никаких персональных данных** и **никаких паролей в коде** — только в `.env`.
- Работа в git: ветка `feature/<кратко>`, merge request, ревью одного участника команды,
  коммиты на русском в повелительном наклонении («Добавить правило А1»).
- В документах команды не используется буква «е с двумя точками».

## 8. Если что-то не так

| Симптом | Что делать |
|---|---|
| `connection refused` на `localhost:5432` | `make db-up`; Docker Desktop должен быть запущен |
| `password authentication failed` | пароль роли — из своего `.env`; если меняли `.env` после создания базы — `make migrate` обновит пароли ролей |
| `permission denied for schema core` | вы под `sma_app` — ему `core` не нужен, берите данные из `mart` |
| витрина пустая или старая | `SELECT * FROM mart.data_freshness` — идут ли загрузки; `make load-files DATA=...` загружает набор заново |
| загрузились не все данные после смены набора | `make db-reset`, затем загрузить заново |
| `kim_stats` пуст | экзамен еще не закончился — КИМ публикуется после конца |

Вопросы по данным и витринам — разработчику пайплайнов.
