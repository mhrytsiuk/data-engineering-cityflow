"""Готові утиліти: походження даних і транзакційна публікація. Не змінюйте без потреби."""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
import uuid
import hashlib

CSV_COLUMNS = {
    'orders': ['source_batch_id','source_available_at','order_id','customer_id',
        'merchant_id','created_at','order_status','currency','subtotal_amount',
        'delivery_fee','discount_amount','total_amount'],
    'order_items': ['source_batch_id','source_available_at','order_item_id','order_id',
        'product_id','quantity','unit_price','line_discount','line_total','currency'],
}
KEYS = {'orders': 'order_id', 'order_items': 'order_item_id', 'delivery_events': 'event_id'}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def save_response(response, raw_dir: Path, page_no: int, params: dict) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    name = f'page_{page_no:04d}'
    # Зберігаємо байти body, отримані від Requests, до JSON-перетворення.
    # Це не копія всього HTTP-сеансу (wire capture).
    body = response.content
    (raw_dir / (name+'.json')).write_bytes(body)
    save_json(raw_dir / (name+'.meta.json'), {
        'page': page_no, 'params': dict(params), 'extracted_at': utc_now(),
        'status_code': response.status_code,
        'request_id': response.headers.get('X-Request-ID'),
        'body_sha256': hashlib.sha256(body).hexdigest(),
    })


def prepare_records(student_id: str, sources: dict, raw_root: Path) -> tuple[list, dict]:
    unique = {}
    stats = {}
    for source, rows in sources.items():
        stats[source] = {'received': len(rows), 'unique': 0, 'duplicates_in_input': 0}
        for n, payload in enumerate(rows, 1):
            if not isinstance(payload, dict):
                raise ValueError(f'INVALID_RECORD:{source}')
            key = payload.get(KEYS[source])
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f'MISSING_KEY:{source}:{n}')
            identity = (student_id, source, key)
            if identity in unique:
                if unique[identity][3] != payload:
                    raise ValueError(f'CONFLICT_IN_INPUT:{source}:{key}')
                stats[source]['duplicates_in_input'] += 1
                continue
            raw_path = (str(raw_root / 'api') if source == 'delivery_events'
                        else str(raw_root / 'source.zip') + '!' + source + '.csv')
            unique[identity] = (student_id, source, key, payload, raw_path, n)
            stats[source]['unique'] += 1
    return list(unique.values()), stats


def db_connect(dsn: str):
    import psycopg
    return psycopg.connect(dsn, autocommit=True)


def init_db(dsn: str, sql_path: Path) -> None:
    with db_connect(dsn) as conn:
        conn.execute(sql_path.read_text(encoding='utf-8'))


def begin_run(dsn: str, run_id: str, student_id: str, raw_dir: Path) -> None:
    with db_connect(dsn) as conn:
        conn.execute('INSERT INTO audit.lab04_runs(run_id,student_id,status,raw_dir) VALUES (%s,%s,%s,%s)',
                     (run_id, student_id, 'RUNNING', str(raw_dir)))


def fail_run(dsn: str, run_id: str, code: str) -> None:
    with db_connect(dsn) as conn:
        conn.execute("UPDATE audit.lab04_runs SET status='FAILED',finished_at=clock_timestamp(),error_code=%s WHERE run_id=%s",
                     (code[:160], run_id))


def publish(dsn: str, run_id: str, records: list, stats: dict, *, fail_before_commit: bool = False) -> dict:
    from psycopg.types.json import Jsonb
    result = {s: dict(v) for s, v in stats.items()}
    with db_connect(dsn) as conn:
        with conn.transaction():
            conn.execute('CREATE TEMP TABLE lab04_incoming (student_id text, source text, record_key text, payload jsonb, raw_path text, source_row_number integer) ON COMMIT DROP')
            with conn.cursor() as cur:
                cur.executemany('INSERT INTO lab04_incoming VALUES (%s,%s,%s,%s,%s,%s)',
                    [(sid, source, key, Jsonb(payload), path, n) for sid,source,key,payload,path,n in records])
            conflict = conn.execute('SELECT i.source,i.record_key FROM lab04_incoming i\n                JOIN stg.lab04_records t USING(student_id,source,record_key)\n                WHERE i.payload IS DISTINCT FROM t.payload LIMIT 1').fetchone()
            if conflict:
                raise ValueError(f'CONFLICT_WITH_TARGET:{conflict[0]}:{conflict[1]}')
            inserted = conn.execute('INSERT INTO stg.lab04_records(student_id,source,record_key,payload,raw_path,source_row_number,first_run_id)\n                SELECT student_id,source,record_key,payload,raw_path,source_row_number,%s FROM lab04_incoming\n                ON CONFLICT(student_id,source,record_key) DO NOTHING RETURNING source', (run_id,)).fetchall()
            for source in result:
                n = sum(s[0] == source for s in inserted)
                result[source]['inserted'] = n
                result[source]['already_present'] = result[source]['unique'] - n
            if fail_before_commit:
                raise RuntimeError('INJECTED_BEFORE_COMMIT')
            conn.execute("UPDATE audit.lab04_runs SET status='SUCCESS',finished_at=clock_timestamp(),summary=%s WHERE run_id=%s",
                         (Jsonb(result), run_id))
    return result


def verify_db(dsn: str, student_id: str, expected: dict) -> dict:
    with db_connect(dsn) as conn:
        got = dict(conn.execute('SELECT source,count(*) FROM stg.lab04_records WHERE student_id=%s GROUP BY source',
                                (student_id,)).fetchall())
        want = expected['staging_unique']
        # Перевірка доречна для ЛР4: лише цей виданий пакет, не наступні batches.
        ok = all(got.get(k, 0) == v for k,v in want.items())
        return {'ok': ok, 'expected': want, 'actual': got}
