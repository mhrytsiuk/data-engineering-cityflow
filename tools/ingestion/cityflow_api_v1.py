from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Query, Response, status


DATA_DIR = Path(os.getenv("CITYFLOW_DATA_DIR", "/data")).resolve()
KEYS_FILE = Path(os.getenv("CITYFLOW_KEYS_FILE", DATA_DIR / "api_keys.json"))
FAULTS_FILE = Path(os.getenv("CITYFLOW_FAULTS_FILE", DATA_DIR / "faults.json"))

app = FastAPI(
    title="CityFlow Teaching API",
    version="1.0.0",
    description="Deterministic local REST API for the CityFlow Data Engineering course.",
)

_cache_lock = Lock()
_event_cache: dict[str, list[dict[str, Any]]] = {}
_rate_cache: dict[str, list[dict[str, Any]]] = {}
_attempts: dict[tuple[str, str, int], int] = defaultdict(int)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _keys() -> dict[str, dict[str, str]]:
    return _read_json(KEYS_FILE, {})


def _faults() -> dict[str, Any]:
    return _read_json(FAULTS_FILE, {})


def _identity(api_key: str | None) -> dict[str, str]:
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key header")
    identity = _keys().get(api_key)
    if not identity:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return identity


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ISO timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _encode_cursor(offset: int) -> str:
    # Human-readable on purpose: students should treat it as opaque despite its simplicity.
    return f"c{offset}"


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.startswith("c") or not cursor[1:].isdigit():
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return int(cursor[1:])


def _load_events(student_id: str) -> list[dict[str, Any]]:
    with _cache_lock:
        if student_id in _event_cache:
            return _event_cache[student_id]
        path = DATA_DIR / "events" / f"{student_id}.jsonl"
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"No event dataset for {student_id}")
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        rows.sort(key=lambda row: (str(row.get("available_at")), str(row.get("event_id"))))
        _event_cache[student_id] = rows
        return rows


def _load_rates(student_id: str) -> list[dict[str, Any]]:
    with _cache_lock:
        if student_id in _rate_cache:
            return _rate_cache[student_id]
        path = DATA_DIR / "exchange_rates" / f"{student_id}.csv"
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"No exchange-rate dataset for {student_id}")
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            row["rate_to_uah"] = float(row["rate_to_uah"])
        _rate_cache[student_id] = rows
        return rows


def _request_id(student_id: str, endpoint: str, cursor: str | None, limit: int) -> str:
    raw = f"{student_id}|{endpoint}|{cursor}|{limit}|{sum(_attempts.values())}".encode("utf-8")
    return "req_" + hashlib.sha256(raw).hexdigest()[:16]


def _apply_faults(
    *,
    student_id: str,
    endpoint: str,
    offset: int,
    incident: bool,
    total_count: int,
    limit: int,
) -> tuple[int, bool]:
    """Return possibly modified offset and whether the final page should be suppressed."""
    config = _faults().get(student_id, {})
    key = (student_id, endpoint, offset)
    _attempts[key] += 1
    attempt = _attempts[key]

    if offset in config.get("rate_limit_offsets", []) and attempt == 1:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Teaching fault: temporary rate limit",
            headers={"Retry-After": "1"},
        )
    if offset in config.get("server_error_offsets", []) and attempt == 1:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Teaching fault: temporary upstream error",
        )

    if offset in config.get("repeat_page_offsets", []) and attempt == 1:
        offset = max(0, offset - limit)

    suppress_final = bool(
        incident
        and config.get("skip_final_page_on_incident")
        and offset < total_count
        and offset + limit >= total_count
    )
    return offset, suppress_final


def _to_local_offset(value: str) -> str:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return value
    return parsed.astimezone(ZoneInfo("Europe/Kyiv")).isoformat()


def _transform_payload(
    rows: list[dict[str, Any]],
    *,
    student_id: str,
    scenario: str,
    incident: bool,
) -> tuple[list[dict[str, Any]], int]:
    config = _faults().get(student_id, {})
    threshold = _parse_timestamp(config.get("breaking_schema_after"))
    transformed: list[dict[str, Any]] = []
    response_schema_version = 1

    for index, original in enumerate(rows):
        row = dict(original)

        if scenario == "timezones" and index % 3 == 1:
            row["occurred_at"] = _to_local_offset(str(row["occurred_at"]))

        occurred = _parse_timestamp(str(row.get("occurred_at")))
        use_breaking_schema = bool(
            incident
            and threshold is not None
            and occurred is not None
            and occurred >= threshold
            and config.get("rename_event_type_to_event_name")
        )
        if use_breaking_schema:
            row["event_name"] = row.pop("event_type", None)
            row["sequence_no"] = str(row.get("sequence_no"))
            row["payload_version"] = 3
            row["producer_version"] = "delivery-service-3.0"
            response_schema_version = max(response_schema_version, 3)
        else:
            response_schema_version = max(response_schema_version, int(row.get("payload_version", 1)))
        transformed.append(row)

    return transformed, response_schema_version


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "data_dir": DATA_DIR.as_posix(),
        "keys_loaded": len(_keys()),
    }


@app.get("/api/v1/me")
def me(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> dict[str, str]:
    return _identity(x_api_key)


@app.get("/api/v1/delivery-events")
def delivery_events(
    response: Response,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    date_from: str | None = Query(default=None, description="Filter by occurred_at, ISO date/datetime"),
    date_to: str | None = Query(default=None, description="Filter by occurred_at, ISO date/datetime"),
    available_before: str | None = Query(default=None, description="Only rows available by this timestamp"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    incident: bool = Query(default=False, description="Enable the student's final incident behavior"),
) -> dict[str, Any]:
    identity = _identity(x_api_key)
    student_id = identity["student_id"]
    scenario = identity.get("scenario", "")
    rows = _load_events(student_id)

    from_ts = _parse_timestamp(date_from)
    to_ts = _parse_timestamp(date_to)
    available_ts = _parse_timestamp(available_before)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        occurred = _parse_timestamp(str(row.get("occurred_at")))
        available = _parse_timestamp(str(row.get("available_at")))
        if from_ts and occurred and occurred < from_ts:
            continue
        if to_ts and occurred and occurred > to_ts:
            continue
        if available_ts and available and available > available_ts:
            continue
        filtered.append(row)

    requested_offset = _decode_cursor(cursor)
    effective_offset, suppress_final = _apply_faults(
        student_id=student_id,
        endpoint="delivery-events",
        offset=requested_offset,
        incident=incident,
        total_count=len(filtered),
        limit=limit,
    )

    if suppress_final:
        page: list[dict[str, Any]] = []
        next_cursor = None
    else:
        page = filtered[effective_offset : effective_offset + limit]
        next_offset = requested_offset + limit
        next_cursor = _encode_cursor(next_offset) if next_offset < len(filtered) else None

    payload, schema_version = _transform_payload(
        page,
        student_id=student_id,
        scenario=scenario,
        incident=incident,
    )
    request_id = _request_id(student_id, "delivery-events", cursor, limit)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Data-Schema-Version"] = str(schema_version)

    return {
        "request_id": request_id,
        "student_id": student_id,
        "schema_version": schema_version,
        "data": payload,
        "count": len(payload),
        "total_count": len(filtered),
        "next_cursor": next_cursor,
    }


@app.get("/api/v1/exchange-rates")
def exchange_rates(
    response: Response,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    currency: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    identity = _identity(x_api_key)
    student_id = identity["student_id"]
    rows = _load_rates(student_id)
    filtered = [
        row
        for row in rows
        if (currency is None or str(row["currency"]).upper() == currency.upper())
        and (date_from is None or str(row["rate_date"]) >= date_from[:10])
        and (date_to is None or str(row["rate_date"]) <= date_to[:10])
    ]
    offset = _decode_cursor(cursor)
    page = filtered[offset : offset + limit]
    next_offset = offset + limit
    next_cursor = _encode_cursor(next_offset) if next_offset < len(filtered) else None
    request_id = _request_id(student_id, "exchange-rates", cursor, limit)
    response.headers["X-Request-ID"] = request_id
    return {
        "request_id": request_id,
        "student_id": student_id,
        "data": page,
        "count": len(page),
        "total_count": len(filtered),
        "next_cursor": next_cursor,
    }
