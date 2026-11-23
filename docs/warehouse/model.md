# Модель аналітичного сховища CityFlow (`dwh.*`)

## 1. Архітектура схеми (Galaxy / Star Schema)

Аналітичне сховище проєкту **CityFlow** побудоване за багатовимірною моделлю **Galaxy Schema** (дві фактові таблиці, що спільно використовують чотири конформні виміри). Візуальна діаграма зв’язків наведена у файлах `docs/warehouse/erd.png` та `docs/warehouse/erd.pdf`.

Джерелами завантаження виступають незмінні таблиці `core.customers`, `core.merchants`, `core.products` (початковий стан довідників), `validated.orders` та `validated.order_items` (прийняті записи після автоматизованого контролю якості), а також пакети змін вимірів `data/changes/S03/customers_delta.csv` і `products_delta.csv`.

## 2. Рівень деталізації (Grain) та ключі таблиць

| Таблиця | Що представляє один рядок (Grain) | Первинний ключ (PK) | Бізнес-ключ / Зовнішні ключі (FK) | Політика оновлення |
|---|---|---|---|---|
| `dwh.dim_customer` | Одна історична версія клієнта у напіввідкритому інтервалі `[valid_from, valid_to)` | `customer_key` (`bigint IDENTITY`) | `customer_id` (бізнес-ключ; `UNIQUE(customer_id, valid_from)`, частковий унікальний індекс `WHERE is_current`) | **SCD Type 2** за зміною `city` |
| `dwh.dim_merchant` | Один заклад-партнер CityFlow у поточному описі | `merchant_key` (`bigint IDENTITY`) | `merchant_id` (`UNIQUE`) | Перезапис змінених атрибутів (`IS DISTINCT FROM`) зі збереженням `merchant_key` |
| `dwh.dim_product` | Один товар у поточному описі каталогу | `product_key` (`bigint IDENTITY`) | `product_id` (`UNIQUE`), `merchant_id` | **SCD Type 1** (`unit_price_uah`, `is_active`, `source_version_at`) |
| `dwh.dim_date` | Одна календарна дата у часовій зоні `Europe/Kyiv` | `date_key` (`date`) | Природний ключ дати (`calendar_year`, `calendar_month`, `day_of_month`) | Покриває повний діапазон дат `validated.orders` |
| `dwh.fact_orders` | Одне прийняте замовлення (рівень заголовка чека) | `order_id` (`text`) | FK: `customer_key`, `merchant_key`, `date_key` | Ідемпотентний `UPSERT`; `warehouse_modified_at` оновлюється лише при зміні бізнес-стану |
| `dwh.fact_order_items` | Одна прийнята товарна позиція у межах замовлення | `order_item_id` (`text`) | FK: `order_id`, `customer_key`, `merchant_key`, `product_key`, `date_key` | Ідемпотентний `UPSERT`; історичні `unit_price` та `line_total` не перераховуються |

## 3. Ключові правила та навчальні припущення

1. **Початкова історія `dim_customer` (`valid_from = '-infinity'`):**  
   Початкові атрибути клієнтів із `core.customers` завантажуються з `valid_from = '-infinity'::timestamptz`, `valid_to = NULL`, `is_current = true`. Це **явне навчальне припущення**: ми приймаємо поточний доступний snapshot за чинний для всієї попередньої історії до першої зафіксованої зміни у `customers_delta.csv`. Без історичного журналу змін (CDC) у джерелі відновити справжнє минуле місто клієнта до моменту вивантаження неможливо.
2. **Часова зона дати замовлення (`Europe/Kyiv`):**  
   Для кожного замовлення `date_key = (created_at AT TIME ZONE 'Europe/Kyiv')::date`. Позиції у `fact_order_items` успадковують `date_key`, `customer_key` та `merchant_key` свого батьківського замовлення з `fact_orders`.
3. **Обробка `NULL` та порівняння атрибутів:**  
   Невідоме або порожнє значення `city` зберігається як `NULL` (без підстановки вигаданих значень на кшталт `'Unknown'`). Усі порівняння nullable-атрибутів виконуються через `IS DISTINCT FROM`, щоб уникнути хибних спрацювань SCD Type 2 при порівнянні `NULL` із `NULL`.
4. **Захист від розмноження сум (Grain Safety):**  
   `fact_orders` (grain = 1 замовлення) та `fact_order_items` (grain = 1 позиція) мають різні рівні деталізації. Заборонено підсумовувати `fact_orders.total_amount` після звичайного `JOIN` із `fact_order_items` — звірка сум заголовків (`SUM(total_amount)`) і сум позицій (`SUM(line_total)`) завжди виконується окремо в розрізі кожної валюти (`UAH`, `USD`, `EUR`). Замовлення без позицій залишаються у `fact_orders` без створення фіктивних рядків із `quantity = 0`.
5. **Незмінність історичних цін продажу:**  
   Оновлення каталожної ціни `dim_product.unit_price_uah` за правилом SCD Type 1 не змінює історичні колонки `fact_order_items.unit_price` та `fact_order_items.line_total`, які фіксують фактичну ціну на момент купівлі.
6. **Призначення `warehouse_modified_at`:**  
   `warehouse_modified_at` у `fact_orders` фіксує технічний час останньої зміни бізнес-стану або історичного `customer_key` факту у сховищі (не `created_at` замовлення і не `updated_at` довідника). При повторних запусках для незмінних фактів це поле не змінюється, що забезпечує надійну основу для інкрементальних моделей dbt.
