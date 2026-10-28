"""Preview or explicitly apply an in-place product repair with a local rollback plan.

Default: read-only preview. No products are deleted. Prices and publication status are untouched.
Requires pymongo and the adjacent product_contract.py.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import getpass
import gzip
import json
import os
from pathlib import Path
import sys

from bson import json_util
from pymongo import MongoClient, UpdateOne
from product_contract import ProductContractError, normalize_product

PATCH_FIELDS = ('category', 'standard_specs', 'source_attributes', 'quality_flags', 'provenance', 'item_id')


def delta(document):
    normalized = normalize_product(document)
    return {field: normalized[field] for field in PATCH_FIELDS
            if field in normalized and (field not in document or normalized[field] != document[field])}


def connect(args):
    uri = None if args.prompt else os.environ.get('MONGODB_URI')
    if uri:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    else:
        host = input('MongoDB host IP: ').strip()
        username = input('MongoDB user [match_agent_api for preview]: ').strip() or 'match_agent_api'
        password = getpass.getpass('MongoDB password (hidden): ')
        auth_db = input('Authentication database [admin]: ').strip() or 'admin'
        client = MongoClient(host=host, port=args.port, username=username, password=password,
                             authSource=auth_db, serverSelectionTimeoutMS=5000)
    db = client[args.database]
    db.command('ping')
    return client, db['products']


def flush_batch(collection, operations, count):
    if not operations:
        return count
    result = collection.bulk_write(operations, ordered=True)
    if result.matched_count != len(operations):
        raise RuntimeError('Concurrent edit detected; stop and inspect the saved plan before continuing')
    return count + result.modified_count


def apply_plan(collection, plan, rollback=False):
    applied = 0
    operations = []
    with gzip.open(plan, 'rt', encoding='utf-8') as stream:
        for line in stream:
            entry = json_util.loads(line)
            before, after = entry['before'], entry['after']
            if rollback:
                current = collection.find_one({'_id': entry['_id'], 'material_code': entry['material_code']})
                if current is None:
                    raise RuntimeError('Rollback target missing; no replacement will be inserted')
                matches_before = all(key in current and current[key] == value for key, value in before.items()) and all(key not in current for key in entry.get('missing', []))
                if matches_before:
                    continue
                if not all(key in current and current[key] == value for key, value in after.items()):
                    raise RuntimeError('Concurrent edit detected during rollback')
                expected = after
                update = {}
                if before:
                    update['$set'] = before
                missing = entry.get('missing', [])
                if missing:
                    update['$unset'] = {field: '' for field in missing}
            else:
                expected = before
                update = {'$set': after}
            query = {'_id': entry['_id'], 'material_code': entry['material_code']}
            # Optimistic guards protect records changed since the preview/backup was created.
            for field, value in expected.items():
                query[field] = {'$eq': value}
            if not rollback:
                for field in entry.get('missing', []):
                    query[field] = {'$exists': False}
            operations.append(UpdateOne(query, update))
            if len(operations) == 500:
                applied = flush_batch(collection, operations, applied)
                operations = []
    return flush_batch(collection, operations, applied)


def build_plan(collection, folder):
    plan = folder / 'products_repair_plan.jsonl.gz'
    stats = Counter(scanned=0, documents_to_change=0, invalid_documents=0)
    fields = Counter()
    errors = []
    with gzip.open(plan, 'wt', encoding='utf-8') as stream:
        # Iterate only products. Never read or update prices in this migration.
        for doc in collection.find({}).batch_size(500):
            stats['scanned'] += 1
            if stats['scanned'] % 5000 == 0:
                print('Preview scanned:', stats['scanned'], '(no database writes yet)', file=sys.stderr)
            try:
                patch = delta(doc)
            except ProductContractError as exc:
                stats['invalid_documents'] += 1
                if len(errors) < 30:
                    errors.append({'material_code': str(doc.get('material_code', '')), 'field': exc.field})
                continue
            if patch:
                entry = {'_id': doc['_id'], 'material_code': doc['material_code'],
                         'before': {key: doc[key] for key in patch if key in doc},
                         'missing': [key for key in patch if key not in doc], 'after': patch}
                stream.write(json_util.dumps(entry, ensure_ascii=False) + '\n')
                stats['documents_to_change'] += 1
                fields.update(patch.keys())
    # The complete before/after plan is closed and flushed BEFORE any writes begin.
    with plan.open('rb+') as raw:
        os.fsync(raw.fileno())
    report = {'mode': 'preview', 'stats': dict(stats), 'changed_fields': dict(fields),
              'invalid_examples': errors, 'plan_file': plan.name,
              'preserved': ['material_code', 'source_status', 'data_status', 'all price records']}
    (folder/'repair_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return plan, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default='match_agent')
    parser.add_argument('--port', type=int, default=27017)
    parser.add_argument('--prompt', action='store_true', help='Prompt for connection details instead of using MONGODB_URI')
    parser.add_argument('--apply', action='store_true', help='Explicitly apply after creating a complete local before/after backup')
    parser.add_argument('--rollback', type=Path, help='Explicitly roll back changes using a saved repair plan')
    args = parser.parse_args()
    if args.apply and args.rollback:
        parser.error('--apply and --rollback are mutually exclusive')
    folder = Path.cwd() / ('products_repair_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(mode=0o700)
    client = None
    try:
        print('Mode:', 'rollback' if args.rollback else 'apply after backup' if args.apply else 'read-only preview', flush=True)
        client, collection = connect(args)
        if args.rollback:
            changed = apply_plan(collection, args.rollback, rollback=True)
            report = {'mode': 'rollback', 'modified_documents': changed}
        else:
            plan, report = build_plan(collection, folder)
            if args.apply:
                if report['stats'].get('invalid_documents', 0):
                    raise RuntimeError('Malformed products found; review repair_report.json before applying')
                # Administrator/owner must have granted products write permission.
                changed = apply_plan(collection, plan)
                report.update(mode='applied', modified_documents=changed)
                remaining = Counter()
                for doc in collection.find({}).batch_size(500):
                    try:
                        if delta(doc): remaining['still_need_repair'] += 1
                    except ProductContractError:
                        remaining['invalid_documents'] += 1
                report['verification'] = dict(remaining)
        (folder/'repair_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print('Local report folder:', folder)
        if report.get('verification'):
            print('STOPPED: verification found remaining changes; inspect repair_report.json.', file=sys.stderr)
            return 2
    except Exception as exc:
        # Do not print credential-bearing connection details.
        report_path = folder / 'repair_report.json'
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding='utf-8'))
        else:
            report = {'mode': 'rollback' if args.rollback else 'apply' if args.apply else 'preview'}
        report.update(result='stopped', error_type=type(exc).__name__)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('STOPPED:', type(exc).__name__, 'Check permissions/connectivity and repair_report.json.', file=sys.stderr)
        print('Local report folder:', folder, file=sys.stderr)
        if args.apply or args.rollback:
            print('Partial application is possible after a database write failure; retain the plan for inspection or rollback.', file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
