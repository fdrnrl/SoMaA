-- 005: витрины схемы mart (раздел 12).
--
-- Правила для всех витрин:
--   * берутся только записи с deleted = false;
--   * если у одного естественного ключа несколько record_id — берется запись
--     с наибольшим updated_at (DISTINCT ON), при равенстве — с большим record_id;
--   * материализованные витрины имеют уникальный индекс (для REFRESH ... CONCURRENTLY)
--     и обновляются функцией mart.refresh_all() после загрузки новых данных;
--   * результаты (баллы, отметки, доли) считаются только по сданным работам
--     с окончательным итогом: status = finished и result_status <> pending.
-- Процент выполнения = primary_score / max_primary_score × 100.


-- Актуальные сессии: одна строка на участника платформы, с названиями региона и школы
-- и итогами из блока экзамена отдельными столбцами.
CREATE VIEW mart.sessions_current AS
SELECT
    s.exam_type, s.record_id, s.participant_id, s.subject_code, s.grade_level, s.variant_id,
    s.region_code, r.name AS region_name,
    s.school_code, sc.name AS school_name,
    s.room_code, s.status, s.started_at, s.finished_at,
    s.primary_score, s.max_primary_score, s.result_status,
    (s.exam_block ->> 'grade')::smallint      AS grade,          -- ОГЭ, ВПР
    (s.exam_block ->> 'test_score')::smallint AS test_score,     -- ЕГЭ
    s.exam_block ->> 'result_level'           AS result_level,   -- НИКО
    s.exam_block, s.updated_at, s.deleted, s.payload, s.loaded_at, s.load_id
FROM (
    SELECT DISTINCT ON (exam_type, participant_id) *
    FROM core.sessions
    WHERE NOT deleted
    ORDER BY exam_type, participant_id, updated_at DESC, record_id DESC
) s
LEFT JOIN core.regions r  ON r.region_code = s.region_code
LEFT JOIN core.schools sc ON sc.school_code = s.school_code;


-- Ответы части 1 с данными сессии ученика (для А2, П1). Ответ без сессии сюда
-- не попадает — такие ответы находит проверка качества Q-CONS-01.
CREATE VIEW mart.answers_flat AS
SELECT
    a.exam_type, a.record_id, a.participant_id, a.task_number,
    a.answer, a.answer_norm, a.is_correct, a.score, a.max_score,
    a.answered_at, a.time_spent_sec, a.audio_plays,
    s.subject_code, s.variant_id, s.region_code, s.school_code, s.room_code,
    s.status, s.result_status,
    a.updated_at
FROM (
    SELECT DISTINCT ON (exam_type, participant_id, task_number) *
    FROM core.answers
    WHERE NOT deleted
    ORDER BY exam_type, participant_id, task_number, updated_at DESC, record_id DESC
) a
JOIN mart.sessions_current s
  ON s.exam_type = a.exam_type AND s.participant_id = a.participant_id;


-- Итоги школ (А1, визуализация).
CREATE MATERIALIZED VIEW mart.school_results AS
SELECT
    s.exam_type, s.subject_code, s.region_code, s.school_code,
    count(*)                                                       AS participants,
    avg(s.primary_score)::numeric(6, 2)                            AS mean_score,
    stddev_samp(s.primary_score)::numeric(6, 2)                    AS sd_score,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY s.primary_score)  AS median_score,
    avg(100.0 * s.primary_score / s.max_primary_score)::numeric(6, 2) AS mean_pct,
    CASE WHEN s.exam_type IN ('OGE', 'VPR')
         THEN avg(CASE WHEN s.grade = 2 THEN 1 ELSE 0 END)
    END::numeric(5, 4)                                             AS share_grade2
FROM mart.sessions_current s
WHERE s.status = 'finished' AND s.result_status <> 'pending'
GROUP BY s.exam_type, s.subject_code, s.region_code, s.school_code;

CREATE UNIQUE INDEX school_results_key ON mart.school_results (exam_type, subject_code, school_code);


-- Итоги регионов: средние — по участникам (а не по средним школ); доли неявок
-- и аннулирований — от всех сессий региона.
CREATE MATERIALIZED VIEW mart.region_results AS
SELECT
    s.exam_type, s.subject_code, s.region_code,
    max(s.region_name)                                                      AS region_name,
    count(*) FILTER (WHERE s.status = 'finished' AND s.result_status <> 'pending') AS participants,
    count(DISTINCT s.school_code)
        FILTER (WHERE s.status = 'finished' AND s.result_status <> 'pending')    AS schools,
    (avg(s.primary_score)
        FILTER (WHERE s.status = 'finished' AND s.result_status <> 'pending'))::numeric(6, 2)
                                                                            AS mean_score,
    (avg(100.0 * s.primary_score / s.max_primary_score)
        FILTER (WHERE s.status = 'finished' AND s.result_status <> 'pending'))::numeric(6, 2)
                                                                            AS mean_pct,
    CASE WHEN s.exam_type IN ('OGE', 'VPR')
         THEN avg(CASE WHEN s.grade = 2 THEN 1 ELSE 0 END)
                  FILTER (WHERE s.status = 'finished' AND s.result_status <> 'pending')
    END::numeric(5, 4)                                                      AS share_grade2,
    avg(CASE WHEN s.status = 'absent' THEN 1 ELSE 0 END)::numeric(5, 4)     AS share_absent,
    avg(CASE WHEN s.status = 'annulled' THEN 1 ELSE 0 END)::numeric(5, 4)   AS share_annulled
FROM mart.sessions_current s
GROUP BY s.exam_type, s.subject_code, s.region_code;

CREATE UNIQUE INDEX region_results_key ON mart.region_results (exam_type, subject_code, region_code);


-- Статистика заданий части 1 по варианту (П1–П3): доля верных, доля пустых и пять
-- самых частых неверных непустых ответов с долями от всех ответов на задание.
-- Только ответы сданных работ (status = finished).
--
-- Один проход без соединений: ответы группируются по тексту, оконные функции дают
-- общее число ответов и место ответа, затем — одна строка на задание. С соединением
-- промежуточных результатов планировщик при неточной статистике выбирал вложенные
-- циклы, и обновление на medium шло 40 с вместо долей секунды.
CREATE MATERIALIZED VIEW mart.task_stats AS
WITH counted AS (
    SELECT exam_type, subject_code, variant_id, task_number, answer_norm, is_correct,
           count(*) AS answers_count
    FROM mart.answers_flat
    WHERE status = 'finished'
    GROUP BY exam_type, subject_code, variant_id, task_number, answer_norm, is_correct
),
ranked AS (
    SELECT c.*,
           NOT c.is_correct AND c.answer_norm <> '' AS is_wrong,
           sum(c.answers_count) OVER task AS n,
           row_number() OVER (
               PARTITION BY exam_type, subject_code, variant_id, task_number
               ORDER BY (NOT c.is_correct AND c.answer_norm <> '') DESC,
                        c.answers_count DESC, c.answer_norm
           ) AS place
    FROM counted c
    WINDOW task AS (PARTITION BY exam_type, subject_code, variant_id, task_number)
)
SELECT exam_type, subject_code, variant_id, task_number,
       sum(answers_count)::bigint AS n,
       (sum(answers_count) FILTER (WHERE is_correct) / sum(answers_count))::numeric(5, 4)
           AS share_correct,
       (coalesce(sum(answers_count) FILTER (WHERE answer_norm = ''), 0) / sum(answers_count))
           ::numeric(5, 4) AS share_empty,
       coalesce(
           jsonb_agg(jsonb_build_object('answer', answer_norm,
                                        'share', round(answers_count / n, 4))
                     ORDER BY place) FILTER (WHERE is_wrong AND place <= 5),
           '[]'::jsonb) AS top_wrong
FROM ranked
GROUP BY exam_type, subject_code, variant_id, task_number;

CREATE UNIQUE INDEX task_stats_key ON mart.task_stats (exam_type, subject_code, variant_id, task_number);


-- То же по региону (П3: трудность задания по региону).
CREATE MATERIALIZED VIEW mart.task_stats_region AS
WITH counted AS (
    SELECT exam_type, subject_code, variant_id, region_code, task_number, answer_norm, is_correct,
           count(*) AS answers_count
    FROM mart.answers_flat
    WHERE status = 'finished'
    GROUP BY exam_type, subject_code, variant_id, region_code, task_number, answer_norm, is_correct
),
ranked AS (
    SELECT c.*,
           NOT c.is_correct AND c.answer_norm <> '' AS is_wrong,
           sum(c.answers_count) OVER task AS n,
           row_number() OVER (
               PARTITION BY exam_type, subject_code, variant_id, region_code, task_number
               ORDER BY (NOT c.is_correct AND c.answer_norm <> '') DESC,
                        c.answers_count DESC, c.answer_norm
           ) AS place
    FROM counted c
    WINDOW task AS (PARTITION BY exam_type, subject_code, variant_id, region_code, task_number)
)
SELECT exam_type, subject_code, variant_id, region_code, task_number,
       sum(answers_count)::bigint AS n,
       (sum(answers_count) FILTER (WHERE is_correct) / sum(answers_count))::numeric(5, 4)
           AS share_correct,
       (coalesce(sum(answers_count) FILTER (WHERE answer_norm = ''), 0) / sum(answers_count))
           ::numeric(5, 4) AS share_empty,
       coalesce(
           jsonb_agg(jsonb_build_object('answer', answer_norm,
                                        'share', round(answers_count / n, 4))
                     ORDER BY place) FILTER (WHERE is_wrong AND place <= 5),
           '[]'::jsonb) AS top_wrong
FROM ranked
GROUP BY exam_type, subject_code, variant_id, region_code, task_number;

CREATE UNIQUE INDEX task_stats_region_key
    ON mart.task_stats_region (exam_type, subject_code, variant_id, region_code, task_number);


-- Статистика по темам кодификатора: средняя доля балла (балл / максимум) по элементам
-- содержания. Часть 1 — по ответам, часть 2 — по проверенным оценкам экспертов;
-- только сданные работы. Задание без кодов (content_codes = []) сюда не попадает.
CREATE MATERIALIZED VIEW mart.kim_stats AS
WITH kim AS (
    SELECT DISTINCT ON (exam_type, subject_code, exam_year, variant_id, task_number) *
    FROM core.kim_tasks
    WHERE NOT deleted
    ORDER BY exam_type, subject_code, exam_year, variant_id, task_number, updated_at DESC, record_id DESC
),
reviews AS (
    SELECT DISTINCT ON (exam_type, participant_id, task_number) *
    FROM core.expert_reviews
    WHERE NOT deleted
    ORDER BY exam_type, participant_id, task_number, updated_at DESC, record_id DESC
),
scores AS (
    SELECT a.exam_type, a.subject_code, a.variant_id, a.task_number,
           a.score::numeric / a.max_score AS share
    FROM mart.answers_flat a
    WHERE a.status = 'finished'
    UNION ALL
    SELECT r.exam_type, s.subject_code, s.variant_id, r.task_number,
           r.score::numeric / r.max_score
    FROM reviews r
    JOIN mart.sessions_current s ON s.exam_type = r.exam_type AND s.participant_id = r.participant_id
    WHERE r.check_status = 'checked' AND s.status = 'finished'
)
SELECT k.exam_type, k.subject_code, k.exam_year, k.section, code.content_code, k.level,
       count(DISTINCT (k.variant_id, k.task_number)) AS tasks,
       count(sc.share)                               AS n_answers,
       avg(sc.share)::numeric(5, 4)                  AS mean_share_score
FROM kim k
CROSS JOIN LATERAL unnest(k.content_codes) AS code(content_code)
LEFT JOIN scores sc
       ON sc.exam_type = k.exam_type AND sc.subject_code = k.subject_code
      AND sc.variant_id = k.variant_id AND sc.task_number = k.task_number
GROUP BY k.exam_type, k.subject_code, k.exam_year, k.section, code.content_code, k.level;

CREATE UNIQUE INDEX kim_stats_key
    ON mart.kim_stats (exam_type, subject_code, exam_year, section, content_code, level);


-- Сравнение вариантов: средняя доля балла за задание в каждом варианте и разность
-- со средним по остальным вариантам того же задания.
CREATE MATERIALIZED VIEW mart.variant_comparison AS
WITH reviews AS (
    SELECT DISTINCT ON (exam_type, participant_id, task_number) *
    FROM core.expert_reviews
    WHERE NOT deleted
    ORDER BY exam_type, participant_id, task_number, updated_at DESC, record_id DESC
),
scores AS (
    SELECT a.exam_type, a.subject_code, a.variant_id, a.task_number, a.participant_id,
           a.score::numeric / a.max_score AS share
    FROM mart.answers_flat a
    WHERE a.status = 'finished'
    UNION ALL
    SELECT r.exam_type, s.subject_code, s.variant_id, r.task_number, r.participant_id,
           r.score::numeric / r.max_score
    FROM reviews r
    JOIN mart.sessions_current s ON s.exam_type = r.exam_type AND s.participant_id = r.participant_id
    WHERE r.check_status = 'checked' AND s.status = 'finished'
),
per_variant AS (
    SELECT exam_type, subject_code, task_number, variant_id,
           avg(share)::numeric(5, 4)       AS share_score,
           count(DISTINCT participant_id)  AS participants
    FROM scores
    GROUP BY exam_type, subject_code, task_number, variant_id
)
SELECT p.exam_type, p.subject_code, p.task_number, p.variant_id, p.share_score, p.participants,
       (p.share_score - (
           SELECT avg(o.share_score)
           FROM per_variant o
           WHERE o.exam_type = p.exam_type AND o.subject_code = p.subject_code
             AND o.task_number = p.task_number AND o.variant_id <> p.variant_id
       ))::numeric(6, 4) AS diff_from_others
FROM per_variant p;

CREATE UNIQUE INDEX variant_comparison_key
    ON mart.variant_comparison (exam_type, subject_code, task_number, variant_id);


-- События прокторинга по участникам (А3, живой экран). Засчитываемые — все, кроме focus_lost.
CREATE VIEW mart.proctoring_counts AS
SELECT e.exam_type, e.participant_id, max(s.school_code) AS school_code,
       count(*) FILTER (WHERE e.event_type <> 'focus_lost') AS counted_events,
       count(*) FILTER (WHERE e.event_type = 'focus_lost')  AS focus_lost,
       max(e.occurred_at)                                   AS last_event_at
FROM core.proctoring_events e
LEFT JOIN mart.sessions_current s
       ON s.exam_type = e.exam_type AND s.participant_id = e.participant_id
WHERE NOT e.deleted
GROUP BY e.exam_type, e.participant_id;


-- Ход проверки экспертами.
CREATE VIEW mart.expert_progress AS
WITH reviews AS (
    SELECT DISTINCT ON (exam_type, participant_id, task_number) *
    FROM core.expert_reviews
    WHERE NOT deleted
    ORDER BY exam_type, participant_id, task_number, updated_at DESC, record_id DESC
)
SELECT r.exam_type, s.subject_code,
       count(*) FILTER (WHERE r.check_status = 'pending')  AS pending,
       count(*) FILTER (WHERE r.check_status = 'checked')  AS checked,
       count(*) FILTER (WHERE r.check_stage = 'appeal')    AS appeal,
       (avg(CASE WHEN r.check_status = 'checked' THEN 1 ELSE 0 END))::numeric(5, 4) AS share_checked
FROM reviews r
LEFT JOIN mart.sessions_current s
       ON s.exam_type = r.exam_type AND s.participant_id = r.participant_id
GROUP BY r.exam_type, s.subject_code;


-- Живой экран: последняя сводка платформы, время последней успешной загрузки
-- и число аномалий по правилам.
CREATE VIEW mart.live AS
SELECT l.exam_type, l.in_progress, l.finished, l.annulled, l.absent, l.generated_at,
       (SELECT max(g.finished_at) FROM ops.load_log g
         WHERE g.platform = l.exam_type AND g.status IN ('ok', 'partial')) AS last_successful_load_at,
       coalesce((SELECT jsonb_object_agg(a.rule, a.anomalies)
                   FROM (SELECT rule, count(*) AS anomalies FROM analytics.anomalies
                          WHERE exam_type = l.exam_type GROUP BY rule) a),
                '{}'::jsonb) AS anomalies_by_rule
FROM core.live_status l;


-- Свежесть данных по каждому адресу платформы (живой экран, мониторинг загрузчика).
CREATE VIEW mart.data_freshness AS
WITH last_error AS (
    SELECT DISTINCT ON (platform, endpoint) platform, endpoint, error_reason
    FROM ops.load_log
    WHERE status = 'error'
    ORDER BY platform, endpoint, started_at DESC, id DESC
)
SELECT g.platform, g.endpoint,
       max(g.finished_at) FILTER (WHERE g.status IN ('ok', 'partial')) AS last_ok_at,
       max(g.finished_at) FILTER (WHERE g.status = 'error')            AS last_error_at,
       max(e.error_reason)                                             AS last_error_reason,
       extract(epoch FROM now() - max(g.finished_at) FILTER (WHERE g.status IN ('ok', 'partial')))
           ::integer                                                   AS lag_sec
FROM ops.load_log g
LEFT JOIN last_error e ON e.platform = g.platform AND e.endpoint = g.endpoint
GROUP BY g.platform, g.endpoint;


-- Обновление материализованных витрин. Обновлять витрину может только ее владелец,
-- поэтому функция выполняется с правами владельца (SECURITY DEFINER), а загрузчику
-- дано право ее вызывать. CONCURRENTLY — читатели не блокируются во время обновления.
CREATE FUNCTION mart.refresh_all() RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mart.school_results;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mart.region_results;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mart.task_stats;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mart.task_stats_region;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mart.kim_stats;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mart.variant_comparison;
END;
$$;

REVOKE ALL ON FUNCTION mart.refresh_all() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.refresh_all() TO sma_loader;
