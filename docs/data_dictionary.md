# Словник даних (Data Dictionary)

Документ описує структуру та атрибути даних проєкту CityFlow: таблиці ядра (`core.*`), аналітичні представлення (`lab03_cf.*`), таблиці приймання та аудиту інгестії (`stg.ingestion_records`, `audit.ingestion_runs`, `ingestion_stg.*`), а також об'єкти автоматизованого контролю якості даних, карантину та валідованого шару (`quality.*`, `quarantine.*`, `validated.*`).

## Таблиця `core.customers`
**Файл джерела:** `customers.csv`  
**Grain:** Один унікальний зареєстрований клієнт.

| Поле | Опис | Тип даних (PostgreSQL `core`) | Приклад значення | Тип атрибута | Обов'язкове | Обмеження / Примітки |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `customer_id` | Унікальний ідентифікатор клієнта | `TEXT` | `S03-C000001` | Первинний ключ (PK) | Так | Формат: `S03-CXXXXXX` |
| `full_name` | Повне ім'я та прізвище клієнта | `TEXT` | `Олена Коваль` | Атрибут | Так | Непорожній рядок |
| `city` | Місто клієнта | `TEXT` | `Lviv` | Атрибут | Так | Текстове поле |
| `registered_at` | Дата та час реєстрації акаунту | `TIMESTAMPTZ` | `2025-08-15 08:30:00+03` | Атрибут | Так | Валідна часова мітка |

## Таблиця `core.merchants`
**Файл джерела:** `merchants.csv`  
**Grain:** Один унікальний заклад-партнер.

| Поле | Опис | Тип даних (PostgreSQL `core`) | Приклад значення | Тип атрибута | Обов'язкове | Обмеження / Примітки |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `merchant_id` | Унікальний ідентифікатор закладу | `TEXT` | `S03-M00001` | Первинний ключ (PK) | Так | Формат: `S03-MXXXXX` |
| `merchant_name` | Назва закладу | `TEXT` | `Fresh Bakery` | Атрибут | Так | Текстове поле |
| `city` | Місто розташування закладу | `TEXT` | `Lviv` | Атрибут | Так | Текстове поле |
| `opened_at` | Дата та час приєднання до платформи | `TIMESTAMPTZ` | `2025-05-10 10:00:00+03` | Атрибут | Так | Валідна часова мітка |

## Таблиця `core.products`
**Файл джерела:** `products.csv`  
**Grain:** Один унікальний товар у меню закладу.

| Поле | Опис | Тип даних (PostgreSQL `core`) | Приклад значення | Тип атрибута | Обов'язкове | Обмеження / Примітки |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `product_id` | Унікальний ідентифікатор товару | `TEXT` | `S03-P000101` | Первинний ключ (PK) | Так | Формат: `S03-PXXXXXX` |
| `merchant_id` | Ідентифікатор закладу-власника | `TEXT` | `S03-M00001` | Зовнішній ключ (FK) | Так | Посилання на `core.merchants(merchant_id)` |
| `product_name` | Назва товару | `TEXT` | `Круасан з мигдалем` | Атрибут | Так | Текстове поле |
| `unit_price_uah` | Ціна за одиницю товару в UAH | `NUMERIC(12,2)` | `85.00` | Атрибут | Так | `CHECK (unit_price_uah > 0)` |
| `is_active` | Прапорець активності товару в меню | `BOOLEAN` | `true` | Атрибут | Так | `true` / `false` |

## Таблиця `core.orders`
**Файл джерела:** `orders.csv`  
**Grain:** Одне унікальне замовлення клієнта.

| Поле | Опис | Тип даних (PostgreSQL `core`) | Приклад значення | Тип атрибута | Обов'язкове | Обмеження / Примітки |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `order_id` | Унікальний ідентифікатор замовлення | `TEXT` | `S03-O0000001` | Первинний ключ (PK) | Так | Формат: `S03-OXXXXXXX` |
| `customer_id` | Ідентифікатор клієнта | `TEXT` | `S03-C000001` | Зовнішній ключ (FK) | Так | Посилання на `core.customers(customer_id)` |
| `merchant_id` | Ідентифікатор закладу | `TEXT` | `S03-M00001` | Зовнішній ключ (FK) | Так | Посилання на `core.merchants(merchant_id)` |
| `created_at` | Дата та час створення замовлення | `TIMESTAMPTZ` | `2025-09-01 12:15:00+03` | Атрибут | Так | Валідна часова мітка |
| `order_status` | Поточний статус замовлення | `TEXT` | `delivered` | Атрибут | Так | Напр.: `created`, `delivered`, `cancelled` |
| `currency` | Валюта транзакції | `TEXT` | `UAH` | Атрибут | Так | Код валюти (`UAH`, `EUR`, `USD` тощо) |
| `subtotal_amount` | Сума за товари | `NUMERIC(12,2)` | `255.00` | Атрибут | Так | `>= 0` |
| `delivery_fee` | Вартість доставки | `NUMERIC(12,2)` | `40.00` | Атрибут | Так | `>= 0` |
| `discount_amount` | Сума знижки | `NUMERIC(12,2)` | `0.00` | Атрибут | Так | `>= 0` |
| `total_amount` | Загальна сума замовлення | `NUMERIC(12,2)` | `295.00` | Атрибут | Так | `>= 0` |

## Таблиця `core.order_items`
**Файл джерела:** `order_items.csv`  
**Grain:** Окрема товарна позиція в межах замовлення.

| Поле | Опис | Тип даних (PostgreSQL `core`) | Приклад значення | Тип атрибута | Обов'язкове | Обмеження / Примітки |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `order_item_id` | Унікальний ідентифікатор позиції | `TEXT` | `S03-I0000001` | Первинний ключ (PK) | Так | Формат: `S03-IXXXXXXX` |
| `order_id` | Ідентифікатор замовлення | `TEXT` | `S03-O0000001` | Зовнішній ключ (FK) | Так | Посилання на `core.orders(order_id)` |
| `product_id` | Ідентифікатор товару | `TEXT` | `S03-P000474` | Зовнішній ключ (FK) | Так | Посилання на `core.products(product_id)` |
| `quantity` | Кількість одиниць товару в позиції | `INTEGER` | `2` | Атрибут | Так | `CHECK (quantity > 0)` |
| `unit_price` | Ціна за одиницю під час замовлення | `NUMERIC(12,2)` | `187.12` | Атрибут | Так | `>= 0` |
| `line_discount` | Знижка на позицію | `NUMERIC(12,2)` | `18.71` | Атрибут | Ні | `>= 0` |
| `line_total` | Підсумкова сума за позицією | `NUMERIC(12,2)` | `168.41` | Атрибут | Ні | `>= 0` |
| `currency` | Валюта позиції | `TEXT` | `UAH` | Атрибут | Ні | Код валюти |

## Аналітичні об'єкти звітності та продуктивності (`sql/analyze/`)

| Об'єкт | Тип | Grain (рівень деталізації рядка) | Ключові поля | Призначення |
| :--- | :--- | :--- | :--- | :--- |
| `lab03_cf.settings` | `TABLE` | Єдиний рядок конфігурації (`id = 1`) | `id`, `date_from`, `date_to` | Зберігає межі першого календарного місяця з доставленими замовленнями (`Europe/Kyiv`). |
| `lab03_cf.v_period_orders` | `VIEW` | Одне замовлення звітного місяця (усі статуси) | `order_id`, `customer_id`, `merchant_id`, `created_at`, `order_status`, `currency`, `total_amount`, `report_date` | Базова вибірка замовлень за період `[date_from, date_to)` із локальною датою `report_date`. |
| `lab03_cf.v_daily_orders` | `VIEW` | `report_date × merchant_id × currency` (лише `delivered`) | `report_date`, `merchant_id`, `currency`, `order_count`, `order_amount`, `avg_order_amount`, `accepted_units`, `orders_without_items` | Щоденний звіт за доставленими замовленнями з попередньою агрегацією позицій через CTE. |
| `lab03_cf.v_running_orders` | `VIEW` | `report_date × merchant_id × currency` (лише `delivered`) | `report_date`, `merchant_id`, `currency`, `order_amount`, `cumulative_amount` | Накопичувальний підсумок (`running total`) суми замовлень від початку місяця в розрізі закладу та валюти. |
| `lab03_cf.v_latest_customer_order` | `VIEW` | Один клієнт (`customer_id`), активний у звітному місяці | `customer_id`, `order_id`, `merchant_id`, `created_at`, `order_status`, `currency`, `total_amount` | Визначає останнє створене замовлення клієнта за допомогою `ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY created_at DESC, order_id DESC)`. |
| `lab03_cf.bench_orders` | `TABLE` | Одне замовлення (копія `core.orders`) | `order_id`, `merchant_id`, `created_at`, `total_amount` | Ізольована таблиця для експерименту з планами виконання та індексом `idx_lab03_cf_bench_merchant_created`. |

## Об'єкти приймання та аудиту інгестії (`sql/ingestion/`)

| Об'єкт | Тип | Grain (рівень деталізації рядка) | Ключові поля | Призначення |
| :--- | :--- | :--- | :--- | :--- |
| `stg.ingestion_records` | `TABLE` | `student_id × source × record_key` | `student_id`, `source`, `record_key`, `payload`, `raw_path`, `source_row_number`, `first_run_id`, `loaded_at` | Ідемпотентне зберігання сирих записів із ZIP-пакета (`orders`, `order_items`) та REST API (`delivery_events`). |
| `audit.ingestion_runs` | `TABLE` | Один запуск конвеєра інгестії (`run_id`) | `run_id`, `student_id`, `status`, `started_at`, `finished_at`, `raw_dir`, `summary`, `error_code` | Журнал виконання запусків інгестії зі статусами `RUNNING`, `SUCCESS`, `FAILED`. |
| `ingestion_stg.orders` / `delivery_events` | `TABLE` | Одне замовлення (`order_id`) / подія доставки (`event_id`) | `student_id`, `order_id` / `event_id`, `payload` | JSONB-зріз замовлень і подій доставки для експорту та перевірки контракту якості. |

## Об'єкти контролю якості даних, карантину та валідованого шару (`sql/quality/00_schema.sql`)

| Об'єкт | Тип | Grain (рівень деталізації рядка) | Ключові поля | Призначення |
| :--- | :--- | :--- | :--- | :--- |
| `quality.runs` | `TABLE` | Один запуск перевірки якості (`run_id`) | `run_id`, `student_id`, `scope`, `reference_time`, `contract_version`, `status`, `started_at`, `finished_at`, `source_manifest`, `error_code` | Журнал запусків контролю якості (`RUNNING`, `PASS`, `WARN`, `BLOCKED`, `FAILED`). |
| `quality.row_decisions` | `TABLE` | Один вхідний запис джерела (`run_id × source_name × source_row_no`) | `run_id`, `source_name`, `source_row_no`, `business_key`, `decision`, `reason_codes` | Поелементне рішення (`accepted`, `rejected`, `duplicate`) та коди причин для кожного вхідного запису. |
| `quality.rule_results` | `TABLE` | Одне правило контракту в запуску (`run_id × rule_id`) | `run_id`, `rule_id`, `severity`, `failed_count`, `details` | Кількість спрацювань і деталі кожного правила `Q01`–`Q10` (`BLOCK`, `REJECT`, `DUPLICATE`, `WARN`). |
| `quarantine.records` | `TABLE` | Один відхилений запис (`run_id × source_name × source_row_no`) | `run_id`, `source_name`, `source_row_no`, `payload`, `reason_codes` | Ізольоване збереження повного первинного JSON-payload кожного `rejected` запису разом із причинами відхилення. |
| `validated.orders` | `TABLE` | Одне прийняте замовлення (`order_id`) | `order_id`, `customer_id`, `merchant_id`, `created_at`, `order_status`, `currency`, `subtotal_amount`, `delivery_fee`, `discount_amount`, `total_amount`, `quality_run_id` | Останній успішно опублікований зріз валідованих замовлень (`core` + `api_orders`). |
| `validated.order_items` | `TABLE` | Одна прийнята позиція замовлення (`order_item_id`) | `order_item_id`, `order_id`, `product_id`, `quantity`, `unit_price`, `line_discount`, `line_total`, `currency`, `quality_run_id` | Останній успішно опублікований зріз валідованих позицій замовлень із перевіреними FK на `validated.orders`. |
| `validated.delivery_events` | `TABLE` | Одна прийнята подія доставки (`event_id`) | `event_id`, `delivery_id`, `order_id`, `event_type`, `occurred_at`, `available_at`, `courier_id`, `service_zone_id`, `payload_version`, `payload`, `quality_run_id` | Останній успішно опублікований зріз валідованих подій доставки з посиланням на `validated.orders`. |
