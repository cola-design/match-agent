"""One page-wide inventory after local reading. No drawing-specific counts or numbering."""
import base64
import json
import re
import zlib
from collections import Counter
from spatial import *


def fragment_reason(row):
    raw = norm(row['raw']).upper()
    literal = decode(raw)
    p = literal['explicit_parameters']
    if raw in ('I/O', 'IO', 'L1', 'L2', 'L3', 'N', 'PE'):
        return 'port_or_phase_not_purchase_entity'
    # Reject a recognisable clipped rating, not all unfamiliar manufacturer model names.
    stripped = re.sub(r'^L[123](?:[~～-]L?[123])?\s*', '', raw)
    if re.fullmatch(r'(?:MCB|MCCB|RCB|RCBO|RCCB|RCD)(?:[-/][A-Z0-9]*)?', stripped) and not p.get('pole_marking'):
        return 'clipped_protection_label'
    if literal['kind'] == 'cable':
        if re.search(r'UTP/CAT$|STP/CAT$|WDZ[A-Z0-9]*-$', raw):
            return 'clipped_cable_label'
    # One box spanning two distinct printed component labels is not one product.
    if re.search(r'\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\s*V', raw) and p.get('drawing_abbreviation'):
        return 'combined_transformer_and_breaker_labels'
    return ''


def acceptable_anchor(tag, cabinet_tags=()):
    key = canonical(tag)
    if not key or key in {canonical(t) for t in cabinet_tags}:
        return False
    if decode(tag)['kind'] in ('cable', 'phase', 'power_marking', 'voltage_ratio'):
        return False
    if re.search(r'WDZ|NHBV|ZNBV|CAT[5-8]|JDG|\d+[*X×]\d+|\d+\s*(?:VA|MA|KV|KW)$', key):
        return False
    if key in ('I/O', 'IO', 'N', 'PE', '回路总线', '总线'):
        return False
    # A cabinet-style tag with a floor prefix is not an outgoing circuit number.
    if re.fullmatch(r'\d+[A-Z]{2,}\d*', key):
        return False
    return True


def request_view(context_json):
    x = json.loads(context_json)
    out = {'mode': x['mode'], 'page': x['page'], 'view_id': x['view_id'],
           'coordinate_system': '当前图片左上0,0，右下1000,1000；不得输出整页坐标'}
    if x['mode'] == 'reconcile':
        out['unverified_local_readings'] = x.get('reconcile_hints', {})
        out['instructions'] = '局部读法只是核对提示，不是器件清单或坐标依据。重新在完整图面定位每个独立符号及印刷编号。同一位置只输出一次；不把裁图次数当实体数量。'
    text = j(out)
    if len(text) > 55000:
        raise ValueError('整页复核提示超过传输预算，未截断局部读法')
    return {'request_json': text}


def prepare_reconciliation(packets, manifest_compact):
    records = [json.loads(p) for p in packets]
    manifest = uncompress(manifest_compact)
    jobs = []
    for page in manifest['images']:
        number = page['page_number']
        reads = []
        for packet in records:
            if packet['job']['page'] != number or packet['job']['mode'] == 'overview':
                continue
            for row in packet.get('observations', []) + packet.get('quarantined_observations', []):
                if row['kind'] in ('device', 'cable', 'unknown', 'parameter'):
                    reads.append({'view': packet['job']['view_id'], 'raw': row['raw'],
                                  'kind': row['kind'], 'category': row.get('category'),
                                  'warning': fragment_reason(row)})
        # Group hints by wording while retaining which views observed each wording.
        grouped = {}
        for r in reads:
            k = (r['raw'], r['kind'], r.get('category'), r['warning'])
            grouped.setdefault(k, []).append(r['view'])
        hints = {'readings': [dict(raw=k[0], kind=k[1], category=k[2], warning=k[3],
                                  views=sorted(set(v))) for k, v in grouped.items()],
                 'warning': '各局部重复、相别、文字截断、回路号与位置都可能有错，必须以本轮完整图片重新核对。'}
        # Native counts are available only as whole-file completeness hints, never positions.
        if manifest['page_count'] == 1:
            inv = source_inventory(manifest.get('native_text', ''))
            hints['unordered_native_mentions'] = inv
        job = {'job_id': 'P%d-R' % number, 'page': number, 'view_id': 'R',
               'mode': 'reconcile', 'file_serial': manifest['uploaded_names'].index(page['filename']) + 1,
               'image_name': page['filename'], 'rect': [0, 0, 1000, 1000], 'reconcile_hints': hints}
        if len(j(job)) > 55000:
            raise ValueError('单页局部读法超过复核预算，请按子图拆分；未截断内容')
        jobs.append(j(job))
    return {'jobs': jobs}


def sanitise_master(packet):
    """Keep rejected text and diagnostics; remove it from purchase candidates."""
    packet = json.loads(j(packet))
    objects = []; rejected = packet.setdefault('quarantined_observations', [])
    for row in packet.get('observations', []):
        reason = fragment_reason(row) if row['kind'] in ('device', 'cable', 'unknown') else ''
        if reason:
            rejected.append(dict(row, quarantine_reason=reason))
            packet['issues'].append(problem('recognition_fragment', '复核仍含残缺或拼接标注，未作为采购器件：' + row['raw']))
        else:
            objects.append(row)
    packet['observations'] = objects
    tags = [t for d in packet.get('drawings', []) for t in d.get('cabinet_tags', [])]
    valid = []
    for anchor in packet.get('anchors', []):
        if acceptable_anchor(anchor['tag'], tags):
            valid.append(anchor)
        else:
            packet['issues'].append(problem('invalid_circuit_label', '柜号、相别或线缆文字未作为出线回路号：' + anchor['tag']))
    packet['anchors'] = valid
    # Duplicate JSON records at the very same pixel location are not separate symbols.
    deduped = []; redirects = {}
    for row in packet['observations']:
        old = next((v for v in deduped if v['kind'] == row['kind'] and
                    label_key(v['raw']) == label_key(row['raw']) and v.get('bbox') and row.get('bbox') and
                    overlap(v['bbox'], row['bbox']) >= 0.85 and
                    max(abs(a-b) for a,b in zip(center(v['bbox']), center(row['bbox']))) < 2), None)
        if old:
            redirects[row['local_id']] = old['local_id']
            rejected.append(dict(row, quarantine_reason='exact_duplicate_master_record'))
        else:
            deduped.append(row)
    packet['observations'] = deduped
    for link in packet.get('links', []):
        link['source'] = redirects.get(link['source'], link['source'])
        if link['type'] == 'parameter':
            link['target'] = redirects.get(link['target'], link['target'])
    return packet


def join_result(value):
    result = json.loads(value['result_json'])
    if result.get('details_in') != 'result_pages':
        return result
    out = {'schema_version': result['schema_version'], 'summary': result['summary']}
    for part in value['result_pages']:
        for key, rows in json.loads(part).items():
            if isinstance(rows, list):
                out.setdefault(key, []).extend(rows)
    return out


def pack_result(result):
    pages = []; current = {'schema_version': result['schema_version'], 'summary': result['summary']}
    for key in ('drawings', 'items', 'circuits', 'document_annotations', 'issues', 'diagnostics', 'quarantined_observations'):
        for entry in result.get(key, []):
            if len(j(entry)) > 55000:
                raise ValueError('单条识图证据超过传输预算，未删减')
            test = dict(current); test[key] = list(test.get(key, [])) + [entry]
            if len(j(test)) > 58000:
                pages.append(j(current)); current = {'schema_version': result['schema_version']}
            current.setdefault(key, []).append(entry)
    pages.append(j(current))
    text = j(result)
    if len(text) > 60000:
        text = j({'schema_version': result['schema_version'], 'summary': result['summary'],
                  'details_in': 'result_pages', 'parts': len(pages)})
    return {'result_json': text, 'result_pages': pages}


def collect_reconciled(local_packets, master_packets, manifest_compact):
    local = [json.loads(p) for p in local_packets]
    master = [sanitise_master(json.loads(p)) for p in master_packets]
    manifest = uncompress(manifest_compact); manifest['views_per_page'] = 1
    encoded = base64.b64encode(zlib.compress(j(manifest).encode())).decode()
    value = collect_spatial([j(p) for p in master], encoded)
    result = join_result(value)
    items = result.get('items', []); issues = result.setdefault('issues', [])
    evidence = result.setdefault('quarantined_observations', [])
    # Local reads never create extra procurement entities, including unmatched fragments.
    for packet in local:
        for row in packet.get('observations', []) + packet.get('quarantined_observations', []):
            if row['kind'] not in ('device', 'cable', 'unknown'):
                continue
            matches = [x['id'] for x in items if x['page'] == row['page'] and
                       label_key(x['raw']) == label_key(row['raw'])]
            reason = 'local_reading_corresponds_to_master' if matches else fragment_reason(row) or 'local_only_unverified'
            key = label_key(row['raw'])
            if not matches and fragment_reason(row) and len(key) >= 4:
                possible = [x['id'] for x in items if x['page'] == row['page'] and key in label_key(x['raw'])]
                if possible:
                    reason = 'local_fragment_of_master'; matches = possible
            if packet['job']['mode'] == 'overview':
                reason = 'overview_not_inventory'
            evidence.append(dict(row, quarantine_reason=reason, candidate_master_ids=matches))
    # Check source-text multiplicity in both directions, as a discrepancy, never a quantity.
    if manifest['page_count'] == 1:
        inv = source_inventory(manifest.get('native_text', ''))
        for entry in inv['candidates']:
            matching = [x for x in items if label_key(x['raw']) == label_key(entry['raw'])]
            if len(matching) > entry['text_mentions']:
                message = '“%s”原生文字出现%d处，整图器件候选%d处，存在重复或文字提取差异，须核对实体位置' % (entry['raw'], entry['text_mentions'], len(matching))
                issues.append(problem('native_count_discrepancy', message, page=1))
                for x in matching:
                    issues.append(problem('native_count_discrepancy', message, page=1, item_id=x['id']))
        ratios = [e for e in inv['candidates'] if e['kind'] == 'voltage_ratio']
        for e in ratios:
            ratio = decode(e['raw'])['explicit_parameters'].get('voltage_pair_v')
            found = [x for x in items if x['category'] == 'transformer' and
                     x.get('literal_parameters', {}).get('voltage_pair_v') == ratio]
            if not found:
                issues.append(problem('transformer_symbol_review', '原文有电压比“%s”，但整图未得到对应独立变压器候选；应复核符号，不能拼入断路器型号或按文字自动补造器件' % e['raw'], page=1))
    # A single master must not have the same circuit tag at separated positions in one drawing.
    circuits = result.get('circuits', [])
    counts = Counter((a['page'], a.get('drawing_id'), canonical(a['tag'])) for a in circuits)
    ambiguous = {a['id'] for a in circuits if counts[(a['page'], a.get('drawing_id'), canonical(a['tag']))] > 1}
    for x in items:
        if x.get('circuit_id') in ambiguous:
            x.update(circuit=None, load=None, circuit_id=None, binding_status='conflict')
            issues.append(problem('duplicate_circuit_tag', '整图同一子图的回路号出现不同位置，未采用歧义归属', page=x['page'], item_id=x['id']))
    # Keep circuit members consistent with the final binding state.
    for a in circuits:
        a['members'] = [x['id'] for x in items if x.get('circuit_id') == a['id']]
    unmatched = {}
    for row in evidence:
        if row['quarantine_reason'] == 'local_only_unverified':
            unmatched.setdefault((row['page'], row['raw']), []).append(row['job_id'])
    for (page, raw), views in unmatched.items():
        issues.append(problem('local_reading_unresolved', '局部读到但整图尚未核实“%s”；保留在识图待复核，不直接列入采购器件' % raw, page=page))
    failures = [p['job']['job_id'] for p in local + master if p['status'] not in ('parsed', 'blank')]
    result['schema_version'] = 'pdf-spatial-circuit/30.0'
    result['summary'].update(workflow_version='30.0', views_expected=8*manifest['page_count'],
        views_received=len(local)+len(master), failed_views=failures,
        inventory_basis='one_pagewide_reconciliation_after_local_views',
        local_observations_are_procurement_entities=False,
        candidate_objects=len(items), pending_checks=len(issues), quarantined_observations=len(evidence),
        unplaced_observations=len(unmatched), recognition_accuracy_verified=False,
        status='needs_review' if issues or failures else 'no_rule_issue_detected')
    result['diagnostics'].extend({'job': p['job'], 'status': p['status'], 'error': p.get('error')} for p in local)
    result['summary']['master_pages_incomplete'] = sorted({p['job']['page'] for p in master
        if p['status'] not in ('parsed', 'blank') or any(q['code'] == 'view_incomplete' for q in p.get('issues', []))})
    out = pack_result(result)
    out['report'] = 'V30：先读局部，再回到完整有效图面统一核对。\n%d页、%d/%d视图；整图形成%d项器件/线缆候选，%d项待核对。局部观测不累加为采购数量。\n%s' % (
        manifest['page_count'], len(local)+len(master), 8*manifest['page_count'], len(items), len(issues),
        '\n'.join('- '+q['message'] for q in issues[:35]))
    out['source_text'] = manifest.get('native_text', '')
    return out
