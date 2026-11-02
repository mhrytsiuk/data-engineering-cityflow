# Словник даних (Data Dictionary)

Документ описує структуру та атрибути даних проєкту CityFlow: таблиці ядра (`core.*`), аналітичні представлення (`lab03_cf.*`), а також таблиці ідемпотентного приймання та аудиту інгестії (`stg.ingestion_records`, `audit.ingestion_runs`).

## 1. Таблиця `core.customers`
**Файл джерела:** `customers.csv`  
**Grain:** Один унікальний зареєстрований клієнт.

| Поле | Опис | Тип даних (PostgreSQL `core`) | Приклад значення | Тип атрибута | Обов'язкове | Обмеження / Примітки |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `customer_id` | Унікальний ідентифікатор клієнта | `TEXT` | `S03-C000001` | Первинний ключ (PK) | Так | Формат: `S03-CXXXXXX` |
| `full_name` | Повне ім'я та прізвище клієнта | `TEXT` | `Олена Коваль` | Атрибут | Так | Непорожній рядок |
| `city` | Місто клієнта | `TEXT` | `Lviv` | Атрибут | Так | Текстове поле |
| `registered_at` | Дата та час реєстрації акаунту | `TIMESTAMPTZ` | `2025-08-15 08:30:00+03` | Атрибут | Так | Валідна часова мітка |

## 2. Таблиця `core.merchants`
**Файл джерела:** `merchants.csv`  
**Grain:** Один унікальний заклад-партнер.

| Поле | Опис | Тип даних (PostgreSQL `core`) | Приклад значення | Тип атрибута | Обов'язкове | Обмеження / Примітки |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `merchant_id` | Унікальний ідентифікатор закладу | `TEXT` | `S03-M00001` | Первинний ключ (PK) | Так | Формат: `S03-MXXXXX` |
| `merchant_name` | Назва закладу | `TEXT` | `Fresh Bakery` | Атрибут | Так | Текстове поле |
| `city` | Місто розташування закладу | `TEXT` | `Lviv` | Атрибут | Так | Текстове поле |
| `opened_at` | Дата та час приєднання до платформи | `TIMESTAMPTZ` | `2025-05-10 10:00:00+03` | Атрибут | Так | Валідна часова мітка |

## 3. Таблиця `core.products`
**Файл джерела:** `products.csv`  
**Grain:** Один унікальний товар у меню закладу.

| Поле | Опис | Тип даних (PostgreSQL `core`) | Приклад значення | Тип атрибута | Обов'язкове | Обмеження / Примітки |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `product_id` | Унікальний ідентифікатор товару | `TEXT` | `S03-P000101` | Первинний ключ (PK) | Так | Формат: `S03-PXXXXXX` |
| `merchant_id` | Ідентифікатор закладу-власника | `TEXT` | `S03-M00001` | Зовнішній ключ (FK) | Так | Посилання на `core.merchants(merchant_id)` |
| `product_name` | Назва товару | `TEXT` | `Круасан з мигдалем` | Атрибут | Так | Текстове поле |
| `unit_price_uah` | Ціна за одиницю товару в UAH | `NUMERIC(12,2)` | `85.00` | Атрибут | Так | `CHECK (unit_price_uah > 0)` |
| `is_active` | Прапорець активності товару в меню | `BOOLEAN` | `true` | Атрибут | Так | `true` / `false` |

## 4. Таблиця `core.orders`
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

## 5. Таблиця `core.order_items`
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

## 6. Аналітичні об'єкти звітності та продуктивності (`sql/analyze/`)

| Об'єкт | Тип | Grain (рівень деталізації рядка) | Ключові поля | Призначення |
| :--- | :--- | :--- | :--- | :--- |
| `lab03_cf.settings` | `TABLE` | Єдиний рядок конфігурації (`id = 1`) | `id`, `date_from`, `date_to` | Зберігає межі першого календарного місяця з доставленими замовленнями (`Europe/Kyiv`). |
| `lab03_cf.v_period_orders` | `VIEW` | Одне замовлення звітного місяця (усі статуси) | `order_id`, `customer_id`, `merchant_id`, `created_at`, `order_status`, `currency`, `total_amount`, `report_date` | Базова вибірка замовлень за період `[date_from, date_to)` із локальною датою `report_date`. |
| `lab03_cf.v_daily_orders` | `VIEW` | `report_date × merchant_id × currency` (лише `delivered`) | `report_date`, `merchant_id`, `currency`, `order_count`, `order_amount`, `avg_order_amount`, `accepted_units`, `orders_without_items` | Щоденний звіт за доставленими замовленнями з попередньою агрегацією позицій через CTE. |
| `lab03_cf.v_running_orders` | `VIEW` | `report_date × merchant_id × currency` (лише `delivered`) | `report_date`, `merchant_id`, `currency`, `order_amount`, `cumulative_amount` | Накопичувальний підсумок (`running total`) суми замовлень від початку місяця в розрізі закладу та валюти. |
| `lab03_cf.v_latest_customer_order` | `VIEW` | Один клієнт (`customer_id`), активний у звітному місяці | `customer_id`, `order_id`, `merchant_id`, `created_at`, `order_status`, `currency`, `total_amount` | Визначає останнє створене замовлення клієнта за допомогою `ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY created_at DESC, order_id DESC)`. |
| `lab03_cf.bench_orders` | `TABLE` | Одне замовлення (копія `core.orders`) | `order_id`, `merchant_id`, `created_at`, `total_amount` | Ізольована таблиця для експерименту з планами виконання та індексом `idx_lab03_cf_bench_merchant_created`. |

## 7. Об'єкти приймання та аудиту інгестії (`sql/ingestion/`)

### 7.1. Таблиця `stg.ingestion_records`
**Джерела:** Файловий ZIP-пакет `cityflow_S03_batch_01_normal_v1.zip` (`orders`, `order_items`) та REST API `/api/v1/delivery-events` (`delivery_events`).  
**Grain:** Один унікальний бізнес-запис джерела (`student_id × source × record_key`).

| Поле | Опис | Тип даних (PostgreSQL) | Приклад значення | Тип атрибута | Обов'язкове | Обмеження / Примітки |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `student_id` | Ідентифікатор варіанта | `TEXT` | `S03` | Складений первинний ключ (PK) | Так | Частина `PRIMARY KEY (student_id, source, record_key)` |
| `source` | Назва джерела даних | `TEXT` | `delivery_events` | Складений первинний ключ (PK) | Так | `CHECK (source IN ('orders', 'order_items', 'delivery_events'))` |
| `record_key` | Бізнес-ключ запису | `TEXT` | `S03-E000001` | Складений первинний ключ (PK) | Так | `order_id`, `order_item_id` або `event_id` |
| `payload` | Повний сирий запис у форматі JSON | `JSONB` | `{"event_id": "S03-E000001", ...}` | Атрибут | Так | Перевіряється на відсутність конфліктів вмісту при однаковому `record_key` |
| `raw_path` | Шлях походження сирого артефакту | `TEXT` | `data/raw/ingestion/.../api` | Атрибут | Так | Вказує на ZIP-файл або каталог сирих сторінок API |
| `source_row_number` | Порядковий номер запису у вхідному джерелі | `INTEGER` | `1` | Атрибут | Так | `CHECK (source_row_number > 0)` |
| `first_run_id` | Ідентифікатор запуску, який вперше вставив запис | `UUID` | `aa2142a5-db97-...` | Зовнішній ключ (FK) | Так | Посилання на `audit.ingestion_runs(run_id)` |
| `loaded_at` | Час першої вставки запису у staging | `TIMESTAMPTZ` | `2026-11-02 12:35:00+02` | Атрибут | Так | `DEFAULT clock_timestamp()` |

### 7.2. Таблиця `audit.ingestion_runs`
**Призначення:** Журнал виконання запусків модуля Ingestion (фіксує старт, успішне завершення та аварійні зупинки).  
**Grain:** Один запуск конвеєра (`run_id`).

| Поле | Опис | Тип даних (PostgreSQL) | Приклад значення | Тип атрибута | Обов'язкове | Обмеження / Примітки |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `run_id` | Унікальний ідентифікатор запуску | `UUID` | `c628d85e-5050-...` | Первинний ключ (PK) | Так | Генерується через `uuid.uuid4()` |
| `student_id` | Ідентифікатор варіанта | `TEXT` | `S03` | Атрибут | Так | `S03` |
| `status` | Підсумковий статус запуску | `TEXT` | `SUCCESS` | Атрибут | Так | `CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED'))` |
| `started_at` | Час початку запуску | `TIMESTAMPTZ` | `2026-11-02 12:34:00+02` | Атрибут | Так | `DEFAULT clock_timestamp()` |
| `finished_at` | Час завершення запуску | `TIMESTAMPTZ` | `2026-11-02 12:34:05+02` | Атрибут | Ні | Заповнюється при `SUCCESS` або `FAILED` |
| `raw_dir` | Шлях до каталогу сирих артефактів запуску | `TEXT` | `data/raw/ingestion/c628d85e-...` | Атрибут | Так | Зберігає `source.zip`, `api/page_*.json`, `run.json`, `run.log` |
| `summary` | JSON-підсумок кількостей за джерелами | `JSONB` | `{"orders": {"received": 420, ...}}` | Атрибут | Ні | Заповнюється при `SUCCESS` |
| `error_code` | Короткий код помилки (у разі збою) | `TEXT` | `INJECTED_BEFORE_COMMIT` | Атрибут | Ні | Заповнюється при `FAILED` |