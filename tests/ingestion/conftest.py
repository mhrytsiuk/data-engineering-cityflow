import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT / 'src/ingestion', ROOT / 'src', ROOT / 'src/lab04'):
    if candidate.exists():
        sys.path.insert(0, str(candidate))