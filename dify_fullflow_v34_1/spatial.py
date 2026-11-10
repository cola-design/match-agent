"""Merge observations by page position; no sample-specific circuit map."""
from collections import Counter
import html
import json
import math
import re
from literal_decode import decode, explain
from transport import j, norm, canonical, bind_pages, uncompress
from inventory import source_inventory


def problem(code, message, **fields):
    return dict(code=code, message=message, **fields)


def bbox(value):
    if not isinstance(value, list) or len(value) != 4:
        return None
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
        return None
    x0, y0, x1, y1 = value
    return [float(v) for v in value] if 0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000 else None


def center(b):
    return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]


def area(b):
    return (b[2] - b[0]) * (b[3] - b[1])


def overlap(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return intersection / max(1, area(a) + area(b) - intersection)


def same_position(a, b):
    ca, cb = center(a), center(b)
    return max(abs(ca[0] - cb[0]), abs(ca[1] - cb[1])) <= 15 and overlap(a, b) >= 0.35


def map_box(local, rect):
    b = bbox(local)
    if b is None:
        return None
    return [rect[0] + b[0] * (rect[2] - rect[0]) / 1000,
            rect[1] + b[1] * (rect[3] - rect[1]) / 1000,
            rect[0] + b[2] * (rect[2] - rect[0]) / 1000,
            rect[1] + b[3] * (rect[3] - rect[1]) / 1000]


def label_key(raw):
    # Keep original text; remove a clearly appended destination note only for matching.
    text=canonical(re.split(r'引自|接至|备注', raw, maxsplit=1)[0])
    return re.sub(r'^L[123](?:[~～-]L?[123])?(?=(?:MCB|MCCB|RCB|RCBO|RCCB))','',text)


def prepare_jobs(tool_json, images, document_text, notes):
    bound = bind_pages(tool_json, images, document_text, notes)
    if bound['valid'] != 'YES':
        return dict(bound, jobs=[])
    packet = uncompress(bound['manifest_compact']); jobs = []
    rectangles = [('overview', [0, 0, 1000, 1000])]
    rectangles += [('H%d' % (i + 1), [0, a, 1000, b]) for i, (a, b) in enumerate([(0, 400), (300, 700), (600, 1000)])]
    rectangles += [('V%d' % (i + 1), [a, 0, b, 1000]) for i, (a, b) in enumerate([(0, 400), (300, 700), (600, 1000)])]
    for page in packet['images']:
        serial = packet['uploaded_names'].index(page['filename']) + 1
        for name, rect in rectangles:
            jobs.append(j({'job_id': 'P%d-%s' % (page['page_number'], name), 'page': page['page_number'],
                           'file_serial': serial, 'image_name': page['filename'], 'view_id': name,
                           'mode': 'overview' if name == 'overview' else 'detail', 'rect': rect}))
    return dict(bound, jobs=jobs)


def job_context(job_json, manifest_compact):
    job = json.loads(job_json); packet = uncompress(manifest_compact)
    # The unordered source is a spelling aid, never a numbering or location map.
    context = dict(job, evidence_scope='仅当前图片；全页辅助文字保留在最终汇总，不注入局部视图')
    return {'file_serial': str(job['file_serial']), 'mode': job['mode'], 'context_json': j(context), 'job_json': job_json}


def read_object(raw):
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 65000:
        raise ValueError('模型回复为空或超过65000字符预算')
    text = raw.strip()
    if text.startswith('```') and text.endswith('```'):
        text = '\n'.join(text.splitlines()[1:-1])
    def pairs(values):
        out = {}
        for key, value in values:
            if key in out:
                raise ValueError('JSON重复字段：' + key)
            out[key] = value
        return out
    def reject(_):
        raise ValueError('JSON含非有限数值')
    data = json.loads(text, object_pairs_hook=pairs, parse_constant=reject)
    if not isinstance(data, dict):
        raise ValueError('模型回复不是JSON对象')
    return data


def clean_text(value, limit=180):
    return value.strip() if isinstance(value, str) and value.strip() and len(value) <= limit else None


def kind_of(row):
    raw = row['raw']; literal = decode(raw); kind = row.get('kind', 'unknown')
    if re.match(r'^(?:注[：:]|说明[：:]|备注[：:])', raw):
        return 'note'
    if literal['kind'] in ('apparent_power', 'residual_current', 'ats_class'):
        return 'parameter'
    if literal['kind'] in ('calculation', 'phase', 'circuit_tag'):
        return 'note' if literal['kind'] == 'calculation' else literal['kind']
    if re.fullmatch(r'(?:PC|CB)\s*级|\d+(?:\.\d+)?\s*(?:mA|kA|VA)', raw, re.I):
        return 'parameter'
    if literal['kind'] == 'cable':
        return 'cable'
    if literal['explicit_parameters'].get('drawing_abbreviation'):
        return 'device'
    if kind in ('cabinet_tag', 'note', 'load', 'phase', 'circuit_tag', 'parameter'):
        return kind
    if kind == 'unknown' and literal['kind'] == 'other' and not row.get('symbol'):
        return 'unclassified_annotation'
    return kind if kind in ('device', 'cable') else 'unknown'


def parse_view(raw, context_json):
    ctx = json.loads(context_json); issues = []; rows = []; anchors = []; drawings = []; links = []; ids = set()
    rect = ctx.get('crop_audit', {}).get('actual_rect', ctx['rect'])
    packet = {'job': ctx, 'status': 'parsed', 'observations': rows, 'anchors': anchors,
              'drawings': drawings, 'links': links, 'issues': issues,'quarantined_observations':[]}
    # Do not duplicate the full source in every view packet.
    packet['job'] = {k: v for k, v in ctx.items() if k not in ('source_text', 'source_text_scope', 'notes', 'reconcile_hints')}
    try:
        data = read_object(raw)
        if type(data.get('readable')) is not bool or type(data.get('view_complete')) is not bool:
            raise ValueError('回复缺少可读状态和当前视图完整状态')
        if data['readable'] is False:
            packet['status'] = 'unreadable'
            issues.append(problem('view_unreadable', '模型报告当前视图不可读；保留已有文字，不认定本视图通过'))
        for key, limit in [('objects', 64), ('anchors', 30), ('drawings', 8), ('links', 120)]:
            if not isinstance(data.get(key, []), list) or len(data.get(key, [])) > limit:
                raise ValueError(key + '结构或数量超过协议预算；未截去后续记录')
        object_ids = Counter(clean_text(v.get('id'), 50) for v in data.get('objects', []) if isinstance(v, dict))
        anchor_ids = Counter(clean_text(v.get('id'), 50) for v in data.get('anchors', []) if isinstance(v, dict))
        ambiguous_refs = {key for key, count in object_ids.items() if key is not None and count > 1}
        ambiguous_refs.update(key for key, count in anchor_ids.items() if key is not None and count > 1)
        for index, value in enumerate(data.get('objects', []), 1):
            if not isinstance(value, dict) or not clean_text(value.get('raw')):
                issues.append(problem('unparsed_observation', '第%d条标注缺少可读原文，已保留诊断' % index,
                                      raw_excerpt=j(value)[:400])); continue
            local_id = clean_text(value.get('id'), 50)
            if local_id is None or local_id in ids:
                local_id = 'UNBOUND%d' % index
                issues.append(problem('ambiguous_local_id', '缺失或重复ID已另存；引用原ID的关系不能据此自动确认'))
            ids.add(local_id)
            row = {'local_id': local_id, 'raw': value['raw'].strip(), 'kind': value.get('kind'),
                   'category': clean_text(value.get('category'), 60) or 'unknown',
                   'bbox': map_box(value.get('bbox'), rect), 'local_bbox': value.get('bbox'),
                   'symbol': clean_text(value.get('symbol'), 60)}
            row['kind'] = kind_of(row)
            row['job_id'] = ctx['job_id']; row['page'] = ctx['page']
            if row['kind'] in ('device','cable','unknown') and (ctx['mode']=='overview' or row['bbox'] is None):
                row['quarantine_reason']='overview_not_inventory' if ctx['mode']=='overview' else 'unplaced_local_label'
                packet['quarantined_observations'].append(row)
                if ctx['mode']!='overview':issues.append(problem('unplaced_local_label','局部文字缺少位置，单列待定位，不当作独立器件：'+row['raw']))
                continue
            if row['bbox'] is None:
                issues.append(problem('position_unknown', '“%s”缺少有效位置；文字保留，不能据此合并或绑定回路' % row['raw']))
            rows.append(row)
        for index, value in enumerate(data.get('anchors', []), 1):
            if ctx['mode']=='overview':continue
            if not isinstance(value, dict) or not clean_text(value.get('tag'), 60):
                issues.append(problem('anchor_unparsed', '有回路号记录缺少原文')); continue
            tag = value['tag'].strip()
            if tag.upper() in ('I/O', 'IO', 'PE', 'N', 'L1', 'L2', 'L3') or tag in ('回路总线', '总线'):
                issues.append(problem('not_outgoing_circuit', '“%s”作为端口/相别/说明保留，不编造为出线回路' % tag)); continue
            local_id = clean_text(value.get('id'), 50) or 'A%d' % index
            if anchor_ids.get(local_id, 0) > 1:
                local_id = 'UNBOUND_ANCHOR%d' % index
                issues.append(problem('ambiguous_anchor_id', '回路记录ID重复，保留回路位置但不使用歧义引用'))
            tag_box = map_box(value.get('bbox'), rect)
            if tag_box is None:
                issues.append(problem('anchor_position_unknown', '回路%s缺少可用的图面位置，未按顺序补号' % tag)); continue
            anchors.append({'local_id': local_id, 'tag': tag, 'bbox': tag_box, 'page': ctx['page'],
                            'load': clean_text(value.get('load')), 'phase': clean_text(value.get('phase'), 40),
                            'load_bbox': map_box(value.get('load_bbox'), rect),
                            'direction': value.get('direction') if value.get('direction') in ('horizontal', 'vertical') else 'unknown',
                            'job_id': ctx['job_id']})
        for index, value in enumerate(data.get('drawings', []), 1):
            if not isinstance(value, dict):
                continue
            tags = value.get('cabinet_tags', [])
            drawings.append({'id': 'D%d' % index, 'model_id': clean_text(value.get('id'), 50),
                             'bbox': map_box(value.get('bbox'), rect), 'title': clean_text(value.get('title')),
                             'cabinet_tags': [x for x in tags if clean_text(x, 80)] if isinstance(tags, list) else [],
                             'page': ctx['page'], 'job_id': ctx['job_id']})
        for value in data.get('links', []):
            if ctx['mode']=='overview':continue
            if not isinstance(value, dict) or value.get('type') not in ('member', 'parameter'):
                continue
            source, target = clean_text(value.get('source'), 50), clean_text(value.get('target'), 50)
            if source in ambiguous_refs or target in ambiguous_refs:
                issues.append(problem('ambiguous_relation', '有关系引用了重复ID，未猜测它指向哪个位置'))
                continue
            if source and target:
                links.append({'type': value['type'], 'source': source, 'target': target,
                              'trace_visible': value.get('trace_visible') is True,
                              'reason': clean_text(value.get('reason')), 'job_id': ctx['job_id'], 'page': ctx['page']})
        if data.get('view_complete') is not True:
            issues.append(problem('view_incomplete', '当前视图未完整读取；不会用本视图替代整页验收'))
        for text in data.get('unresolved', []) if isinstance(data.get('unresolved', []), list) else []:
            if clean_text(text, 500):
                issues.append(problem('visual_uncertainty', text))
        packet['diagnostics'] = {'response_chars': len(raw), 'raw_excerpt': raw[:2000],
                                 'excerpt_truncated': len(raw) > 2000}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        packet['status'] = 'failed'; packet['error'] = str(exc)
        packet['diagnostics'] = {'response_chars': len(raw) if isinstance(raw, str) else 0,
                                 'raw_excerpt': raw[:2000] if isinstance(raw, str) else '',
                                 'excerpt_truncated': isinstance(raw, str) and len(raw) > 2000}
        issues.append(problem('view_failed', str(exc)))
    result = j(packet)
    if len(result) > 60000:
        return {'packet_json': j({'job': packet['job'], 'status': 'failed', 'error': '视图结果超过传输预算',
                                  'observations': [], 'anchors': [], 'drawings': [], 'links': [],
                                  'issues': [problem('view_failed', '视图结果超过传输预算，未假称完整')]})}
    return {'packet_json': result}


def blank_view(context_json):
    ctx=json.loads(context_json)
    return {'packet_json':j({'job':ctx,'status':'blank','observations':[],'anchors':[],'drawings':[],
            'links':[],'issues':[],'quarantined_observations':[],'diagnostics':{'blank_verified_by_pixels':True}})}


def crop_failure(context_json, message):
    ctx = json.loads(context_json)
    ctx = {k: v for k, v in ctx.items() if k not in ('source_text', 'source_text_scope', 'notes')}
    return {'packet_json': j({'job': ctx, 'status': 'failed', 'error': message,
                              'observations': [], 'anchors': [], 'drawings': [], 'links': [],
                              'issues': [problem('crop_failed', message)]})}


def check_view_files(files, context_json):
    ctx = json.loads(context_json)
    expected = ctx['image_name'] if ctx['mode'] == 'overview' else ctx['job_id'] + '.png'
    names = [x.get('filename') or x.get('name') for x in files if isinstance(x, dict)] if isinstance(files, list) else []
    valid = names == [expected]
    return {'valid': 'BLANK' if valid and ctx.get('crop_audit',{}).get('verified_blank') else 'YES' if valid else 'NO', 'message': '' if valid else '当前视图图片缺失或文件名不匹配，未将原图冒充局部图'}


def assign_drawing(row, drawings):
    if not row.get('bbox'):
        return None
    x, y = center(row['bbox'])
    found = [d for d in drawings if d['page'] == row['page'] and d.get('bbox') and
             d['bbox'][0] <= x <= d['bbox'][2] and d['bbox'][1] <= y <= d['bbox'][3]]
    return found[0]['id'] if len(found) == 1 else None


def merge_positions(observations, prefix):
    groups = []; lookup = {}
    for obs in observations:
        matches = [g for g in groups if g['page'] == obs['page'] and g['kind'] == obs['kind'] and
                   ((g.get('native_ref') and g.get('native_ref') == obs.get('native_ref')) or
                    (not g.get('native_ref') and not obs.get('native_ref') and label_key(g['raw']) == label_key(obs['raw']) and obs.get('bbox') and g.get('bbox') and same_position(g['bbox'], obs['bbox']))) and (obs['job_id'] not in g['view_ids'] or obs.get('native_ref') and obs.get('native_ref')==g.get('native_ref'))]
        if len(matches) == 1:
            group = matches[0]
        else:
            group = dict(obs, id=prefix + str(len(groups) + 1), observations=[], view_ids=[])
            groups.append(group)
        group['observations'].append(obs); group['view_ids'].append(obs['job_id'])
        cats={x.get('category') for x in group['observations'] if x.get('category') not in (None,'unknown')}
        if len(cats)>1:
            group['category']='unknown';group['classification_conflict']=sorted(cats)
        lookup[(obs['job_id'], obs['local_id'])] = group['id']
    return groups, lookup


def geometry_compatible(item, anchor, anchors):
    if not item.get('bbox') or not anchor.get('bbox'):
        return False
    if item.get('drawing_id') is None or item.get('drawing_id') != anchor.get('drawing_id'):
        return False
    direction = anchor.get('direction')
    if direction not in ('horizontal', 'vertical'):
        return False
    axis = 1 if direction == 'horizontal' else 0
    a, p = center(anchor['bbox'])[axis], center(item['bbox'])[axis]
    neighbors = [abs(center(x['bbox'])[axis] - a) for x in anchors if x['id'] != anchor['id'] and
                 x['page'] == anchor['page'] and x.get('drawing_id') == anchor['drawing_id'] and
                 x.get('direction') == direction and abs(center(x['bbox'])[axis] - a) > 8]
    # This is a sanity check on a model-traced connection, never an automatic row assignment.
    tolerance = min(25, min(neighbors) * 0.42) if neighbors else 18
    return abs(a - p) <= tolerance


def parameter_geometry_compatible(note,target):
    if not note.get('bbox') or not target.get('bbox'):return False
    return max(abs(a-b) for a,b in zip(center(note['bbox']),center(target['bbox'])))<=150


def parameter_type_conflict(params,target):
    abbreviation=target.get('literal_parameters',{}).get('drawing_abbreviation')
    if 'ats_class' in params and abbreviation!='ATS':return 'PC/CB类别只能关联自动转换开关'
    if 'voltage_pair_v' in params and target.get('category')!='transformer':return '变压比只能作为对应变压器参数，不得写入断路器选型'
    if 'apparent_power_va' in params and target.get('category')!='transformer':return 'VA容量与目标器件类型尚未核实'
    if 'residual_current_ma' in params and abbreviation in ('MCB','MCCB'):return '漏电动作电流与图面断路器类型需要人工核对'
    return ''


def base_view_coverage(records,manifest):
    issues=[];names=['overview','H1','H2','H3','V1','V2','V3']
    for page in range(1,manifest['page_count']+1):
        base=[r for r in records if r['job']['page']==page and r['job'].get('mode')!='repair']
        counts=Counter(r['job'].get('view_id') for r in base)
        missing=[n for n in names if not counts[n]]
        duplicate=[n for n,v in counts.items() if v>1]
        unexpected=[n for n in counts if n not in names]
        if missing or duplicate or unexpected:
            parts=[]
            if missing:parts.append('缺少基础视图'+','.join(missing))
            if duplicate:parts.append('基础视图重复'+','.join(map(str,duplicate)))
            if unexpected:parts.append('存在未知基础视图'+','.join(map(str,unexpected)))
            issues.append(problem('view_missing','第%d页：%s；补充复核任务单独统计，不替代缺失视图'%(page,'；'.join(parts)),page=page,missing_view_ids=missing,duplicate_view_ids=duplicate,unexpected_view_ids=unexpected))
    for r in records:
        if not 1<=r['job']['page']<=manifest['page_count']:
            issues.append(problem('view_page_mismatch','视图页码不在本项目页码范围内，未按完整覆盖处理',page=r['job']['page']))
    return issues


def collect_spatial(packets, manifest_compact):
    manifest = uncompress(manifest_compact); records = [json.loads(x) for x in packets]
    problems = []; drawings = []; raw_objects = []; raw_anchors = []; relations = []
    for r in records:
        job = r['job']
        problems.extend(dict(x, page=job['page'], view=job['view_id']) for x in r.get('issues', []))
        if job['mode'] in ('overview', 'reconcile'):
            for d in r.get('drawings', []):
                d = dict(d, id='P%d-%s' % (d['page'], d['id']))
                if d['bbox']:
                    drawings.append(d)
        raw_objects.extend(r.get('observations', [])); raw_anchors.extend(r.get('anchors', [])); relations.extend(r.get('links', []))
    quarantine=[x for r in records for x in r.get('quarantined_observations',[])]
    checked=[]
    for row in raw_objects:
        reason=None
        if row['kind'] in ('device','cable','unknown'):
            if row['job_id'].endswith('-overview'):reason='overview_not_inventory'
            elif not row.get('bbox'):reason='unplaced_local_label'
            elif drawings and not row.get('native_ref') and assign_drawing(row,drawings) is None:reason='outside_overview_regions'
        if reason:quarantine.append(dict(row,quarantine_reason=reason))
        else:checked.append(row)
    raw_objects=checked
    raw_anchors=[a for a in raw_anchors if not a['job_id'].endswith('-overview')]
    cabinet_keys = {canonical(tag) for d in drawings for tag in d['cabinet_tags']}
    for row in raw_objects:
        tokens = re.split(r'[\s,，、;；]+', row['raw'])
        if tokens and all(canonical(token) in cabinet_keys for token in tokens):
            row['kind'] = 'cabinet_tag'
    annotations_raw = [x for x in raw_objects if x['kind'] not in ('device', 'cable', 'unknown')]
    for row in annotations_raw:
        if row['kind'] == 'unclassified_annotation':
            problems.append(problem('annotation_unclassified', '“%s”缺少器件或符号依据，先保留为未分类标注，不列作产品' % row['raw'], page=row['page']))
    objects_raw = [x for x in raw_objects if x['kind'] in ('device', 'cable', 'unknown')]
    items, item_map = merge_positions(objects_raw, 'O')
    annotations, annotation_map = merge_positions(annotations_raw, 'N')
    anchor_observations = [dict(x, raw=x['tag'], kind='circuit') for x in raw_anchors]
    anchors, anchor_map = merge_positions(anchor_observations, 'C')
    all_items = {x['id']: x for x in items}; all_notes = {x['id']: x for x in annotations}; all_anchors = {x['id']: x for x in anchors}
    for row in items + annotations + anchors:
        row['drawing_id'] = assign_drawing(row, drawings)
    conflicting_anchors = set()
    for index, anchor in enumerate(anchors):
        for other in anchors[index + 1:]:
            if anchor['page'] == other['page'] and anchor['drawing_id'] == other['drawing_id'] and \
               anchor['tag'] != other['tag'] and same_position(anchor['bbox'], other['bbox']):
                conflicting_anchors.update((anchor['id'], other['id']))
                problems.append(problem('anchor_label_conflict', '同一位置的回路号被读成%s/%s，未选择其中一个强行绑定' %
                                        (anchor['tag'], other['tag']), page=anchor['page']))
        for other in anchor['observations'][1:]:
            if other.get('load') and anchor.get('load') and canonical(other['load']) != canonical(anchor['load']):
                problems.append(problem('load_label_conflict', '回路%s的负荷文字在不同视图中不一致，需对照原图' % anchor['tag'], page=anchor['page']))
                anchor['load'] = None
        axis = 1 if anchor['direction'] == 'horizontal' else 0
        anchor['load_position_checked'] = bool(anchor.get('load_bbox') and anchor['direction'] != 'unknown' and
                                              abs(center(anchor['load_bbox'])[axis] - center(anchor['bbox'])[axis]) <= 30)
        if anchor.get('load') and not anchor['load_position_checked']:
            problems.append(problem('load_position_unconfirmed', '回路%s负荷文字缺少同支路位置依据，保留原读法但不自动配入器件' %
                                    anchor['tag'], page=anchor['page']))
    for item in items:
        item['instance_status'] = 'position_observed' if item.get('bbox') else 'position_unknown'
        if item.get('classification_conflict'):
            problems.append(problem('classification_conflict','同位置的器件类别不一致，保留原读法并要求人工确认',page=item['page'],item_id=item['id']))
    for index, item in enumerate(items):
        for other in items[index + 1:]:
            if item['page'] == other['page'] and item['kind'] == other['kind'] and item.get('bbox') and other.get('bbox') and \
               label_key(item['raw']) != label_key(other['raw']) and same_position(item['bbox'], other['bbox']):
                item['instance_status'] = 'conflicting_readings'; other['instance_status'] = 'conflicting_readings'
                problems.append(problem('object_label_conflict', '同一位置存在“%s”/“%s”两种读法，未认定为两台产品' %
                                        (item['raw'], other['raw']), page=item['page']))
    owner_proposals = {}; marker_proposals = {}
    for link in relations:
        src_key = (link['job_id'], link['source']); dst_key = (link['job_id'], link['target'])
        if link['type'] == 'member':
            src, dst = item_map.get(src_key), anchor_map.get(dst_key)
            if src and dst and link['trace_visible']:
                if dst in conflicting_anchors:
                    continue
                if geometry_compatible(all_items[src], all_anchors[dst], anchors):
                    owner_proposals.setdefault(src, {}).setdefault(dst, []).append(link['job_id'])
                else:
                    problems.append(problem('binding_geometry_conflict', '“%s”与回路%s的位置不支持本次绑定；未按顺序改号' %
                                            (all_items[src]['raw'], all_anchors[dst]['tag']), page=link['page'], item_id=src))
            elif src:
                problems.append(problem('binding_unverified', '“%s”的连线或回路号未在同一视图确认' % all_items[src]['raw'],
                                        page=link['page'], item_id=src))
        else:
            src, dst = annotation_map.get(src_key), item_map.get(dst_key)
            if src and dst and link['trace_visible']:
                marker_proposals.setdefault(src, {}).setdefault(dst, []).append(link['job_id'])
    for item in items:
        options = owner_proposals.get(item['id'], {})
        item['circuit_id'] = next(iter(options)) if len(options) == 1 else None
        item['binding_status'] = 'candidate_geometry_checked' if len(options) == 1 else 'conflict' if options else 'unbound'
        item['candidate_circuit_ids'] = list(options)
        item['binding_views'] = {k: sorted(set(v)) for k, v in options.items()}
        if len(options) > 1:
            problems.append(problem('multiple_circuit_owners', '“%s”有相互冲突的回路归属，保留位置与候选关系' % item['raw'],
                                    page=item['page'], item_id=item['id']))
        if item['kind'] == 'cable' and item['circuit_id'] is None:
            problems.append(problem('cable_scope_unconfirmed', '“%s”保留为线缆位置候选，尚未关联回路/功能连接' % item['raw'],
                                    page=item['page'], item_id=item['id']))
        if item['drawing_id'] is None:
            problems.append(problem('drawing_unknown', '“%s”的子图范围未确认，未自动塞入第一幅图' % item['raw'],
                                    page=item['page'], item_id=item['id']))
        item['literal_parameters'] = decode(item['raw'])['explicit_parameters']
        item['associated_parameters'] = {}; item['associated_markings'] = []
    for note in annotations:
        options = marker_proposals.get(note['id'], {})
        note['target_id'] = next(iter(options)) if len(options) == 1 else None
        if note['kind'] == 'parameter' and len(options) != 1:
            problems.append(problem('parameter_unassigned', '“%s”的目标未确认，参数保留但不猜测归属' % note['raw'],
                                    page=note['page'],annotation_id=note['id'],marker_raw=note['raw']))
        if note['target_id']:
            target = all_items[note['target_id']]
            params = decode(note['raw'])['explicit_parameters']
            conflict=parameter_type_conflict(params,target)
            if conflict or not parameter_geometry_compatible(note,target):
                problems.append(problem('parameter_association_unverified',conflict or '参数与器件缺少可靠位置关联，保留原文待核对',page=note['page'],item_id=target['id'],annotation_id=note['id'],marker_raw=note['raw']))
                note['target_id']=None
                continue
            if 'ats_class' in params and target['literal_parameters'].get('drawing_abbreviation') != 'ATS':
                problems.append(problem('parameter_target_incompatible', '“%s”不能作为“%s”的转换开关类别，未采用此关联' %
                                        (note['raw'], target['raw']), page=note['page'],annotation_id=note['id'],marker_raw=note['raw']))
                note['target_id'] = None
                continue
            for key, value in params.items():
                existing = target['literal_parameters'].get(key, target['associated_parameters'].get(key))
                if existing is not None and existing != value:
                    problems.append(problem('parameter_conflict', '“%s”的参数%s有冲突' % (target['raw'], key),
                                            page=note['page'], item_id=target['id']))
                else:
                    target['associated_parameters'][key] = value
            target['associated_markings'].append(note['raw'])
    for item in items:
        decoded = decode(item['raw']); abbreviation = decoded['explicit_parameters'].get('drawing_abbreviation')
        category = {'RCB': 'residual_current_device_unspecified', 'RCD': 'residual_current_device_unspecified'}.get(abbreviation, abbreviation)
        if not category:
            category = 'cable' if item['kind'] == 'cable' else item['category']
        if category not in ('MCB', 'MCCB', 'RCBO', 'RCCB', 'residual_current_device_unspecified', 'ATS', 'SPD',
                            'controller', 'contactor', 'relay', 'timer', 'transformer', 'dc_power_supply', 'fuse', 'cable'):
            category = 'unknown'
        item['category'] = category
        params = dict(item['literal_parameters']); params.update(item['associated_parameters'])
        explanation = explain(item['raw'], category, params)
        item.update(explanation)
        for text in explanation.get('pending', []):
            problems.append(problem('item_parameter_pending', '“%s”：%s' % (item['raw'], text),
                                    page=item['page'], item_id=item['id']))
        owner = all_anchors.get(item['circuit_id'])
        item['circuit'] = owner['tag'] if owner else None
        item['load'] = owner['load'] if owner and owner['load_position_checked'] else None
        item['position_source'] = 'native_pdf_text' if item.get('native_ref') else 'model_reported_bbox_mapped_through_real_crop'
        if item['kind'] == 'unknown' or category == 'unknown':
            problems.append(problem('classification_unknown', '“%s”尚未确认器件类别' % item['raw'], page=item['page'], item_id=item['id']))
        if re.search(r'时间控制器|时控', item['raw']) and re.search(r'\bKM\b|tKM', item['raw'], re.I):
            problems.append(problem('combined_control_labels', '“%s”可能包含不同符号标注，须拆分定位；未把它当成已确认单件' % item['raw'],
                                    page=item['page'], item_id=item['id']))
    # Inventory is a spelling/count warning only. It never creates physical devices.
    inventory = source_inventory(manifest['native_text'])
    if manifest['page_count'] == 1:
        for entry in inventory['candidates']:
            found = [x for x in items if label_key(x['raw']) == label_key(entry['raw'])]
            if not found:
                problems.append(problem('source_label_unplaced', '辅助原文“%s”尚未定位到器件/线缆；不据文字补造位置' % entry['raw'], page=1))
            elif entry['text_mentions'] > len(found):
                problems.append(problem('instance_count_pending', '“%s”文字出现%d处、独立位置候选%d处；需核对漏识或重复视图，不作为采购数量' %
                                        (entry['raw'], entry['text_mentions'], len(found)), page=1))
    for anchor in anchors:
        anchor['members'] = [x['id'] for x in items if x['circuit_id'] == anchor['id']]
    problems.extend(base_view_coverage(records,manifest))
    unique = {}; problems = [unique.setdefault(j(x), x) for x in problems if j(x) not in unique]
    failed = [x['job']['job_id'] for x in records if x['status'] not in ('parsed','blank')]
    summary = {'workflow_version': '29.0', 'model': 'qwen3-vl-plus', 'provider': 'tongyi', 'pages': manifest['page_count'],
               'views_expected': manifest.get('views_per_page', 7) * manifest['page_count'], 'views_received': len(records), 'failed_views': failed,
               'candidate_objects': len(items), 'cable_candidates': sum(x['kind'] == 'cable' for x in items),
               'document_annotations': len(annotations), 'circuits': len(anchors), 'pending_checks': len(problems),
               'unbound_cable_candidates': sum(x['kind'] == 'cable' and x['circuit_id'] is None for x in items),
               'automatic_lossless_crop': True, 'automatic_upscale': False, 'native_vector_coordinates_available': False,
               'lossless_crop_views_completed': sum(bool(r['job'].get('crop_audit')) for r in records),
               'recognition_accuracy_verified': False, 'selection_approved': False,
               'status': 'needs_review' if problems or failed else 'no_rule_issue_detected'}
    diagnostics = [{'job': x['job'], 'status': x['status'], 'error': x.get('error'), 'diagnostics': x.get('diagnostics')} for x in records]
    summary['verified_blank_views']=[x['job']['job_id'] for x in records if x['status']=='blank']
    summary['quarantined_observations']=len(quarantine)
    summary['unplaced_observations']=sum(x['quarantine_reason']!='overview_not_inventory' for x in quarantine)
    result = {'schema_version': 'pdf-spatial-circuit/29.0', 'summary': summary, 'drawings': drawings,
              'items': items, 'circuits': anchors, 'document_annotations': annotations, 'issues': problems,
              'diagnostics': diagnostics, 'source_inventory': inventory,'quarantined_observations':quarantine,
              'scope': '裁切坐标由程序计算，标注位置与连线由模型报告；规则检查不等于原图验收或采购数量确认'}
    def cell(x):
        return html.escape(str(x if x is not None else '未确认')).replace('|', '&#124;').replace('\n', '<br>')
    report = ['模型：通义千问 / qwen3-vl-plus；V29有效图面裁切与独立定位',
              '%d页、%d/%d个视图、%d个视图失败；%d项器件/线缆候选，%d条文档标注，%d项待核对。候选数量不是采购数量。' %
              (summary['pages'], len(records), summary['views_expected'], len(failed), len(items), len(annotations), len(problems)),
              '每页保留整页范围，并真实裁切3个横向与3个纵向重叠区域。保持原像素和颜色，不以模型自报完整代替验收。',
              '', '|位置实例|原文|中文含义|回路/负荷|归属状态|', '|---|---|---|---|---|']
    for x in items[:60]:
        binding_cn = {'candidate_geometry_checked': '候选归属，已做位置检查', 'conflict': '归属冲突', 'unbound': '归属未确认'}[x['binding_status']]
        report.append('|' + '|'.join(cell(v) for v in [x['id'], x['raw'], x.get('meaning_cn'),
                      (x['circuit'] or '未确认') + '/' + (x['load'] or ''), binding_cn]) + '|')
    if len(items) > 60:
        report.append('聊天展示前60项，完整位置实例在result_pages中。')
    report += ['', '待核对：']
    report += ['概览器件不计入清单；缺少有效位置或超出子图的读法保存在quarantined_observations中，仍须核对漏项。']
    report += ['- 第%s页：%s' % (x.get('page', '?'), x['message']) for x in problems[:35]]
    if len(problems) > 35:
        report.append('其余待核对项在结构化issues中。')
    report += ['', '未调用产品库、价格或CAD；未批准选型和采购数量。裁切是实际像素处理，bbox和连线关系仍需对照原图验收。']
    # Keep every list entry in bounded JSON chunks, instead of cutting a JSON string.
    pages = []; current = {'schema_version': result['schema_version'], 'summary': summary}; current_size = len(j(current))
    for key in ('drawings', 'items', 'circuits', 'document_annotations', 'issues', 'diagnostics','quarantined_observations'):
        for entry in result[key]:
            size = len(j(entry))
            if size > 55000:
                raise ValueError('单条结构化记录超过传输预算，未截断')
            if current_size + size + 100 > 58000:
                pages.append(j(current)); current = {'schema_version': result['schema_version']}; current_size = len(j(current))
            current.setdefault(key, []).append(entry); current_size += size + 2
    pages.append(j(current))
    result_json = j(result)
    if len(result_json) > 60000:
        result_json = j({'schema_version': result['schema_version'], 'summary': summary, 'details_in': 'result_pages', 'parts': len(pages)})
    return {'report': '\n'.join(report)[:58000], 'result_json': result_json, 'result_pages': pages,
            'source_text': manifest['native_text']}
