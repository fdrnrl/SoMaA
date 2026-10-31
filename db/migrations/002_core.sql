-- 002: таблицы схемы core — нормализованные данные платформ (раздел 7.3).
--
-- Правила для всех таблиц с данными платформ:
--   * первичный ключ (exam_type, record_id) — по нему идет upsert;
--   * поле контракта с «null: нет» → столбец NOT NULL;
--   * время — timestamptz; исходная запись — в payload (jsonb);
--   * служебные столбцы loaded_at и load_id. Внешний ключ load_id → ops.load_log
--     добавляется в 003_ops.sql, потому что ops.load_log создается там.


-- Сессия ученика: один ученик на одном экзамене (контракт 6.4).
CREATE TABLE core.sessions (
    exam_type          text        NOT NULL
        CHECK (exam_type IN ('EGE', 'OGE', 'VPR', 'NIKO')),
    record_id          text        NOT NULL,
    participant_id     text        NOT NULL,
    subject_code       text        NOT NULL,
    grade_level        smallint    NOT NULL
        CHECK(grade_level BETWEEN 4 AND 11),
    variant_id         text        NOT NULL,
    region_code        text        NOT NULL,
    school_code        text        NOT NULL,
    room_code          text,
    status             text        NOT NULL
        CHECK (status IN ('absent', 'started', 'in_progress', 'finished', 'annulled')),
    started_at         timestamptz,
    finished_at        timestamptz,
    primary_score      smallint
        CHECK(primary_score >= 0),
    max_primary_score  smallint    NOT NULL
        CHECK(max_primary_score > 0),
    result_status      text        NOT NULL
        CHECK (result_status IN ('pending', 'final', 'appeal_changed')),
    exam_block         jsonb,                          -- содержимое блока ege/oge/vpr/niko
    updated_at         timestamptz NOT NULL,
    deleted            boolean     NOT NULL,
    payload            jsonb       NOT NULL,           -- исходная запись платформы
    loaded_at          timestamptz NOT NULL DEFAULT now(),
    load_id            bigint,
    PRIMARY KEY (exam_type, record_id)
);

CREATE INDEX sessions_participant_idx ON core.sessions (exam_type, participant_id);
CREATE INDEX sessions_school_idx      ON core.sessions (exam_type, subject_code, region_code, school_code);
CREATE INDEX sessions_updated_idx     ON core.sessions (updated_at);


CREATE TABLE core.answers (
    exam_type          text        NOT NULL
        CHECK (exam_type IN ('EGE', 'OGE', 'VPR', 'NIKO')),
    record_id          text        NOT NULL,
    participant_id     text        NOT NULL,
    task_number        smallint    NOT NULL
        CHECK(task_number > 0),
    answer             text        NOT NULL,
    answer_norm        text        NOT NULL,
    is_correct         boolean     NOT NULL,
    score          smallint    NOT NULL
        CHECK(score >= 0),
    max_score      smallint    NOT NULL
        CHECK(max_score > 0),
    answered_at    timestamptz NOT NULL,
    time_spent_sec integer    CHECK(time_spent_sec >= 0),
    audio_plays    smallint    CHECK(audio_plays >= 0),
    updated_at     timestamptz NOT NULL,
    deleted        boolean     NOT NULL,
    payload            jsonb       NOT NULL,           -- исходная запись платформы
    loaded_at          timestamptz NOT NULL DEFAULT now(),  
    load_id        bigint,     
    PRIMARY KEY (exam_type, record_id)
);

CREATE INDEX answers_participant_idx ON core.answers (exam_type, participant_id);
CREATE INDEX answers_task_idx ON core.answers (exam_type, task_number);


CREATE TABLE core.expert_reviews (
    exam_type          text        NOT NULL
        CHECK (exam_type IN ('EGE', 'OGE', 'VPR', 'NIKO')),
    record_id          text        NOT NULL,
    participant_id     text        NOT NULL,
    task_number        smallint    NOT NULL
        CHECK(task_number > 0),
    check_status       text        NOT NULL
        CHECK(check_status IN ('pending', 'checked')),
    check_stage        text        CHECK(check_stage IN ('first', 'third_check', 'appeal')),
    expert_code        text,
    score              smallint   CHECK(score >= 0),
    max_score          smallint   NOT NULL
        CHECK(max_score > 0),
    reviewed_at        timestamptz,
    updated_at     timestamptz NOT NULL,
    deleted        boolean     NOT NULL,
    payload            jsonb       NOT NULL,           -- исходная запись платформы
    loaded_at          timestamptz NOT NULL DEFAULT now(),
    load_id            bigint,
    PRIMARY KEY (exam_type, record_id)
);
CREATE INDEX expert_reviews_participant_idx ON core.expert_reviews (exam_type, participant_id);


CREATE TABLE core.proctoring_events (
     exam_type          text        NOT NULL
        CHECK (exam_type IN ('EGE', 'OGE', 'VPR', 'NIKO')),
    record_id          text        NOT NULL,
    participant_id     text        NOT NULL,
    event_type         text        NOT NULL
        CHECK(event_type IN ('tab_switch', 'copy', 'paste', 'fullscreen_exit', 'focus_lost')),
    occurred_at        timestamptz NOT NULL,
    task_number        smallint,
    updated_at     timestamptz NOT NULL,
    deleted        boolean     NOT NULL,
    payload            jsonb       NOT NULL,           -- исходная запись платформы
    loaded_at          timestamptz NOT NULL DEFAULT now(),
    load_id            bigint,
    PRIMARY KEY (exam_type, record_id)
);
CREATE INDEX proctoring_events_participant_idx ON core.proctoring_events (exam_type, participant_id);

-- Структура КИМ: одно задание одного варианта (контракт 6.10).
CREATE TABLE core.kim_tasks (
    exam_type      text        NOT NULL,
    record_id      text        NOT NULL,
    subject_code   text        NOT NULL,
    exam_year      smallint    NOT NULL,
    variant_id     text        NOT NULL,
    task_number    smallint    NOT NULL,
    part           smallint    NOT NULL CHECK (part IN (1, 2)),
    check_type     text        NOT NULL CHECK (check_type IN ('auto', 'expert')),
    max_score      smallint    NOT NULL,
    level          text        NOT NULL CHECK (level IN ('basic', 'advanced', 'high')),
    content_codes  text[]      NOT NULL,
    skill_codes    text[]      NOT NULL,
    section        text        NOT NULL,
    block          text,
    updated_at     timestamptz NOT NULL,
    deleted        boolean     NOT NULL,
    payload        jsonb       NOT NULL,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    load_id        bigint,
    PRIMARY KEY (exam_type, record_id)
);

CREATE INDEX kim_tasks_task_idx ON core.kim_tasks (exam_type, subject_code, exam_year, variant_id, task_number);


-- Шкалы перевода баллов (контракт 6.11). Новая версия шкалы — новая запись.
CREATE TABLE core.scales (
    exam_type      text        NOT NULL,
    record_id      text        NOT NULL,
    subject_code   text        NOT NULL,
    exam_year      smallint    NOT NULL,
    scale_type     text        NOT NULL
        CHECK (scale_type IN ('primary_to_test', 'primary_to_grade', 'primary_to_level')),
    points         jsonb       NOT NULL,
    conditions     jsonb       NOT NULL,
    scale_version  integer     NOT NULL,
    valid_from     timestamptz NOT NULL,
    updated_at     timestamptz NOT NULL,
    deleted        boolean     NOT NULL,
    payload        jsonb       NOT NULL,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    load_id        bigint,
    PRIMARY KEY (exam_type, record_id)
);


-- Справочники (контракт 6.9). Справочники всех платформ сливаются в общие таблицы,
-- поэтому ключ — код, а не (exam_type, record_id).
CREATE TABLE core.regions (
    region_code  text        PRIMARY KEY,
    name         text        NOT NULL,
    updated_at   timestamptz NOT NULL,
    loaded_at    timestamptz NOT NULL DEFAULT now(),
    load_id      bigint
);

CREATE TABLE core.schools (
    school_code  text        PRIMARY KEY,
    region_code  text        NOT NULL,
    name         text        NOT NULL,
    updated_at   timestamptz NOT NULL,
    loaded_at    timestamptz NOT NULL DEFAULT now(),
    load_id      bigint
);

CREATE TABLE core.subjects (
    exam_type          text        NOT NULL,
    subject_code       text        NOT NULL,
    name               text        NOT NULL,
    grade_level        smallint    NOT NULL,
    max_primary_score  smallint    NOT NULL,
    threshold_score    smallint    NOT NULL,
    updated_at         timestamptz NOT NULL,
    loaded_at          timestamptz NOT NULL DEFAULT now(),
    load_id            bigint,
    PRIMARY KEY (exam_type, subject_code)
);


-- Последняя сводка /live-status по платформе (контракт 6.8). История — ops.live_status_history.
CREATE TABLE core.live_status (
    exam_type     text        PRIMARY KEY,
    in_progress   integer     NOT NULL,
    finished      integer     NOT NULL,
    annulled      integer     NOT NULL,
    absent        integer     NOT NULL,
    generated_at  timestamptz NOT NULL,
    loaded_at     timestamptz NOT NULL DEFAULT now(),
    load_id       bigint
);
