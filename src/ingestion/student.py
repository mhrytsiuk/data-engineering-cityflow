"""Модуль надійного отримання даних із файлових пакетів та REST API (Ingestion)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from pathlib import Path
import time
from typing import Any, Callable
import zipfile

import requests

try:
    from .support import CSV_COLUMNS, save_response
except ImportError:  # pragma: no cover - прямий запуск без пакета
    from support import CSV_COLUMNS, save_response


LOGGER = logging.getLogger("cityflow.ingestion")


def read_file_batch(package_path: Path, student_id: str) -> dict[str, list[dict[str, str]]]:
    """Читає вхідний ZIP-пакет, перевіряє контракт маніфесту і повертає сирі рядки.

    Очікуваний результат:
        {
            "orders": [dict, ...],
            "order_items": [dict, ...],
        }

    Обов'язкові перевірки:
    - у ZIP є PACKAGE_INFO.json;
    - student_id у маніфесті збігається з очікуваним;
    - кожен файл із маніфесту реально існує в архіві;
    - розмір кожного файла збігається із size_bytes;
    - SHA-256 кожного файла збігається із sha256;
    - CSV читається як UTF-8 (або UTF-8 з BOM), роздільник кома;
    - заголовки точно збігаються з CSV_COLUMNS["orders"] та CSV_COLUMNS["order_items"];
    - жоден рядок не має зайвих або пропущених колонок.

    При будь-якому порушенні піднімає ValueError із коротким зрозумілим повідомленням.
    Повертає значення як рядки без зміни типів полів.
    """
    package_path = Path(package_path)
    if not package_path.exists():
        raise ValueError(f"FILE_NOT_FOUND: {package_path}")

    try:
        zf_ctx = zipfile.ZipFile(package_path, "r")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"INVALID_ZIP_ARCHIVE: {package_path}") from exc

    with zf_ctx as zf:
        archive_names = set(zf.namelist())
        if "PACKAGE_INFO.json" not in archive_names:
            raise ValueError("MISSING_MANIFEST: PACKAGE_INFO.json not found in archive")

        try:
            manifest_raw = zf.read("PACKAGE_INFO.json").decode("utf-8-sig")
            manifest = json.loads(manifest_raw)
        except Exception as exc:
            raise ValueError("INVALID_MANIFEST_JSON: failed to parse PACKAGE_INFO.json") from exc

        if not isinstance(manifest, dict):
            raise ValueError("INVALID_MANIFEST_FORMAT: manifest root must be an object")

        manifest_student_id = manifest.get("student_id")
        if manifest_student_id != student_id:
            raise ValueError(
                f"STUDENT_ID_MISMATCH: expected {student_id!r}, got {manifest_student_id!r}"
            )

        manifest_files = manifest.get("files")
        if not isinstance(manifest_files, list) or not manifest_files:
            raise ValueError("INVALID_MANIFEST_FILES: 'files' must be a non-empty list")

        listed_paths: set[str] = set()
        for file_entry in manifest_files:
            if not isinstance(file_entry, dict):
                raise ValueError("INVALID_MANIFEST_ENTRY: file entry must be an object")

            rel_path = file_entry.get("path")
            expected_size = file_entry.get("size_bytes")
            expected_sha256 = file_entry.get("sha256")

            if not isinstance(rel_path, str) or not rel_path:
                raise ValueError("INVALID_MANIFEST_PATH: missing file path in manifest")
            if rel_path not in archive_names:
                raise ValueError(f"MISSING_FILE_IN_ARCHIVE: {rel_path}")
            if not isinstance(expected_size, int) or isinstance(expected_size, bool):
                raise ValueError(f"INVALID_MANIFEST_SIZE: {rel_path}")
            if not isinstance(expected_sha256, str) or not expected_sha256:
                raise ValueError(f"INVALID_MANIFEST_SHA256: {rel_path}")

            file_bytes = zf.read(rel_path)
            actual_size = len(file_bytes)
            if actual_size != expected_size:
                raise ValueError(
                    f"FILE_SIZE_MISMATCH for {rel_path}: expected {expected_size}, got {actual_size}"
                )

            actual_sha256 = hashlib.sha256(file_bytes).hexdigest()
            if actual_sha256.lower() != expected_sha256.lower():
                raise ValueError(
                    f"SHA256_MISMATCH for {rel_path}: expected {expected_sha256}, got {actual_sha256}"
                )

            listed_paths.add(rel_path)

        result: dict[str, list[dict[str, str]]] = {}
        for source_name, expected_columns in CSV_COLUMNS.items():
            csv_filename = f"{source_name}.csv"
            if csv_filename not in listed_paths:
                raise ValueError(f"MISSING_REQUIRED_CSV_IN_MANIFEST: {csv_filename}")
            if csv_filename not in archive_names:
                raise ValueError(f"MISSING_REQUIRED_CSV_IN_ARCHIVE: {csv_filename}")

            try:
                csv_text = zf.read(csv_filename).decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValueError(f"INVALID_UTF8_ENCODING: {csv_filename}") from exc

            reader = csv.reader(io.StringIO(csv_text, newline=""), delimiter=",")
            try:
                header = next(reader)
            except StopIteration as exc:
                raise ValueError(f"EMPTY_CSV_FILE: {csv_filename}") from exc

            if header != expected_columns:
                raise ValueError(
                    f"CSV_HEADER_MISMATCH in {csv_filename}: expected {expected_columns}, got {header}"
                )

            rows: list[dict[str, str]] = []
            expected_col_count = len(expected_columns)
            for line_idx, row in enumerate(reader, start=2):
                if len(row) != expected_col_count:
                    raise ValueError(
                        f"CSV_COLUMN_COUNT_MISMATCH in {csv_filename} at line {line_idx}: "
                        f"expected {expected_col_count} columns, got {len(row)}"
                    )
                row_dict = {col: str(val) for col, val in zip(expected_columns, row)}
                rows.append(row_dict)

            result[source_name] = rows

    return result


def request_page(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any],
    headers: dict[str, str],
    timeout: tuple[float, float] = (3, 10),
    max_attempts: int = 4,
    sleep: Callable[[float], None] = time.sleep,
) -> requests.Response:
    """Виконує один GET-запит до API з retry, backoff і класифікацією помилок.

    Правила:
    - використовує timeout=(3, 10) за замовчуванням;
    - не дозволяє редіректи: allow_redirects=False;
    - при 200 повертає response;
    - тимчасові помилки: 429, 500, 502, 503, 504, Timeout, ConnectionError -> retry;
    - постійні помилки: 400, 401, 403, 404, 422, редіректи 301/302, SSLError -> без retry;
    - розклад backoff для невдалих спроб #1, #2, #3: 1, 2, 4 секунди;
    - якщо 429 має Retry-After, чекає max(backoff, int(Retry-After));
    - якщо Retry-After некоректний або затримка > 30 секунд -> піднімає ValueError;
    - не логує секрети (X-API-Key, headers із ключем, повний DSN).
    """
    transient_statuses = {429, 500, 502, 503, 504}

    for attempt in range(1, max_attempts + 1):
        backoff_delay: float = float(2 ** (attempt - 1))
        try:
            response = session.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
            )
        except requests.exceptions.SSLError:
            LOGGER.error(
                "Permanent SSL error on GET %s (attempt %d/%d) - aborting without retry",
                url,
                attempt,
                max_attempts,
            )
            raise
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            if attempt >= max_attempts:
                LOGGER.error(
                    "Network error %s on GET %s exhausted %d attempts",
                    type(exc).__name__,
                    url,
                    max_attempts,
                )
                raise
            if backoff_delay > 30:
                raise ValueError(f"RETRY_DELAY_TOO_LARGE: {backoff_delay}")
            LOGGER.warning(
                "Transient network error %s on GET %s (attempt %d/%d), sleeping %s s",
                type(exc).__name__,
                url,
                attempt,
                max_attempts,
                backoff_delay,
            )
            sleep(int(backoff_delay) if backoff_delay.is_integer() else backoff_delay)
            continue

        status = response.status_code
        if status == 200:
            return response

        if status in transient_statuses:
            if attempt >= max_attempts:
                LOGGER.error(
                    "Transient HTTP %d on GET %s exhausted %d attempts",
                    status,
                    url,
                    max_attempts,
                )
                raise requests.HTTPError(
                    f"HTTP {status} after {max_attempts} attempts for {url}",
                    response=response,
                )

            wait_seconds: float = backoff_delay
            if status == 429:
                retry_after_header = None
                if isinstance(response.headers, dict) or hasattr(response.headers, "get"):
                    retry_after_header = response.headers.get("Retry-After") or response.headers.get(
                        "retry-after"
                    )
                if retry_after_header is not None:
                    try:
                        retry_after_val = int(str(retry_after_header).strip())
                    except ValueError as exc:
                        raise ValueError(
                            f"INVALID_RETRY_AFTER: {retry_after_header!r}"
                        ) from exc
                    if retry_after_val < 0:
                        raise ValueError(f"NEGATIVE_RETRY_AFTER: {retry_after_val}")
                    wait_seconds = max(wait_seconds, float(retry_after_val))

            if wait_seconds > 30:
                raise ValueError(f"RETRY_AFTER_TOO_LARGE: {wait_seconds} > 30s")

            LOGGER.warning(
                "Transient HTTP %d on GET %s (attempt %d/%d), sleeping %s s",
                status,
                url,
                attempt,
                max_attempts,
                wait_seconds,
            )
            sleep(int(wait_seconds) if wait_seconds.is_integer() else wait_seconds)
            continue

        LOGGER.error(
            "Permanent or redirect HTTP %d on GET %s (attempt %d/%d) - no retry",
            status,
            url,
            attempt,
            max_attempts,
        )
        raise requests.HTTPError(
            f"Permanent or unexpected HTTP status {status} for {url}",
            response=response,
        )

    raise RuntimeError("UNREACHABLE: max_attempts must be >= 1")


def fetch_pages(
    session: requests.Session,
    base_url: str,
    api_key: str,
    student_id: str,
    params: dict[str, Any],
    raw_dir: Path,
    request_fn: Callable[..., requests.Response] | None = None,
) -> dict[str, Any]:
    """Завантажує всі сторінки delivery-events через cursor pagination.

    Повертає словник:
        {
            "events": [...],
            "page_count": <int>,
            "total_count": <int>,
        }
    """
    req_callable = request_fn or request_page
    url = f"{base_url.rstrip('/')}/api/v1/delivery-events"
    headers = {"X-API-Key": api_key}

    query_params: dict[str, Any] = dict(params)
    query_params.pop("cursor", None)

    raw_dir = Path(raw_dir)
    events: list[dict[str, Any]] = []
    page_no = 0
    expected_total_count: int | None = None
    seen_cursors: set[str] = set()

    while True:
        page_no += 1
        if page_no > 10000:
            raise ValueError("PAGINATION_LIMIT_EXCEEDED: page_no > 10000")

        current_params = dict(query_params)
        response = req_callable(
            session,
            url,
            params=current_params,
            headers=headers,
        )

        save_response(response, raw_dir, page_no, current_params)

        try:
            payload = response.json()
        except Exception as exc:
            raise ValueError(f"INVALID_JSON_RESPONSE on page {page_no}") from exc

        if not isinstance(payload, dict):
            raise ValueError(f"INVALID_ENVELOPE_ROOT on page {page_no}: expected dict")

        resp_student_id = payload.get("student_id")
        if resp_student_id != student_id:
            raise ValueError(
                f"ENVELOPE_STUDENT_ID_MISMATCH on page {page_no}: "
                f"expected {student_id!r}, got {resp_student_id!r}"
            )

        schema_version = payload.get("schema_version")
        if isinstance(schema_version, bool) or schema_version not in (1, 2):
            raise ValueError(
                f"UNSUPPORTED_SCHEMA_VERSION on page {page_no}: {schema_version!r}"
            )

        data = payload.get("data")
        if not isinstance(data, list):
            raise ValueError(f"INVALID_DATA_FIELD on page {page_no}: 'data' must be a list")

        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(
                    f"INVALID_EVENT_ITEM on page {page_no} index {idx}: expected dict"
                )
            event_id = item.get("event_id")
            if not isinstance(event_id, str) or not event_id.strip():
                raise ValueError(
                    f"MISSING_EVENT_ID on page {page_no} index {idx}: {event_id!r}"
                )

        count = payload.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"INVALID_COUNT_FIELD on page {page_no}: {count!r}")
        if count != len(data):
            raise ValueError(
                f"COUNT_MISMATCH on page {page_no}: count={count}, len(data)={len(data)}"
            )

        total_count = payload.get("total_count")
        if isinstance(total_count, bool) or not isinstance(total_count, int) or total_count < 0:
            raise ValueError(f"INVALID_TOTAL_COUNT on page {page_no}: {total_count!r}")

        if expected_total_count is None:
            expected_total_count = total_count
        elif total_count != expected_total_count:
            raise ValueError(
                f"TOTAL_COUNT_CHANGED on page {page_no}: "
                f"was {expected_total_count}, now {total_count}"
            )

        if "next_cursor" not in payload:
            raise ValueError(f"MISSING_NEXT_CURSOR_KEY on page {page_no}")
        next_cursor = payload["next_cursor"]
        if next_cursor is not None and (
            not isinstance(next_cursor, str) or not next_cursor.strip()
        ):
            raise ValueError(f"INVALID_NEXT_CURSOR on page {page_no}: {next_cursor!r}")

        events.extend(data)

        if next_cursor is None:
            break

        if next_cursor in seen_cursors:
            raise ValueError(f"REPEATED_CURSOR on page {page_no}: {next_cursor!r}")

        seen_cursors.add(next_cursor)
        query_params["cursor"] = next_cursor

    assert expected_total_count is not None
    if len(events) != expected_total_count:
        raise ValueError(
            f"TOTAL_COUNT_MISMATCH: collected {len(events)} events, "
            f"expected total_count={expected_total_count}"
        )

    return {
        "events": events,
        "page_count": page_no,
        "total_count": expected_total_count,
    }