-- Перевірка результатів автоматизованого контролю якості даних (quality, quarantine, validated · Варіант S03)

-- 1. Історія всіх запусків у quality.runs
SELECT run_id, student_id, scope, reference_time, contract_version, status, error_code, started_at, finished_at
FROM quality.runs
ORDER BY started_at;

-- 2. Баланс рішень за останнім успішним (PASS/WARN) project-запуском: input = accepted + rejected + duplicate
WITH latest_project_run AS (
    SELECT run_id
    FROM quality.runs
    WHERE scope = 'project' AND status IN ('PASS', 'WARN')
    ORDER BY started_at DESC
    LIMIT 1
),
mapped AS (
    SELECT CASE
               WHEN d.source_name IN ('core_orders', 'api_orders', 'orders') THEN 'orders'
               WHEN d.source_name IN ('core_order_items', 'items', 'order_items') THEN 'items'
               ELSE 'events'
           END AS entity,
           d.decision
    FROM quality.row_decisions d
    JOIN latest_project_run l ON l.run_id = d.run_id
)
SELECT entity,
       COUNT(*) AS input_count,
       COUNT(*) FILTER (WHERE decision = 'accepted') AS accepted_count,
       COUNT(*) FILTER (WHERE decision = 'rejected') AS rejected_count,
       COUNT(*) FILTER (WHERE decision = 'duplicate') AS duplicate_count,
       (COUNT(*) = COUNT(*) FILTER (WHERE decision = 'accepted')
                 + COUNT(*) FILTER (WHERE decision = 'rejected')
                 + COUNT(*) FILTER (WHERE decision = 'duplicate')) AS balance_ok
FROM mapped
GROUP BY entity
ORDER BY entity;

-- 3. Результати перевірки правил Q01–Q10 за останнім успішним project-запуском
WITH latest_project_run AS (
    SELECT run_id
    FROM quality.runs
    WHERE scope = 'project' AND status IN ('PASS', 'WARN')
    ORDER BY started_at DESC
    LIMIT 1
)
SELECT rr.rule_id, rr.severity, rr.failed_count, rr.details
FROM quality.rule_results rr
JOIN latest_project_run l ON l.run_id = rr.run_id
ORDER BY rr.rule_id;

-- 4. Перевірка карантину (quarantine.records)
SELECT q.run_id, q.source_name, q.source_row_no, q.reason_codes, q.payload
FROM quarantine.records q
ORDER BY q.run_id, q.source_name, q.source_row_no
LIMIT 10;

-- 5. Кількість рядків і відсутність дублікатів у таблицях validated.*
SELECT 'validated.orders' AS table_name, COUNT(*) AS total_rows, COUNT(DISTINCT order_id) AS unique_keys FROM validated.orders
UNION ALL
SELECT 'validated.order_items', COUNT(*), COUNT(DISTINCT order_item_id) FROM validated.order_items
UNION ALL
SELECT 'validated.delivery_events', COUNT(*), COUNT(DISTINCT event_id) FROM validated.delivery_events;

-- 6. Звірка сум замовлень (total_amount) у validated.orders за валютами
SELECT currency,
       COUNT(*) AS orders_count,
       SUM(subtotal_amount) AS sum_subtotal,
       SUM(delivery_fee) AS sum_delivery_fee,
       SUM(discount_amount) AS sum_discount,
       SUM(total_amount) AS sum_total_amount
FROM validated.orders
GROUP BY currency
ORDER BY currency;