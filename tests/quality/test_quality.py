"""Модульні автотести автоматизованого контролю якості даних CityFlow."""

from __future__ import annotations

import copy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random

import pytest

from pipeline.validator import (
    load_contract,
    validate_export_directory,
    validate_fixture_data,
    validate_fixture_file,
)


def _resolve_fixtures_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    for candidate in (
        Path(__file__).resolve().parent / "fixtures",
        root / "tests" / "quality" / "fixtures",
        root / "lab05" / "test_data" / "S03",
    ):
        if candidate.is_dir() and (candidate / "normal.json").is_file():
            return candidate
    return Path(__file__).resolve().parent / "fixtures"


TEST_DATA_DIR = _resolve_fixtures_dir()


def _load_s03_json(name: str) -> dict:
    return json.loads((TEST_DATA_DIR / name).read_text(encoding="utf-8"))


def test_contract_has_all_ten_rules():
    contract = load_contract()
    assert contract["contract_version"] == "cityflow_v1"
    rule_ids = [r["rule_id"] for r in contract["rules"]]
    assert rule_ids == [f"Q{i:02d}" for i in range(1, 11)]


def test_normal_fixture_matches_expected():
    expected = _load_s03_json("expected.json")["normal"]
    result = validate_fixture_file(TEST_DATA_DIR / "normal.json")

    assert result.status == "PASS"
    assert result.publish is False
    assert result.entity_summary["events"] == expected
    assert result.entity_summary["orders"] == {
        "input": 8,
        "accepted": 8,
        "rejected": 0,
        "duplicate": 0,
    }
    assert len(result.quarantine_records) == 0


def test_mixed_fixture_matches_expected_and_reasons():
    expected = _load_s03_json("expected.json")["mixed"]
    result = validate_fixture_file(TEST_DATA_DIR / "mixed.json")

    ev_summary = result.entity_summary["events"]
    assert ev_summary == expected
    assert ev_summary["input"] == ev_summary["accepted"] + ev_summary["rejected"] + ev_summary["duplicate"]

    event_decisions = [d for d in result.decisions if d.source_name == "events"]
    rejected_events = [d for d in event_decisions if d.decision == "rejected"]
    duplicate_events = [d for d in event_decisions if d.decision == "duplicate"]

    assert len(rejected_events) == 6
    assert len(duplicate_events) == 1
    assert duplicate_events[0].business_key == "S03-EVT000050422"
    assert "EXACT_DUPLICATE" in duplicate_events[0].reason_codes

    conflict_rows = [d for d in rejected_events if d.business_key == "S03-EVT000050489"]
    assert len(conflict_rows) == 2
    assert all("CONFLICTING_DUPLICATE_KEY" in d.reason_codes for d in conflict_rows)

    by_key = {d.business_key: d for d in rejected_events if d.business_key != "S03-EVT000050489"}
    assert None in by_key and "MISSING_OR_INVALID_ID" in by_key[None].reason_codes
    dim_keys = set(by_key.keys()) - {None}
    for k in dim_keys:
        reasons = by_key[k].reason_codes
        assert any(
            code in reasons
            for code in ("EVENT_TYPE_NOT_ALLOWED", "ORDER_NOT_FOUND", "INVALID_TIMESTAMP")
        )


@pytest.mark.parametrize("seed", [1, 7, 42, 99, 2026])
def test_mixed_fixture_permutation_invariance(seed: int):
    """Зміна порядку масиву events не змінює кількості та набір прийнятих event_id."""
    baseline = validate_fixture_file(TEST_DATA_DIR / "mixed.json")
    baseline_accepted_ids = {e["event_id"] for e in baseline.accepted_events}

    mixed_data = _load_s03_json("mixed.json")
    rng = random.Random(seed)
    shuffled_events = list(mixed_data["events"])
    rng.shuffle(shuffled_events)
    mixed_data["events"] = shuffled_events

    permuted = validate_fixture_data(mixed_data)
    permuted_accepted_ids = {e["event_id"] for e in permuted.accepted_events}

    assert permuted.entity_summary["events"] == baseline.entity_summary["events"]
    assert permuted_accepted_ids == baseline_accepted_ids
    assert len(permuted_accepted_ids) == 23


def test_incomplete_fixture_is_blocked():
    expected = _load_s03_json("expected.json")["incomplete"]
    result = validate_fixture_file(TEST_DATA_DIR / "incomplete.json")

    assert result.entity_summary["events"]["input"] == expected["input"]
    assert result.status == expected["batch_status"] == "BLOCKED"
    assert result.publish is expected["publish"] is False
    assert result.rule_results["Q01"].failed_count >= 1


def test_parent_rejected_propagates_to_items_and_events():
    fixture = _load_s03_json("normal.json")
    fixture["orders"][0]["currency"] = "PLN"
    fixture["order_items"] = [
        {
            "order_item_id": "S03-I0099001",
            "order_id": "S03-O0006001",
            "product_id": "S03-P000101",
            "quantity": 2,
            "unit_price": "100.00",
            "line_discount": "0.00",
            "line_total": "200.00",
            "currency": "UAH",
        }
    ]

    result = validate_fixture_data(fixture)
    item_dec = [d for d in result.decisions if d.source_name == "items"][0]
    assert item_dec.decision == "rejected"
    assert "PARENT_REJECTED" in item_dec.reason_codes

    child_events = [
        d for d in result.decisions if d.source_name == "events" and d.raw_payload.get("order_id") == "S03-O0006001"
    ]
    assert len(child_events) == 3
    assert all(d.decision == "rejected" and "PARENT_REJECTED" in d.reason_codes for d in child_events)


def test_q09_amount_warning_and_q10_freshness_warning():
    fixture = _load_s03_json("normal.json")
    fixture["orders"][0]["total_amount"] = "373.97"

    res_fresh = validate_fixture_data(fixture, reference_time="2026-09-08T08:00:00Z")
    assert res_fresh.status == "WARN"
    assert res_fresh.rule_results["Q09"].failed_count == 1
    assert res_fresh.rule_results["Q10"].failed_count == 0
    order_01 = next(o for o in res_fresh.accepted_orders if o["order_id"] == "S03-O0006001")
    assert order_01["total_amount"] == Decimal("373.97")

    normal_clean = _load_s03_json("normal.json")
    res_stale = validate_fixture_data(normal_clean, reference_time="2026-10-15T08:00:00Z")
    assert res_stale.status == "WARN"
    assert res_stale.rule_results["Q09"].failed_count == 0
    assert res_stale.rule_results["Q10"].failed_count == 1


def test_export_directory_q01_and_cross_source_dedup(tmp_path: Path):
    normal = _load_s03_json("normal.json")
    customers = [{"customer_id": o["customer_id"], "full_name": "Test"} for o in normal["orders"]]
    merchants = [{"merchant_id": o["merchant_id"], "merchant_name": "Shop"} for o in normal["orders"]]
    products = [{"product_id": "S03-P000001", "merchant_id": "S03-M00010", "unit_price_uah": "50.00"}]
    items = [
        {
            "order_item_id": "S03-I0000001",
            "order_id": "S03-O0006001",
            "product_id": "S03-P000001",
            "quantity": 1,
            "unit_price": "297.83",
            "line_discount": "0.00",
            "line_total": "297.83",
            "currency": "UAH",
        }
    ]
    core_orders = [copy.deepcopy(normal["orders"][0])]
    core_orders[0]["discount_amount"] = "0.00"
    api_orders = [{**copy.deepcopy(o), "_source_key": o["order_id"]} for o in normal["orders"]]
    events = [{**copy.deepcopy(e), "_source_key": e["event_id"]} for e in normal["events"]]

    files_payload = {
        "core_customers.json": customers,
        "core_merchants.json": merchants,
        "core_products.json": products,
        "core_orders.json": core_orders,
        "core_order_items.json": items,
        "api_orders.json": api_orders,
        "events.json": events,
    }
    manifest = {"student_id": "S03", "exported_at": "2026-09-08T08:00:00Z", "files": {}}
    for fname, rows in files_payload.items():
        raw_bytes = (json.dumps(rows, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        (tmp_path / fname).write_bytes(raw_bytes)
        manifest["files"][fname] = {
            "rows": len(rows),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    res = validate_export_directory(
        tmp_path,
        student_id="S03",
        reference_time="2026-09-08T08:00:00Z",
        scope="project",
    )
    assert res.status == "PASS"
    assert res.publish is True
    assert res.entity_summary["orders"] == {"input": 9, "accepted": 8, "rejected": 0, "duplicate": 1}
    assert res.entity_summary["items"] == {"input": 1, "accepted": 1, "rejected": 0, "duplicate": 0}
    assert res.entity_summary["events"] == {"input": 24, "accepted": 24, "rejected": 0, "duplicate": 0}

    (tmp_path / "events.json").write_bytes(b"[]\n")
    blocked_res = validate_export_directory(
        tmp_path,
        student_id="S03",
        reference_time="2026-09-08T08:00:00Z",
        scope="project",
    )
    assert blocked_res.status == "BLOCKED"
    assert blocked_res.publish is False
    assert blocked_res.rule_results["Q01"].failed_count >= 1