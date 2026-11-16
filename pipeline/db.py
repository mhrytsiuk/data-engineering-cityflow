"""Модуль транзакційного запису журналу якості, quarantine та публікації у validated.* (PostgreSQL)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .validator import BatchValidationResult


def resolve_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if dsn:
        return dsn
    for env_file in (Path(".env"), Path(".env.lab04"), Path(".env.ingestion")):
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "DATABASE_URL" and v.strip():
                    return v.strip().strip("'").strip('"')
    raise RuntimeError("DATABASE_URL is not set in environment or .env")


def connect_autocommit(dsn: str):
    import psycopg
    return psycopg.connect(dsn, autocommit=True)


def begin_quality_run(
    dsn: str,
    *,
    run_id: str,
    student_id: str,
    scope: str,
    reference_time: Any,
    contract_version: str,
    source_manifest: dict[str, Any],
) -> None:
    """Реєструє початок запуску зі статусом RUNNING в окремому autocommit-з'єднанні."""
    from psycopg.types.json import Jsonb

    with connect_autocommit(dsn) as conn:
        conn.execute(
            """
            INSERT INTO quality.runs (
                run_id, student_id, scope, reference_time, contract_version,
                status, source_manifest
            )
            VALUES (%s, %s, %s, %s, %s, 'RUNNING', %s)
            """,
            (
                run_id,
                student_id,
                scope,
                reference_time,
                contract_version,
                Jsonb(source_manifest),
            ),
        )


def mark_quality_run_failed(dsn: str, *, run_id: str, error_code: str) -> None:
    """Фіксує статус FAILED в окремому autocommit-з'єднанні після відкату основної транзакції."""
    with connect_autocommit(dsn) as conn:
        conn.execute(
            """
            UPDATE quality.runs
               SET status = 'FAILED',
                   finished_at = clock_timestamp(),
                   error_code = %s
             WHERE run_id = %s
            """,
            (error_code[:160], run_id),
        )


def persist_validation_result(
    dsn: str,
    *,
    run_id: str,
    result: BatchValidationResult,
    fail_before_commit: bool = False,
) -> None:
    """Атомарно зберігає результати перевірки та (для scope='project' при PASS/WARN) публікує у validated.*."""
    from psycopg.types.json import Jsonb

    with connect_autocommit(dsn) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                # 1. Запис рішень для кожного вхідного рядка (quality.row_decisions)
                if result.decisions:
                    cur.executemany(
                        """
                        INSERT INTO quality.row_decisions (
                            run_id, source_name, source_row_no, business_key, decision, reason_codes
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        [
                            (
                                run_id,
                                d.source_name,
                                d.source_row_no,
                                d.business_key,
                                d.decision,
                                Jsonb(d.reason_codes),
                            )
                            for d in result.decisions
                        ],
                    )

                # 2. Запис відхилених рядків у карантин (quarantine.records)
                rejected_rows = result.quarantine_records
                if rejected_rows:
                    cur.executemany(
                        """
                        INSERT INTO quarantine.records (
                            run_id, source_name, source_row_no, payload, reason_codes
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        [
                            (
                                run_id,
                                d.source_name,
                                d.source_row_no,
                                Jsonb(d.raw_payload),
                                Jsonb(d.reason_codes),
                            )
                            for d in rejected_rows
                        ],
                    )

                # 3. Запис підсумків за правилами (quality.rule_results)
                cur.executemany(
                    """
                    INSERT INTO quality.rule_results (
                        run_id, rule_id, severity, failed_count, details
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            run_id,
                            rr.rule_id,
                            rr.severity,
                            rr.failed_count,
                            Jsonb(rr.details),
                        )
                        for rr in result.rule_results.values()
                    ],
                )

                # 4. Якщо запуск BLOCKED або це тестовий scope ('test') — НЕ змінюємо validated.*
                if result.status == "BLOCKED" or not result.publish or result.scope != "project":
                    cur.execute(
                        """
                        UPDATE quality.runs
                           SET status = %s,
                               finished_at = clock_timestamp(),
                               source_manifest = %s,
                               error_code = %s
                         WHERE run_id = %s
                        """,
                        (
                            result.status,
                            Jsonb(result.source_manifest),
                            result.error_code,
                            run_id,
                        ),
                    )
                    if fail_before_commit:
                        raise RuntimeError("INJECTED_BEFORE_COMMIT")
                    return

                # 5. Для scope='project' та статусів PASS/WARN замінюємо всі три таблиці validated.*
                # Спочатку видаляємо дочірні таблиці, потім батьківську orders:
                cur.execute("DELETE FROM validated.delivery_events")
                cur.execute("DELETE FROM validated.order_items")
                cur.execute("DELETE FROM validated.orders")

                # Вставляємо батьківські orders перед дочірніми таблицями:
                cur.executemany(
                    """
                    INSERT INTO validated.orders (
                        order_id, customer_id, merchant_id, created_at, order_status,
                        currency, subtotal_amount, delivery_fee, discount_amount,
                        total_amount, quality_run_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            o["order_id"],
                            o["customer_id"],
                            o["merchant_id"],
                            o["created_at"],
                            o["order_status"],
                            o["currency"],
                            o["subtotal_amount"],
                            o["delivery_fee"],
                            o["discount_amount"],
                            o["total_amount"],
                            run_id,
                        )
                        for o in result.accepted_orders
                    ],
                )

                cur.executemany(
                    """
                    INSERT INTO validated.order_items (
                        order_item_id, order_id, product_id, quantity,
                        unit_price, line_discount, line_total, currency, quality_run_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            i["order_item_id"],
                            i["order_id"],
                            i["product_id"],
                            i["quantity"],
                            i["unit_price"],
                            i["line_discount"],
                            i["line_total"],
                            i["currency"],
                            run_id,
                        )
                        for i in result.accepted_items
                    ],
                )

                cur.executemany(
                    """
                    INSERT INTO validated.delivery_events (
                        event_id, delivery_id, order_id, event_type,
                        occurred_at, available_at, courier_id, service_zone_id,
                        payload_version, payload, quality_run_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            e["event_id"],
                            e["delivery_id"],
                            e["order_id"],
                            e["event_type"],
                            e["occurred_at"],
                            e["available_at"],
                            e["courier_id"],
                            e["service_zone_id"],
                            e["payload_version"],
                            Jsonb(e["clean_payload"]),
                            run_id,
                        )
                        for e in result.accepted_events
                    ],
                )

                if fail_before_commit:
                    raise RuntimeError("INJECTED_BEFORE_COMMIT")

                cur.execute(
                    """
                    UPDATE quality.runs
                       SET status = %s,
                           finished_at = clock_timestamp(),
                           source_manifest = %s,
                           error_code = NULL
                     WHERE run_id = %s
                    """,
                    (result.status, Jsonb(result.source_manifest), run_id),
                )