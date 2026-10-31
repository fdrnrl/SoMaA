-- 004: таблицы analytics и app по ТЗ СМА v0.3 (раздел 7.5). Только структура:
-- логикой analytics владеет аналитик аномалий, логикой app — разработчик дашбордов.


-- Срабатывания правил аномалий А1–А3.
CREATE TABLE analytics.anomalies (
    anomaly_id    bigserial   PRIMARY KEY,
    rule          text        NOT NULL CHECK (rule IN ('A1', 'A2', 'A3')),
    exam_type     text        NOT NULL,
    subject_code  text,
    object_type   text        NOT NULL CHECK (object_type IN ('school', 'group', 'participant')),
    object_id     text        NOT NULL,
    region_code   text,
    school_code   text,
    value         numeric,
    threshold     numeric,
    detected_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (rule, exam_type, object_type, object_id)
);


-- Запуски расчета индекса и П1–П4.
CREATE TABLE analytics.analysis_runs (
    run_id       bigserial   PRIMARY KEY,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    params       jsonb
);


-- Группы совпадающих ответов (П1).
CREATE TABLE analytics.p1_groups (
    run_id           bigint      NOT NULL REFERENCES analytics.analysis_runs (run_id),
    group_id         integer     NOT NULL,
    exam_type        text        NOT NULL,
    subject_code     text,
    school_code      text,
    room_code        text,
    variant_id       text,
    participant_ids  text[]      NOT NULL,
    size             integer     NOT NULL,
    min_p_value      double precision,
    PRIMARY KEY (run_id, group_id)
);


-- Индекс школы по показателям П1–П4.
CREATE TABLE analytics.school_index (
    run_id        bigint   NOT NULL REFERENCES analytics.analysis_runs (run_id),
    exam_type     text     NOT NULL,
    subject_code  text     NOT NULL,
    region_code   text,
    school_code   text     NOT NULL,
    participants  integer,
    p1            numeric,
    p2            numeric,
    p3            numeric,
    p4a           numeric,
    p4b           numeric,
    z1            numeric,
    z2            numeric,
    z3            numeric,
    z4a           numeric,
    z4b           numeric,
    index         numeric,
    in_list       boolean,
    PRIMARY KEY (run_id, exam_type, subject_code, school_code)
);


-- Пользователи дашборда (сотрудники Рособрнадзора, без персональных данных учеников).
CREATE TABLE app.users (
    login            text        PRIMARY KEY,
    password_hash    text        NOT NULL,
    role             text        NOT NULL CHECK (role IN ('staff', 'admin')),
    blocked          boolean     NOT NULL DEFAULT false,
    failed_attempts  integer     NOT NULL DEFAULT 0,
    locked_until     timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.login_log (
    id            bigserial   PRIMARY KEY,
    login         text        NOT NULL,
    attempted_at  timestamptz NOT NULL DEFAULT now(),
    success       boolean     NOT NULL
);
