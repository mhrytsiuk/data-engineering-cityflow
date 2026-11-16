# Модуль автоматизованого контролю якості даних (Data Quality) — результати

student_id: `S03`  
Посилання на PR: `https://github.com/unknownpanic/data-engineering-cityflow-S03/pull/5`  
Версії Python / PostgreSQL / залежностей: Python 3.12+ / PostgreSQL 16+ / `psycopg[binary]==3.2.10`, `pytest>=8,<10`  
Каталог робочого експорту / contract_version / reference_time: `runtime/source_01` / `cityflow_v1` (`contracts/cityflow_v1.json`) / `2026-09-08T08:00:00Z` (та `2026-10-15T08:00:00Z` для перевірки свіжості `Q10`)  
Команди запуску (без паролів):
```bash
python tools/quality/run_sql.py sql/quality/00_schema.sql
python tools/quality/export_sources.py --student S03 --out runtime/source_01
python tools/quality/inspect_export.py runtime/source_01
python -m pytest -q tests/quality
python -m pipeline quality --student S03 --input runtime/source_01 --reference-time 2026-09-08T08:00:00Z
python -m pipeline quality --student S03 --input runtime/source_01 --reference-time 2026-10-15T08:00:00Z --dry-run
python -m pipeline quality --student S03 --input runtime/source_01 --reference-time 2026-09-08T08:00:00Z --fail-before-commit
```

## 1. Контрольні тести — події з фікстур `tests/quality/fixtures/`

| Сценарій | Очікування | Фактично | Доказ |
|---|---|---|---|
| `normal` | 24 accepted / 0 rejected / 0 duplicate | 24 accepted / 0 rejected / 0 duplicate (`status = PASS`) | `pytest -q tests/quality -k normal` (`12 passed`) |
| `mixed` | 23 accepted / 6 rejected / 1 duplicate | 23 accepted / 6 rejected / 1 duplicate (`status = PASS`) | Відхилено: `None` (`Q02`), `BADTYPE` (`Q08`), `ORPHAN` (`Q07`), `BADTIME` (`Q03`) та обидві копії конфлікту `S03-EVT000050489` (`Q06`); дублікат: `S03-EVT000050422` (`Q05`) |
| `incomplete` | `BLOCKED`; публікації немає | `BLOCKED` (`input = 23`, `expected = 24`, `publish = false`, код виходу `1`) | Спрацювало правило `Q01` (`EXPECTED_EVENT_ROWS_MISMATCH: expected 24, got 23`) |
| Зміна порядку `mixed` | Ті самі кількості та accepted `event_id` | 23 accepted / 6 rejected / 1 duplicate; множина з 23 `event_id` 100% ідентична | Параметризований тест `test_mixed_fixture_permutation_invariance` для 5 випадкових перестановок (`seed = 1, 7, 42, 99, 2026`) |

## 2. Робочий CityFlow — фактичні кількості

| Сутність | input | accepted | rejected | duplicate | Чи збігається баланс? |
|---|---:|---:|---:|---:|---|
| `orders` (`core_orders` + `api_orders`) | 6004 (5996 + 8) | 6004 | 0 | 0 | Так (`6004 = 6004 + 0 + 0`) |
| `items` (`core_order_items`) | 13735 | 13735 | 0 | 0 | Так (`13735 = 13735 + 0 + 0`) |
| `events` (`ingestion_stg.delivery_events`) | 24 | 24 | 0 | 0 | Так (`24 = 24 + 0 + 0`) |

Для кожної сутності виконується інваріант: `input = accepted + rejected + duplicate`.  
Попередження (`Q09`, `Q10`) фіксуються у `quality.rule_results` і визначають статус `WARN`, але не створюють четвертого класу рядків.

## 3. Публікація та відновлення

| Сценарій | run_id / статус | Стан `validated` до / після | Доказ |
|---|---|---|---|
| Основний запуск (`2026-09-08T08:00:00Z`) | `PASS` (або `WARN` за `Q09`) | До: `0` рядків; Після: `orders = 6004`, `order_items = 13735`, `delivery_events = 24` | Усі три таблиці `validated.*` заповнені в одній транзакції з поточним `quality_run_id` |
| Повтор того самого експорту | Новий `run_id`, статус `PASS` / `WARN` | До і після: `orders = 6004`, `order_items = 13735`, `delivery_events = 24` | Оновився `quality_run_id` та додано новий запис у `quality.runs`; бізнес-ключі й суми за валютами незмінні |
| Неповний export (`Q01`) | Статус `BLOCKED` (код виходу `1`) | Без змін (`orders = 6004`, `order_items = 13735`, `delivery_events = 24`) | Спрацював гейт `Q01`; у `quality.runs` записано `BLOCKED`, таблиці `validated.*` не перезаписані |
| Збій перед `COMMIT` (`--fail-before-commit`) | Статус `FAILED` (`error_code = INJECTED_BEFORE_COMMIT`, код виходу `1`) | Без змін (попередній snapshot `validated.*` повністю збережено) | Транзакція вставки/оновлення `validated.*` автоматично відкотилася (`ROLLBACK`), статус `FAILED` збережено окремим з'єднанням |
| Повтор після збою | Новий `run_id`, статус `PASS` / `WARN` | Відновлено актуальний `quality_run_id` при незмінних `6004 / 13735 / 24` записах | Повторний запуск пройшов успішно, історія `quality.runs` містить обидві спроби (`FAILED` і `PASS`/`WARN`) |

### Приклад запису `quarantine.records` (зі сценарію `mixed`)

```json
{
  "source_name": "events",
  "source_row_no": 27,
  "business_key": "S03-LAB05-BADTYPE",
  "decision": "rejected",
  "reason_codes": ["EVENT_TYPE_NOT_ALLOWED"],
  "payload": {
    "event_id": "S03-LAB05-BADTYPE",
    "delivery_id": "S03-D0006001",
    "order_id": "S03-O0006001",
    "event_type": "teleported",
    "occurred_at": "2026-09-01T08:35:04Z",
    "available_at": "2026-09-01T08:52:04Z",
    "courier_id": "S03-CR00090",
    "service_zone_id": "DNI-Z02",
    "sequence_no": 7,
    "payload_version": 1,
    "source": "cityflow_delivery_service",
    "source_batch_id": "batch_01_normal"
  }
}
```

### Перевірка правила свіжості `Q10` для двох моментів `reference_time`

- При `reference_time = 2026-09-08T08:00:00Z`: максимальний `available_at` серед подій доставки дорівнює `2026-09-01T12:29:15Z`, вік даних становить **`6.81` доби** (`<= 10` днів) → `Q10.failed_count = 0`.
- При `reference_time = 2026-10-15T08:00:00Z`: вік тих самих подій відносно точки перевірки становить **`43.81` доби** (`> 10` днів) → `Q10.failed_count = 1`, статус запуску змінюється на **`WARN`** без перезапису часових міток подій.

## 4. Висновок

Розроблено формалізований контракт якості даних `contracts/cityflow_v1.json` (правила `Q01`–`Q10`) та програмний конвеєр `pipeline` для класифікації записів на `accepted`, `rejected` і `duplicate` зі збереженням журналу рішень у `quality.*`, ізоляцією невалідних записів у `quarantine.records` і транзакційною публікацією перевіреного зрізу у `validated.*`. Доведено, що конфліктні дублікати за ключем відхиляються цілою групою (`Q06`), відхилення батьківського замовлення каскадно маркує дочірні записи кодом `PARENT_REJECTED` (`Q07`), а неповний експорт (`Q01`) або аварійний збій перед `COMMIT` не пошкоджують і не затирають попередній валідний стан таблиць `validated.*`. Водночас автоматизовані технічні та доменні перевірки не замінюють бізнес-аудиту первинних процесів (наприклад, `Q09` виявляє розбіжність у формулі чека як `WARN`, але не може самостійно визначити, яке саме поле — знижка, доставка чи підсумок — було введено з помилкою у зовнішній системі).

