-- Комплексна звірка аналітичного сховища CityFlow (dwh.* проти validated.* та core.*)

-- 1. Кількість рядків у таблицях сховища
SELECT 'dim_customer (total versions)' AS table_name, count(*) AS row_count FROM dwh.dim_customer
UNION ALL
SELECT 'dim_customer (current)', count(*) FROM dwh.dim_customer WHERE is_current
UNION ALL
SELECT 'dim_merchant', count(*) FROM dwh.dim_merchant
UNION ALL
SELECT 'dim_product', count(*) FROM dwh.dim_product
UNION ALL
SELECT 'dim_date', count(*) FROM dwh.dim_date
UNION ALL
SELECT 'fact_orders', count(*) FROM dwh.fact_orders
UNION ALL
SELECT 'fact_order_items', count(*) FROM dwh.fact_order_items;

-- 2. Звірка ключів замовлень в обох напрямах (EXCEPT ALL -> очікується 0)
SELECT
    (SELECT count(*) FROM (SELECT order_id FROM validated.orders EXCEPT ALL SELECT order_id FROM dwh.fact_orders) s1) AS validated_minus_dwh_orders,
    (SELECT count(*) FROM (SELECT order_id FROM dwh.fact_orders EXCEPT ALL SELECT order_id FROM validated.orders) s2) AS dwh_minus_validated_orders,
    (SELECT count(*) FROM (SELECT order_item_id FROM validated.order_items EXCEPT ALL SELECT order_item_id FROM dwh.fact_order_items) s3) AS validated_minus_dwh_items,
    (SELECT count(*) FROM (SELECT order_item_id FROM dwh.fact_order_items EXCEPT ALL SELECT order_item_id FROM validated.order_items) s4) AS dwh_minus_validated_items;

-- 3. Звірка сум заголовків замовлень за валютами (validated.orders vs dwh.fact_orders)
SELECT
    COALESCE(v.currency, f.currency) AS currency,
    COALESCE(v.cnt, 0) AS validated_orders_cnt,
    COALESCE(f.cnt, 0) AS dwh_orders_cnt,
    COALESCE(v.sum_total, 0) AS validated_sum_total,
    COALESCE(f.sum_total, 0) AS dwh_sum_total,
    COALESCE(f.sum_total, 0) - COALESCE(v.sum_total, 0) AS diff_total
FROM (
    SELECT currency, count(*) AS cnt, SUM(total_amount) AS sum_total
    FROM validated.orders
    GROUP BY currency
) v
FULL OUTER JOIN (
    SELECT currency, count(*) AS cnt, SUM(total_amount) AS sum_total
    FROM dwh.fact_orders
    GROUP BY currency
) f ON v.currency = f.currency
ORDER BY currency;

-- 4. Звірка сум позицій замовлень за валютами (validated.order_items vs dwh.fact_order_items)
SELECT
    COALESCE(v.currency, f.currency) AS currency,
    COALESCE(v.cnt, 0) AS validated_items_cnt,
    COALESCE(f.cnt, 0) AS dwh_items_cnt,
    COALESCE(v.sum_line, 0) AS validated_sum_line_total,
    COALESCE(f.sum_line, 0) AS dwh_sum_line_total,
    COALESCE(f.sum_line, 0) - COALESCE(v.sum_line, 0) AS diff_line_total
FROM (
    SELECT currency, count(*) AS cnt, SUM(line_total) AS sum_line
    FROM validated.order_items
    GROUP BY currency
) v
FULL OUTER JOIN (
    SELECT currency, count(*) AS cnt, SUM(line_total) AS sum_line
    FROM dwh.fact_order_items
    GROUP BY currency
) f ON v.currency = f.currency
ORDER BY currency;

-- 5. Приклад SCD Type 1: оновлені товари (S03-P000477, S03-P000077) та незмінність історичних фактів
SELECT
    p.product_key,
    p.product_id,
    p.product_name,
    cp.unit_price_uah AS core_initial_price,
    p.unit_price_uah AS dwh_current_price,
    cp.is_active AS core_initial_active,
    p.is_active AS dwh_current_active,
    p.source_version_at::text AS source_version_at,
    COUNT(fi.order_item_id) AS historical_items_cnt,
    COALESCE(SUM(fi.line_total), 0) AS historical_line_total_sum
FROM dwh.dim_product p
JOIN core.products cp ON cp.product_id = p.product_id
LEFT JOIN dwh.fact_order_items fi ON fi.product_key = p.product_key
WHERE p.product_id IN ('S03-P000477', 'S03-P000077', 'S03-P000072')
GROUP BY p.product_key, p.product_id, p.product_name, cp.unit_price_uah, p.unit_price_uah, cp.is_active, p.is_active, p.source_version_at
ORDER BY p.product_id;

-- 6. Приклад SCD Type 2: клієнти, які мають більше ніж одну версію (історія змін міста)
SELECT
    c.customer_key,
    c.customer_id,
    c.full_name,
    c.city,
    c.valid_from::text AS valid_from,
    c.valid_to::text AS valid_to,
    c.is_current
FROM dwh.dim_customer c
WHERE c.customer_id IN (
    SELECT customer_id
    FROM dwh.dim_customer
    GROUP BY customer_id
    HAVING count(*) > 1
)
ORDER BY c.customer_id, c.valid_from
LIMIT 12;

-- 7. Три точкові перевірки часових меж [valid_from, valid_to) через SELECT із VALUES
--    для першого клієнта зі зміненим містом у dwh.dim_customer
WITH changed_customer AS (
    SELECT customer_id, MIN(valid_to) AS change_ts
    FROM dwh.dim_customer
    WHERE valid_to IS NOT NULL
    GROUP BY customer_id
    ORDER BY customer_id
    LIMIT 1
),
probe_points AS (
    SELECT
        cc.customer_id,
        p.probe_label,
        p.probe_ts
    FROM changed_customer cc
    CROSS JOIN LATERAL (
        VALUES
            ('1_before_1us (updated_at - 1us)', cc.change_ts - interval '1 microsecond'),
            ('2_exact (updated_at)',             cc.change_ts),
            ('3_after (updated_at + 1 day)',     cc.change_ts + interval '1 day')
    ) AS p(probe_label, probe_ts)
)
SELECT
    pp.customer_id,
    pp.probe_label,
    pp.probe_ts,
    dc.customer_key,
    dc.city,
    dc.valid_from::text AS valid_from,
    dc.valid_to::text AS valid_to,
    dc.is_current
FROM probe_points pp
JOIN dwh.dim_customer dc
  ON dc.customer_id = pp.customer_id
 AND pp.probe_ts >= dc.valid_from
 AND pp.probe_ts < COALESCE(dc.valid_to, 'infinity'::timestamptz)
ORDER BY pp.probe_label;

-- 8. Контрольна вибірка warehouse_modified_at для перевірки стабільності при повторних запусках
SELECT
    order_id,
    customer_key,
    merchant_key,
    date_key,
    total_amount,
    currency,
    warehouse_modified_at
FROM dwh.fact_orders
ORDER BY order_id
LIMIT 5;