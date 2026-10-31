-- 003: служебные таблицы ops (раздел 7.4) и внешние ключи load_id из core.
-- ops.schema_migrations создает db/migrate.py до применения миграций.


-- Журнал каждого запроса загрузчика к адресу платформы.
-- Строка создается в начале запроса (status = running) и обновляется в конце:
-- так строки core могут ссылаться на свой load_id еще во время загрузки.
CREATE TABLE ops.load_log (
    id            bigserial   PRIMARY KEY,
    platform      text        NOT NULL,
    endpoint      text        NOT NULL,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        text        NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'ok', 'error', 'partial')),
    http_status   smallint,
    since_value   timestamptz,
    pages         integer     NOT NULL DEFAULT 0,
    received      integer     NOT NULL DEFAULT 0,
    saved         integer     NOT NULL DEFAULT 0,
    rejected      integer     NOT NULL DEFAULT 0,
    skipped_stale integer     NOT NULL DEFAULT 0,
    max_updated   timestamptz,
    error_reason  text,
    message       text
);

CREATE INDEX load_log_endpoint_idx ON ops.load_log (platform, endpoint, started_at);


-- Отклоненные записи. Значения запрещенных полей сюда не попадают (заменяются на "<removed>").
CREATE TABLE ops.rejected_records (
    id           bigserial   PRIMARY KEY,
    load_id      bigint      REFERENCES ops.load_log (id),
    platform     text        NOT NULL,
    endpoint     text        NOT NULL,
    record_id    text,
    reason_code  text        NOT NULL CHECK (reason_code IN (
        'missing_field', 'null_not_allowed', 'wrong_type', 'bad_enum', 'bad_format',
        'out_of_range', 'pii_field', 'wrong_exam_block', 'exam_type_mismatch', 'bad_record_id'
    )),
    field        text,
    message      text,
    payload      jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX rejected_records_load_idx ON ops.rejected_records (load_id);


-- Водяные знаки: до какого updated_at адрес уже загружен.
CREATE TABLE ops.watermarks (
    platform    text        NOT NULL,
    endpoint    text        NOT NULL,
    watermark   timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (platform, endpoint)
);


-- Снимки справочников каждой платформы (для сверки между платформами, Q-CONS-08).
CREATE TABLE ops.dictionary_snapshots (
    id           bigserial   PRIMARY KEY,
    platform     text        NOT NULL,
    endpoint     text        NOT NULL,
    received_at  timestamptz NOT NULL DEFAULT now(),
    payload      jsonb       NOT NULL
);


-- Все полученные снимки /live-status.
CREATE TABLE ops.live_status_history (
    id            bigserial   PRIMARY KEY,
    exam_type     text        NOT NULL,
    generated_at  timestamptz NOT NULL,
    payload       jsonb       NOT NULL,
    received_at   timestamptz NOT NULL DEFAULT now()
);


-- Проверки качества данных (раздел 13).
CREATE TABLE ops.quality_runs (
    id            bigserial   PRIMARY KEY,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    issues_total  integer,
    summary       jsonb
);

CREATE TABLE ops.quality_issues (
    id          bigserial   PRIMARY KEY,
    run_id      bigint      NOT NULL REFERENCES ops.quality_runs (id),
    check_code  text        NOT NULL,
    severity    text        NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    exam_type   text,
    entity      text,
    entity_key  text,
    details     jsonb
);

CREATE INDEX quality_issues_run_idx ON ops.quality_issues (run_id);


-- Резервные копии (раздел 14).
CREATE TABLE ops.backup_log (
    id           bigserial   PRIMARY KEY,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    file_path    text,
    size_bytes   bigint,
    status       text        NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'error')),
    message      text
);


-- Внешние ключи: каждая строка core ссылается на запрос, которым она загружена.
ALTER TABLE core.sessions    ADD FOREIGN KEY (load_id) REFERENCES ops.load_log (id);
ALTER TABLE core.kim_tasks   ADD FOREIGN KEY (load_id) REFERENCES ops.load_log (id);
ALTER TABLE core.scales      ADD FOREIGN KEY (load_id) REFERENCES ops.load_log (id);
ALTER TABLE core.regions     ADD FOREIGN KEY (load_id) REFERENCES ops.load_log (id);
ALTER TABLE core.schools     ADD FOREIGN KEY (load_id) REFERENCES ops.load_log (id);
ALTER TABLE core.subjects    ADD FOREIGN KEY (load_id) REFERENCES ops.load_log (id);
ALTER TABLE core.live_status ADD FOREIGN KEY (load_id) REFERENCES ops.load_log (id);
ALTER TABLE core.expert_reviews   ADD FOREIGN KEY (load_id) REFERENCES ops.load_log (id);
ALTER TABLE core.proctoring_events   ADD FOREIGN KEY (load_id) REFERENCES ops.load_log (id);
ALTER TABLE core.answers    ADD FOREIGN KEY (load_id) REFERENCES ops.load_log (id);
