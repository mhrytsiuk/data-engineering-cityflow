-- Перевірка стану таблиць модуля Ingestion (stg.ingestion_records та audit.ingestion_runs).
SELECT student_id, source, count(*) AS unique_records
FROM stg.ingestion_records
GROUP BY student_id, source
ORDER BY student_id, source;

SELECT run_id, status, started_at, finished_at, summary, error_code
FROM audit.ingestion_runs
ORDER BY started_at;

SELECT record_key,
       payload->>'order_id' AS order_id,
       payload->>'event_type' AS event_type,
       payload->>'occurred_at' AS occurred_at
FROM stg.ingestion_records
WHERE source = 'delivery_events'
ORDER BY record_key
LIMIT 10;

-- Після повторного запуску: у новому SUCCESS записі inserted=0 для всіх трьох джерел.
-- Після контрольованого збою: FAILED і жодних нових бізнес-записів цього run_id.
SELECT a.run_id, a.status, count(r.record_key) AS records_first_inserted_by_run
FROM audit.ingestion_runs a
LEFT JOIN stg.ingestion_records r ON r.first_run_id = a.run_id
GROUP BY a.run_id, a.status, a.started_at
ORDER BY a.started_at;