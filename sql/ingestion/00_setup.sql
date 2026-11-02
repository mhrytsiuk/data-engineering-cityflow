-- Об'єкти приймання та аудиту модуля Ingestion (core, аналітичні схеми та попередні stg-таблиці не змінюються).
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS stg;

CREATE TABLE IF NOT EXISTS audit.ingestion_runs (
    run_id uuid PRIMARY KEY,
    student_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('RUNNING','SUCCESS','FAILED')),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    raw_dir text NOT NULL,
    summary jsonb,
    error_code text
);

CREATE TABLE IF NOT EXISTS stg.ingestion_records (
    student_id text NOT NULL,
    source text NOT NULL CHECK(source IN ('orders','order_items','delivery_events')),
    record_key text NOT NULL,
    payload jsonb NOT NULL,
    raw_path text NOT NULL,
    source_row_number integer NOT NULL CHECK(source_row_number > 0),
    first_run_id uuid NOT NULL REFERENCES audit.ingestion_runs(run_id),
    loaded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(student_id,source,record_key)
);

-- Сумісні представлення (якщо зовнішні скрипти звертаються за старими іменами)
CREATE OR REPLACE VIEW audit.lab04_runs AS SELECT * FROM audit.ingestion_runs;
CREATE OR REPLACE VIEW stg.lab04_records AS SELECT * FROM stg.ingestion_records;