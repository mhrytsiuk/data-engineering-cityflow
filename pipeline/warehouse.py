"""Завантажувач аналітичного сховища CityFlow (dwh.*): baseline, SCD Type 1 (dim_product),
SCD Type 2 (dim_customer), транзакційна звірка та ідемпотентні повторні запуски.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KYIV_TZ = ZoneInfo("Europe/Kyiv")


def parse_iso_utc(raw: str | datetime) -> datetime:
    """Парсить ISO-8601 timestamp (включно з наносекундами та суфіксом Z) у UTC timestamptz."""
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc)
    text = str(raw).strip()
    if not text:
        raise ValueError("Empty timestamp value")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Підтримка наносекунд (Python fromisoformat підтримує до 6 знаків після крапки)
    if "." in text:
        head, tail = text.split(".", 1)
        tz_pos = -1
        for sep in ("+", "-"):
            idx = tail.find(sep)
            if idx != -1:
                tz_pos = idx
                break
        if tz_pos != -1:
            frac = tail[:tz_pos][:6]
            tz_part = tail[tz_pos:]
            text = f"{head}.{frac}{tz_part}"
        else:
            text = f"{head}.{tail[:6]}"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError(f"Timestamp must contain timezone offset: {raw!r}")
    return dt.astimezone(timezone.utc)


def order_date_kyiv(created_at: str | datetime) -> date:
    """Повертає календарну дату створення замовлення у часовій зоні Europe/Kyiv."""
    dt = parse_iso_utc(created_at)
    return dt.astimezone(KYIV_TZ).date()


def normalize_nullable_text(value: Any) -> str | None:
    """Порожні рядки трактує як NULL (не вигадує значень на кшталт 'Unknown')."""
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def parse_decimal_2(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw).strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid decimal amount: {raw!r}") from exc


def parse_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in ("true", "t", "1", "yes"):
        return True
    if text in ("false", "f", "0", "no"):
        return False
    raise ValueError(f"Invalid boolean value: {raw!r}")


@dataclass(frozen=True)
class ProductDeltaRow:
    product_id: str
    merchant_id: str
    product_name: str
    unit_price_uah: Decimal
    is_active: bool
    updated_at: datetime


@dataclass(frozen=True)
class CustomerDeltaRow:
    customer_id: str
    full_name: str
    city: str | None
    updated_at: datetime


@dataclass
class ProductState:
    product_key: int
    product_id: str
    merchant_id: str
    product_name: str
    unit_price_uah: Decimal
    is_active: bool
    source_version_at: datetime | None  # None означає '-infinity'


@dataclass
class CustomerVersion:
    customer_key: int
    customer_id: str
    full_name: str
    city: str | None
    valid_from: datetime | None  # None означає '-infinity'
    valid_to: datetime | None    # None означає відкритий інтервал ('infinity')
    is_current: bool


@dataclass
class DeltaApplySummary:
    products_updated: int = 0
    products_unchanged_or_repeat: int = 0
    products_stale_skipped: int = 0
    unmatched_products: list[str] = field(default_factory=list)
    customers_new_versions: int = 0
    customers_unchanged_city: int = 0
    customers_repeat_skipped: int = 0
    unmatched_customers: list[str] = field(default_factory=list)
    out_of_order_customers: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "products_updated": self.products_updated,
            "products_unchanged_or_repeat": self.products_unchanged_or_repeat,
            "products_stale_skipped": self.products_stale_skipped,
            "unmatched_products": self.unmatched_products,
            "customers_new_versions": self.customers_new_versions,
            "customers_unchanged_city": self.customers_unchanged_city,
            "customers_repeat_skipped": self.customers_repeat_skipped,
            "unmatched_customers": self.unmatched_customers,
            "out_of_order_customers": self.out_of_order_customers,
        }


def load_products_delta_csv(path: Path) -> list[ProductDeltaRow]:
    rows: list[ProductDeltaRow] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(
                ProductDeltaRow(
                    product_id=row["product_id"].strip(),
                    merchant_id=row["merchant_id"].strip(),
                    product_name=row["product_name"].strip(),
                    unit_price_uah=parse_decimal_2(row["unit_price_uah"]),
                    is_active=parse_bool(row["is_active"]),
                    updated_at=parse_iso_utc(row["updated_at"]),
                )
            )
    rows.sort(key=lambda r: (r.updated_at, r.product_id))
    return rows


def load_customers_delta_csv(path: Path) -> list[CustomerDeltaRow]:
    rows: list[CustomerDeltaRow] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(
                CustomerDeltaRow(
                    customer_id=row["customer_id"].strip(),
                    full_name=row["full_name"].strip(),
                    city=normalize_nullable_text(row.get("city")),
                    updated_at=parse_iso_utc(row["updated_at"]),
                )
            )
    rows.sort(key=lambda r: (r.updated_at, r.customer_id))
    return rows


# ---------------------------------------------------------------------------
# Чиста доменна логіка SCD Type 1 / Type 2 (для юніт-тестів і перевірки інваріантів)
# ---------------------------------------------------------------------------

def apply_scd1_products_in_memory(
    products_by_id: dict[str, ProductState],
    deltas: list[ProductDeltaRow],
) -> DeltaApplySummary:
    """Застосовує SCD Type 1 до словника товарів у пам'яті з дотриманням версійності source_version_at."""
    summary = DeltaApplySummary()
    for delta in sorted(deltas, key=lambda r: (r.updated_at, r.product_id)):
        current = products_by_id.get(delta.product_id)
        if current is None:
            summary.unmatched_products.append(delta.product_id)
            continue
        if current.source_version_at is None or delta.updated_at > current.source_version_at:
            current.unit_price_uah = delta.unit_price_uah
            current.is_active = delta.is_active
            current.source_version_at = delta.updated_at
            summary.products_updated += 1
        elif delta.updated_at == current.source_version_at:
            if current.unit_price_uah == delta.unit_price_uah and current.is_active == delta.is_active:
                summary.products_unchanged_or_repeat += 1
            else:
                raise RuntimeError(
                    f"SCD1_CONFLICT: product_id={delta.product_id} at {delta.updated_at.isoformat()}"
                )
        else:
            summary.products_stale_skipped += 1
    return summary


def apply_scd2_customers_in_memory(
    versions: list[CustomerVersion],
    deltas: list[CustomerDeltaRow],
) -> DeltaApplySummary:
    """Застосовує SCD Type 2 за зміною city у пам'яті з напіввідкритим інтервалом [valid_from, valid_to)."""
    summary = DeltaApplySummary()
    next_key = max((v.customer_key for v in versions), default=0) + 1

    for delta in sorted(deltas, key=lambda r: (r.updated_at, r.customer_id)):
        cust_versions = [v for v in versions if v.customer_id == delta.customer_id]
        if not cust_versions:
            summary.unmatched_customers.append(delta.customer_id)
            continue

        # Перевірка наявності версії з тим самим (customer_id, valid_from)
        exact = next((v for v in cust_versions if v.valid_from == delta.updated_at), None)
        if exact is not None:
            if exact.city == delta.city:
                summary.customers_repeat_skipped += 1
                continue
            raise RuntimeError(
                f"SCD2_CONFLICT: customer_id={delta.customer_id} valid_from={delta.updated_at.isoformat()} "
                f"existing_city={exact.city!r} new_city={delta.city!r}"
            )

        current = next((v for v in cust_versions if v.is_current), None)
        if current is None:
            raise RuntimeError(f"SCD2_NO_CURRENT_VERSION: customer_id={delta.customer_id}")

        if current.valid_from is not None and delta.updated_at < current.valid_from:
            summary.out_of_order_customers.append(
                {
                    "customer_id": delta.customer_id,
                    "updated_at": delta.updated_at.isoformat(),
                    "current_valid_from": current.valid_from.isoformat(),
                }
            )
            continue

        # IS DISTINCT FROM для nullable city
        if current.city == delta.city:
            summary.customers_unchanged_city += 1
            continue

        # Закриваємо поточну версію і створюємо нову
        current.valid_to = delta.updated_at
        current.is_current = False
        versions.append(
            CustomerVersion(
                customer_key=next_key,
                customer_id=delta.customer_id,
                full_name=delta.full_name or current.full_name,
                city=delta.city,
                valid_from=delta.updated_at,
                valid_to=None,
                is_current=True,
            )
        )
        next_key += 1
        summary.customers_new_versions += 1

    return summary


def resolve_customer_version_at(
    versions: list[CustomerVersion],
    customer_id: str,
    event_ts: datetime,
) -> CustomerVersion:
    """Знаходить рівно одну версію клієнта за правилом [valid_from, valid_to)."""
    ts = parse_iso_utc(event_ts)
    matched = [
        v
        for v in versions
        if v.customer_id == customer_id
        and (v.valid_from is None or ts >= v.valid_from)
        and (v.valid_to is None or ts < v.valid_to)
    ]
    if len(matched) != 1:
        raise RuntimeError(
            f"CUSTOMER_VERSION_MISMATCH: customer_id={customer_id} ts={ts.isoformat()} matches={len(matched)}"
        )
    return matched[0]


# ---------------------------------------------------------------------------
# PostgreSQL реалізація завантаження та звірки DWH
# ---------------------------------------------------------------------------

def _load_baseline_in_tx(cur: Any, publish_ts: datetime) -> dict[str, Any]:
    """Початкове та ідемпотентне повторне завантаження вимірів і фактів із core.* та validated.*."""
    # 1. dim_merchant: додаємо нові та оновлюємо змінені атрибути, зберігаючи merchant_key
    cur.execute(
        """
        INSERT INTO dwh.dim_merchant (merchant_id, merchant_name, city)
        SELECT
            m.merchant_id,
            m.merchant_name,
            NULLIF(BTRIM(m.city), '')
        FROM core.merchants m
        ON CONFLICT (merchant_id) DO UPDATE
           SET merchant_name = EXCLUDED.merchant_name,
               city = EXCLUDED.city
         WHERE dwh.dim_merchant.merchant_name IS DISTINCT FROM EXCLUDED.merchant_name
            OR dwh.dim_merchant.city IS DISTINCT FROM EXCLUDED.city
        """
    )

    # 2. dim_product (SCD Type 1): baseline додає лише відсутні product_id
    #    (не перезаписує новіші версії, застосовані через delta!)
    cur.execute(
        """
        INSERT INTO dwh.dim_product (
            product_id, merchant_id, product_name, unit_price_uah, is_active, source_version_at
        )
        SELECT
            p.product_id,
            p.merchant_id,
            p.product_name,
            p.unit_price_uah,
            p.is_active,
            '-infinity'::timestamptz
        FROM core.products p
        ON CONFLICT (product_id) DO NOTHING
        """
    )

    # 3. dim_customer (SCD Type 2): baseline додає лише відсутніх клієнтів
    #    із навчальним припущенням valid_from = '-infinity', valid_to = NULL, is_current = TRUE
    cur.execute(
        """
        INSERT INTO dwh.dim_customer (
            customer_id, full_name, city, valid_from, valid_to, is_current
        )
        SELECT
            c.customer_id,
            c.full_name,
            NULLIF(BTRIM(c.city), ''),
            '-infinity'::timestamptz,
            NULL,
            TRUE
        FROM core.customers c
        WHERE NOT EXISTS (
            SELECT 1
            FROM dwh.dim_customer dc
            WHERE dc.customer_id = c.customer_id
        )
        """
    )

    # 4. dim_date: покриває весь діапазон календарних дат прийнятих замовлень у Europe/Kyiv
    cur.execute(
        """
        WITH bounds AS (
            SELECT
                MIN((created_at AT TIME ZONE 'Europe/Kyiv')::date) AS min_d,
                MAX((created_at AT TIME ZONE 'Europe/Kyiv')::date) AS max_d
            FROM validated.orders
        ),
        series AS (
            SELECT gs::date AS d
            FROM bounds,
                 LATERAL generate_series(bounds.min_d, bounds.max_d, interval '1 day') AS gs
            WHERE bounds.min_d IS NOT NULL
        )
        INSERT INTO dwh.dim_date (date_key, calendar_year, calendar_month, day_of_month)
        SELECT
            d,
            EXTRACT(YEAR FROM d)::integer,
            EXTRACT(MONTH FROM d)::integer,
            EXTRACT(DAY FROM d)::integer
        FROM series
        ON CONFLICT (date_key) DO NOTHING
        """
    )

    # 5. Захист від тихого зникнення раніше прийнятих ключів у новому повному snapshot
    cur.execute(
        """
        SELECT f.order_id
        FROM dwh.fact_orders f
        LEFT JOIN validated.orders v ON v.order_id = f.order_id
        WHERE v.order_id IS NULL
        ORDER BY f.order_id
        LIMIT 10
        """
    )
    missing_orders = [r[0] for r in cur.fetchall()]
    if missing_orders:
        raise RuntimeError(
            f"MISSING_ACCEPTED_ORDERS: previously published orders absent from validated.orders: {missing_orders}"
        )

    cur.execute(
        """
        SELECT fi.order_item_id
        FROM dwh.fact_order_items fi
        LEFT JOIN validated.order_items vi ON vi.order_item_id = fi.order_item_id
        WHERE vi.order_item_id IS NULL
        ORDER BY fi.order_item_id
        LIMIT 10
        """
    )
    missing_items = [r[0] for r in cur.fetchall()]
    if missing_items:
        raise RuntimeError(
            f"MISSING_ACCEPTED_ITEMS: previously published items absent from validated.order_items: {missing_items}"
        )

    # 6. Перевірка точної (1:1) відповідності surrogate keys для кожного validated.orders
    cur.execute(
        """
        WITH matches AS (
            SELECT
                o.order_id,
                o.customer_id,
                o.merchant_id,
                COUNT(DISTINCT c.customer_key) AS cust_matches,
                COUNT(DISTINCT m.merchant_key) AS merch_matches,
                COUNT(DISTINCT d.date_key) AS date_matches
            FROM validated.orders o
            LEFT JOIN dwh.dim_customer c
              ON o.customer_id = c.customer_id
             AND o.created_at >= c.valid_from
             AND o.created_at < COALESCE(c.valid_to, 'infinity'::timestamptz)
            LEFT JOIN dwh.dim_merchant m
              ON o.merchant_id = m.merchant_id
            LEFT JOIN dwh.dim_date d
              ON d.date_key = (o.created_at AT TIME ZONE 'Europe/Kyiv')::date
            GROUP BY o.order_id, o.customer_id, o.merchant_id
        )
        SELECT order_id, customer_id, merchant_id, cust_matches, merch_matches, date_matches
        FROM matches
        WHERE cust_matches <> 1 OR merch_matches <> 1 OR date_matches <> 1
        ORDER BY order_id
        LIMIT 10
        """
    )
    bad_order_matches = cur.fetchall()
    if bad_order_matches:
        raise RuntimeError(f"UNRESOLVED_ORDER_DIMENSIONS: {bad_order_matches}")

    # 7. UPSERT fact_orders: warehouse_modified_at змінюється лише при реальній зміні бізнес-стану або версії клієнта
    cur.execute(
        """
        INSERT INTO dwh.fact_orders (
            order_id, customer_key, merchant_key, date_key, created_at,
            order_status, currency, subtotal_amount, delivery_fee,
            discount_amount, total_amount, warehouse_modified_at
        )
        SELECT
            o.order_id,
            c.customer_key,
            m.merchant_key,
            (o.created_at AT TIME ZONE 'Europe/Kyiv')::date,
            o.created_at,
            o.order_status,
            o.currency,
            o.subtotal_amount,
            o.delivery_fee,
            o.discount_amount,
            o.total_amount,
            %s
        FROM validated.orders o
        JOIN dwh.dim_customer c
          ON o.customer_id = c.customer_id
         AND o.created_at >= c.valid_from
         AND o.created_at < COALESCE(c.valid_to, 'infinity'::timestamptz)
        JOIN dwh.dim_merchant m
          ON o.merchant_id = m.merchant_id
        ON CONFLICT (order_id) DO UPDATE
           SET customer_key = EXCLUDED.customer_key,
               merchant_key = EXCLUDED.merchant_key,
               date_key = EXCLUDED.date_key,
               created_at = EXCLUDED.created_at,
               order_status = EXCLUDED.order_status,
               currency = EXCLUDED.currency,
               subtotal_amount = EXCLUDED.subtotal_amount,
               delivery_fee = EXCLUDED.delivery_fee,
               discount_amount = EXCLUDED.discount_amount,
               total_amount = EXCLUDED.total_amount,
               warehouse_modified_at = EXCLUDED.warehouse_modified_at
         WHERE (
                   dwh.fact_orders.customer_key,
                   dwh.fact_orders.merchant_key,
                   dwh.fact_orders.date_key,
                   dwh.fact_orders.created_at,
                   dwh.fact_orders.order_status,
                   dwh.fact_orders.currency,
                   dwh.fact_orders.subtotal_amount,
                   dwh.fact_orders.delivery_fee,
                   dwh.fact_orders.discount_amount,
                   dwh.fact_orders.total_amount
               ) IS DISTINCT FROM (
                   EXCLUDED.customer_key,
                   EXCLUDED.merchant_key,
                   EXCLUDED.date_key,
                   EXCLUDED.created_at,
                   EXCLUDED.order_status,
                   EXCLUDED.currency,
                   EXCLUDED.subtotal_amount,
                   EXCLUDED.delivery_fee,
                   EXCLUDED.discount_amount,
                   EXCLUDED.total_amount
               )
        """,
        (publish_ts,),
    )
    orders_affected = cur.rowcount

    # 8. Перевірка точної відповідності батьків для validated.order_items
    cur.execute(
        """
        SELECT oi.order_item_id, oi.order_id, oi.product_id
        FROM validated.order_items oi
        LEFT JOIN dwh.fact_orders fo ON fo.order_id = oi.order_id
        LEFT JOIN dwh.dim_product dp ON dp.product_id = oi.product_id
        WHERE fo.order_id IS NULL OR dp.product_key IS NULL
        ORDER BY oi.order_item_id
        LIMIT 10
        """
    )
    bad_item_matches = cur.fetchall()
    if bad_item_matches:
        raise RuntimeError(f"UNRESOLVED_ITEM_DIMENSIONS: {bad_item_matches}")

    # 9. UPSERT fact_order_items (історичні unit_price / line_total завжди беруться з validated.order_items)
    cur.execute(
        """
        INSERT INTO dwh.fact_order_items (
            order_item_id, order_id, customer_key, merchant_key, product_key,
            date_key, quantity, unit_price, line_discount, line_total, currency
        )
        SELECT
            oi.order_item_id,
            fo.order_id,
            fo.customer_key,
            fo.merchant_key,
            dp.product_key,
            fo.date_key,
            oi.quantity,
            oi.unit_price,
            oi.line_discount,
            oi.line_total,
            oi.currency
        FROM validated.order_items oi
        JOIN dwh.fact_orders fo ON fo.order_id = oi.order_id
        JOIN dwh.dim_product dp ON dp.product_id = oi.product_id
        ON CONFLICT (order_item_id) DO UPDATE
           SET order_id = EXCLUDED.order_id,
               customer_key = EXCLUDED.customer_key,
               merchant_key = EXCLUDED.merchant_key,
               product_key = EXCLUDED.product_key,
               date_key = EXCLUDED.date_key,
               quantity = EXCLUDED.quantity,
               unit_price = EXCLUDED.unit_price,
               line_discount = EXCLUDED.line_discount,
               line_total = EXCLUDED.line_total,
               currency = EXCLUDED.currency
         WHERE (
                   dwh.fact_order_items.order_id,
                   dwh.fact_order_items.customer_key,
                   dwh.fact_order_items.merchant_key,
                   dwh.fact_order_items.product_key,
                   dwh.fact_order_items.date_key,
                   dwh.fact_order_items.quantity,
                   dwh.fact_order_items.unit_price,
                   dwh.fact_order_items.line_discount,
                   dwh.fact_order_items.line_total,
                   dwh.fact_order_items.currency
               ) IS DISTINCT FROM (
                   EXCLUDED.order_id,
                   EXCLUDED.customer_key,
                   EXCLUDED.merchant_key,
                   EXCLUDED.product_key,
                   EXCLUDED.date_key,
                   EXCLUDED.quantity,
                   EXCLUDED.unit_price,
                   EXCLUDED.line_discount,
                   EXCLUDED.line_total,
                   EXCLUDED.currency
               )
        """
    )
    items_affected = cur.rowcount

    return {
        "orders_upserted_or_modified": orders_affected,
        "items_upserted_or_modified": items_affected,
    }


def _apply_deltas_in_tx(cur: Any, changes_dir: Path) -> DeltaApplySummary:
    """Застосовує products_delta.csv (SCD Type 1) та customers_delta.csv (SCD Type 2) у межах транзакції."""
    summary = DeltaApplySummary()

    products_csv = changes_dir / "products_delta.csv"
    customers_csv = changes_dir / "customers_delta.csv"
    if not products_csv.is_file() or not customers_csv.is_file():
        raise FileNotFoundError(
            f"Expected products_delta.csv and customers_delta.csv in {changes_dir}"
        )

    # --- SCD Type 1: dim_product ---
    for p_delta in load_products_delta_csv(products_csv):
        cur.execute(
            """
            SELECT
                product_key,
                unit_price_uah,
                is_active,
                (%s::timestamptz > source_version_at) AS is_newer,
                (%s::timestamptz = source_version_at) AS is_same_ts
            FROM dwh.dim_product
            WHERE product_id = %s
            FOR UPDATE
            """,
            (p_delta.updated_at, p_delta.updated_at, p_delta.product_id),
        )
        row = cur.fetchone()
        if row is None:
            summary.unmatched_products.append(p_delta.product_id)
            continue

        _pkey, cur_price, cur_active, is_newer, is_same_ts = row

        if is_newer:
            cur.execute(
                """
                UPDATE dwh.dim_product
                   SET unit_price_uah = %s,
                       is_active = %s,
                       source_version_at = %s
                 WHERE product_id = %s
                """,
                (p_delta.unit_price_uah, p_delta.is_active, p_delta.updated_at, p_delta.product_id),
            )
            summary.products_updated += 1
        elif is_same_ts:
            if Decimal(str(cur_price)).quantize(Decimal("0.01")) == p_delta.unit_price_uah and bool(cur_active) == p_delta.is_active:
                summary.products_unchanged_or_repeat += 1
            else:
                raise RuntimeError(
                    f"SCD1_CONFLICT: product_id={p_delta.product_id} at {p_delta.updated_at.isoformat()}"
                )
        else:
            summary.products_stale_skipped += 1

    # --- SCD Type 2: dim_customer ---
    for c_delta in load_customers_delta_csv(customers_csv):
        cur.execute(
            """
            SELECT customer_key, city
            FROM dwh.dim_customer
            WHERE customer_id = %s AND valid_from = %s
            """,
            (c_delta.customer_id, c_delta.updated_at),
        )
        exact = cur.fetchone()
        if exact is not None:
            _ekey, existing_city = exact
            if existing_city == c_delta.city:
                summary.customers_repeat_skipped += 1
                continue
            raise RuntimeError(
                f"SCD2_CONFLICT: customer_id={c_delta.customer_id} valid_from={c_delta.updated_at.isoformat()} "
                f"existing_city={existing_city!r} new_city={c_delta.city!r}"
            )

        cur.execute(
            """
            SELECT customer_key, full_name, city,
                   (%s::timestamptz < valid_from) AS is_out_of_order,
                   valid_from::text AS valid_from_txt
            FROM dwh.dim_customer
            WHERE customer_id = %s AND is_current = TRUE
            FOR UPDATE
            """,
            (c_delta.updated_at, c_delta.customer_id),
        )
        curr = cur.fetchone()
        if curr is None:
            summary.unmatched_customers.append(c_delta.customer_id)
            continue

        cur_key, cur_full_name, cur_city, is_out_of_order, valid_from_txt = curr
        if is_out_of_order:
            summary.out_of_order_customers.append(
                {
                    "customer_id": c_delta.customer_id,
                    "updated_at": c_delta.updated_at.isoformat(),
                    "current_valid_from": valid_from_txt,
                }
            )
            continue

        # Порівняння nullable city (аналог IS DISTINCT FROM)
        if cur_city == c_delta.city:
            summary.customers_unchanged_city += 1
            continue

        # Закриваємо поточну версію перед вставкою нової (дотримуємося ux_dim_customer_current)
        cur.execute(
            """
            UPDATE dwh.dim_customer
               SET valid_to = %s,
                   is_current = FALSE
             WHERE customer_key = %s
            """,
            (c_delta.updated_at, cur_key),
        )
        cur.execute(
            """
            INSERT INTO dwh.dim_customer (
                customer_id, full_name, city, valid_from, valid_to, is_current
            )
            VALUES (%s, %s, %s, %s, NULL, TRUE)
            """,
            (
                c_delta.customer_id,
                c_delta.full_name or cur_full_name,
                c_delta.city,
                c_delta.updated_at,
            ),
        )
        summary.customers_new_versions += 1

    return summary


def _reconcile_in_tx(cur: Any) -> dict[str, Any]:
    """Виконує повну транзакційну звірку ключів, валютних сум та трьох інваріантів SCD."""
    cur.execute(
        """
        SELECT
            (SELECT count(*) FROM (SELECT order_id FROM validated.orders EXCEPT ALL SELECT order_id FROM dwh.fact_orders) a),
            (SELECT count(*) FROM (SELECT order_id FROM dwh.fact_orders EXCEPT ALL SELECT order_id FROM validated.orders) b),
            (SELECT count(*) FROM (SELECT order_item_id FROM validated.order_items EXCEPT ALL SELECT order_item_id FROM dwh.fact_order_items) c),
            (SELECT count(*) FROM (SELECT order_item_id FROM dwh.fact_order_items EXCEPT ALL SELECT order_item_id FROM validated.order_items) d)
        """
    )
    o_v_minus_d, o_d_minus_v, i_v_minus_d, i_d_minus_v = cur.fetchone()
    if any(x != 0 for x in (o_v_minus_d, o_d_minus_v, i_v_minus_d, i_d_minus_v)):
        raise RuntimeError(
            f"RECONCILIATION_KEY_MISMATCH: orders=({o_v_minus_d},{o_d_minus_v}) items=({i_v_minus_d},{i_d_minus_v})"
        )

    cur.execute(
        """
        SELECT
            COALESCE(v.currency, f.currency) AS currency,
            COALESCE(v.sum_total, 0)::text,
            COALESCE(f.sum_total, 0)::text,
            (COALESCE(f.sum_total, 0) - COALESCE(v.sum_total, 0))::text
        FROM (SELECT currency, SUM(total_amount) AS sum_total FROM validated.orders GROUP BY currency) v
        FULL OUTER JOIN (SELECT currency, SUM(total_amount) AS sum_total FROM dwh.fact_orders GROUP BY currency) f
          ON v.currency = f.currency
        ORDER BY currency
        """
    )
    orders_currency = [
        {"currency": r[0], "validated": r[1], "dwh": r[2], "diff": r[3]}
        for r in cur.fetchall()
    ]
    if any(Decimal(x["diff"]) != 0 for x in orders_currency):
        raise RuntimeError(f"RECONCILIATION_ORDER_SUM_MISMATCH: {orders_currency}")

    cur.execute(
        """
        SELECT
            COALESCE(v.currency, f.currency) AS currency,
            COALESCE(v.sum_line, 0)::text,
            COALESCE(f.sum_line, 0)::text,
            (COALESCE(f.sum_line, 0) - COALESCE(v.sum_line, 0))::text
        FROM (SELECT currency, SUM(line_total) AS sum_line FROM validated.order_items GROUP BY currency) v
        FULL OUTER JOIN (SELECT currency, SUM(line_total) AS sum_line FROM dwh.fact_order_items GROUP BY currency) f
          ON v.currency = f.currency
        ORDER BY currency
        """
    )
    items_currency = [
        {"currency": r[0], "validated": r[1], "dwh": r[2], "diff": r[3]}
        for r in cur.fetchall()
    ]
    if any(Decimal(x["diff"]) != 0 for x in items_currency):
        raise RuntimeError(f"RECONCILIATION_ITEM_SUM_MISMATCH: {items_currency}")

    # Перевірка 3 інваріантів SCD (аналог 01_check_scd.sql)
    cur.execute(
        """
        SELECT count(*) FROM (
            SELECT customer_id FROM dwh.dim_customer
            GROUP BY customer_id HAVING count(*) FILTER (WHERE is_current) <> 1
        ) q1
        """
    )
    scd1_err = cur.fetchone()[0]

    cur.execute(
        """
        SELECT count(*) FROM (
            SELECT a.customer_id
            FROM dwh.dim_customer a
            JOIN dwh.dim_customer b
              ON a.customer_id = b.customer_id AND a.customer_key < b.customer_key
            WHERE a.valid_from < COALESCE(b.valid_to, 'infinity'::timestamptz)
              AND b.valid_from < COALESCE(a.valid_to, 'infinity'::timestamptz)
        ) q2
        """
    )
    scd2_err = cur.fetchone()[0]

    cur.execute(
        """
        SELECT count(*) FROM (
            SELECT f.order_id
            FROM dwh.fact_orders f
            JOIN dwh.dim_customer c USING (customer_key)
            WHERE NOT (f.created_at >= c.valid_from AND f.created_at < COALESCE(c.valid_to, 'infinity'::timestamptz))
        ) q3
        """
    )
    scd3_err = cur.fetchone()[0]

    if scd1_err or scd2_err or scd3_err:
        raise RuntimeError(
            f"SCD_INVARIANT_FAILED: single_current={scd1_err}, overlap={scd2_err}, fact_bounds={scd3_err}"
        )

    cur.execute(
        """
        SELECT
            (SELECT count(*) FROM dwh.dim_customer),
            (SELECT count(*) FROM dwh.dim_customer WHERE is_current),
            (SELECT count(*) FROM dwh.dim_merchant),
            (SELECT count(*) FROM dwh.dim_product),
            (SELECT count(*) FROM dwh.dim_date),
            (SELECT count(*) FROM dwh.fact_orders),
            (SELECT count(*) FROM dwh.fact_order_items)
        """
    )
    c_all, c_curr, m_cnt, p_cnt, d_cnt, fo_cnt, foi_cnt = cur.fetchone()

    return {
        "counts": {
            "dim_customer_versions": c_all,
            "dim_customer_current": c_curr,
            "dim_merchant": m_cnt,
            "dim_product": p_cnt,
            "dim_date": d_cnt,
            "fact_orders": fo_cnt,
            "fact_order_items": foi_cnt,
        },
        "keys_except_all_zero": True,
        "orders_currency": orders_currency,
        "items_currency": items_currency,
        "scd_checks": {
            "single_current_violations": scd1_err,
            "interval_overlap_violations": scd2_err,
            "fact_interval_violations": scd3_err,
            "all_passed": True,
        },
    }


def run_warehouse_load(
    dsn: str,
    *,
    student_id: str,
    changes_dir: Path | None = None,
) -> dict[str, Any]:
    """Головна транзакційна точка входу для `python -m pipeline warehouse`."""
    from .db import connect_autocommit

    publish_ts = datetime.now(timezone.utc)
    mode = "delta" if changes_dir is not None else "baseline"

    with connect_autocommit(dsn) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                # 1. Гарантуємо наявність baseline-вимірів та фактів (ідемпотентно)
                baseline_stats = _load_baseline_in_tx(cur, publish_ts)
                delta_summary: dict[str, Any] | None = None
                if changes_dir is not None:
                    delta_obj = _apply_deltas_in_tx(cur, changes_dir)
                    delta_summary = delta_obj.to_dict()
                    # Якщо з'явилися нові версії клієнтів, повторно синхронізуємо прив'язку фактів
                    if delta_obj.customers_new_versions > 0:
                        baseline_stats = _load_baseline_in_tx(cur, publish_ts)
                reconciliation = _reconcile_in_tx(cur)

    return {
        "student_id": student_id,
        "mode": mode,
        "executed_at_utc": publish_ts.isoformat(),
        "changes_dir": str(changes_dir) if changes_dir is not None else None,
        "baseline_stats": baseline_stats,
        "delta_summary": delta_summary,
        "reconciliation": reconciliation,
    }