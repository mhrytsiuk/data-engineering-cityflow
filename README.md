# CityFlow Data Engineering Project

## Предметна область
CityFlow — це сервіс міських замовлень і доставки. Цей проєкт реалізує побудову наскрізного конвеєра даних: первинне профілювання, валідацію та транзакційне завантаження сирих даних у реляційну базу PostgreSQL (`stg` → `core` з аудитом у `audit`), аналітичну звітність і оптимізацію продуктивності SQL-запитів, надійне ідемпотентне отримання даних (Data Ingestion) із файлових пакетів та REST API, автоматизований контроль якості даних за контрактом (`quality`, `quarantine`, `validated`), а також багатовимірне аналітичне сховище даних (`dwh.*`) із підтримкою SCD Type 1 та SCD Type 2.

## Структура проєкту
- `contracts/cityflow_v1.json` — формалізований контракт правил якості даних (`Q01`–`Q10`).
- `pipeline/` — Python-пакет автоматизованого контролю якості даних (`pipeline quality`) та завантаження аналітичного сховища (`pipeline warehouse` із SCD Type 1 / Type 2 та транзакційною звіркою).
- `data/raw/` — оригінальні CSV-файли джерел та сирі артефакти запусків конвеєра (ігноруються Git).
- `data/changes/S03/` — навчальний пакет змін вимірів (`customers_delta.csv`, `products_delta.csv`, `BATCH_METADATA.json`).
- `runtime/` — незмінні експорти джерел (`runtime/source_01/`) та JSON-звіти запусків (ігноруються Git).
- `docs/` — словник даних (`data_dictionary.md`), реєстр проблем якості (`data_quality_findings.md`), політика завантаження (`load_policy.md`), архітектурна схема (`architecture_v0.png`), плани виконання запитів (`docs/analyze/`), результати інгестії (`docs/ingestion/results.md`), результати контролю якості (`docs/quality/results.md`), а також ERD, опис моделі та результати аналітичного сховища (`docs/warehouse/`).
- `notebooks/` — Jupyter Notebook із кодом первинного дослідницького аналізу та профілювання (`01_source_profiling.ipynb`).
- `src/` — Python-модулі автоматизованого імпорту (`src/load_staging.py`) та надійного отримання даних із файлів і REST API (`src/ingestion/`).
- `sql/` — DDL та DML скрипти для створення схем і трансформації (`01`–`08`), аналітичного SQL (`sql/analyze/`), таблиць інгестії (`sql/ingestion/`), таблиць контролю якості (`sql/quality/`) та аналітичного сховища (`sql/warehouse/`).
- `tools/` — локальний FastAPI-сервер (`tools/ingestion/`), утиліти експорту джерел і перевірки контрольних сум (`tools/quality/`) та утиліта виконання SQL (`tools/warehouse/`).
- `tests/` — модульні автотести `pytest` для перевірки інгестії (`tests/ingestion/`), правил якості даних (`tests/quality/`) та аналітичного сховища (`tests/warehouse/`).

## Як запустити

### Підготовка середовища та базове завантаження (`stg` → `core`)
1. Створіть та активуйте віртуальне середовище:
   `python -m venv .venv && source .venv/bin/activate`
2. Встановіть необхідні залежності:
   `pip install -r requirements.txt`
3. Створіть структуру БД та завантажте перевірені дані у схему `core`:
   `psql -d cityflow -f sql/01_schemas_and_stg.sql`
   `psql -d cityflow -f sql/02_core_ddl.sql`
   `python src/load_staging.py`
   `psql -d cityflow -f sql/03_transform_and_load.sql`

### Аналітичний SQL: коректність і продуктивність (`sql/analyze/`)
Виконуйте скрипти послідовно у базі `cityflow` (через `psql -v ON_ERROR_STOP=1`):
1. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/00_prepare.sql`
2. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/01_daily_report.sql`
3. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/02_check_daily.sql`
4. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/03_windows.sql`
5. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/04_check_windows.sql`
6. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/05_bench_prepare.sql`
7. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/06_bench_query.sql`
8. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/07_bench_index.sql`
9. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/06_bench_query.sql`
10. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/08_bench_check.sql`

### Надійне отримання даних із файлів та REST API (`src/ingestion/`)
1. Запустіть модульні автотести інгестії (`26 passed`):
   `pytest -q tests/ingestion`
2. Запустіть локальний API-сервер (`python tools/ingestion/start_api.py`) та виконайте конвеєр:
   `python src/ingestion/main.py check`
   `python src/ingestion/main.py init-db`
   `python src/ingestion/main.py run`
   `python src/ingestion/main.py verify`

### Автоматизований контроль якості даних (`pipeline/`, `contracts/`, `sql/quality/`, `tools/quality/`)
Версії: Python 3.12+, PostgreSQL 16+, `psycopg[binary]==3.2.10`, `pytest>=8,<10`.  
Коди завершення `python -m pipeline quality`: `0` для статусів `PASS` та `WARN`; `1` (ненульовий код) для статусів `BLOCKED` та `FAILED`.

```bash
python tools/quality/run_sql.py sql/quality/00_schema.sql
python tools/quality/export_sources.py --student S03 --out runtime/source_01
python tools/quality/inspect_export.py runtime/source_01
python -m pytest -q tests/quality
python -m pipeline quality --student S03 --input runtime/source_01 --reference-time 2026-09-08T08:00:00Z
python tools/quality/run_sql.py sql/quality/01_verify.sql
```

### Аналітичне сховище даних (`dwh.*`: `pipeline/warehouse.py`, `sql/warehouse/`, `docs/warehouse/`)
1. Створіть схему `dwh` та запустіть модульні тести (`8 passed`):
   ```bash
   python tools/warehouse/run_sql.py sql/warehouse/00_schema.sql
   python -m pytest -v tests/warehouse
   ```
2. Виконайте початкове завантаження (`baseline`), застосуйте зміни вимірів (`SCD Type 1` та `SCD Type 2`) і перевірте ідемпотентність повторних запусків:
   ```bash
   python -m pipeline warehouse --student S03
   python -m pipeline warehouse --student S03 --changes data/changes/S03
   python -m pipeline warehouse --student S03 --changes data/changes/S03
   python -m pipeline warehouse --student S03
   ```
3. Виконайте перевірку SCD-інваріантів (усі 3 запити мають повернути `0` рядків) та комплексну звірку ключів і валютних сум:
   ```bash
   python tools/warehouse/run_sql.py sql/warehouse/01_check_scd.sql
   python tools/warehouse/run_sql.py sql/warehouse/02_verify_dwh.sql
   ```

## Ключові висновки по проєкту

- **Якість даних та транзакційність:** Розмежування `stg` і `core` та атомарні транзакції `BEGIN ... COMMIT` гарантують узгодженість реляційного ядра.
- **Контроль Grain та індексація:** Попередня агрегація позицій у CTE `items_by_order` перед `LEFT JOIN` усуває розмноження сум замовлень (`PASS`), а складений індекс `(merchant_id, created_at)` переводить план виконання з `Seq Scan` на `Index Scan`.
- **Надійна інгестія з файлів і REST API:** Перевірка маніфесту `PACKAGE_INFO.json` та `SHA-256`, retry/backoff із `Retry-After` та курсорна пагінація забезпечують стійке й ідемпотентне приймання зовнішніх батчів.
- **Автоматизований контроль якості за контрактом (`Q01`–`Q10`):** Двопрохідна класифікація (`accepted` / `rejected` / `duplicate`), каскадна перевірка батьківських ключів (`PARENT_REJECTED`), збереження відхилених рядків у `quarantine.records` та атомарне оновлення `validated.*` гарантують захист попереднього валідного стану.
- **Багатовимірне сховище (`dwh.*`):** Зіркова схема з 4 конформних вимірів і 2 фактів зберігає історичні суми продажу при оновленні товарів (SCD Type 1), відстежує історію змін міста клієнтів у `[valid_from, valid_to)` (SCD Type 2) і підтримує ідемпотентний `UPSERT` зі стабільними сурогатними ключами та `warehouse_modified_at`.

## Архітектура (v0)

![Architecture](docs/architecture_v0.png)