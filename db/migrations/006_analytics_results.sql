-- 006: сохранение результатов аналитики аномалий (sma/analytics_job.py) и витрины для дашборда.
--
-- Запуск расчета (analysis_runs) и все его результаты пишутся одной транзакцией вместе
-- с отметкой status = 'ok'. Поэтому витрины ниже показывают только завершенные расчеты,
-- а «расчет есть, аномалий нет» (запуск ok, строк нет) отличается от «расчета нет».


ALTER TABLE analytics.analysis_runs
    ADD COLUMN reason      text,                                   -- periodic, final, manual
    ADD COLUMN status      text  NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'ok', 'error')),
    ADD COLUMN data_as_of  timestamptz,                            -- момент снимка данных
    ADD COLUMN scope       jsonb NOT NULL DEFAULT '[]'::jsonb,     -- [{"exam_type", "subject_code"}]
    ADD COLUMN summary     jsonb,                                  -- число аномалий, школ, групп
    ADD COLUMN error       text;

-- Аномалия хранится один раз (ключ rule, exam_type, object_type, object_id, FR-13):
-- anomaly_id не меняется между расчетами; run_id — запуск, который нашел ее впервые,
-- last_run_id — последний запуск, который ее подтвердил.
ALTER TABLE analytics.anomalies
    ADD COLUMN run_id       bigint REFERENCES analytics.analysis_runs (run_id),
    ADD COLUMN last_run_id  bigint REFERENCES analytics.analysis_runs (run_id),
    ADD COLUMN details      jsonb;                                 -- z, σ, состав группы А2 и т. п.

ALTER TABLE analytics.p1_groups
    ADD COLUMN region_code  text,
    ADD COLUMN min_p_raw    double precision;                      -- p до поправки Холма


-- Входные данные для расчета: модуль аналитика читает их под ролью sma_app (без доступа к core).
CREATE VIEW mart.proctoring_events AS
SELECT exam_type, record_id, participant_id, event_type, occurred_at, task_number
FROM core.proctoring_events
WHERE NOT deleted;

CREATE VIEW mart.subjects AS
SELECT exam_type, subject_code, name, grade_level, max_primary_score, threshold_score
FROM core.subjects;

CREATE VIEW mart.schools AS
SELECT s.school_code, s.region_code, s.name AS school_name, r.name AS region_name
FROM core.schools s
LEFT JOIN core.regions r ON r.region_code = s.region_code;


-- Витрины результатов для дашборда (читает sma_reader).

-- Только завершенные расчеты.
CREATE VIEW mart.analysis_runs AS
SELECT run_id, started_at, finished_at, reason, data_as_of, scope, params, summary
FROM analytics.analysis_runs
WHERE status = 'ok' AND finished_at IS NOT NULL;

-- Аномалии. is_active — подтверждена последним завершенным расчетом своего экзамена;
-- is_new — найдена именно этим расчетом.
CREATE VIEW mart.anomalies AS
WITH latest AS (
    SELECT scope_item ->> 'exam_type' AS exam_type, max(r.run_id) AS run_id
    FROM mart.analysis_runs r
    CROSS JOIN LATERAL jsonb_array_elements(r.scope) AS scope_item
    GROUP BY scope_item ->> 'exam_type'
)
SELECT a.anomaly_id, a.rule, a.exam_type, a.subject_code, a.object_type, a.object_id,
       a.region_code, a.school_code, a.value, a.threshold, a.detected_at,
       a.run_id, a.last_run_id,
       a.last_run_id = l.run_id AS is_active,
       a.run_id = l.run_id      AS is_new,
       a.details
FROM analytics.anomalies a
LEFT JOIN latest l ON l.exam_type = a.exam_type
WHERE a.run_id IS NOT NULL;

-- Показатели П1–П4 и индекс школ; calculated_at — момент снимка данных расчета.
CREATE VIEW mart.school_index AS
SELECT s.run_id, r.data_as_of AS calculated_at, s.exam_type, s.subject_code, s.region_code,
       s.school_code, s.participants, s.p1, s.p2, s.p3, s.p4a, s.p4b,
       s.z1, s.z2, s.z3, s.z4a, s.z4b, s.index, s.in_list
FROM analytics.school_index s
JOIN analytics.analysis_runs r ON r.run_id = s.run_id
WHERE r.status = 'ok';

-- Группы П1.
CREATE VIEW mart.p1_groups AS
SELECT g.group_id, g.exam_type, g.subject_code, g.region_code, g.school_code, g.room_code,
       g.variant_id, g.participant_ids, g.size, g.min_p_value, g.min_p_raw, g.run_id
FROM analytics.p1_groups g
JOIN analytics.analysis_runs r ON r.run_id = g.run_id
WHERE r.status = 'ok';
