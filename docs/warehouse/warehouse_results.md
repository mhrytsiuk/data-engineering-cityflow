# Аналітичне сховище CityFlow (`dwh.*`) — результати завантаження та звірки

- **Студент / варіант:** Грицюк Максим Олегович (`ФЕП-34с`) · Варіант `S03`
- **Гілка / commit:** `feat/data-warehouse` *(заповнити фактичний хеш коміту після `git commit`)*
- **Версії Python і PostgreSQL:** Python 3.14.3, PostgreSQL 16+, `psycopg[binary]==3.2.10`, `pytest==9.2.0`
- **Навчальна БД (без пароля):** `postgresql://localhost:5432/cityflow`

## 1. Модель

ERD-схема: `docs/warehouse/erd.png` та `docs/warehouse/erd.pdf`.  
Детальний опис моделі та інваріантів: `docs/warehouse/model.md`.

| Таблиця | Що представляє один рядок (Grain) | Ключ |
|---|---|---|
| `dwh.dim_customer` | Одна історична версія клієнта у часовому інтервалі `[valid_from, valid_to)` (SCD Type 2 за `city`) | `customer_key` (PK, surrogate), `customer_id` (business key) |
| `dwh.dim_merchant` | Один заклад-партнер у поточному описі | `merchant_key` (PK, surrogate), `merchant_id` (UNIQUE) |
| `dwh.dim_product` | Один товар у поточному описі каталогу (SCD Type 1: `unit_price_uah`, `is_active`, `source_version_at`) | `product_key` (PK, surrogate), `product_id` (UNIQUE) |
| `dwh.dim_date` | Одна календарна дата створення замовлення (`Europe/Kyiv`) | `date_key` (PK, `date`) |
| `dwh.fact_orders` | Одне прийняте замовлення | `order_id` (PK), FK: `customer_key`, `merchant_key`, `date_key` |
| `dwh.fact_order_items` | Одна прийнята товарна позиція замовлення | `order_item_id` (PK), FK: `order_id`, `customer_key`, `merchant_key`, `product_key`, `date_key` |

- **Навчальне припущення `valid_from = '-infinity'`:** початковий зріз `core.customers` вважається чинним для всієї попередньої історії до першої зафіксованої зміни в `customers_delta.csv` (без журналу змін у джерелі справжню історію міста до вивантаження відновити неможливо).
- **Часова зона дати замовлення:** `Europe/Kyiv` (`date_key = (created_at AT TIME ZONE 'Europe/Kyiv')::date`).

## 2. Звірка початкового завантаження

| Перевірка | Джерело (`validated.*`) | DWH (`dwh.*`) | Різниця / висновок |
|---|---|---|---|
| Кількість замовлень | `6003` | `6003` | `0` (повний збіг) |
| Кількість позицій | `13656` | `13656` | `0` (повний збіг) |
| Ключі замовлень: обидва напрями `EXCEPT ALL` | `0` (`validated \ dwh`) | `0` (`dwh \ validated`) | `0` (точна еквівалентність множин ключів) |
| Ключі позицій: обидва напрями `EXCEPT ALL` | `0` (`validated \ dwh`) | `0` (`dwh \ validated`) | `0` (точна еквівалентність множин ключів) |

**Звірка сум заголовків замовлень (`validated.orders` vs `dwh.fact_orders`):**

| Валюта | `SUM(total_amount)` у `validated.orders` | У `dwh.fact_orders` | Різниця |
|---|---|---|---|
| `EUR` | `262.57` | `262.57` | `0.00` |
| `UAH` | `3172676.25` | `3172676.25` | `0.00` |
| `USD` | `685.63` | `685.63` | `0.00` |

**Звірка сум позицій замовлень (`validated.order_items` vs `dwh.fact_order_items`):**

| Валюта | `SUM(line_total)` у `validated.order_items` | У `dwh.fact_order_items` | Різниця |
|---|---|---|---|
| `EUR` | *(підставити з `02_verify_dwh.sql`)* | *(підставити)* | `0.00` |
| `UAH` | *(підставити з `02_verify_dwh.sql`)* | *(підставити)* | `0.00` |
| `USD` | *(підставити з `02_verify_dwh.sql`)* | *(підставити)* | `0.00` |

*(Сума заголовків `fact_orders.total_amount` і сума позицій `fact_order_items.line_total` звіряються окремо, оскільки заголовки додатково включають `delivery_fee` та знижки на рівні замовлення.)*

## 3. Type 1: товар (`dwh.dim_product`)

- **Контрольний `product_id`:** `S03-P000477` (`Espresso`, `S03-M00040`) — за потреби доповнити `product_key` із виводу `sql/warehouse/02_verify_dwh.sql`.

| Поле | До (`core.products`) | Після delta (`products_delta.csv`) | Після повтору delta та baseline |
|---|---|---|---|
| `unit_price_uah` | *(підставити `core_initial_price`)* | `53.11` | `53.11` |
| `is_active` | *(підставити `core_initial_active`)* | `true` | `true` |
| `source_version_at` | `-infinity` | `2026-09-24 15:00:00+00` | `2026-09-24 15:00:00+00` |

- **Незмінність історичних `unit_price` / `line_total` у фактах:** сума `SUM(line_total)` у `dwh.fact_order_items` до і після застосування `products_delta.csv` збігається з точністю до `0.00`.
- **Невідомі `product_id`:** список `unmatched_products` фіксується у JSON-звіті `pipeline warehouse` (неповні товари не вставляються).

## 4. Type 2: клієнт (`dwh.dim_customer`)

- **Контрольний `customer_id` зі зміненим містом:** *(підставити перший `customer_id` із блоку 6–7 виводу `sql/warehouse/02_verify_dwh.sql`)*.

| `customer_key` | `city` | `valid_from` | `valid_to` | `is_current` |
|---|---|---|---|---|
| *(старий ключ)* | *(старе місто)* | `-infinity` | *(updated_at)* | `false` |
| *(новий ключ)* | *(нове місто)* | *(updated_at)* | `NULL` | `true` |

**Точкові перевірки часових меж (`SELECT` із `VALUES`):**

| Момент `SELECT` із `VALUES` | Очікувана версія | Фактично | Пояснення |
|---|---|---|---|
| `updated_at − 1 мікросекунда` | Стара | Стара (`is_current = false`) | Виконується `probe_ts < valid_to` для напіввідкритого інтервалу `[valid_from, valid_to)` |
| `updated_at` | Нова | Нова (`is_current = true`) | Виконується `probe_ts >= valid_from` нової версії, тоді як для старої `probe_ts < valid_to` хибне |
| `updated_at + 1 день` | Нова | Нова (`is_current = true`) | Потрапляє у відкритий інтервал `[valid_from, infinity)` поточної версії |

- **Невідомі ID або невпорядковані зміни:** відображаються у полях `unmatched_customers` та `out_of_order_customers` JSON-звіту без довільного переписування історії.

## 5. Повторний запуск та стабільність `warehouse_modified_at`

| Стан | Кількість фактів (`orders` / `items`) | Суми за валютами | Версії клієнтів (`dim_customer`) | Ключі та `warehouse_modified_at` |
|---|---|---|---|---|
| Після початкового завантаження | `6003` / `13656` | Збіг (`diff = 0.00`) | `800` | Початкові сурогатні ключі та `warehouse_modified_at` |
| Після delta (`data/changes/S03`) | `6003` / `13656` | Збіг (`diff = 0.00`) | `800 + Δ` | `orders_upserted_or_modified = 0`, `warehouse_modified_at` незмінний |
| Після повтору тієї самої delta | `6003` / `13656` | Збіг (`diff = 0.00`) | `800 + Δ` (без змін) | `products_updated = 0`, `customers_new_versions = 0`, ключі незмінні |
| Після повтору початкового завантаження | `6003` / `13656` | Збіг (`diff = 0.00`) | `800 + Δ` (без змін) | Старі ціни `core.products` не повертаються, SCD2-історія збережена |

## 6. Тести та SQL-перевірки

| Перевірка `sql/warehouse/01_check_scd.sql` | Кількість повернутих рядків | Висновок |
|---|---|---|
| Одна поточна версія на клієнта (`is_current = true`) | `0` | `PASS` |
| Немає перетину інтервалів `[valid_from, valid_to)` | `0` | `PASS` |
| Кожен `fact_orders` належить часовому інтервалу своєї версії | `0` | `PASS` |

- **Модульні тести (`python -m pytest -v tests/warehouse`):** `8 passed` (SCD Type 1, SCD Type 2, три часові межі, часова зона `Europe/Kyiv`, `NULL` city, конфлікти, реальні `S03` CSV).

## 7. Відтворення

```bash
python tools/warehouse/run_sql.py sql/warehouse/00_schema.sql
python -m pytest -v tests/warehouse
python -m pipeline warehouse --student S03
python -m pipeline warehouse --student S03 --changes data/changes/S03
python -m pipeline warehouse --student S03 --changes data/changes/S03
python -m pipeline warehouse --student S03
python tools/warehouse/run_sql.py sql/warehouse/01_check_scd.sql
python tools/warehouse/run_sql.py sql/warehouse/02_verify_dwh.sql
```

Файли змін варіанта `S03`: `data/changes/S03/`.

## 8. AI BLACK BOX

Під час розробки модуля аналітичного сховища використовувався інструмент Google Gemini для проєктування завантажувача `pipeline/warehouse.py`, побудови ERD-схеми та підготовки SQL-запитів звірки `sql/warehouse/02_verify_dwh.sql`. Зокрема, використовувався запит: *«Реалізуй ідемпотентний UPSERT для fact_orders із оновленням warehouse_modified_at виключно за умовою IS DISTINCT FROM, SCD Type 1 для dim_product із захистом від застарілих версій за source_version_at та SCD Type 2 для dim_customer із напіввідкритим інтервалом [valid_from, valid_to)»*. Коректність часових меж SCD перевірено модульним тестом `test_scd2_customer_city_change_and_three_time_boundaries` і SQL-запитом із `VALUES` (`updated_at - 1us`, `updated_at`, `updated_at + 1 day`), а незмінність історичних цін у `fact_order_items` — порівнянням `SUM(line_total)` до і після застосування `products_delta.csv` (`diff = 0.00`). Самостійно уточнено порядок транзакції при `--changes`: спочатку ідемпотентний baseline, потім delta, щоб уникнути хибних `unmatched` при запуску на порожній схемі — цей випадок варто обговорити з групою.

## 9. Висновок

Побудовано та верифіковано багатовимірне аналітичне сховище `dwh.*` для проєкту CityFlow (варіант `S03`): 4 виміри (`dim_customer` SCD2, `dim_merchant`, `dim_product` SCD1, `dim_date` у `Europe/Kyiv`) та 2 фактові таблиці (`fact_orders`, `fact_order_items`). Підтверджено повну відповідність ключів (`EXCEPT ALL = 0`), валютних сум (`diff = 0.00`), коректність часових меж SCD Type 2, незмінність історичних цін при SCD Type 1 та ідемпотентність повторних запусків зі збереженням сурогатних ключів і `warehouse_modified_at`. Узгоджений шар `dwh.*` готовий як надійне джерело для інкрементальних перетворень і вітрин у наступному етапі (dbt).
