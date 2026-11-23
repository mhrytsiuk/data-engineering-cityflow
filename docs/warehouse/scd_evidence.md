# Докази коректності SCD Type 1 та SCD Type 2 (Варіант `S03`)

## 1. Вхідні пакети змін (`data/changes/S03/`)

- **`BATCH_METADATA.json`**: `batch_id = batch_03_dimensions`, `kind = dimension_changes`, період подій `2026-09-15` .. `2026-09-21`, `source_available_at = 2026-09-22T08:00:00Z`.
- **`products_delta.csv`**: `30` записів змін цін (`unit_price_uah`) та статусів активності (`is_active`) товарів `S03-P...`.
- **`customers_delta.csv`**: `18` записів змін адреси/міста (`city`) клієнтів `S03-C...`.
- Для всіх змін як effective time використовується колонка **`updated_at`** конкретного рядка (а не час запуску скрипту і не `source_available_at` із метаданих).

## 2. Політика SCD Type 1 (`dwh.dim_product`)

1. Початкове завантаження встановлює `source_version_at = '-infinity'::timestamptz`.
2. При застосуванні `products_delta.csv` оновлюються лише існуючі в `dwh.dim_product` товари, для яких `updated_at > source_version_at`; `product_key` залишається незмінним.
3. Повторне застосування тієї самої delta (`updated_at = source_version_at` із тими самими значеннями) є ідемпотентним no-op; інше значення під тим самим `updated_at` піднімає `SCD1_CONFLICT`.
4. Старіші версії (`updated_at < source_version_at`) та повторний запуск baseline не повертають стару ціну з `core.products`.
5. Історичні `unit_price` та `line_total` у `dwh.fact_order_items` беруться з `validated.order_items` і не перераховуються.

## 3. Політика SCD Type 2 (`dwh.dim_customer`)

1. Початкові версії мають `valid_from = '-infinity'`, `valid_to = NULL`, `is_current = true`.
2. Зміни з `customers_delta.csv` обробляються за `updated_at` за зростанням.
3. Якщо `new_city` не відрізняється від поточного `city` (`IS NOT DISTINCT FROM`, включно з `NULL`), нова версія не створюється.
4. При реальній зміні `city` в одній транзакції:
   - закривається поточна версія (`valid_to = updated_at`, `is_current = false`);
   - вставляється нова версія (`valid_from = updated_at`, `valid_to = NULL`, `is_current = true`, новий `customer_key`).
5. Часові межі перевіряються за напіввідкритим інтервалом `[valid_from, valid_to)` у трьох точках (`sql/warehouse/02_verify_dwh.sql`, блок 7):
   - `updated_at - interval '1 microsecond'` → повертає **стару** версію (`created_at < valid_to`);
   - `updated_at` → повертає **нову** версію (`created_at >= valid_from`);
   - `updated_at + interval '1 day'` → повертає **нову** поточну версію.

## 4. Інваріанти перевірки (`sql/warehouse/01_check_scd.sql`)

Усі три SQL-перевірки повертають **0 рядків**:
1. Кожен `customer_id` має рівно одну версію з `is_current = true`.
2. Жодні дві версії одного клієнта не перетинаються за інтервалом `[valid_from, valid_to)`.
3. Кожне замовлення `dwh.fact_orders` прив’язане до версії `dim_customer`, чинної на момент `fact_orders.created_at`.
