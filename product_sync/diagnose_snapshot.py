"""Inspect the last saved source pages locally. No network, MongoDB or secrets required."""
import argparse
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import re
import sys


def code_issue(row):
    """Match the code validation in the delivered source.py exactly."""
    if not isinstance(row, dict):
        return 'ROW_NOT_OBJECT'
    if 'ITEM_NUM' not in row:
        return 'MATERIAL_CODE_FIELD_MISSING'
    value = row['ITEM_NUM']
    if not isinstance(value, str):
        return 'MATERIAL_CODE_NOT_STRING'
    value = value.strip()
    if not value:
        return 'MATERIAL_CODE_EMPTY_OR_WHITESPACE'
    if any(character.isspace() for character in value):
        return 'MATERIAL_CODE_CONTAINS_INTERNAL_WHITESPACE'
    return ''


def safe_scalar(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:160]
    return {'type': type(value).__name__, 'note': 'Non-scalar value omitted'}


def page_diagnostic(path, kind, limit=20):
    with gzip.open(path, 'rb') as stream:
        body = stream.read(64 * 1024 * 1024 + 1)
    if len(body) > 64 * 1024 * 1024:
        raise ValueError('SAVED_PAGE_TOO_LARGE')
    payload = json.loads(body.decode('utf-8-sig'))
    result = {'source_type': kind, 'source_kind': 'products' if kind == 1 else 'attributes',
              'saved_file': 'raw/type%s/%s' % (kind, path.name),
              'page': int(re.fullmatch(r'page-(\d+)\.json\.gz', path.name).group(1)),
              'rows_on_page': 0, 'invalid_code_rows': 0, 'examples': []}
    if not isinstance(payload, dict) or payload.get('status') is not True or not isinstance(payload.get('data'), list):
        result['response_issue'] = 'SOURCE_BUSINESS_OR_RESPONSE_ERROR'
        return result
    rows = payload['data']
    result['rows_on_page'] = len(rows)
    for index, row in enumerate(rows, 1):
        issue = code_issue(row)
        if not issue:
            continue
        result['invalid_code_rows'] += 1
        if len(result['examples']) >= limit:
            continue
        example = {'row_on_page': index, 'issue': issue, 'row_type': type(row).__name__}
        if isinstance(row, dict):
            value = row.get('ITEM_NUM')
            example.update(code_field_present='ITEM_NUM' in row,
                           code_value_type=type(value).__name__, code_value=safe_scalar(value),
                           code_value_repr=repr(value)[:200] if isinstance(value, (str, int, float, bool, type(None))) else '<non-scalar>',
                           available_fields=sorted(str(key) for key in row)[:40],
                           record_identifiers={key: safe_scalar(row[key]) for key in
                               ('RN', 'ITEM_ID', 'ATTR_ID', 'SA_ID', 'STATUS', 'LAST_UPDATE_DATE') if key in row})
        result['examples'].append(example)
    result['examples_truncated'] = result['invalid_code_rows'] > len(result['examples'])
    return result


def diagnose(state_dir, run_id=None):
    state = Path(state_dir).resolve()
    latest = {}
    if run_id is None:
        latest = json.loads((state / 'latest.json').read_text(encoding='utf-8-sig'))
        run_id = latest.get('run_id')
    if not isinstance(run_id, str) or not re.fullmatch(r'\d{8}T\d{6}Z_[a-f0-9]{8}', run_id):
        raise ValueError('INVALID_OR_MISSING_RUN_ID')
    folder = state / run_id
    saved_report = folder / 'report.json'
    if saved_report.is_file():
        latest = json.loads(saved_report.read_text(encoding='utf-8-sig'))
    report = {'run_id': run_id, 'diagnostic_version': 1,
              'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'scope': 'Last saved page of each source type in the selected run; offline, no refetch',
              'original_error': latest.get('error'), 'original_phase': latest.get('phase'),
              'network_used': False, 'database_accessed': False, 'pages_checked': []}
    for kind in (1, 2):
        paths = sorted(path for path in (folder / 'raw' / ('type' + str(kind))).glob('page-*.json.gz')
                       if re.fullmatch(r'page-\d+\.json\.gz', path.name))
        if paths:
            path = max(paths, key=lambda entry: int(entry.name.split('-')[1].split('.')[0]))
            report['pages_checked'].append(page_diagnostic(path, kind))
    if not report['pages_checked']:
        raise ValueError('NO_SAVED_SOURCE_PAGES_FOUND_IN_SELECTED_RUN')
    report['invalid_code_rows'] = sum(page['invalid_code_rows'] for page in report['pages_checked'])
    report['result'] = 'invalid_source_codes_located' if report['invalid_code_rows'] else 'no_invalid_code_in_checked_pages'
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    (folder / 'source_diagnostic.json').write_text(text, encoding='utf-8')
    output = state / 'source_diagnostic.json'
    output.write_text(text, encoding='utf-8')
    return report, output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', default='sync_state')
    parser.add_argument('--run-id')
    args = parser.parse_args()
    try:
        report, output = diagnose(args.state_dir, args.run_id)
    except Exception as exc:
        print('Local diagnostic failed:', str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
        return 1
    print('Result:', report['result'])
    for page in report['pages_checked']:
        print('type=%s page=%s rows=%s invalid_code_rows=%s' %
              (page['source_type'], page['page'], page['rows_on_page'], page['invalid_code_rows']))
    print('Report:', output)
    return 0


if __name__ == '__main__':
    sys.exit(main())
