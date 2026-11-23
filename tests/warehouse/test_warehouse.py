"""Модульні тести аналітичного сховища CityFlow (SCD Type 1, SCD Type 2, часові межі, дати, ідемпотентність)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from pipeline.warehouse import (
    CustomerDeltaRow,
    CustomerVersion,
    ProductDeltaRow,
    ProductState,
    apply_scd1_products_in_memory,
    apply_scd2_customers_in_memory,
    load_customers_delta_csv,
    load_products_delta_csv,
    normalize_nullable_text,
    order_date_kyiv,
    parse_iso_utc,
    resolve_customer_version_at,
)


def _find_changes_dir() -> Path:
    for candidate in (
        Path("data/changes/S03"),
        Path("lab06/changes/S03"),
        Path(__file__).resolve().parent / "fixtures",
    ):
        if (candidate / "customers_delta.csv").is_file() and (candidate / "products_delta.csv").is_file():
            return candidate
    pytest.skip("S03 delta CSV files not found in data/changes/S03")


def test_order_date_kyiv_timezone_boundary() -> None:
    # 2026-09-01T21:30:00Z у Києві (UTC+3 влітку) — це вже 2026-09-02 00:30:00+03:00
    assert order_date_kyiv("2026-09-01T21:30:00Z") == date(2026, 9, 2)
    assert order_date_kyiv("2026-09-01T20:59:59Z") == date(2026, 9, 1)


def test_nullable_city_normalization() -> None:
    assert normalize_nullable_text(None) is None
    assert normalize_nullable_text("") is None
    assert normalize_nullable_text("   ") is None
    assert normalize_nullable_text(" Lviv ") == "Lviv"


def test_scd1_product_update_idempotency_and_unmatched() -> None:
    products = {
        "S03-P000477": ProductState(
            product_key=101,
            product_id="S03-P000477",
            merchant_id="S03-M00040",
            product_name="Espresso",
            unit_price_uah=Decimal("45.00"),
            is_active=True,
            source_version_at=None,  # -infinity
        )
    }
    ts = parse_iso_utc("2026-09-24T15:00:00Z")
    delta = [
        ProductDeltaRow("S03-P000477", "S03-M00040", "Espresso", Decimal("53.11"), True, ts),
        ProductDeltaRow("S03-P999999", "S03-M00040", "Ghost", Decimal("99.00"), True, ts),
    ]

    first = apply_scd1_products_in_memory(products, delta)
    assert first.products_updated == 1
    assert first.unmatched_products == ["S03-P999999"]
    assert "S03-P999999" not in products
    assert products["S03-P000477"].product_key == 101
    assert products["S03-P000477"].unit_price_uah == Decimal("53.11")
    assert products["S03-P000477"].source_version_at == ts

    # Повтор тієї самої delta — без змін
    repeat = apply_scd1_products_in_memory(products, delta)
    assert repeat.products_updated == 0
    assert repeat.products_unchanged_or_repeat == 1
    assert products["S03-P000477"].product_key == 101

    # Старіша версія не перезаписує новішу
    stale = apply_scd1_products_in_memory(
        products,
        [ProductDeltaRow("S03-P000477", "S03-M00040", "Espresso", Decimal("40.00"), False, ts - timedelta(hours=1))],
    )
    assert stale.products_stale_skipped == 1
    assert products["S03-P000477"].unit_price_uah == Decimal("53.11")
    assert products["S03-P000477"].is_active is True


def test_scd1_conflict_same_timestamp_raises() -> None:
    ts = parse_iso_utc("2026-09-24T15:00:00Z")
    products = {
        "S03-P000477": ProductState(101, "S03-P000477", "S03-M00040", "Espresso", Decimal("53.11"), True, ts)
    }
    with pytest.raises(RuntimeError, match="SCD1_CONFLICT"):
        apply_scd1_products_in_memory(
            products,
            [ProductDeltaRow("S03-P000477", "S03-M00040", "Espresso", Decimal("60.00"), True, ts)],
        )


def test_scd2_customer_city_change_and_three_time_boundaries() -> None:
    versions = [
        CustomerVersion(
            customer_key=1,
            customer_id="S03-C000708",
            full_name="Andrii Kravets",
            city="Lviv",
            valid_from=None,  # -infinity
            valid_to=None,
            is_current=True,
        )
    ]
    change_ts = parse_iso_utc("2026-09-23T06:00:00Z")
    deltas = [CustomerDeltaRow("S03-C000708", "Andrii Kravets", "Kyiv", change_ts)]

    res = apply_scd2_customers_in_memory(versions, deltas)
    assert res.customers_new_versions == 1
    assert len(versions) == 2

    old_v, new_v = versions[0], versions[1]
    assert old_v.customer_key == 1
    assert old_v.city == "Lviv"
    assert old_v.valid_to == change_ts
    assert old_v.is_current is False

    assert new_v.customer_key == 2
    assert new_v.city == "Kyiv"
    assert new_v.valid_from == change_ts
    assert new_v.valid_to is None
    assert new_v.is_current is True

    # Три обов'язкові часові межі [valid_from, valid_to):
    v_before = resolve_customer_version_at(versions, "S03-C000708", change_ts - timedelta(microseconds=1))
    v_exact = resolve_customer_version_at(versions, "S03-C000708", change_ts)
    v_after = resolve_customer_version_at(versions, "S03-C000708", change_ts + timedelta(days=1))

    assert v_before.customer_key == 1 and v_before.city == "Lviv"
    assert v_exact.customer_key == 2 and v_exact.city == "Kyiv"
    assert v_after.customer_key == 2 and v_after.city == "Kyiv"

    # Повтор тієї самої delta не створює нової версії
    repeat = apply_scd2_customers_in_memory(versions, deltas)
    assert repeat.customers_new_versions == 0
    assert repeat.customers_repeat_skipped == 1
    assert len(versions) == 2


def test_scd2_unchanged_city_and_null_comparison() -> None:
    versions = [
        CustomerVersion(1, "S03-C000001", "Test User", None, None, None, True),
        CustomerVersion(2, "S03-C000002", "Test User 2", "Kyiv", None, None, True),
    ]
    ts = parse_iso_utc("2026-09-23T10:00:00Z")
    res = apply_scd2_customers_in_memory(
        versions,
        [
            CustomerDeltaRow("S03-C000001", "Test User", None, ts),
            CustomerDeltaRow("S03-C000002", "Test User 2", "Kyiv", ts),
        ],
    )
    assert res.customers_new_versions == 0
    assert res.customers_unchanged_city == 2
    assert len(versions) == 2


def test_scd2_conflict_unmatched_and_out_of_order() -> None:
    t2 = parse_iso_utc("2026-09-24T00:00:00Z")
    t1 = parse_iso_utc("2026-09-23T00:00:00Z")
    versions = [
        CustomerVersion(1, "S03-C000708", "Andrii Kravets", "Lviv", None, t2, False),
        CustomerVersion(2, "S03-C000708", "Andrii Kravets", "Kyiv", t2, None, True),
    ]

    # Невідомий ID та запис із часом раніше поточної valid_from відкладаються
    res = apply_scd2_customers_in_memory(
        versions,
        [
            CustomerDeltaRow("S03-C999999", "Unknown", "Odesa", t2),
            CustomerDeltaRow("S03-C000708", "Andrii Kravets", "Dnipro", t1),
        ],
    )
    assert res.unmatched_customers == ["S03-C999999"]
    assert len(res.out_of_order_customers) == 1
    assert len(versions) == 2

    # Конфлікт під тією самою версією (customer_id, valid_from) викликає помилку
    with pytest.raises(RuntimeError, match="SCD2_CONFLICT"):
        apply_scd2_customers_in_memory(
            versions,
            [CustomerDeltaRow("S03-C000708", "Andrii Kravets", "Odesa", t2)],
        )


def test_real_s03_delta_csv_loading() -> None:
    changes_dir = _find_changes_dir()
    c_rows = load_customers_delta_csv(changes_dir / "customers_delta.csv")
    p_rows = load_products_delta_csv(changes_dir / "products_delta.csv")

    assert len(c_rows) == 18
    assert len(p_rows) == 30
    assert all(r.customer_id.startswith("S03-C") for r in c_rows)
    assert all(r.product_id.startswith("S03-P") for r in p_rows)
    # Перевірка впорядкування за updated_at зростанням
    assert [r.updated_at for r in c_rows] == sorted(r.updated_at for r in c_rows)
    assert [r.updated_at for r in p_rows] == sorted(r.updated_at for r in p_rows)