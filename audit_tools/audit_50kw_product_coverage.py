"""Read-only, full-snapshot coverage audit for the supplied 50 kW test case."""
from collections import Counter
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import tarfile
import unicodedata
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ('QF', 'TGB1N-125H 4P C100 10kA', '335012800535'),
    ('QS', 'TGH1N-125 4P 100A', '336010500031'),
    ('SCB', 'TGSCB1-40II 4P II 20/40kA', '338020700012'),
    ('SPD', 'TGDY55II-40 385V 4P Imax40kA', '338020100237'),
    ('XG', 'PG29', 'TMP-AUX-PG29-01'),
    ('L25', 'DTLC-25mm²', 'TMP-AUX-DTLC25-01'),
    ('L16', 'DTLC-16mm²', 'TMP-AUX-DTLC16-01'),
    ('BR270', '表位支架270', 'TMP-AUX-BRACKET270-01'),
    ('BR160', '表位支架160', 'TMP-AUX-BRACKET160-01'),
    ('PE', 'PE接地排标准5孔', 'TMP-AUX-PE5-01'),
    ('F', '304不锈钢膨胀螺栓M8×80', 'TMP-AUX-ANCHOR-M8X80-01'),
    ('BOX', '550×700×150mm 201不锈钢 0.8mm IP54 RAL7035 双门', 'TMP-AUX-BOX-550700150-01'),
]


def norm(value):
    text = unicodedata.normalize('NFKC', str(value or '')).upper()
    return re.sub(r'\s+', '', text).replace('×', 'X').replace('*', 'X').replace('✕', 'X')


def json_parts(path):
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.endswith('.json'):
                yield from json.loads(archive.read(name))


def searchable(row):
    return norm(' '.join(str(row.get(key) or '') for key in
                        ('description', 'category', 'specification_text', 'source_attributes', 'standard_specs')))


def hits(row):
    text = searchable(row)
    role = norm(str(row.get('category') or '') + ' ' + str(row.get('description') or ''))
    result = []
    for symbol, model in [('QF', 'TGB1N-125H'), ('QS', 'TGH1N-125'), ('SCB', 'TGSCB1-40II'), ('SPD', 'TGDY55II-40')]:
        if model in text:
            result.append(symbol)
    if re.search(r'PG[-_]?29(?!\d)', text):
        result.append('XG')
    for symbol, size in [('L25', '25'), ('L16', '16')]:
        if re.search(r'DTLC[-_/]?' + size + r'(?!\d)', text):
            result.append(symbol)
    if any(word in role for word in ('支架', '表架', '表位')):
        for symbol, size in [('BR270', '270'), ('BR160', '160')]:
            if re.search(r'(?<!\d)' + size + r'(?!\d)', text):
                result.append(symbol)
    if any(word in role for word in ('接地排', 'PE排', '接地铜排', '接地端子排')):
        # Retain all grounding-bar records for manual hole-count review as well.
        result.append('PE')
    if re.search(r'M8(?:X|[-/])80(?!\d)', text) and any(word in role for word in ('螺栓', '螺丝', '紧固')):
        result.append('F')
    if re.search(r'550(?:X|[-/])700(?:X|[-/])150(?!\d)', text):
        result.append('BOX')
    return result


def brief(row, active_codes):
    code = str(row.get('material_code') or '')
    return {'material_code': code, 'description': row.get('description'),
            'category': row.get('category'), 'source_status': row.get('source_status'),
            'effective_data_status': 'ACTIVE' if code in active_codes else row.get('data_status'),
            'specification_text': row.get('specification_text'), 'quality_flags': row.get('quality_flags')}


def run(legacy_archive=None):
    paths = [ROOT / name for name in ('products_pending_review_json_parts.zip',
             'products_auto_active_json_parts.zip', 'formal_mongodb_import_bundle.tar.gz')]
    active_codes = {row['material_code'] for row in json_parts(paths[1])}
    matches = {case[0]: [] for case in CASES}
    code_records = {}
    categories = Counter()
    statuses = Counter()
    seen_codes = set()
    count = 0
    for row in json_parts(paths[0]):
        count += 1
        seen_codes.add(row['material_code'])
        statuses[row.get('source_status')] += 1
        category = row.get('category') or {}
        categories[category.get('middle', '') if isinstance(category, dict) else str(category)] += 1
        for symbol in hits(row):
            matches[symbol].append(brief(row, active_codes))
        if row['material_code'] in {case[2] for case in CASES}:
            code_records[row['material_code']] = brief(row, active_codes)
    type2_matches = {case[0]: [] for case in CASES}
    type2_count = 0
    with tarfile.open(paths[2]) as archive:
        for line in archive.extractfile('type2_only_candidates.jsonl'):
            row = json.loads(line)
            type2_count += 1
            for symbol in hits(row):
                type2_matches[symbol].append(brief(row, active_codes))
    assert len(seen_codes) == count and active_codes <= seen_codes
    legacy_records = {}
    if legacy_archive:
        with zipfile.ZipFile(legacy_archive) as archive:
            for name in archive.namelist():
                if not name.endswith('.md'):
                    continue
                text = archive.read(name).decode('utf-8-sig')
                for block in text.split('### PRODUCT'):
                    record = dict(re.findall(r'^([^\n:：]+)[:：]\s*([^\n]*)$', block, re.M))
                    code = record.get('物料编码')
                    if code in {case[2] for case in CASES}:
                        legacy_records[code] = record
    report = {'scope': 'Full saved formal-product import snapshot; read-only; not a new query of user MongoDB',
              'sources': [{'name': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths],
              'products_scanned': count, 'active_overlay_codes': len(active_codes),
              'other_main_records': count - len(active_codes), 'source_status_counts': dict(statuses),
              'type2_only_candidates_scanned': type2_count, 'category_counts': dict(categories),
              'cases': []}
    if legacy_archive:
        report['legacy_archive'] = {'name': legacy_archive.name,
            'sha256': hashlib.sha256(legacy_archive.read_bytes()).hexdigest(),
            'note': 'Original uploaded Markdown product archive, independently read without the cleaning output'}
    for symbol, requirement, code in CASES:
        rows = matches[symbol]
        report['cases'].append({'symbol': symbol, 'requirement': requirement,
            'known_formal_or_legacy_test_code': code, 'code_present_in_formal_snapshot': code in code_records,
            'known_code_record': code_records.get(code), 'broad_model_or_relevant_family_matches': len(rows),
            'active_broad_matches': sum(row['effective_data_status'] == 'ACTIVE' for row in rows),
            'matches': rows, 'type2_only_matches': type2_matches[symbol],
            'legacy_test_record': legacy_records.get(code)})
    output = ROOT / 'audit_evidence/test_50kw_formal_product_coverage_20261028.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    with output.with_suffix('.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['位号', '测试用例要求', '正式产品快照有对应记录', '对应正式编码',
                         '该编码发布状态', '旧测试编码', '旧测试记录状态'])
        for case in report['cases']:
            row = case['known_code_record'] or {}
            legacy = case['legacy_test_record'] or {}
            writer.writerow([case['symbol'], case['requirement'], '有' if row else '未找到',
                             row.get('material_code', ''), row.get('effective_data_status', ''),
                             legacy.get('物料编码', ''), legacy.get('状态', '')])
    print(json.dumps({key: report[key] for key in ('products_scanned', 'active_overlay_codes', 'other_main_records',
                     'source_status_counts', 'type2_only_candidates_scanned')}, ensure_ascii=False))
    for case in report['cases']:
        print(case['symbol'], 'known_code_present', case['code_present_in_formal_snapshot'],
              'broad_matches', case['broad_model_or_relevant_family_matches'],
              'active', case['active_broad_matches'], 'type2_only', len(case['type2_only_matches']))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--legacy-archive', type=Path)
    run(parser.parse_args().legacy_archive)
