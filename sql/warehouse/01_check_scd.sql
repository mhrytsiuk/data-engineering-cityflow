-- Усі три запити мають повернути 0 рядків даних (інваріанти SCD Type 2 і часових меж).

-- 1. Рівно одна поточна версія (is_current = true) на кожного клієнта
SELECT customer_id
FROM dwh.dim_customer
GROUP BY customer_id
HAVING count(*) FILTER (WHERE is_current) <> 1;

-- 2. Відсутність перетину часових інтервалів [valid_from, valid_to) для одного клієнта
SELECT a.customer_id, a.customer_key, b.customer_key
FROM dwh.dim_customer a
JOIN dwh.dim_customer b
  ON a.customer_id = b.customer_id
 AND a.customer_key < b.customer_key
WHERE a.valid_from < COALESCE(b.valid_to, 'infinity'::timestamptz)
  AND b.valid_from < COALESCE(a.valid_to, 'infinity'::timestamptz);

-- 3. Кожен факт fact_orders належить часовому інтервалу [valid_from, valid_to) своєї версії клієнта
SELECT f.order_id
FROM dwh.fact_orders f
JOIN dwh.dim_customer c USING (customer_key)
WHERE NOT (
    f.created_at >= c.valid_from
    AND f.created_at < COALESCE(c.valid_to, 'infinity'::timestamptz)
);