# Витрины СМА: как ими пользоваться

Для разработчика дашбордов и аналитика аномалий. Как поднять базу и подключиться — в
[developer_guide.md](developer_guide.md) (разделы 1–2); здесь — как читать витрины: что
значит строка, как их соединять, готовые запросы под типичные задачи и ловушки.

Все запросы в этом документе проверены на наборе medium (`make gen-medium`,
`make load-files DATA=data/generated/medium`) под ролью `sma_reader`; цифры в примерах — оттуда.

---

## 1. Главное за минуту

- **Витрина** — готовая таблица в схеме `mart` под конкретную задачу. В `core` лежат «сырые»
  версии записей платформ; витрины уже убрали удаленные записи, оставили последние версии,
  подставили названия и посчитали показатели.
- **Два вида витрин:**
  - *представление* (`sessions_current`, `answers_flat`, `proctoring_counts`, `expert_progress`,
    `live`, `data_freshness`) — считается при каждом запросе, всегда свежее;
  - *материализованная* (`school_results`, `region_results`, `task_stats`, `task_stats_region`,
    `kim_stats`, `variant_comparison`) — посчитана заранее, читается мгновенно, обновляется
    **раз в 30 секунд**, если пришли новые данные. Поэтому в течение этих 30 секунд она может
    отставать от представлений.
- **Итоги — только по сданным работам с окончательным результатом**
  (`status = 'finished' AND result_status <> 'pending'`). Материализованные витрины уже
  отфильтрованы так; в `sessions_current` и `answers_flat` есть все — фильтруйте сами.
- **Ученик в данных** — пара `(exam_type, participant_id)`; школа — `school_code`
  (уникален по всем платформам); регион — `region_code`.

## 2. Общие правила

### Ключи и соединения

| Витрина | Одна строка = | Ключ (уникален) |
|---|---|---|
| `sessions_current` | сессия ученика | `exam_type, participant_id` |
| `answers_flat` | ответ на задание части 1 | `exam_type, participant_id, task_number` |
| `school_results` | школа | `exam_type, subject_code, school_code` |
| `region_results` | регион | `exam_type, subject_code, region_code` |
| `task_stats` | задание варианта | `exam_type, subject_code, variant_id, task_number` |
| `task_stats_region` | задание варианта в регионе | + `region_code` |
| `kim_stats` | элемент содержания | `exam_type, subject_code, exam_year, section, content_code, level` |
| `variant_comparison` | задание варианта | `exam_type, subject_code, task_number, variant_id` |
| `proctoring_counts` | ученик с событиями | `exam_type, participant_id` |
| `expert_progress` | экзамен и предмет | `exam_type, subject_code` |
| `live` | платформа | `exam_type` |
| `data_freshness` | адрес API платформы | `platform, endpoint` |

Соединяйте по **полному** ключу: `exam_type` есть почти везде, и его легко забыть —
тогда ОГЭ смешается с ЕГЭ. Код предмета один и тот же (`02`) у математики ЕГЭ и ОГЭ.

### Типы и единицы

- `share_*`, `mean_share_score` — **доли от 0 до 1**; `mean_pct` — **проценты от 0 до 100**.
- Баллы и доли — тип `numeric`. В pandas они приходят как `Decimal`: приводите
  `df["mean_score"].astype(float)` перед расчетами и графиками.
- Время — `timestamptz` (с часовым поясом). Пользователю показывайте по Москве:
  `AT TIME ZONE 'Europe/Moscow'` в SQL или `.dt.tz_convert("Europe/Moscow")` в pandas.
- `null` значит «значения нет» (не явился, еще не проверено, нет отметки в журнале);
  `0` — настоящий ноль баллов. Не заменяйте `null` нулем в средних.

### Свежесть и согласованность

- Данные меняются каждые 30–60 секунд. Если страница строит несколько графиков, читайте их
  **в одной транзакции «снимка»**, иначе графики могут разойтись на одну загрузку
  (пример — в разделе 5).
- Перед тем как доверять цифрам, проверьте `mart.data_freshness` или
  `mart.live.last_successful_load_at`: если загрузок давно нет, цифры устарели.

### Чего избегать

- `SELECT * FROM mart.sessions_current` — там есть `payload` и `exam_block` (полные исходные
  записи): это тяжело и не нужно. Перечисляйте нужные столбцы.
- `answers_flat` — около 20 000 строк на medium и до 200 × 4 платформы × 20 заданий на
  экзамене. Агрегируйте в SQL (`GROUP BY`), а не забирайте все строки в pandas без нужды.
- Не считайте средние по `answers_flat` для итогов — для итогов есть `school_results`
  и `region_results`.

---

## 3. Витрины подробно

### `mart.live` — живой экран

Одна строка на платформу: сводка платформы и служебные поля СМА.

| Столбец | Значение |
|---|---|
| `in_progress`, `finished`, `annulled`, `absent` | сколько работ идет, сдано, аннулировано, неявок — по сводке платформы |
| `generated_at` | когда платформа составила сводку |
| `last_successful_load_at` | последняя успешная загрузка СМА с этой платформы |
| `anomalies_by_rule` | `{"A1": 1, "A3": 2}` — срабатывания правил из `analytics.anomalies`; `{}` — нет |

```sql
SELECT exam_type, in_progress, finished, annulled, absent,
       last_successful_load_at AT TIME ZONE 'Europe/Moscow' AS last_load_msk,
       now() - last_successful_load_at > interval '2 minutes'   AS stale,
       anomalies_by_rule
FROM mart.live
ORDER BY exam_type;
```

Если `stale` — покажите предупреждение «данные платформы не обновляются».

### `mart.data_freshness` — что с загрузкой

Одна строка на адрес API платформы (`sessions`, `answers`, ...).

```sql
SELECT platform, endpoint, lag_sec, last_error_reason,
       last_error_at > coalesce(last_ok_at, '-infinity') AS failing_now
FROM mart.data_freshness
ORDER BY failing_now DESC, lag_sec DESC;
```

`last_error_reason`: `http_401` — неверный токен; `http_503`, `timeout`, `network` — платформа
недоступна; `bad_json`, `no_wrapper` — платформа прислала испорченный ответ.

### `mart.sessions_current` — сессии учеников

Последняя версия каждой сессии с названиями региона и школы. Итог — отдельными столбцами:
`grade` (отметка ОГЭ, ВПР), `test_score` (тестовый балл ЕГЭ), `result_level` (уровень НИКО).

```sql
-- Ход экзамена: сколько учеников в каком состоянии, по платформам
SELECT exam_type, status, result_status, count(*) AS students
FROM mart.sessions_current
GROUP BY exam_type, status, result_status
ORDER BY exam_type, status, result_status;
```

```sql
-- Распределение отметок ОГЭ (только окончательные итоги)
SELECT grade, count(*) AS students,
       round(count(*) * 1.0 / sum(count(*)) OVER (), 4) AS share
FROM mart.sessions_current
WHERE exam_type = 'OGE' AND status = 'finished' AND result_status <> 'pending'
GROUP BY grade
ORDER BY grade;
```

Остальные полезные поля блока экзамена — в `exam_block` (jsonb): у ОГЭ
`exam_block -> 'block_scores' ->> 'geometry'` (баллы по геометрии), `exam_block ->> 'block_failed'`;
у ВПР — `exam_block ->> 'term_mark'` (отметка за четверть по журналу).

### `mart.region_results` и `mart.school_results` — итоги

`region_results` — регион; `school_results` — школа (названия школы в ней нет — берите
из `sessions_current`). Средний балл региона — по всем участникам региона, а не среднее
средних школ. `share_grade2` есть только у ОГЭ и ВПР.

```sql
-- Регионы ОГЭ: от лучшего к худшему по проценту выполнения
SELECT region_name, participants, schools, mean_score, mean_pct, share_grade2,
       share_absent, share_annulled
FROM mart.region_results
WHERE exam_type = 'OGE'
ORDER BY mean_pct DESC;
```

На medium первая строка — Республика Татарстан: 93 участника, 10 школ, 13,51 балла, 43,57 %.

```sql
-- Школы региона с названиями и отставанием от среднего по региону
SELECT sr.school_code, names.school_name, sr.participants, sr.mean_score,
       round(sr.mean_pct - rr.mean_pct, 2) AS pct_vs_region
FROM mart.school_results sr
JOIN mart.region_results rr USING (exam_type, subject_code, region_code)
JOIN (SELECT DISTINCT school_code, school_name FROM mart.sessions_current) names
  USING (school_code)
WHERE sr.exam_type = 'OGE' AND sr.region_code = '77'
ORDER BY sr.mean_pct DESC;
```

**Маленькие группы:** у школы с 10 участниками средний процент «гуляет» на ±10 п. п. просто
случайно. Показывайте `participants` рядом со средним и не ранжируйте школы меньше 10 участников.

### `mart.answers_flat` — ответы части 1

Каждый ответ с данными ученика (`variant_id`, `school_code`, `room_code`, `status`).
Сравнивайте ответы по `answer_norm` (без пробелов, «.» → «,», строчными), а не по `answer`:
« 0.5», «0,5» и «0.5» — один ответ.

```sql
-- Время на задания ЕГЭ: медиана по номерам заданий, секунды
SELECT task_number,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY time_spent_sec) AS median_sec,
       count(*) FILTER (WHERE time_spent_sec IS NULL) AS no_time
FROM mart.answers_flat
WHERE exam_type = 'EGE' AND status = 'finished'
GROUP BY task_number
ORDER BY task_number;
```

### `mart.task_stats` и `mart.task_stats_region` — задания

Доля верных, доля пустых и пять самых частых **неверных** ответов (`top_wrong`, jsonb).
Доли в `top_wrong` — от всех ответов на задание, поэтому их сумма не больше
`1 − share_correct − share_empty`.

```sql
-- Самые трудные задания ОГЭ
SELECT variant_id, task_number, n, share_correct, share_empty
FROM mart.task_stats
WHERE exam_type = 'OGE'
ORDER BY share_correct
LIMIT 5;
```

На medium самое трудное — задание 19 варианта v2: верно 13 % из 216.

```sql
-- Частые ошибки задания: jsonb → строки
SELECT t.variant_id, t.task_number, wrong.place, wrong.item ->> 'answer' AS answer,
       (wrong.item ->> 'share')::numeric AS share
FROM mart.task_stats t
CROSS JOIN LATERAL jsonb_array_elements(t.top_wrong) WITH ORDINALITY AS wrong(item, place)
WHERE t.exam_type = 'OGE' AND t.task_number = 19
ORDER BY t.variant_id, wrong.place;
```

На medium у задания 19 варианта v2 самый частый неверный ответ «506» — у 38 % учеников:
скорее всего, типичная ошибка в решении, которую стоит разобрать с учителями.

`task_stats_region` — то же по регионам (`region_code`); нужна для П3 и для сравнения
регионов по отдельным заданиям.

### `mart.kim_stats` — темы кодификатора

Средняя доля балла (балл / максимум) по заданиям, проверяющим элемент содержания;
часть 1 и часть 2 вместе. Пуста до окончания экзамена — КИМ публикуется после него.

```sql
-- Слабые темы ЕГЭ
SELECT section, content_code, level, tasks, n_answers, mean_share_score
FROM mart.kim_stats
WHERE exam_type = 'EGE'
ORDER BY mean_share_score
LIMIT 5;
```

Для тепловой карты «раздел × уровень» сгруппируйте по `section, level`
и возьмите `avg(mean_share_score)`.

### `mart.variant_comparison` — равноценность вариантов

`share_score` — средняя доля балла за задание в варианте; `diff_from_others` — насколько
вариант легче (+) или труднее (−) остальных вариантов того же задания.

```sql
-- Задания, где варианты сильнее всего различаются (больше 10 п. п.)
SELECT exam_type, task_number, variant_id, participants, share_score, diff_from_others
FROM mart.variant_comparison
WHERE abs(diff_from_others) > 0.10
ORDER BY abs(diff_from_others) DESC;
```

### `mart.expert_progress` — проверка части 2

```sql
SELECT exam_type, pending, checked, appeal, share_checked
FROM mart.expert_progress
ORDER BY exam_type;
```

Во время проверки `share_checked` растет от 0 до 1; итоги экзамена окончательные, когда он равен 1.

### `mart.proctoring_counts` — события прокторинга

`counted_events` — переключение вкладки, копирование, вставка, выход из полноэкранного режима;
`focus_lost` (окно потеряло фокус) не засчитывается. У ВПР прокторинга нет.

```sql
SELECT exam_type, participant_id, school_code, counted_events, focus_lost
FROM mart.proctoring_counts
WHERE counted_events >= 3
ORDER BY counted_events DESC;
```

---

## 4. Данные для правил аномалий

Ниже — как достать входные данные правил; сами боевые правила (пороги, поправки, запись
в `analytics`) — работа аналитика. Эталонная реализация по спецификации — `sma/reference/rules.py`.

```sql
-- А1: средние школ и регионов, z школы относительно школ своего региона
-- (σ вместе с самой школой — как в ТЗ; школы ≥ 10 участников, регионы ≥ 3 таких школ)
WITH big AS (
    SELECT * FROM mart.school_results WHERE participants >= 10
), scored AS (
    SELECT exam_type, subject_code, region_code, school_code, mean_score,
           avg(mean_score) OVER region         AS region_mean,
           stddev_samp(mean_score) OVER region AS sigma,
           count(*) OVER region                AS schools
    FROM big
    WINDOW region AS (PARTITION BY exam_type, subject_code, region_code)
)
SELECT exam_type, region_code, school_code, mean_score,
       round((mean_score - region_mean) / nullif(sigma, 0), 2) AS z
FROM scored
WHERE schools >= 3
ORDER BY z DESC NULLS LAST
LIMIT 5;
```

На medium первой строкой идет школа, заложенная генератором как А1 (сверьте с `answer_key.json`).

```sql
-- А2: одинаковые наборы ответов части 1 в школе и варианте
WITH works AS (
    SELECT exam_type, school_code, variant_id, participant_id,
           string_agg(answer_norm, '|' ORDER BY task_number) AS answers
    FROM mart.answers_flat
    WHERE status = 'finished' AND result_status <> 'pending'
    GROUP BY exam_type, school_code, variant_id, participant_id
)
SELECT exam_type, school_code, variant_id, count(*) AS students,
       array_agg(participant_id ORDER BY participant_id) AS participant_ids
FROM works
GROUP BY exam_type, school_code, variant_id, answers
HAVING count(*) >= 5
ORDER BY students DESC;
```

Сюда попадут и группы, у которых все ответы пустые (ловушка генератора) — по ТЗ их нужно
отсеять условием «непустых ответов не меньше половины».

```sql
-- П1: насколько распространен каждый неверный ответ в варианте (основа для λ)
SELECT exam_type, variant_id, task_number, answer_norm,
       count(*) * 1.0 / sum(count(*)) OVER (PARTITION BY exam_type, variant_id, task_number)
           AS share_among_wrong
FROM mart.answers_flat
WHERE status = 'finished' AND NOT is_correct AND answer_norm <> ''
GROUP BY exam_type, variant_id, task_number, answer_norm
ORDER BY exam_type, variant_id, task_number, share_among_wrong DESC
LIMIT 10;
```

Для λ по ТЗ нужна доля **от всех участников варианта**, а не от неверных: делите на число
участников варианта (`count(DISTINCT participant_id)` по варианту).

```sql
-- П4б: доля учеников школы с баллом у порога ОГЭ (порог 8 → окно [8, 10])
SELECT school_code, count(*) AS participants,
       round(avg(CASE WHEN primary_score BETWEEN 8 AND 10 THEN 1.0 ELSE 0 END), 3) AS share_window
FROM mart.sessions_current
WHERE exam_type = 'OGE' AND status = 'finished' AND result_status <> 'pending'
GROUP BY school_code
HAVING count(*) >= 10
ORDER BY share_window DESC
LIMIT 5;
```

Пороги — в справочнике (`core.subjects.threshold_score`, роль `sma_reader`) или в спецификации:
ЕГЭ 6, ОГЭ 8, ВПР 7, НИКО 5 (в синтетических данных).

---

## 5. Из Python

```python
import os

import pandas as pd
from dotenv import find_dotenv, load_dotenv
from sqlalchemy import create_engine

load_dotenv(find_dotenv(usecwd=True))
engine = create_engine(
    f"postgresql+psycopg://sma_reader:{os.environ['SMA_READER_PASSWORD']}@localhost:5432/sma"
)

regions = pd.read_sql(
    "SELECT region_name, participants, mean_pct FROM mart.region_results WHERE exam_type = 'OGE'",
    engine,
)
regions["mean_pct"] = regions["mean_pct"].astype(float)  # numeric → float
print(regions.sort_values("mean_pct", ascending=False).head(3))
```

**Параметры запроса** передавайте отдельно, а не вставляйте в текст SQL (иначе ошибки
с кавычками и риск SQL-инъекции):

```python
from sqlalchemy import text

schools = pd.read_sql(
    text("SELECT school_code, participants, mean_score FROM mart.school_results "
         "WHERE exam_type = :exam AND region_code = :region ORDER BY mean_score DESC"),
    engine,
    params={"exam": "OGE", "region": "77"},
)
```

**Время по Москве:**

```python
live = pd.read_sql("SELECT exam_type, last_successful_load_at FROM mart.live", engine)
live["last_load_msk"] = live["last_successful_load_at"].dt.tz_convert("Europe/Moscow")
```

**Несколько графиков страницы из одного снимка данных** (одна транзакция REPEATABLE READ —
все запросы видят базу на один момент, даже если загрузчик пишет новые данные):

```python
with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
    with conn.begin():
        counters = pd.read_sql("SELECT * FROM mart.live", conn)
        statuses = pd.read_sql(
            "SELECT exam_type, status, count(*) AS n FROM mart.sessions_current "
            "GROUP BY exam_type, status",
            conn,
        )
```

**Streamlit:** оберните чтение в `@st.cache_data(ttl=30)` — чаще витрины все равно
не обновляются.

## 6. Проверка своих запросов

- **По ключу генератора.** В `data/generated/medium/answer_key.json` записано, какие
  аномалии где заложены: правильный запрос «школы с максимальным z» должен первой показать
  школу А1 из ключа, группа А2 — совпасть с ключом и т. д.
- **По пересчету.** Сравните итог витрины с пересчетом из `sessions_current`: например,
  `sum(participants)` из `school_results` = число сданных работ с итогом. Так же устроены
  проверки качества Q-MART-01..03 (`make quality`).
- **На маленьком наборе.** `make gen-small` (40 участников на экзамен) удобно разбирать руками.
- **«Проиграть» экзамен** — `make mock-replay SPEED=10` и `make loader-mock`
  (после `make db-reset`): видно, как меняются витрины во время экзамена и проверки.

Нужна витрина, которой нет, или столбец в существующей — попросите разработчика пайплайнов:
витрины меняются только файлом миграции, чтобы у всех была одна и та же схема.
