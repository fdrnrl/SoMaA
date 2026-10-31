-- 001: схемы хранилища (раздел 7.1) и права ролей.
-- Сами роли sma_loader, sma_reader, sma_app создает db/migrate.py: их пароли берутся из .env.

CREATE SCHEMA IF NOT EXISTS core;       -- данные платформ, источник истины
CREATE SCHEMA IF NOT EXISTS ops;        -- журналы загрузчика, качество, бэкапы
CREATE SCHEMA IF NOT EXISTS mart;       -- витрины
CREATE SCHEMA IF NOT EXISTS analytics;  -- результаты правил аномалий (логика — у аналитика)
CREATE SCHEMA IF NOT EXISTS app;        -- пользователи дашборда (логика — у разработчика дашбордов)

-- Право «заходить» в схему.
GRANT USAGE ON SCHEMA core, ops, mart TO sma_loader;
GRANT USAGE ON SCHEMA core, mart TO sma_reader;
GRANT USAGE ON SCHEMA mart, analytics, app TO sma_app;

-- Права на таблицы, которые будут созданы следующими миграциями.
-- ALTER DEFAULT PRIVILEGES действует на все будущие таблицы, поэтому выдавать права
-- на каждую таблицу отдельно не нужно.

-- sma_loader: пишет в core и ops (без DELETE: удаление в core мягкое), читает mart.
ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT, INSERT, UPDATE ON TABLES TO sma_loader;
ALTER DEFAULT PRIVILEGES IN SCHEMA ops  GRANT SELECT, INSERT, UPDATE ON TABLES TO sma_loader;
ALTER DEFAULT PRIVILEGES IN SCHEMA ops  GRANT USAGE, SELECT ON SEQUENCES TO sma_loader;

-- sma_reader: только чтение core и mart (аналитики, Streamlit).
ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT ON TABLES TO sma_reader;

-- mart читают все три роли.
ALTER DEFAULT PRIVILEGES IN SCHEMA mart GRANT SELECT ON TABLES TO sma_loader, sma_reader, sma_app;

-- sma_app: пишет в analytics и app.
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sma_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA app       GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sma_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT USAGE, SELECT ON SEQUENCES TO sma_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA app       GRANT USAGE, SELECT ON SEQUENCES TO sma_app;
