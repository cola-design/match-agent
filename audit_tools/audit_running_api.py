"""Read-only audit of the running API. Writes only a local JSON report."""
import getpass
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CASES = [
    ('QF', '335012800535', 'TGB1N-125H', 'TGB1N-125H 4P C100；TGB1N-125H；进线小型断路器；4P；C 100A；分断能力 10kA'),
    ('QS', '336010500031', 'TGH1N-125', 'TGH1N-125 4P 100A；TGH1N-125；交流隔离开关；4P；100A'),
    ('SCB', '338020700012', 'TGSCB1-40II', 'TGSCB1-40II 4P Imax40kA；TGSCB1-40II；SPD后备保护器；4P；最大放电电流 40kA'),
    ('SPD', '338020100237', 'TGDY55II-40', 'TGDY55II-40 385V 4P；TGDY55II-40；电涌保护器；4P'),
]


def fetch(base_url, key, path):
    req = Request(base_url + path, headers={'X-API-Key': key, 'Accept': 'application/json'})
    try:
        with urlopen(req, timeout=20) as response:
            status = response.status
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            return {'http_status': status, 'error': 'response_too_large'}, None
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            return {'http_status': status, 'error': 'invalid_json'}, None
        return {'http_status': status, 'body_type': type(body).__name__}, body
    except HTTPError as exc:
        return {'http_status': exc.code}, None
    except Exception as exc:
        # Do not echo URI, headers, API key or arbitrary server error text.
        return {'http_status': None, 'error_type': type(exc).__name__}, None


def shape_check(row):
    errors = []
    expected = {'material_code': str, 'description': str, 'category': dict,
                'standard_specs': dict, 'source_attributes': list, 'quality_flags': list,
                'source_status': str, 'data_status': str}
    types = {key: type(row.get(key)).__name__ for key in expected}
    for key, required_type in expected.items():
        value = row.get(key)
        if not isinstance(value, required_type):
            issue = {'field': key, 'expected': required_type.__name__, 'actual': type(value).__name__}
            if isinstance(value, str):
                try:
                    decoded = json.loads(value)
                    if isinstance(decoded, required_type):
                        issue['parseable_serialized_json'] = True
                except (ValueError, TypeError):
                    pass
            errors.append(issue)
    numeric_issues = []
    specs = row.get('standard_specs')
    if isinstance(specs, dict):
        for name, spec in specs.items():
            for item in spec if isinstance(spec, list) else [spec]:
                if not isinstance(item, dict) or 'value_num' not in item:
                    continue
                value = item['value_num']
                text = str(item.get('value_text', ''))
                tokens = [float(x) for x in re.findall(r'[+-]?\d+(?:\.\d+)?', text)]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not any(abs(value-token)<1e-8 for token in tokens):
                    numeric_issues.append({'field': name, 'value_text': text, 'value_num': value})
    return {'types': types, 'type_errors': errors, 'numeric_token_inconsistencies': numeric_issues}


def main():
    base = (input('API address [http://127.0.0.1:8000]: ').strip() or 'http://127.0.0.1:8000').rstrip('/')
    if not base.startswith(('http://', 'https://')):
        raise SystemExit('Enter an http:// or https:// API base URL.')
    key = os.environ.get('MONGODB_API_KEY') or getpass.getpass('API key (hidden input): ')
    report = {'checked_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
              'scope': 'read-only API sample and search contract audit; does not certify all live database rows', 'cases': []}
    health, body = fetch(base, key, '/health')
    health['healthy'] = isinstance(body, dict) and body.get('status') == 'ok'
    report['health'] = health
    if health['http_status'] == 401:
        raise SystemExit('API key rejected; use the same key as the running service.')
    day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    for role, code, model, query in CASES:
        case = {'role': role, 'material_code': code, 'required_model': model}
        info, rows = fetch(base, key, '/v1/products/' + code)
        info['document_count'] = len(rows) if isinstance(rows, list) else None
        if isinstance(rows, list):
            info['checks'] = [shape_check(row) for row in rows if isinstance(row, dict)]
        case['exact_product'] = info
        for label, suffix in [('price_with_date', '?' + urlencode({'as_of': day})), ('price_default_date', '')]:
            info, rows = fetch(base, key, '/v1/prices/' + code + suffix)
            if isinstance(rows, list):
                info['document_count'] = len(rows)
                info['field_types'] = [{k: type(row.get(k)).__name__ for k in ('material_code', 'unit_price_ex_tax', 'pricing_unit', 'tax_rate', 'price_version', 'valid_from', 'valid_to', 'status', 'price_basis')} for row in rows if isinstance(row, dict)]
            case[label] = info
        info, rows = fetch(base, key, '/v1/products/search?' + urlencode({'q': query, 'limit': 30}))
        if isinstance(rows, list):
            info['document_count'] = len(rows)
            info['matching_model_codes'] = [row.get('material_code') for row in rows if isinstance(row, dict) and model.upper() in str(row.get('description', '')).upper()]
        case['search'] = info
        report['cases'].append(case)
        print(role, ': product=', case['exact_product']['http_status'], ', search=', case['search']['http_status'])
    output = Path.cwd() / 'api_contract_audit_report.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Saved:', output)
    print('Report excludes API keys and MongoDB credentials. No database writes were performed.')


if __name__ == '__main__':
    main()
