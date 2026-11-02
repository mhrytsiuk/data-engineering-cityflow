# CityFlow Data Engineering Project

## Предметна область
CityFlow — це сервіс міських замовлень і доставки. Цей проєкт реалізує побудову наскрізного конвеєра даних для первинного профілювання, валідації, транзакційного завантаження сирих даних у реляційну базу PostgreSQL (`stg` → `core` з аудитом у `audit`), аналітичної звітності й оптимізації продуктивності SQL-запитів, а також надійного ідемпотентного отримання даних (Data Ingestion) із файлових пакетів та REST API.

## Структура проєкту
- `data/raw/` — оригінальні CSV-файли джерел та сирі артефакти запусків конвеєра (ігноруються Git).
- `data/variant/` — персональний пакет даних варіанта для модуля Ingestion (ігнорується Git).
- `data/sample/` — зразки даних для тестування.
- `docs/` — словник даних (`data_dictionary.md`), реєстр проблем якості (`data_quality_findings.md`), політика завантаження (`load_policy.md`), архітектурна схема (`architecture_v0.png`), плани виконання запитів (`docs/analyze/`) та результати інгестії (`docs/ingestion/results.md`).
- `notebooks/` — Jupyter Notebook з кодом первинного дослідницького аналізу та профілювання (`01_source_profiling.ipynb`).
- `src/` — Python-модулі автоматизованого імпорту (`src/load_staging.py`) та надійного отримання даних із файлів і REST API (`src/ingestion/`).
- `sql/` — DDL та DML скрипти для створення схем бази даних і трансформації (`01`–`08`), аналітичного SQL (`sql/analyze/`) та таблиць приймання й верифікації інгестії (`sql/ingestion/`).
- `tests/ingestion/` — модульні тести перевірки контракту файлів, retry/backoff, курсорної пагінації та дедуплікації.
- `tools/ingestion/` — локальний FastAPI-сервер курсорної пагінації та симуляції мережевих збоїв.

## Як запустити

### 1. Підготовка середовища та завантаження ядра БД (`stg` → `core`)
1. Створіть та активуйте віртуальне середовище:
   `python -m venv .venv && source .venv/bin/activate`
2. Встановіть необхідні залежності:
   `pip install -r requirements.txt`
3. Помістіть вихідні CSV-файли в директорію `data/raw/`.
4. Для перегляду аналізу якості даних запустіть `jupyter lab` та відкрийте `notebooks/01_source_profiling.ipynb`.
5. Налаштуйте підключення до бази даних у файлі `.env` (за шаблоном `.env.example`) та `src/load_staging.py`.
6. Створіть структуру БД (схеми та таблиці), виконавши DDL-скрипти:
   `psql -d cityflow -f sql/01_schemas_and_stg.sql`
   `psql -d cityflow -f sql/02_core_ddl.sql`
7. Запустіть імпорт сирих даних у проміжну схему Staging:
   `python src/load_staging.py`
8. Виконайте SQL-транзакцію для валідації та завантаження перевірених даних у схему Core:
   `psql -d cityflow -f sql/03_transform_and_load.sql`

### 2. Аналітичний SQL: коректність і продуктивність

Виконуйте скрипти послідовно у тій самій базі `cityflow` (через `psql -v ON_ERROR_STOP=1`):

1. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/00_prepare.sql`
2. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/01_daily_report.sql`
3. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/02_check_daily.sql`
4. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/03_windows.sql`
5. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/04_check_windows.sql`
6. `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/05_bench_prepare.sql`
7. Вимірювання без індексу (1 прогрів + 3 контрольні запуски -> `docs/analyze/plan_before.txt`):
   `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/06_bench_query.sql`
8. Створення індексу `idx_lab03_cf_bench_merchant_created (merchant_id, created_at)`:
   `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/07_bench_index.sql`
9. Вимірювання з індексом (1 прогрів + 3 контрольні запуски -> `docs/analyze/plan_after.txt`):
   `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/06_bench_query.sql`
10. Перевірка незмінності результату:
    `psql -d cityflow -v ON_ERROR_STOP=1 -f sql/analyze/08_bench_check.sql`

### 3. Надійне отримання даних із файлів та REST API (Data Ingestion)

1. Налаштуйте змінні оточення у кореневому файлі `.env` (`CITYFLOW_STUDENT_ID`, `CITYFLOW_API_BASE_URL`, `CITYFLOW_API_KEY`, `CITYFLOW_VARIANT_DIR`, `DATABASE_URL`).
2. Запустіть набір модульних автотестів (`26 passed`):
   `pytest -q tests/ingestion`
3. В окремому вікні термінала запустіть локальний сервер REST API:
   `python tools/ingestion/start_api.py`
4. Перевірте доступність API та ініціалізуйте таблиці `stg.ingestion_records` і `audit.ingestion_runs`:
   `python src/ingestion/main.py check`
   `python src/ingestion/main.py init-db`
5. Перевірте ізольоване читання файлового ZIP-пакета та пагінацію REST API (без запису в БД):
   `python src/ingestion/main.py file`
   `python src/ingestion/main.py api`
6. Перевірте атомарний відкат транзакції при збої перед `COMMIT`, виконайте два послідовні ідемпотентні запуски та звірку стану БД:
   `python src/ingestion/main.py run --fail-before-commit`
   `python src/ingestion/main.py run`
   `python src/ingestion/main.py verify`
   `python src/ingestion/main.py run`
   `python src/ingestion/main.py verify`
   `psql -d cityflow -f sql/ingestion/01_verify.sql`

## Ключові висновки по проєкту

Якість даних та транзакційність: Вхідні файли містили дублікати первинних ключів, пропущені контакти та невалідні дати створення замовлень. Завдяки розмежуванню `stg` і `core` та атомарній транзакції `BEGIN ... COMMIT` до `core` потрапили виключно валідовані узгоджені записи.

Контроль Grain та уникнення fan-out при JOIN: Пряме з'єднання `orders` із `order_items` множить суму замовлення `total_amount` на кількість позицій у чеку. Попередня агрегація позицій у CTE `items_by_order` (`GROUP BY order_id`) перед `LEFT JOIN` гарантує співвідношення 1:1 і повну збіжність сум зі звіркою (`difference_rows = 0`, `PASS`).

Віконні функції та індексація: Віконні конструкції `SUM(...) OVER` та `ROW_NUMBER() OVER` дозволяють обчислювати накопичувальні суми та знаходити останнє замовлення клієнта без втрати деталізації рядків. Дослідження багатоколонкового індексу `(merchant_id, created_at)` за допомогою `EXPLAIN (ANALYZE, BUFFERS)` показало перехід від послідовного сканування до індексного доступу зі збереженням 100% ідентичності вихідного набору даних.

Контракт файлового пакета, стійкість до збоїв REST API та ідемпотентність інгестії: Перевірка маніфесту `PACKAGE_INFO.json` та контрольних сум `SHA-256` перед парсингом CSV гарантує відхилення пошкоджених або чужих пакетів. Механізм retry з експоненційним backoff (`1, 2, 4 с`) та підтримкою заголовка `Retry-After` забезпечує надійне проходження 42 сторінок курсорної пагінації попри тимчасові збої `HTTP 429` і `HTTP 500`, а транзакційна публікація з `ON CONFLICT (student_id, source, record_key) DO NOTHING` гарантує атомарний відкат при аварії перед `COMMIT` та нуль дублікатів при повторних запусках.

## Архітектура (v0)
![Architecture](docs/architecture_v0.png)
