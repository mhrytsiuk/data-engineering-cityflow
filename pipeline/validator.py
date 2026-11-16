"""Ядро перевірки контракту якості даних, класифікації записів і формування рішень (Q01–Q10)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "contracts" / "cityflow_v1.json"

ALLOWED_CURRENCIES = {"UAH", "USD", "EUR"}
ALLOWED_ORDER_STATUSES = {
    "delivered",
    "cancelled",
    "refunded",
    "partially_refunded",
    "failed",
    "in_progress",
}
ALLOWED_EVENT_TYPES = {
    "order_created",
    "payment_confirmed",
    "merchant_accepted",
    "preparation_started",
    "ready_for_pickup",
    "courier_assigned",
    "courier_picked_up",
    "courier_arrived",
    "delivered",
}
EXPECTED_EXPORT_FILES = (
    "core_customers.json",
    "core_merchants.json",
    "core_products.json",
    "core_orders.json",
    "core_order_items.json",
    "api_orders.json",
    "events.json",
)
TECHNICAL_META_FIELDS = {
    "_source_key",
    "run_id",
    "loaded_at",
    "quality_run_id",
    "source_batch_id",
    "source_available_at",
    "first_run_id",
    "raw_path",
    "source_row_number",
}


@dataclass
class RowDecision:
    source_name: str
    source_row_no: int
    business_key: str | None
    decision: str  # 'accepted', 'rejected', 'duplicate'
    reason_codes: list[str]
    raw_payload: dict[str, Any]
    normalized_record: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "source_row_no": self.source_row_no,
            "business_key": self.business_key,
            "decision": self.decision,
            "reason_codes": list(self.reason_codes),
        }


@dataclass
class RuleResult:
    rule_id: str
    severity: str  # 'BLOCK', 'REJECT', 'DUPLICATE', 'WARN'
    failed_count: int
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "failed_count": self.failed_count,
            "details": self.details,
        }


@dataclass
class BatchValidationResult:
    student_id: str
    scope: str
    reference_time: datetime
    contract_version: str
    status: str  # 'PASS', 'WARN', 'BLOCKED'
    publish: bool
    source_manifest: dict[str, Any]
    decisions: list[RowDecision]
    rule_results: dict[str, RuleResult]
    entity_summary: dict[str, dict[str, int]]
    accepted_orders: list[dict[str, Any]]
    accepted_items: list[dict[str, Any]]
    accepted_events: list[dict[str, Any]]
    error_code: str | None = None

    @property
    def quarantine_records(self) -> list[RowDecision]:
        return [d for d in self.decisions if d.decision == "rejected"]

    def to_report_dict(self, run_id: str) -> dict[str, Any]:
        currency_totals: dict[str, str] = {}
        for order in self.accepted_orders:
            curr = order["currency"]
            currency_totals[curr] = str(
                Decimal(currency_totals.get(curr, "0.00")) + order["total_amount"]
            )
        return {
            "run_id": run_id,
            "student_id": self.student_id,
            "scope": self.scope,
            "reference_time": self.reference_time.isoformat(),
            "contract_version": self.contract_version,
            "status": self.status,
            "publish": self.publish,
            "error_code": self.error_code,
            "entity_summary": self.entity_summary,
            "currency_totals_accepted_orders": currency_totals,
            "quarantine_count": len(self.quarantine_records),
            "rule_results": {k: v.to_dict() for k, v in sorted(self.rule_results.items())},
        }


def load_contract(path: Path | None = None) -> dict[str, Any]:
    contract_path = Path(path) if path else DEFAULT_CONTRACT_PATH
    return json.loads(contract_path.read_text(encoding="utf-8"))


def parse_iso_timestamptz(value: Any) -> datetime | None:
    """Парсить рядок ISO-8601 у datetime із обов'язковим часовим поясом (offset або Z)."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return None
    return dt.astimezone(timezone.utc)


def parse_decimal_money(value: Any) -> Decimal | None:
    """Парсить грошове значення у скінченний Decimal (відхиляє float, bool, NaN, Inf)."""
    if value is None or isinstance(value, (float, bool)):
        return None
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            d = Decimal(s)
        except InvalidOperation:
            return None
    else:
        return None
    if not d.is_finite():
        return None
    return d.quantize(Decimal("0.01"))


def parse_positive_int(value: Any) -> int | None:
    """Парсить додатне ціле число quantity > 0 (відхиляє bool та float)."""
    if value is None or isinstance(value, (bool, float)):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        s = value.strip()
        if not s or not s.isdigit():
            return None
        val = int(s)
        return val if val > 0 else None
    return None


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_order_row(raw: dict[str, Any]) -> tuple[str | None, list[tuple[str, str]], dict[str, Any] | None]:
    """Перевіряє один запис замовлення за Q02, Q03, Q04 і будує нормалізований бізнес-словник."""
    violations: list[tuple[str, str]] = []
    order_id = raw.get("order_id") if _is_nonempty_str(raw.get("order_id")) else None
    customer_id = raw.get("customer_id") if _is_nonempty_str(raw.get("customer_id")) else None
    merchant_id = raw.get("merchant_id") if _is_nonempty_str(raw.get("merchant_id")) else None

    if not order_id or not customer_id or not merchant_id:
        violations.append(("Q02", "MISSING_OR_INVALID_ID"))

    created_dt = parse_iso_timestamptz(raw.get("created_at"))
    if created_dt is None:
        violations.append(("Q03", "INVALID_TIMESTAMP"))

    subtotal = parse_decimal_money(raw.get("subtotal_amount"))
    delivery_fee = parse_decimal_money(raw.get("delivery_fee"))
    discount = parse_decimal_money(raw.get("discount_amount"))
    total = parse_decimal_money(raw.get("total_amount"))
    if None in (subtotal, delivery_fee, discount, total):
        violations.append(("Q03", "INVALID_DECIMAL_AMOUNT"))

    currency = raw.get("currency")
    if not _is_nonempty_str(currency) or currency.strip() not in ALLOWED_CURRENCIES:
        violations.append(("Q04", "CURRENCY_NOT_ALLOWED"))

    order_status = raw.get("order_status")
    if not _is_nonempty_str(order_status) or order_status.strip() not in ALLOWED_ORDER_STATUSES:
        violations.append(("Q04", "ORDER_STATUS_NOT_ALLOWED"))

    for amt in (subtotal, delivery_fee, discount, total):
        if amt is not None and amt < Decimal("0.00"):
            violations.append(("Q04", "NEGATIVE_AMOUNT"))
            break

    if violations:
        return order_id, violations, None

    assert order_id and customer_id and merchant_id and created_dt
    assert subtotal is not None and delivery_fee is not None and discount is not None and total is not None
    normalized = {
        "order_id": order_id.strip(),
        "customer_id": customer_id.strip(),
        "merchant_id": merchant_id.strip(),
        "created_at": created_dt,
        "order_status": str(order_status).strip(),
        "currency": str(currency).strip(),
        "subtotal_amount": subtotal,
        "delivery_fee": delivery_fee,
        "discount_amount": discount,
        "total_amount": total,
    }
    return order_id.strip(), [], normalized


def _order_business_signature(norm: dict[str, Any]) -> tuple[Any, ...]:
    return (
        norm["order_id"],
        norm["customer_id"],
        norm["merchant_id"],
        norm["created_at"].isoformat(),
        norm["order_status"],
        norm["currency"],
        str(norm["subtotal_amount"]),
        str(norm["delivery_fee"]),
        str(norm["discount_amount"]),
        str(norm["total_amount"]),
    )


def _validate_item_row(raw: dict[str, Any]) -> tuple[str | None, list[tuple[str, str]], dict[str, Any] | None]:
    """Перевіряє один запис позиції замовлення за Q02, Q03, Q04."""
    violations: list[tuple[str, str]] = []
    item_id = raw.get("order_item_id") if _is_nonempty_str(raw.get("order_item_id")) else None
    order_id = raw.get("order_id") if _is_nonempty_str(raw.get("order_id")) else None
    product_id = raw.get("product_id") if _is_nonempty_str(raw.get("product_id")) else None

    if not item_id or not order_id or not product_id:
        violations.append(("Q02", "MISSING_OR_INVALID_ID"))

    quantity = parse_positive_int(raw.get("quantity"))
    if quantity is None:
        violations.append(("Q03", "INVALID_QUANTITY"))

    unit_price = parse_decimal_money(raw.get("unit_price"))
    line_discount = parse_decimal_money(raw.get("line_discount"))
    line_total = parse_decimal_money(raw.get("line_total"))
    if None in (unit_price, line_discount, line_total):
        violations.append(("Q03", "INVALID_DECIMAL_AMOUNT"))

    currency = raw.get("currency")
    if not _is_nonempty_str(currency) or currency.strip() not in ALLOWED_CURRENCIES:
        violations.append(("Q04", "CURRENCY_NOT_ALLOWED"))

    for amt in (unit_price, line_discount, line_total):
        if amt is not None and amt < Decimal("0.00"):
            violations.append(("Q04", "NEGATIVE_AMOUNT"))
            break

    if violations:
        return item_id, violations, None

    assert item_id and order_id and product_id and quantity is not None
    assert unit_price is not None and line_discount is not None and line_total is not None
    normalized = {
        "order_item_id": item_id.strip(),
        "order_id": order_id.strip(),
        "product_id": product_id.strip(),
        "quantity": quantity,
        "unit_price": unit_price,
        "line_discount": line_discount,
        "line_total": line_total,
        "currency": str(currency).strip(),
    }
    return item_id.strip(), [], normalized


def _item_business_signature(norm: dict[str, Any]) -> tuple[Any, ...]:
    return (
        norm["order_item_id"],
        norm["order_id"],
        norm["product_id"],
        norm["quantity"],
        str(norm["unit_price"]),
        str(norm["line_discount"]),
        str(norm["line_total"]),
        norm["currency"],
    )


def _validate_event_row(raw: dict[str, Any]) -> tuple[str | None, list[tuple[str, str]], dict[str, Any] | None]:
    """Перевіряє один запис події доставки за Q02, Q03, Q08."""
    violations: list[tuple[str, str]] = []
    event_id = raw.get("event_id") if _is_nonempty_str(raw.get("event_id")) else None
    order_id = raw.get("order_id") if _is_nonempty_str(raw.get("order_id")) else None

    if not event_id or not order_id:
        violations.append(("Q02", "MISSING_OR_INVALID_ID"))

    delivery_id = raw.get("delivery_id") if _is_nonempty_str(raw.get("delivery_id")) else None
    if not delivery_id:
        violations.append(("Q08", "MISSING_DELIVERY_ID"))

    pv = raw.get("payload_version")
    if isinstance(pv, bool) or pv != 1:
        violations.append(("Q08", "UNSUPPORTED_PAYLOAD_VERSION"))

    event_type = raw.get("event_type")
    if not _is_nonempty_str(event_type) or event_type.strip() not in ALLOWED_EVENT_TYPES:
        violations.append(("Q08", "EVENT_TYPE_NOT_ALLOWED"))

    raw_occ = raw.get("occurred_at")
    raw_avail = raw.get("available_at")
    if not _is_nonempty_str(raw_occ) or not _is_nonempty_str(raw_avail):
        violations.append(("Q08", "MISSING_EVENT_TIMESTAMPS"))
        occ_dt = None
        avail_dt = None
    else:
        occ_dt = parse_iso_timestamptz(raw_occ)
        avail_dt = parse_iso_timestamptz(raw_avail)
        if occ_dt is None or avail_dt is None:
            violations.append(("Q03", "INVALID_TIMESTAMP"))

    if violations:
        return event_id.strip() if event_id else None, violations, None

    assert event_id and order_id and delivery_id and occ_dt and avail_dt
    clean_payload = {k: v for k, v in raw.items() if k not in TECHNICAL_META_FIELDS}
    normalized = {
        "event_id": event_id.strip(),
        "delivery_id": delivery_id.strip(),
        "order_id": order_id.strip(),
        "event_type": str(event_type).strip(),
        "occurred_at": occ_dt,
        "available_at": avail_dt,
        "courier_id": raw.get("courier_id").strip() if _is_nonempty_str(raw.get("courier_id")) else None,
        "service_zone_id": (
            raw.get("service_zone_id").strip() if _is_nonempty_str(raw.get("service_zone_id")) else None
        ),
        "sequence_no": raw.get("sequence_no"),
        "payload_version": 1,
        "clean_payload": clean_payload,
    }
    return event_id.strip(), [], normalized


def _event_business_signature(norm: dict[str, Any]) -> tuple[Any, ...]:
    extra = {
        k: v
        for k, v in norm["clean_payload"].items()
        if k
        not in {
            "event_id",
            "delivery_id",
            "order_id",
            "event_type",
            "occurred_at",
            "available_at",
            "courier_id",
            "service_zone_id",
            "payload_version",
        }
    }
    return (
        norm["event_id"],
        norm["delivery_id"],
        norm["order_id"],
        norm["event_type"],
        norm["occurred_at"].isoformat(),
        norm["available_at"].isoformat(),
        norm["courier_id"],
        norm["service_zone_id"],
        norm["payload_version"],
        json.dumps(extra, sort_keys=True, ensure_ascii=False),
    )


def _apply_dedup_and_conflicts(
    decisions: list[RowDecision],
    signature_fn,
    rule_counts: dict[str, int],
) -> None:
    """Класифікує точні повтори (Q05) та конфліктні групи (Q06) серед записів з однаковим ключем."""
    by_key: dict[str, list[RowDecision]] = {}
    for d in decisions:
        if d.business_key is not None and d.decision != "rejected" and d.normalized_record is not None:
            by_key.setdefault(d.business_key, []).append(d)

    for key, group in by_key.items():
        if len(group) <= 1:
            continue
        signatures = {signature_fn(d.normalized_record) for d in group if d.normalized_record is not None}
        if len(signatures) > 1:
            for d in group:
                d.decision = "rejected"
                if "CONFLICTING_DUPLICATE_KEY" not in d.reason_codes:
                    d.reason_codes.append("CONFLICTING_DUPLICATE_KEY")
                rule_counts["Q06"] += 1
        else:
            # Сортування за (source_name, source_row_no) для стабільного вибору першої копії
            ordered = sorted(group, key=lambda x: (x.source_name, x.source_row_no))
            for dup_d in ordered[1:]:
                dup_d.decision = "duplicate"
                if "EXACT_DUPLICATE" not in dup_d.reason_codes:
                    dup_d.reason_codes.append("EXACT_DUPLICATE")
                rule_counts["Q05"] += 1


def _run_validation_pipeline(
    *,
    student_id: str,
    scope: str,
    reference_time: datetime,
    contract_version: str,
    source_manifest: dict[str, Any],
    order_sources: list[tuple[str, list[dict[str, Any]]]],
    item_sources: list[tuple[str, list[dict[str, Any]]]],
    event_sources: list[tuple[str, list[dict[str, Any]]]],
    valid_customers: set[str] | None,
    valid_merchants: set[str] | None,
    valid_products: set[str] | None,
    q01_errors: list[str],
) -> BatchValidationResult:
    rule_counts = {f"Q{i:02d}": 0 for i in range(1, 11)}
    rule_Severities = {
        "Q01": "BLOCK",
        "Q02": "REJECT",
        "Q03": "REJECT",
        "Q04": "REJECT",
        "Q05": "DUPLICATE",
        "Q06": "REJECT",
        "Q07": "REJECT",
        "Q08": "REJECT",
        "Q09": "WARN",
        "Q10": "WARN",
    }

    if q01_errors:
        rule_counts["Q01"] = len(q01_errors)

    all_decisions: list[RowDecision] = []
    order_decisions: list[RowDecision] = []
    item_decisions: list[RowDecision] = []
    event_decisions: list[RowDecision] = []

    # 1. ORDERS: Step 1 (Q02, Q03, Q04)
    for src_name, rows in order_sources:
        for idx, raw in enumerate(rows, start=1):
            bkey, violations, norm = _validate_order_row(raw)
            reasons: list[str] = []
            for r_id, r_code in violations:
                rule_counts[r_id] += 1
                if r_code not in reasons:
                    reasons.append(r_code)
            decision = "rejected" if reasons else "accepted"
            d = RowDecision(
                source_name=src_name,
                source_row_no=idx,
                business_key=bkey,
                decision=decision,
                reason_codes=reasons,
                raw_payload=raw,
                normalized_record=norm,
            )
            order_decisions.append(d)
            all_decisions.append(d)

    # ORDERS: Step 2 (Q05, Q06)
    _apply_dedup_and_conflicts(order_decisions, _order_business_signature, rule_counts)

    # ORDERS: Step 3 (Q07 referential integrity against core.customers & core.merchants)
    for d in order_decisions:
        if d.decision != "accepted" or d.normalized_record is None:
            continue
        cid = d.normalized_record["customer_id"]
        mid = d.normalized_record["merchant_id"]
        fk_Failed = False
        if valid_customers is not None and cid not in valid_customers:
            d.reason_codes.append("CUSTOMER_NOT_FOUND")
            fk_Failed = True
        if valid_merchants is not None and mid not in valid_merchants:
            d.reason_codes.append("MERCHANT_NOT_FOUND")
            fk_Failed = True
        if fk_Failed:
            d.decision = "rejected"
            rule_counts["Q07"] += 1

    accepted_order_ids = {
        d.business_key for d in order_decisions if d.decision == "accepted" and d.business_key is not None
    }
    rejected_order_ids = {
        d.business_key for d in order_decisions if d.decision == "rejected" and d.business_key is not None
    }
    # Якщо перша копія замовлення була відхилена через Q07, її дублікати теж мають бути відхилені як PARENT_REJECTED/CONFLICT
    for d in order_decisions:
        if d.decision == "duplicate" and d.business_key not in accepted_order_ids:
            d.decision = "rejected"
            d.reason_codes = ["REJECTED_PRIMARY_ORDER"]

    # 2. ORDER_ITEMS: Step 1 (Q02, Q03, Q04)
    for src_name, rows in item_sources:
        for idx, raw in enumerate(rows, start=1):
            bkey, violations, norm = _validate_item_row(raw)
            reasons = []
            for r_id, r_code in violations:
                rule_counts[r_id] += 1
                if r_code not in reasons:
                    reasons.append(r_code)
            decision = "rejected" if reasons else "accepted"
            d = RowDecision(
                source_name=src_name,
                source_row_no=idx,
                business_key=bkey,
                decision=decision,
                reason_codes=reasons,
                raw_payload=raw,
                normalized_record=norm,
            )
            item_decisions.append(d)
            all_decisions.append(d)

    # ORDER_ITEMS: Step 2 (Q05, Q06)
    _apply_dedup_and_conflicts(item_decisions, _item_business_signature, rule_counts)

    # ORDER_ITEMS: Step 3 (Q07 referential integrity against accepted orders and core.products)
    for d in item_decisions:
        if d.decision != "accepted" or d.normalized_record is None:
            continue
        oid = d.normalized_record["order_id"]
        pid = d.normalized_record["product_id"]
        fk_failed = False
        if oid not in accepted_order_ids:
            if oid in rejected_order_ids:
                d.reason_codes.append("PARENT_REJECTED")
            else:
                d.reason_codes.append("ORDER_NOT_FOUND")
            fk_failed = True
        if valid_products is not None and pid not in valid_products:
            d.reason_codes.append("PRODUCT_NOT_FOUND")
            fk_failed = True
        if fk_failed:
            d.decision = "rejected"
            rule_counts["Q07"] += 1

    accepted_item_ids = {
        d.business_key for d in item_decisions if d.decision == "accepted" and d.business_key is not None
    }
    for d in item_decisions:
        if d.decision == "duplicate" and d.business_key not in accepted_item_ids:
            d.decision = "rejected"
            d.reason_codes = ["PARENT_REJECTED"]
            rule_counts["Q07"] += 1

    # 3. EVENTS: Step 1 (Q02, Q03, Q08)
    for src_name, rows in event_sources:
        for idx, raw in enumerate(rows, start=1):
            bkey, violations, norm = _validate_event_row(raw)
            reasons = []
            for r_id, r_code in violations:
                rule_counts[r_id] += 1
                if r_code not in reasons:
                    reasons.append(r_code)
            decision = "rejected" if reasons else "accepted"
            d = RowDecision(
                source_name=src_name,
                source_row_no=idx,
                business_key=bkey,
                decision=decision,
                reason_codes=reasons,
                raw_payload=raw,
                normalized_record=norm,
            )
            event_decisions.append(d)
            all_decisions.append(d)

    # EVENTS: Step 2 (Q05, Q06)
    _apply_dedup_and_conflicts(event_decisions, _event_business_signature, rule_counts)

    # EVENTS: Step 3 (Q07 referential integrity against accepted orders)
    for d in event_decisions:
        if d.decision != "accepted" or d.normalized_record is None:
            continue
        oid = d.normalized_record["order_id"]
        if oid not in accepted_order_ids:
            if oid in rejected_order_ids:
                d.reason_codes.append("PARENT_REJECTED")
            else:
                d.reason_codes.append("ORDER_NOT_FOUND")
            d.decision = "rejected"
            rule_counts["Q07"] += 1

    accepted_event_ids = {
        d.business_key for d in event_decisions if d.decision == "accepted" and d.business_key is not None
    }
    for d in event_decisions:
        if d.decision == "duplicate" and d.business_key not in accepted_event_ids:
            d.decision = "rejected"
            d.reason_codes = ["PARENT_REJECTED"]
            rule_counts["Q07"] += 1

    # 4. Формування списків прийнятих записів
    accepted_orders = [
        d.normalized_record for d in order_decisions if d.decision == "accepted" and d.normalized_record is not None
    ]
    accepted_items = [
        d.normalized_record for d in item_decisions if d.decision == "accepted" and d.normalized_record is not None
    ]
    accepted_events = [
        d.normalized_record for d in event_decisions if d.decision == "accepted" and d.normalized_record is not None
    ]

    # 5. Перевірка Q09 (WARN: розбіжність формули суми замовлення > 0.01)
    q09_mismatched_orders: list[str] = []
    for o in accepted_orders:
        expected_total = o["subtotal_amount"] + o["delivery_fee"] - o["discount_amount"]
        if abs(o["total_amount"] - expected_total) > Decimal("0.01"):
            q09_mismatched_orders.append(o["order_id"])
    rule_counts["Q09"] = len(q09_mismatched_orders)

    # 6. Перевірка Q10 (WARN: свіжість подій reference_time - max(available_at) > 10 днів)
    max_available_at: datetime | None = None
    if accepted_events:
        max_available_at = max(e["available_at"] for e in accepted_events)
        if reference_time - max_available_at > timedelta(days=10):
            rule_counts["Q10"] = 1

    # 7. Підрахунок балансу за сутностями: input = accepted + rejected + duplicate
    def _summarize(decs: list[RowDecision]) -> dict[str, int]:
        acc = sum(1 for d in decs if d.decision == "accepted")
        rej = sum(1 for d in decs if d.decision == "rejected")
        dup = sum(1 for d in decs if d.decision == "duplicate")
        return {
            "input": len(decs),
            "accepted": acc,
            "rejected": rej,
            "duplicate": dup,
        }

    entity_summary = {
        "orders": _summarize(order_decisions),
        "items": _summarize(item_decisions),
        "events": _summarize(event_decisions),
    }

    # Якщо для непорожньої сутності не залишилось жодного прийнятого ключа -> BLOCK
    for ent_name, counts in entity_summary.items():
        if counts["input"] > 0 and counts["accepted"] == 0:
            q01_errors.append(f"ZERO_ACCEPTED_RECORDS:{ent_name}")
            rule_counts["Q01"] += 1

    rule_results: dict[str, RuleResult] = {}
    for r_id, sev in rule_Severities.items():
        details: dict[str, Any] = {}
        if r_id == "Q01" and q01_errors:
            details["errors"] = list(q01_errors)
        elif r_id == "Q09" and q09_mismatched_orders:
            details["mismatched_order_ids_sample"] = q09_mismatched_orders[:20]
            details["mismatched_count"] = len(q09_mismatched_orders)
        elif r_id == "Q10" and max_available_at is not None:
            age_seconds = (reference_time - max_available_at).total_seconds()
            details["reference_time"] = reference_time.isoformat()
            details["max_available_at"] = max_available_at.isoformat()
            details["age_days"] = round(age_seconds / 86400.0, 2)
        rule_results[r_id] = RuleResult(
            rule_id=r_id,
            severity=sev,
            failed_count=rule_counts[r_id],
            details=details,
        )

    if rule_counts["Q01"] > 0:
        status = "BLOCKED"
        publish = False
        error_code = "BLOCKED_BY_Q01"
    else:
        if rule_counts["Q09"] > 0 or rule_counts["Q10"] > 0:
            status = "WARN"
        else:
            status = "PASS"
        publish = scope == "project"
        error_code = None

    return BatchValidationResult(
        student_id=student_id,
        scope=scope,
        reference_time=reference_time,
        contract_version=contract_version,
        status=status,
        publish=publish,
        source_manifest=source_manifest,
        decisions=all_decisions,
        rule_results=rule_results,
        entity_summary=entity_summary,
        accepted_orders=accepted_orders,
        accepted_items=accepted_items,
        accepted_events=accepted_events,
        error_code=error_code,
    )


def validate_fixture_data(
    fixture: dict[str, Any],
    *,
    reference_time: datetime | str | None = None,
    contract_version: str = "cityflow_v1",
) -> BatchValidationResult:
    """Валідує словник тестового fixture (normal, mixed, incomplete) без БД."""
    student_id = str(fixture.get("student_id", "S03"))
    ref_raw = reference_time or fixture.get("reference_time", "2026-09-08T08:00:00Z")
    ref_dt = parse_iso_timestamptz(ref_raw) if isinstance(ref_raw, str) else ref_raw
    if ref_dt is None:
        raise ValueError(f"Invalid reference_time: {ref_raw!r}")

    orders = list(fixture.get("orders", []))
    events = list(fixture.get("events", []))
    items = list(fixture.get("order_items", []))
    expected_events = fixture.get("expected_event_rows")

    q01_errors: list[str] = []
    if expected_events is not None and len(events) != int(expected_events):
        q01_errors.append(
            f"EXPECTED_EVENT_ROWS_MISMATCH: expected {expected_events}, got {len(events)}"
        )

    manifest = {
        "fixture_id": fixture.get("fixture_id", "in_memory_fixture"),
        "expected_event_rows": expected_events,
        "actual_event_rows": len(events),
        "actual_order_rows": len(orders),
    }

    return _run_validation_pipeline(
        student_id=student_id,
        scope="test",
        reference_time=ref_dt,
        contract_version=contract_version,
        source_manifest=manifest,
        order_sources=[("orders", orders)],
        item_sources=[("items", items)] if items else [],
        event_sources=[("events", events)],
        valid_customers=None,
        valid_merchants=None,
        valid_products=None,
        q01_errors=q01_errors,
    )


def validate_fixture_file(
    fixture_path: Path,
    *,
    reference_time: datetime | str | None = None,
    contract_version: str = "cityflow_v1",
) -> BatchValidationResult:
    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    return validate_fixture_data(
        data,
        reference_time=reference_time,
        contract_version=contract_version,
    )


def validate_export_directory(
    export_dir: Path,
    *,
    student_id: str,
    reference_time: datetime | str,
    scope: str = "project",
    contract_version: str = "cityflow_v1",
) -> BatchValidationResult:
    """Перевіряє каталог експорту runtime/source_XX за правилами Q01–Q10."""
    export_dir = Path(export_dir)
    ref_dt = parse_iso_timestamptz(reference_time) if isinstance(reference_time, str) else reference_time
    if ref_dt is None:
        raise ValueError(f"Invalid reference_time: {reference_time!r}")

    q01_errors: list[str] = []
    manifest_path = export_dir / "manifest.json"
    manifest: dict[str, Any] = {}
    loaded_files: dict[str, list[dict[str, Any]]] = {}

    if not manifest_path.is_file():
        q01_errors.append("MISSING_MANIFEST_JSON")
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            q01_errors.append("CORRUPT_MANIFEST_JSON")

    if manifest:
        if manifest.get("student_id") != student_id:
            q01_errors.append(
                f"MANIFEST_STUDENT_ID_MISMATCH: expected {student_id}, got {manifest.get('student_id')}"
            )
        files_meta = manifest.get("files")
        if not isinstance(files_meta, dict):
            q01_errors.append("INVALID_MANIFEST_FILES_OBJECT")
        else:
            for req_file in EXPECTED_EXPORT_FILES:
                if req_file not in files_meta:
                    q01_errors.append(f"MISSING_IN_MANIFEST:{req_file}")
                    continue
                fpath = export_dir / req_file
                if not fpath.is_file():
                    q01_errors.append(f"MISSING_EXPORT_FILE:{req_file}")
                    continue
                raw_bytes = fpath.read_bytes()
                actual_sha = hashlib.sha256(raw_bytes).hexdigest()
                expected_sha = files_meta[req_file].get("sha256")
                if actual_sha != expected_sha:
                    q01_errors.append(f"SHA256_MISMATCH:{req_file}")
                try:
                    rows = json.loads(raw_bytes.decode("utf-8"))
                    if not isinstance(rows, list):
                        q01_errors.append(f"NOT_A_JSON_ARRAY:{req_file}")
                        rows = []
                except Exception:
                    q01_errors.append(f"INVALID_JSON:{req_file}")
                    rows = []
                expected_rows = files_meta[req_file].get("rows")
                if len(rows) != expected_rows:
                    q01_errors.append(
                        f"ROW_COUNT_MISMATCH:{req_file}:expected={expected_rows},got={len(rows)}"
                    )
                loaded_files[req_file] = rows

    if q01_errors:
        # При порушенні Q01 повертаємо BLOCKED без публікації
        return _run_validation_pipeline(
            student_id=student_id,
            scope=scope,
            reference_time=ref_dt,
            contract_version=contract_version,
            source_manifest=manifest or {"export_dir": str(export_dir), "q01_errors": q01_errors},
            order_sources=[],
            item_sources=[],
            event_sources=[],
            valid_customers=set(),
            valid_merchants=set(),
            valid_products=set(),
            q01_errors=q01_errors,
        )

    valid_customers = {
        r["customer_id"].strip()
        for r in loaded_files.get("core_customers.json", [])
        if _is_nonempty_str(r.get("customer_id"))
    }
    valid_merchants = {
        r["merchant_id"].strip()
        for r in loaded_files.get("core_merchants.json", [])
        if _is_nonempty_str(r.get("merchant_id"))
    }
    valid_products = {
        r["product_id"].strip()
        for r in loaded_files.get("core_products.json", [])
        if _is_nonempty_str(r.get("product_id"))
    }

    return _run_validation_pipeline(
        student_id=student_id,
        scope=scope,
        reference_time=ref_dt,
        contract_version=contract_version,
        source_manifest=manifest,
        order_sources=[
            ("core_orders", loaded_files.get("core_orders.json", [])),
            ("api_orders", loaded_files.get("api_orders.json", [])),
        ],
        item_sources=[
            ("core_order_items", loaded_files.get("core_order_items.json", [])),
        ],
        event_sources=[
            ("events", loaded_files.get("events.json", [])),
        ],
        valid_customers=valid_customers,
        valid_merchants=valid_merchants,
        valid_products=valid_products,
        q01_errors=[],
    )