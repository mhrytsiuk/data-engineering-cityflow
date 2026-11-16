"""Перевірка контрольних сум SHA-256 та кількості записів у експортованому каталозі."""
import argparse
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("directory", type=Path)
a = p.parse_args()
m = json.loads((a.directory / "manifest.json").read_text(encoding="utf8"))
for name, meta in m["files"].items():
    b = (a.directory / name).read_bytes()
    assert hashlib.sha256(b).hexdigest() == meta["sha256"], f"Changed file: {name}"
    rows = json.loads(b)
    assert len(rows) == meta["rows"], f"Count mismatch: {name}"
    print(f"{name}: {len(rows)} records; checksum OK")