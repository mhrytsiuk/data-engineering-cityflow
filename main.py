"""Запускайте з кореня свого репозиторію. Реалізація 3 функцій — у student.py."""
import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import uuid
from pathlib import Path
import requests
from dotenv import load_dotenv
from support import save_json, prepare_records, init_db, begin_run, fail_run, publish, verify_db
import student

ROOT = Path.cwd()

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['check','init-db','file','api','run','verify'])
    parser.add_argument('--fail-before-commit', action='store_true')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env.lab04')
    variant = Path(os.getenv('CITYFLOW_VARIANT_DIR', 'data/lab04_variant'))
    cfg = json.loads((variant / 'settings.json').read_text(encoding='utf-8'))
    sid = os.getenv('CITYFLOW_STUDENT_ID', '')
    key = os.getenv('CITYFLOW_API_KEY', '')
    base = os.getenv('CITYFLOW_API_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
    dsn = os.getenv('DATABASE_URL', '')
    if sid != cfg['student_id'] or not key:
        raise ValueError('Перевірте student_id та API key у .env.lab04; не змішуйте варіанти.')
    if args.command == 'init-db':
        init_db(dsn, ROOT / 'sql/lab04/00_setup.sql')
        print('Створено об’єкти ЛР4. Попередні дані не змінено.'); return 0
    if args.command == 'verify':
        expected = json.loads((variant/'expected_counts.json').read_text(encoding='utf-8'))
        result = verify_db(dsn, sid, expected)
        print(json.dumps(result,ensure_ascii=False,indent=2)); return 0 if result['ok'] else 1
    if args.command == 'check':
        r = requests.get(base+'/api/v1/me',headers={'X-API-Key':key},timeout=(3,10),allow_redirects=False)
        r.raise_for_status()
        if r.json().get('student_id') != sid:
            raise ValueError('API повернув не ваш варіант.')
        print('API доступний; student_id збігається.'); return 0
    run_id = str(uuid.uuid4())
    raw = ROOT/'data/raw/lab04'/run_id
    raw.mkdir(parents=True,exist_ok=False)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.FileHandler(raw/'run.log',encoding='utf-8'),logging.StreamHandler()])
    full = args.command == 'run'
    started = False
    db_committed = False
    summary = {'run_id':run_id,'student_id':sid,'status':'RUNNING','command':args.command}
    try:
        if full:
            begin_run(dsn,run_id,sid,raw); started = True
        sources = {}
        if args.command in ['file','run']:
            original = variant/'input'/cfg['package_filename']
            shutil.copyfile(original,raw/'source.zip')
            sources.update(student.read_file_batch(raw/'source.zip',sid))
            summary['file_sha256'] = hashlib.sha256((raw/'source.zip').read_bytes()).hexdigest()
        if args.command in ['api','run']:
            with requests.Session() as session:
                fetched = student.fetch_pages(session,base,key,sid,dict(cfg['api_params']),raw/'api')
            sources['delivery_events'] = fetched['events']
            summary['api_pages'] = fetched['page_count']
            summary['api_total_count'] = fetched['total_count']
        records,stats = prepare_records(sid,sources,raw)
        summary['sources'] = stats
        expected = json.loads((variant/'expected_counts.json').read_text(encoding='utf-8'))
        if any(v['unique'] != expected['staging_unique'][src] for src,v in stats.items()):
            raise ValueError('INVALID_VARIANT_COUNTS')
        if full:
            summary['sources'] = publish(dsn,run_id,records,stats,fail_before_commit=args.fail_before_commit)
            db_committed = True
        summary['status'] = 'SUCCESS' if full else 'EXTRACTED_ONLY'
        save_json(raw/'run.json',summary)
        print(json.dumps(summary,ensure_ascii=False,indent=2)); return 0
    except Exception as exc:
        if db_committed:
            logging.error('COMMIT уже відбувся. Audit SUCCESS є джерелом стану; локальний звіт не вдалося записати. Перевірте диск і run_id=%s.',run_id)
            return 1
        # Не виводимо пароль, headers, DSN або повні response bodies.
        code = type(exc).__name__
        text = str(exc)
        if text.startswith(('INJECTED_', 'CONFLICT_', 'MISSING_', 'INVALID_')):
            code = text.split(':')[0]
        summary.update(status='FAILED', error_code=code)
        if started:
            try:
                fail_run(dsn,run_id,code)
            except Exception:
                logging.error('Не вдалося завершити audit; перевірте RUNNING запис %s.',run_id)
        save_json(raw/'run.json',summary)
        logging.error('run_id=%s failed: %s; raw збережено. Перевірте реалізацію та tests.',run_id,code)
        return 1

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'Підготовку не завершено ({type(exc).__name__}). Перевірте README, файли варіанта, .env.lab04 та БД.',file=sys.stderr)
        sys.exit(1)
