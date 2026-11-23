# Аналітичне сховище CityFlow (`dwh.*`) — результати завантаження та звірки

- **Варіант:** `S03`
- **Гілка / commit / tag:** `feat/data-warehouse` · PR commit `6cd76ed` · Tag `lab-06-v1` (`83e4f3d`)
- **Версії Python і PostgreSQL:** Python 3.14.3, PostgreSQL 16+, `psycopg[binary]==3.2.10`, `pytest==9.2.0`
- **Навчальна БД (без пароля):** `postgresql://localhost:5432/cityflow`

## 1. Модель

ERD-схема: `docs/warehouse/erd.png`.  
Детальний опис моделі та інваріантів: `docs/warehouse/model.md`.

| Таблиця | Що представляє один рядок (Grain) | Ключ |
|---|---|---|
| `dwh.dim_customer` | Одна історична версія клієнта у часовому інтервалі `[valid_from, valid_to)` (SCD Type 2 за `city`) | `customer_key` (PK, surrogate), `customer_id` (business key) |
| `dwh.dim_merchant` | Один заклад-партнер у поточному описі | `merchant_key` (PK, surrogate), `merchant_id` (UNIQUE) |
| `dwh.dim_product` | Один товар у поточному описі каталогу (SCD Type 1: `unit_price_uah`, `is_active`, `source_version_at`) | `product_key` (PK, surrogate), `product_id` (UNIQUE) |
| `dwh.dim_date` | Одна календарна дата створення замовлення (`Europe/Kyiv`) | `date_key` (PK, `date`) |
| `dwh.fact_orders` | Одне прийняте замовлення | `order_id` (PK), FK: `customer_key`, `merchant_key`, `date_key` |
| `dwh.fact_order_items` | Одна прийнята товарна позиція замовлення | `order_item_id` (PK), FK: `order_id`, `customer_key`, `merchant_key`, `product_key`, `date_key` |

- **Навчальне припущення `valid_from = '-infinity'`:** початковий зріз `core.customers` приймається чинним для всієї попередньої історії до першої зафіксованої зміни в `customers_delta.csv` (без журналу змін джерела відновити справжню історію міста до вивантаження неможливо).
- **Часова зона дати замовлення:** `Europe/Kyiv` (`date_key = (created_at AT TIME ZONE 'Europe/Kyiv')::date`).

## 2. Звірка початкового завантаження

| Перевірка | Джерело (`validated.*`) | DWH (`dwh.*`) | Різниця / висновок |
|---|---|---|---|
| Кількість замовлень | `6003` | `6003` | `0` (повний збіг) |
| Кількість позицій | `13656` | `13656` | `0` (повний збіг) |
| Ключі замовлень: обидва напрями `EXCEPT ALL` | `0` (`validated \ dwh`) | `0` (`dwh \ validated`) | `0` (точна еквівалентність множин ключів) |
| Ключі позицій: обидва напрями `EXCEPT ALL` | `0` (`validated \ dwh`) | `0` (`dwh \ validated`) | `0` (точна еквівалентність множин ключів) |

**Звірка сум заголовків замовлень (`validated.orders` vs `dwh.fact_orders`):**

| Валюта | Кількість замовлень | `SUM(total_amount)` у `validated.orders` | У `dwh.fact_orders` | Різниця |
|---|---|---|---|---|
| `EUR` | `24` | `262.57` | `262.57` | `0.00` |
| `UAH` | `5926` | `3172676.25` | `3172676.25` | `0.00` |
| `USD` | `53` | `685.63` | `685.63` | `0.00` |

**Звірка сум позицій замовлень (`validated.order_items` vs `dwh.fact_order_items`):**

| Валюта | Кількість позицій | `SUM(line_total)` у `validated.order_items` | У `dwh.fact_order_items` | Різниця |
|---|---|---|---|---|
| `EUR` | `59` | `227.05` | `227.05` | `0.00` |
| `UAH` | `13484` | `2690378.52` | `2690378.52` | `0.00` |
| `USD` | `113` | `588.55` | `588.55` | `0.00` |

*(Дві звірки виконуються окремо: заголовки `fact_orders.total_amount` додатково включають `delivery_fee` та знижки на рівні чека.)*

## 3. Type 1: товар (`dwh.dim_product`)

- **Контрольний `product_id`:** `S03-P000077` (`Margherita`, змінено і ціну, і статус `is_active`); сурогатний ключ `product_key` у локальному запуску зберігся незмінним (`77` до і після delta та повторів). Також підтверджено оновлення `S03-P000477` (`Espresso`: `58.30 → 53.11`, `is_active: True → True`, `13` історичних позицій на суму `1070.18`) та `S03-P000072` (`Veggie Roll`: `322.50 → 354.86`, `18` історичних позицій на суму `7617.50`).

| Поле (`S03-P000077`, Margherita) | До (`core.products`) | Після delta (`products_delta.csv`) | Після повтору delta та baseline |
|---|---|---|---|
| `unit_price_uah` | `304.19` | `340.07` | `340.07` |
| `is_active` | `true` | `false` | `false` |
| `source_version_at` | `-infinity` | `2026-09-22 11:00:00+03` | `2026-09-22 11:00:00+03` |

- **Незмінність історичних `unit_price` / `line_total` у фактах:** усі `20` історичних позицій товару `S03-P000077` зберегли незмінну суму `6750.19`, а загальні суми `fact_order_items` по всіх валютах збіглися з `validated.order_items` (`diff = 0.00`).
- **Невідомі `product_id`:** у `products_delta.csv` усі `30` записів знайдено в `dim_product` (`unmatched_products = []`); у модульному тесті `test_scd1_product_update_idempotency_and_unmatched` підтверджено, що невідомі ID потрапляють у `unmatched_products` без створення неповних товарів.

## 4. Type 2: клієнт (`dwh.dim_customer`)

Із `18` рядків у `customers_delta.csv` у `12` клієнтів місто не змінилося (`customers_unchanged_city = 12` — нові версії не створені), а у `6` клієнтів зафіксовано реальну зміну міста (`S03-C000019`, `S03-C000056`, `S03-C000149`, `S03-C000299`, `S03-C000482`, `S03-C000649`), тому кількість версій у `dim_customer` зросла з `800` до `806`.

- **Контрольний `customer_id` зі зміненим містом:** `S03-C000019` (`Marko Lysenko`, зміна `Dnipro` → `Lviv` у `2026-09-24 15:00:00+03`, тобто `2026-09-24T12:00:00Z`).

| `customer_key` (локальний запуск) | `city` | `valid_from` | `valid_to` | `is_current` |
|---|---|---|---|---|
| `205` | `Dnipro` | `-infinity` | `2026-09-24 15:00:00+03` | `false` |
| `806` | `Lviv` | `2026-09-24 15:00:00+03` | `NULL` | `true` |

**Точкові перевірки часових меж (`SELECT` із `VALUES` для `S03-C000019`):**

| Момент `SELECT` із `VALUES` | Очікувана версія | Фактично | Пояснення |
|---|---|---|---|
| `2026-09-24 14:59:59.999999+03:00` (`updated_at − 1us`) | Стара (`Dnipro`) | `key 205`, `Dnipro`, `is_current = false` | Виконується `probe_ts < valid_to` для `[valid_from, valid_to)` |
| `2026-09-24 15:00:00+03:00` (`updated_at`) | Нова (`Lviv`) | `key 806`, `Lviv`, `is_current = true` | Виконується `probe_ts >= valid_from` нової версії |
| `2026-09-25 15:00:00+03:00` (`updated_at + 1 day`) | Нова (`Lviv`) | `key 806`, `Lviv`, `is_current = true` | Потрапляє у відкритий інтервал `[valid_from, infinity)` |

- **Невідомі ID або невпорядковані зміни:** у `customers_delta.csv` `unmatched_customers = []` і `out_of_order_customers = []`; їхню ізоляцію та помилку `SCD2_CONFLICT` перевірено модульним тестом `test_scd2_conflict_unmatched_and_out_of_order`.

## 5. Повторний запуск та стабільність `warehouse_modified_at`

| Стан | Кількість фактів (`orders` / `items`) | Суми за валютами | Версії клієнтів (`dim_customer`) | Ключі та `warehouse_modified_at` |
|---|---|---|---|---|
| Після початкового завантаження (`10:01:32 UTC`) | `6003` / `13656` | Збіг (`diff = 0.00`) | `800` (`800` поточних) | `orders_upserted = 6003`, `warehouse_modified_at = 2026-11-23 12:01:32.856442+02:00` |
| Після delta (`10:04:37 UTC`) | `6003` / `13656` | Збіг (`diff = 0.00`) | `806` (`800` поточних, `+6` змін) | `products_updated = 30`, `orders_upserted = 0`, `warehouse_modified_at` незмінний |
| Після повтору тієї самої delta (`10:06:31 UTC`) | `6003` / `13656` | Збіг (`diff = 0.00`) | `806` (без змін) | `products_updated = 0`, `products_unchanged_or_repeat = 30`, `customers_new_versions = 0`, `customers_repeat_skipped = 6` |
| Після повтору початкового завантаження (`10:06:50 UTC`) | `6003` / `13656` | Збіг (`diff = 0.00`) | `806` (без змін) | `orders_upserted = 0`, `items_upserted = 0`; старі ціни `core.products` не повернулися, `S03-O0000001` та `S03-O0000002` зберегли `2026-11-23 12:01:32.856442+02:00` |

## 6. Тести та SQL-перевірки

| Перевірка `sql/warehouse/01_check_scd.sql` | Кількість повернутих рядків | Висновок |
|---|---|---|
| Одна поточна версія на клієнта (`is_current = true`) | `0` | `PASS` |
| Немає перетину інтервалів `[valid_from, valid_to)` | `0` | `PASS` |
| Кожен `fact_orders` належить часовому інтервалу своєї версії | `0` | `PASS` |

- **Модульні тести (`python -m pytest -v tests/warehouse tests/quality`):** `20 passed in 0.06s` (`8 passed` у `tests/warehouse` + `12 passed` у `tests/quality`).
- **Виправлена помилка:** під час першого запуску `--changes` виявлено `psycopg.errors.DataError: timestamp too small (before year 1): '-infinity'` при читанні початкового `source_version_at = '-infinity'` у Python; виправлено шляхом порівняння `%s::timestamptz > source_version_at` безпосередньо в SQL та додавання приведення `::text` для колонок `valid_from` / `valid_to` / `source_version_at` у `sql/warehouse/02_verify_dwh.sql`.

## 7. Відтворення

```bash
python tools/warehouse/run_sql.py sql/warehouse/00_schema.sql
python -m pytest -v tests/warehouse tests/quality
python -m pipeline warehouse --student S03
python -m pipeline warehouse --student S03 --changes data/changes/S03
python -m pipeline warehouse --student S03 --changes data/changes/S03
python -m pipeline warehouse --student S03
python tools/warehouse/run_sql.py sql/warehouse/01_check_scd.sql
python tools/warehouse/run_sql.py sql/warehouse/02_verify_dwh.sql
```

Файли змін варіанта `S03`: `data/changes/S03/`.

## 8. AI BLACK BOX

Під час розробки модуля аналітичного сховища використовувався інструмент Google Gemini для проєктування завантажувача `pipeline/warehouse.py`, побудови ERD-моделі у Mermaid та підготовки SQL-запитів звірки `sql/warehouse/02_verify_dwh.sql`. Зокрема, використовувався запит: *«Реалізуй ідемпотентний UPSERT для fact_orders із оновленням warehouse_modified_at виключно за умовою IS DISTINCT FROM, SCD Type 1 для dim_product із захистом від застарілих версій за source_version_at та SCD Type 2 для dim_customer із напіввідкритим інтервалом [valid_from, valid_to)»*. Коректність часових меж SCD перевірено тестом `test_scd2_customer_city_change_and_three_time_boundaries` і запитом із `VALUES` на клієнті `S03-C000019`, а незмінність історичних цін у `fact_order_items` — звіркою `SUM(line_total)` по всіх валютах (`diff = 0.00`). Під час реального запуску на `psycopg 3` виникла помилка `DataError: timestamp too small (before year 1): '-infinity'`, яку виправлено перенесенням порівняння `source_version_at` у SQL — цей практичний нюанс варто розібрати з групою.

## 9. Висновок

Побудовано й верифіковано багатовимірне аналітичне сховище `dwh.*` для варіанта `S03`: `4` виміри (`dim_customer` — `806` версій для `800` клієнтів, `dim_merchant` — `42`, `dim_product` — `513`, `dim_date` — `366` днів у `Europe/Kyiv`) та `2` факти (`fact_orders` — `6003`, `fact_order_items` — `13656`). Підтверджено `EXCEPT ALL = 0`, `diff = 0.00` по `EUR`/`UAH`/`USD`, `0` порушень у `01_check_scd.sql`, незмінність історичних цін та стабільність `warehouse_modified_at` при повторних запусках. Узгоджений шар `dwh.*` передається як джерело фактів і вимірів для наступного етапу (dbt).
