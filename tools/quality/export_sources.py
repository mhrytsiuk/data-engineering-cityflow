"""Експорт джерел даних CityFlow із PostgreSQL у незмінний JSON-каталог з маніфестом SHA-256."""
from __future__ import annotations
import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys

TABLES = {
    "core_customers": ("core.customers", "customer_id"),
    "core_merchants": ("core.merchants", "merchant_id"),
    "core_products": ("core.products", "product_id"),
    "core_orders": ("core.orders", "order_id"),
    "core_order_items": ("core.order_items", "order_item_id"),
    "api_orders": ("ingestion_stg.orders", "order_id"),
    "events": ("ingestion_stg.delivery_events", "event_id"),
}


def _resolve_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if dsn:
        return dsn
    for env_file in (Path(".env"), Path(".env.ingestion")):
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "DATABASE_URL" and v.strip():
                    return v.strip().strip("'").strip('"')
    raise RuntimeError("Set DATABASE_URL in your terminal or .env; do not put secrets in Git.")


def encode(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--student", required=True, choices=[f"S{i:02d}" for i in range(1, 11)])
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()
    dsn = _resolve_dsn()
    if a.out.exists():
        raise RuntimeError("Output directory exists. Choose a new export path; old exports are immutable.")
    import psycopg
    from psycopg.rows import dict_row

    data = {}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for name, (table, key) in TABLES.items():
            if table.startswith("core."):
                sql = f"SELECT * FROM {table} WHERE {key} LIKE %s ORDER BY {key}"
                records = conn.execute(sql, (a.student + "-%",)).fetchall()
            else:
                records = conn.execute(
                    f"SELECT * FROM {table} WHERE student_id=%s ORDER BY {key}", (a.student,)
                ).fetchall()
            if not records:
                raise RuntimeError(f"No source rows in {table} for {a.student}; run schema setup first.")
            if table.startswith("ingestion_stg."):
                records = [{**r["payload"], "_source_key": r[key]} for r in records]
            data[name] = records

    a.out.mkdir(parents=True)
    manifest = {
        "student_id": a.student,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "files": {},
    }
    for name, records in data.items():
        body = (json.dumps(records, ensure_ascii=False, indent=2, default=encode) + "\n").encode("utf8")
        path = a.out / (name + ".json")
        path.write_bytes(body)
        manifest["files"][path.name] = {
            "rows": len(records),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
    (a.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
    )
    print(json.dumps({"student_id": a.student, "counts": {n: len(r) for n, r in data.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "Export failed: " + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__),
            file=sys.stderr,
        )
        sys.exit(1)