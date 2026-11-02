"""Локальний сервер REST API CityFlow v1.0 для модуля Ingestion."""
import argparse
import os
import sys
from pathlib import Path
import uvicorn


def _default_variant_dir() -> str:
    for candidate in ('data/variant', 'data/ingestion_variant', 'data/lab04_variant'):
        if Path(candidate).exists():
            return candidate
    return 'data/variant'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--variant', default=_default_variant_dir())
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    folder = (Path(args.variant) / 'api').resolve()
    if not (folder / 'api_keys.json').is_file():
        parser.error(f'Немає даних API у {folder}; розпакуйте власний варіант у data/variant/.')
    os.environ['CITYFLOW_DATA_DIR'] = str(folder)
    os.environ['CITYFLOW_KEYS_FILE'] = str(folder / 'api_keys.json')
    os.environ['CITYFLOW_FAULTS_FILE'] = str(folder / 'faults.json')
    api_dir = str(Path(__file__).resolve().parent)
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    uvicorn.run('cityflow_api_v1:app', host='127.0.0.1', port=args.port, log_level='warning', app_dir=api_dir)