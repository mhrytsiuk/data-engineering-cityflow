"""Виконання SQL-скрипту з виводом результатів та автоматичним зчитуванням DATABASE_URL із середовища або .env."""
import argparse
import os
from pathlib import Path
import sys


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
    raise SystemExit("Set DATABASE_URL in environment or .env first.")


p = argparse.ArgumentParser()
p.add_argument("file", type=Path)
a = p.parse_args()
dsn = _resolve_dsn()
import psycopg

try:
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(a.file.read_text(encoding="utf8"), prepare=False)
            while True:
                if cur.description:
                    print(" | ".join(x.name for x in cur.description))
                    for row in cur.fetchall():
                        print(" | ".join(str(v) for v in row))
                if not cur.nextset():
                    break
except Exception as e:
    print(f"SQL failed ({type(e).__name__}); inspect the statement in your SQL client.", file=sys.stderr)
    sys.exit(1)