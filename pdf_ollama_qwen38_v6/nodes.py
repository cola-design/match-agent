"""Evidence inventories, circuit-first extraction and Chinese selection fields.

All functions exported to Dify use standard Python libraries only.
"""
from collections import Counter
import base64
import html
import json
import math
import re
import unicodedata
import zlib
from transport import j, canonical, uncompress, check_input, bind_pages, page_context
from literal_decode import norm, decode, decode_base, number_text, explain

CATEGORIES = {'MCB', 'MCCB', 'RCBO', 'RCCB', 'residual_current_device_unspecified', 'ATS',
              'SPD', 'controller', 'contactor', 'relay', 'timer', 'transformer',
              'dc_power_supply', 'fuse', 'cable', 'unknown'}
VIEWS = {'single_line', 'control', 'layout', 'schedule', 'mixed', 'unknown'}


def issue(code, message, item_id=None, repairable=False):
    return {'code': code, 'message': message, 'item_id': item_id, 'repairable': repairable}


def read_json(raw):
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 50000:
        raise ValueError('模型回复为空或超过预算')
    s = raw.strip()
    if s.startswith('<think>'):
        end = s.find('</think>')
        if end < 0: raise ValueError('思考内容未结束，没有可用JSON；请确认think=false')
        s = s[end + 8:].strip()
    if s.startswith('```') and s.endswith('```'):
        s = '\n'.join(s.splitlines()[1:-1]).strip()
    def pairs(values):
        d = {}
        for k, v in values:
            if k in d: raise ValueError('JSON有重复字段：' + k)
            d[k] = v
        return d
    def constant(_): raise ValueError('JSON含非有限数值')
    d = json.loads(s, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(d, dict): raise ValueError('模型顶层不是JSON对象')
    return d


def short(value, name, limit=140, nullable=True):
    if value is None and nullable: return None
    if not isinstance(value, str) or len(value) > limit or (not nullable and not value.strip()):
        raise ValueError(name + '缺失或格式无效')
    return value.strip()


def strings(value, name, limit=20):
    if not isinstance(value, list) or len(value) > limit: raise ValueError(name + '结构或数量无效')
    return [short(x, name, 180, False) for x in value]


def literal_key(raw):
    s = canonical(raw)
    m = re.fullmatch(r'\d+[X*]\((.*)\)', s)
    return m[1] if m else s


def same_label(a, b):
    if literal_key(a) == literal_key(b): return True
    pa, pb = decode(a)['explicit_parameters'], decode(b)['explicit_parameters']
    if 'category_raw' in pa and 'category_raw' in pb and ('UTP' in norm(a).upper()) == ('UTP' in norm(b).upper()):
        return pa['category_raw'] == pb['category_raw']
    return False


def source_inventory(text):
    """Unordered source-text mentions, not geometry or purchase quantities."""
    source = norm(text); candidates = []; markers = []; occupied = []
    patterns = [
        r'(?<![A-Z0-9])(?:\d+\s*[*x×]\s*\()?WDZ[A-Z0-9]*-[A-Z0-9.*x×/+(),\-]+(?:\s+(?:CE|WE|WC|CC|FC)\b)*',
        r'(?<![A-Z0-9])(?:NHBV|ZNBV|BVR?)-[A-Z0-9.*x×/+,\-]+',
        r'(?<![A-Z0-9])(?:UTP|STP)\s*/\s*CAT\s*[5-8](?:E|A)?(?![A-Z0-9])',
        r'(?<![A-Z0-9])(?:RCBO|RCCB|RCB|RCD)[A-Z0-9/+.()\-]*(?:\s+\d+(?:\.\d+)?\s*mA)?',
        r'(?<![A-Z0-9])(?:MCCB|MCB|ATS)[A-Z0-9/+.()\-]*',
        r'(?<![A-Z0-9])[A-Z][A-Z0-9]{1,14}-[A-Z0-9][A-Z0-9./+()\-]{1,70}',
        r'(?<![A-Z0-9])(?:AC|DC)\s*\d+(?:\.\d+)?\s*V\s+\d+(?:\.\d+)?\s*A',
        r'(?<![A-Z0-9])\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\s*V',
        r'时间控制器|时控开关|(?<![A-Z0-9])(?:KM|KA|KT|FU|SPD)\d*(?![A-Z0-9])',
    ]
    found = []
    for pattern in patterns:
        for m in re.finditer(pattern, source, re.I):
            if any(m.start() < b and m.end() > a for a, b in occupied): continue
            raw = m[0].strip()
            if decode(raw)['kind'] in ('other', 'calculation', 'phase', 'circuit_tag'): continue
            found.append((m.start(), raw)); occupied.append((m.start(), m.end()))
    for start, raw in sorted(found):
        old = next((x for x in candidates if canonical(x['raw']) == canonical(raw)), None)
        if old: old['text_mentions'] += 1
        else: candidates.append({'id': 'S%d' % (len(candidates) + 1), 'raw': raw,
                                 'kind': decode(raw)['kind'], 'text_mentions': 1})
    marker_pattern = r'(?:PC|CB)\s*级|(?<![A-Z0-9])\d+(?:\.\d+)?\s*(?:mA|kA|VA)\b'
    for m in re.finditer(marker_pattern, source, re.I):
        if any(m.start() < b and m.end() > a for a, b in occupied): continue
        raw = m[0]
        if not any(canonical(x['raw']) == canonical(raw) for x in markers):
            markers.append({'id': 'M%d' % (len(markers) + 1), 'raw': raw, 'kind': decode(raw)['kind']})
    tags = sorted(set(m[0] for m in re.finditer(r'(?<![0-9A-Z])\d{1,3}#(?![A-Z\u4e00-\u9fff])', source)),
                  key=lambda x: int(x[:-1]))
    return {'candidates': candidates, 'independent_markers': markers, 'circuit_tag_mentions': tags,
            'scope': '全文件无序文字提示；出现次数不是采购数量；不是当前页定位证据'}


def context_with_inventory(index, manifest_compact):
    out = page_context(index, manifest_compact)
    c = json.loads(out['context_json']); c['source_inventory'] = source_inventory(c['whole_document_text'])
    encoded = j(c)
    if len(encoded) > 60000: raise ValueError('全文与原文提示清单超过当前上下文预算，未删减后继续')
    return {'context_json': encoded}


def parse_outline(raw, context_json):
    ctx = json.loads(context_json)
    try:
        d = read_json(raw)
        if type(d.get('readable')) is not bool or d.get('view') not in VIEWS:
            raise ValueError('回路表缺少可读状态或图面类型')
        drawings = d.get('drawings', []); circuits = d.get('circuits', [])
        if not isinstance(drawings, list) or not 1 <= len(drawings) <= 6: raise ValueError('子图信息无效')
        if not isinstance(circuits, list) or len(circuits) > 20: raise ValueError('回路表结构或数量无效')
        keys = set(); ds = []
        for row in drawings:
            key = short(row.get('id'), '子图ID', 16, False)
            if key in keys: raise ValueError('子图ID重复')
            keys.add(key)
            ds.append({'id': key, 'title': short(row.get('title'), '子图名称'),
                       'cabinet_tags': strings(row.get('cabinet_tags', []), '柜号', 8)})
        cs = []; ids = set(); tag_scopes = set()
        for row in circuits:
            cid = short(row.get('id'), '回路ID', 16, False); drawing = row.get('drawing_id')
            if cid in ids or drawing not in keys: raise ValueError('回路ID重复或子图引用无效')
            ids.add(cid); tag = short(row.get('tag'), '图面回路号', 40, False)
            if (drawing, canonical(tag)) in tag_scopes: raise ValueError('同一子图重复列出相同回路号')
            tag_scopes.add((drawing, canonical(tag)))
            cs.append({'id': cid, 'drawing_id': drawing, 'tag': tag,
                       'phase': short(row.get('phase'), '相别', 40), 'load': short(row.get('load'), '负荷原文'),
                       'device_labels': strings(row.get('device_labels', []), '回路器件原文', 12),
                       'cable_labels': strings(row.get('cable_labels', []), '回路线缆原文', 8)})
        plan = {'status': 'parsed', 'readable': d['readable'], 'view': d['view'], 'drawings': ds,
                'circuits': cs, 'unresolved': strings(d.get('unresolved', []), '回路识别待确认', 12)}
        encoded = j(plan)
        if len(encoded) > 20000: raise ValueError('回路表超过当前节点预算')
        return {'outline_json': encoded}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return {'outline_json': j({'status': 'failed', 'error': str(exc), 'view': 'unknown', 'drawings': [], 'circuits': []})}


def extraction_context(context_json, outline_json):
    ctx = json.loads(context_json); ctx['circuit_outline'] = json.loads(outline_json)
    encoded = j(ctx)
    if len(encoded) > 60000: raise ValueError('整页提取上下文超过预算，未截断')
    return {'extraction_json': encoded}


def inventory_checks(inventory, items, circuits, markings, check_counts=True):
    problems = []; placed = []
    for source in inventory['candidates']:
        matches = [o for o in items if same_label(source['raw'], o['raw'])]
        if not matches:
            problems.append(issue('source_label_unplaced', '原文出现“%s”，尚未形成可定位候选' % source['raw'], repairable=True))
            placed.append(source['id']); continue
        p = decode(source['raw'])['explicit_parameters']
        if p.get('explicit_multiplier') and not any(o['literal_parameters'].get('explicit_multiplier') == p['explicit_multiplier'] for o in matches):
            problems.append(issue('source_multiplier_missing', '“%s”的外层倍数没有保留' % source['raw'], repairable=True))
        if check_counts and source['text_mentions'] > len(matches):
            problems.append(issue('source_mention_count_pending', '“%s”在提取文字中出现%d处，候选%d处；须核对重复视图/独立位置，未作为采购数量' % (
                source['raw'], source['text_mentions'], len(matches)), repairable=True))
    used_markers = [x['raw'] for x in markings if x.get('target_id')]
    for marker in inventory['independent_markers']:
        if not any(canonical(marker['raw']) == canonical(raw) for raw in used_markers):
            problems.append(issue('source_marker_unassigned', '独立标注“%s”尚未关联到候选' % marker['raw'], repairable=True))
    tags = {canonical(x['tag']) for x in circuits}
    for tag in inventory['circuit_tag_mentions']:
        if canonical(tag) not in tags:
            problems.append(issue('source_tag_unplaced', '提取文字含编号“%s”，当前回路表尚未对应；需核对是否为支路编号' % tag, repairable=True))
    return problems


def checked(raw, extraction_json):
    ctx = json.loads(extraction_json); plan = ctx['circuit_outline']; problems = []
    try:
        d = read_json(raw)
        if type(d.get('readable')) is not bool or type(d.get('page_complete')) is not bool:
            raise ValueError('缺少readable/page_complete状态')
        rows = d.get('items'); circuits = d.get('circuits'); marks = d.get('markings', [])
        if not isinstance(rows, list) or len(rows) > 40: raise ValueError('候选条目结构或数量无效')
        if not isinstance(circuits, list) or len(circuits) > 20: raise ValueError('回路表结构或数量无效')
        if not isinstance(marks, list) or len(marks) > 24: raise ValueError('独立参数结构或数量无效')
        drawings = d.get('drawings', plan.get('drawings', []))
        if not isinstance(drawings, list) or not 1 <= len(drawings) <= 6: raise ValueError('子图信息无效')
        drawing_by_id = {}; draw_out = []
        for row in drawings:
            key = short(row.get('id'), '子图ID', 16, False)
            if key in drawing_by_id: raise ValueError('子图ID重复')
            row = {'id': key, 'title': short(row.get('title'), '子图名称'), 'cabinet_tags': strings(row.get('cabinet_tags', []), '柜号', 8)}
            drawing_by_id[key] = row; draw_out.append(row)
        items = []; by_id = {}; annotations = []
        for row in rows:
            iid = short(row.get('id'), '候选ID', 16, False); label = short(row.get('raw'), '型号原文', 160, False)
            if iid in by_id: raise ValueError('候选ID重复')
            cat = row.get('category'); drawing = row.get('drawing_id')
            if cat not in CATEGORIES or drawing not in drawing_by_id: raise ValueError('候选类别或子图引用无效')
            literal = decode(label); p = dict(literal['explicit_parameters']); source_cat = p.get('drawing_abbreviation')
            if literal['kind'] in ('phase', 'circuit_tag', 'calculation', 'ats_class', 'apparent_power', 'residual_current'):
                annotations.append({'raw': label, 'reason': '标识/独立参数不是单独器件'})
                problems.append(issue('non_device_item', '“%s”已移到标识，不作独立器件' % label, iid, True)); continue
            if source_cat:
                known = {'RCB': 'residual_current_device_unspecified', 'RCD': 'residual_current_device_unspecified'}.get(source_cat, source_cat)
                if cat != known: problems.append(issue('category_literal_disagreement', '“%s”的视觉类别与原文缩写不同，保留原文类别' % label, iid))
                cat = known
            elif literal['kind'] == 'cable': cat = 'cable'
            elif literal['kind'] == 'voltage_ratio': cat = 'transformer'
            elif literal['kind'] == 'function_label': cat = 'timer'
            o = {'id': iid, 'drawing_id': drawing, 'raw': label, 'category': cat,
                 'position': short(row.get('position'), '图面位置', 80),
                 'literal_parameters': p, 'associated_parameters': {}, 'branch_requirements': {},
                 'associated_markings': [], 'circuit_id': None, 'circuit': None,
                 'phase': short(row.get('phase'), '独立相别标注', 40),
                 'load': None, 'candidate_quantity': None, 'selection_approved': False}
            o['source_text_supported'] = bool(canonical(label) in canonical(ctx['whole_document_text']) or
                any(same_label(label, x['raw']) for x in ctx['source_inventory']['candidates']))
            items.append(o); by_id[iid] = o
        cs = []; cids = set(); scopes = set(); assigned = {}
        for row in circuits:
            cid = short(row.get('id'), '回路ID', 16, False); drawing = row.get('drawing_id')
            tag = short(row.get('tag'), '回路号原文', 40, False)
            if cid in cids or drawing not in drawing_by_id or (drawing, canonical(tag)) in scopes:
                raise ValueError('回路ID/回路号重复或子图引用无效')
            cids.add(cid); scopes.add((drawing, canonical(tag)))
            members = strings(row.get('members', []), '回路成员ID', 24)
            if len(set(members)) != len(members): raise ValueError('同一回路重复引用成员')
            c = {'id': cid, 'drawing_id': drawing, 'tag': tag, 'phase': short(row.get('phase'), '相别', 40),
                 'load': short(row.get('load'), '负荷原文'), 'members': []}
            for iid in members:
                if iid not in by_id:
                    problems.append(issue('unresolved_member', '回路%s引用了未保留的候选%s' % (tag, iid), repairable=True)); continue
                o = by_id[iid]
                if o['drawing_id'] != drawing: raise ValueError('回路成员跨子图引用')
                if iid in assigned:
                    problems.append(issue('multiple_circuit_owners', '“%s”被绑定多个回路，暂不批准归属' % o['raw'], iid, True))
                    o.update(circuit_id=None, circuit=None, phase=None, load=None)
                else:
                    assigned[iid] = cid
                    if o['phase'] and c['phase'] and canonical(o['phase']) != canonical(c['phase']):
                        problems.append(issue('item_phase_disagrees', '“%s”的独立相别与回路表不一致' % o['raw'], iid, True))
                    o.update(circuit_id=cid, circuit=tag, phase=c['phase'], load=c['load'])
                c['members'].append(iid)
            cs.append(c)
        mark_out = []
        for m in marks:
            text = short(m.get('raw'), '独立标注原文', 100, False); target = m.get('target_id')
            mark = {'raw': text, 'target_id': target if target in by_id else None}; mark_out.append(mark)
            if target not in by_id:
                problems.append(issue('marker_target_unknown', '“%s”的归属尚未确认' % text, repairable=True)); continue
            o = by_id[target]; decoded = decode(text); params = decoded['explicit_parameters']
            if decoded['kind'] == 'residual_current': dest = o['branch_requirements']
            elif decoded['kind'] == 'ats_class' and o['category'] != 'ATS':
                problems.append(issue('ats_class_target_invalid', '“%s”没有关联到ATS候选' % text, target, True)); continue
            elif decoded['kind'] in ('ats_class', 'apparent_power', 'voltage_ratio', 'power_marking', 'breaking_marking', 'voltage_marking'):
                dest = o['associated_parameters']
            else:
                annotations.append({'raw': text, 'reason': '保留原文，尚未转为明确选型参数'}); continue
            for k, value in params.items():
                previous = dest.get(k, o['literal_parameters'].get(k))
                if previous is not None and previous != value:
                    problems.append(issue('parameter_conflict', '“%s”存在%s参数冲突，未覆盖原值' % (o['raw'], k), target, True)); continue
                dest[k] = value
            o['associated_markings'].append(text)
        if plan.get('status') != 'parsed':
            problems.append(issue('outline_failed', '第一遍回路表未通过校验，当前归属仍需原图核对', repairable=True))
        else:
            if plan.get('readable') is not True:
                problems.append(issue('outline_unreadable', '第一遍回路定位报告图面不清，仍需核对归属', repairable=True))
            for a in plan['circuits']:
                match = next((c for c in cs if c['drawing_id'] == a['drawing_id'] and canonical(c['tag']) == canonical(a['tag'])), None)
                if match is None:
                    problems.append(issue('outline_circuit_missing', '第一遍看到的回路“%s”在器件表未对应' % a['tag'], repairable=True)); continue
                for key, kind in (('device_labels', 'device'), ('cable_labels', 'cable')):
                    expected = a[key]
                    actual = [by_id[x]['raw'] for x in match['members'] if (by_id[x]['category'] == 'cable') == (kind == 'cable')]
                    left = list(actual)
                    for label in expected:
                        pos = next((i for i, rawlabel in enumerate(left) if same_label(label, rawlabel)), None)
                        if pos is None:
                            problems.append(issue('circuit_members_disagree', '回路%s两遍结果不一致：%s未归在同一支路' % (a['tag'], label), repairable=True))
                        else: left.pop(pos)
                    if left:
                        problems.append(issue('circuit_extra_members', '回路%s在第二遍新增了未对应的%s：%s' % (a['tag'], '线缆' if kind == 'cable' else '器件', '、'.join(left)), repairable=True))
                for field, label in (('phase', '相别'), ('load', '负荷')):
                    if a.get(field) and not match.get(field):
                        problems.append(issue('circuit_context_missing', '回路%s第一遍有%s原文，第二遍未保留' % (a['tag'], label), repairable=True))
                    elif a.get(field) and match.get(field) and canonical(a[field]) != canonical(match[field]):
                        problems.append(issue('circuit_context_disagrees', '回路%s两遍%s不一致' % (a['tag'], label), repairable=True))
            known_scopes = {(a['drawing_id'], canonical(a['tag'])) for a in plan['circuits']}
            for c in cs:
                if (c['drawing_id'], canonical(c['tag'])) not in known_scopes:
                    problems.append(issue('new_circuit_after_outline', '第二遍新增回路%s，尚未与第一遍定位结果对应' % c['tag'], repairable=True))
        if not d['readable']: problems.append(issue('page_unreadable', '模型报告当前页不可可靠读取', repairable=True))
        if not d['page_complete']: problems.append(issue('model_incomplete', '模型自报当前页尚未完整', repairable=True))
        for text in strings(d.get('unresolved', []), '识图待确认', 12):
            problems.append(issue('visual_uncertainty', text, repairable=True))
        for o in items:
            p = dict(o['literal_parameters']); p.update(o['associated_parameters'])
            if o['category'] == 'MCB' and 'residual_current_ma' in p:
                o['branch_requirements'].setdefault('residual_current_ma', p.pop('residual_current_ma'))
                o['literal_parameters'].pop('residual_current_ma', None)
            details = explain(o['raw'], o['category'], p)
            if o['branch_requirements'].get('residual_current_ma') is not None:
                value = o['branch_requirements']['residual_current_ma']
                details['meaning_cn'] += '；支路剩余电流要求%s mA（未擅改原型号）' % number_text(value)
                details['selection_fields_cn']['支路剩余电流要求_mA'] = value
            o.update(details)
            if o['raw'] and canonical(o['raw']) not in canonical(ctx['whole_document_text']):
                o['pending'].append('模型转写未在全文中完整匹配；全文匹配也不证明位置正确')
            for text in o['pending']:
                problems.append(issue('item_pending', '“%s”：%s' % (o['raw'], text), o['id'], o['category'] == 'unknown'))
        if ctx['page_count'] == 1:
            problems.extend(inventory_checks(ctx['source_inventory'], items, cs, mark_out))
        record = {'status': 'parsed', 'page': ctx['page'], 'image_name': ctx['image_name'],
                  'readable': d['readable'], 'model_reported_complete': d['page_complete'],
                  'view': plan.get('view', 'unknown'), 'drawings': draw_out, 'circuits': cs,
                  'items': items, 'markings': mark_out, 'annotations': annotations, 'issues': problems,
                  'recognition_accuracy_verified': False, 'selection_approved': False}
        encoded = j(record)
        if len(encoded) > 50000: raise ValueError('本页完整结果超过预算，未截断')
        action = 'RETRY' if any(x['repairable'] for x in problems) else 'ACCEPT'
        message = '；'.join(x['message'] for x in problems if x['repairable'])[:2200]
        return {'action': action, 'packet_json': encoded, 'message': message}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return {'action': 'RETRY', 'packet_json': j({'status': 'failed', 'page': ctx['page'],
                'image_name': ctx['image_name'], 'error': str(exc), 'issues': []}), 'message': str(exc)}


def choose_best(first_json, second_json):
    first = json.loads(first_json); second = json.loads(second_json)
    if (first['page'], first['image_name']) != (second['page'], second['image_name']): raise ValueError('重看结果串页')
    chosen = second
    if second['status'] != 'parsed' and first['status'] == 'parsed': chosen = first
    elif first['status'] == second['status'] == 'parsed':
        protected = [x for x in first['items'] if x.get('source_text_supported')]
        first_keys = Counter(literal_key(x['raw']) for x in (protected or first['items']))
        second_keys = Counter(literal_key(x['raw']) for x in second['items'])
        lost = first_keys - second_keys
        first_bad = sum(x['repairable'] for x in first['issues']); second_bad = sum(x['repairable'] for x in second['issues'])
        if lost or second_bad > first_bad:
            chosen = first
            chosen['issues'].append(issue('retry_not_adopted', '第二遍完整提取丢失已有条目或待核对增多，保留第一遍可读内容；两次原文见运行记录'))
        elif chosen is second:
            changed = Counter(literal_key(x['raw']) for x in first['items']) - second_keys
            if changed:
                chosen['issues'].append(issue('unverified_transcription_changed', '复看替换了未获全文支持的转写，仍需对照原图核对：' + '、'.join(list(changed)[:8])))
    chosen['attempt_audit'] = {'first_status': first['status'], 'second_status': second['status'],
                               'adopted': 'first' if chosen is first else 'second'}
    return {'packet_json': j(chosen)}


def collect(packets, manifest_compact):
    p = uncompress(manifest_compact); records = [json.loads(x) for x in packets]
    expected = {(x['page_number'], x['filename']) for x in p['images']}
    actual = [(x['page'], x['image_name']) for x in records]
    if len(actual) != len(expected) or len(set(actual)) != len(actual) or set(actual) != expected:
        raise ValueError('页图结果缺失或重复，不能声称整份已处理')
    inventory = source_inventory(p['native_text']); items = []; circuits = []; markings = []; problems = []; failed = []; drawings = []
    for record in sorted(records, key=lambda x: x['page']):
        if record['status'] != 'parsed': failed.append(record); continue
        drawings.extend(dict(x, page=record['page']) for x in record['drawings'])
        for row in record['items']:
            item = dict(row, local_id=row['id'], id='P%d-%s' % (record['page'], row['id']), page=record['page'])
            item['local_circuit_id'] = row['circuit_id']
            item['circuit_id'] = 'P%d-%s' % (record['page'], row['circuit_id']) if row['circuit_id'] else None
            drawing = next(x for x in record['drawings'] if x['id'] == row['drawing_id'])
            item['cabinet_tags'] = drawing['cabinet_tags']
            items.append(item)
        circuits.extend(dict(c, id='P%d-%s' % (record['page'], c['id']), local_id=c['id'], page=record['page'],
                             members=['P%d-%s' % (record['page'], x) for x in c['members']]) for c in record['circuits'])
        markings.extend(record['markings']); problems.extend(dict(x, page=record['page']) for x in record['issues'])
    if p['page_count'] > 1:
        problems.extend(dict(x, page=None) for x in inventory_checks(inventory, items, circuits, markings, check_counts=False))
    # Compact identical warnings without hiding which page produced them.
    unique = {}; dedup = []
    for row in problems:
        key = (row.get('page'), row['code'], row.get('item_id'), row['message'])
        if key not in unique: unique[key] = True; dedup.append(row)
    problems = dedup
    summary = {'model': 'TenGen-ZhiHan:F16', 'provider': 'ollama', 'pages': p['page_count'],
               'candidate_objects': len(items), 'cable_candidates': sum(x['category'] == 'cable' for x in items),
               'device_or_function_candidates': sum(x['category'] != 'cable' for x in items),
               'failed_pages': len(failed), 'pending_checks': len(problems),
               'status': 'needs_review' if problems or failed else 'no_rule_issue_detected',
               'native_text_available': bool(p['native_text'].strip()),
               'recognition_accuracy_verified': False, 'selection_approved': False,
               'native_vector_coordinates_available': False, 'automatic_zoom_enabled': False}
    result = {'schema_version': 'pdf-ollama-circuit/6', 'summary': summary, 'items': items,
              'circuits': circuits, 'drawings': drawings, 'issues': problems, 'failed_pages': failed, 'source_inventory': inventory,
              'source_text': p['native_text'], 'project_notes': p['notes'],
              'scope': '整页图像和全文辅助的选型输入核对；程序检查不代替原图验收，不生成采购数量、设计合规结论或产品库结果'}
    def cell(value): return html.escape(str(value if value is not None else '未明确')).replace('|', '&#124;').replace('\n', '<br>')
    lines = ['模型：Ollama / TenGen-ZhiHan:F16',
             '处理%d页，形成%d项图面候选（含%d项线缆）；回复失败%d页，程序列出%d项待核对。候选项数不是设备采购数量。' % (
                 p['page_count'], len(items), summary['cable_candidates'], len(failed), len(problems)),
             '模型自报“完整”不作为验收；回路表与器件表不一致、原文未定位、unknown等会单独列出。',
             '', '图面范围（不据柜号个数计算采购数量）：']
    for drawing in drawings[:12]:
        lines.append('- 第%s页/%s：%s；柜号：%s' % (drawing['page'], cell(drawing['id']), cell(drawing['title']), cell('、'.join(drawing['cabinet_tags']) or '未提取')))
    if len(drawings) > 12: lines.append('其他子图范围见结构化drawings字段。')
    lines += ['', '回路与负荷核对：', '|页/子图|图面回路号|相别|负荷原文|器件与线缆原文|', '|---|---|---|---|---|']
    for c in circuits[:40]:
        own = [o['raw'] for o in items if o['page'] == c['page'] and o['drawing_id'] == c['drawing_id'] and o['circuit_id'] == c['id']]
        lines.append('|' + '|'.join(cell(x) for x in ['%s/%s' % (c['page'], c['drawing_id']), c['tag'], c['phase'], c['load'], '；'.join(own)]) + '|')
    lines += ['', '标注含义与选型参数：', '|页/实例|原文|逐段中文解释|中文选型字段|回路/负荷|待确认|', '|---|---|---|---|---|---|']
    for o in items[:40]:
        fields = '；'.join(k + '：' + (j(v) if isinstance(v, (dict, list)) else str(v) if v is not None else '未明确') for k, v in o['selection_fields_cn'].items())
        lines.append('|' + '|'.join(cell(x) for x in [str(o['page']) + '/' + o['id'], o['raw'], o['meaning_cn'], fields,
                    (o['circuit'] or '未绑定支路') + '/' + (o['load'] or ''), '；'.join(o['pending'])]) + '|')
    if len(items) > 40 or len(circuits) > 40: lines.append('聊天展示前40项/回路，完整内容保存在结构化输出中，未删除其他条目。')
    if problems:
        lines += ['', '待核对（识图与选型信息，不是设计问题单）：']
        for row in problems[:35]: lines.append('- ' + cell(('第%s页：' % row['page'] if row.get('page') else '全文件：') + row['message']))
        if len(problems) > 35: lines.append('其余待核对项见结构化issues字段。')
    for f in failed: lines.append('- 第%d页回复失败：%s' % (f['page'], cell(f['error'])))
    if not p['native_text'].strip(): lines.append('没有可用辅助文字，本次完全依赖图像转写，无法用原文清单做漏项检查。')
    lines.append('\n整份结构化结果在“统一汇总中文解释与回路核对”的result_json；较大文件详情在result_pages，完整辅助文字在source_text。没有调用产品库、价格或CAD服务。')
    encoded = j(result); pages = [j(r) for r in records]; report = '\n'.join(lines)
    if len(report) > 60000 or any(len(x) > 60000 for x in pages): raise ValueError('完整输出超过节点预算，未截断冒充成功')
    if len(encoded) > 60000:
        encoded = j({'schema_version': result['schema_version'], 'summary': summary,
                     'details_location': 'result_pages', 'source_text_location': 'source_text',
                     'source_inventory': inventory, 'issues': problems,
                     'message': '完整详情按页保存在result_pages，未截断'})
        if len(encoded) > 60000: raise ValueError('汇总信息超过预算，未删除待核对项')
    return {'report': report, 'result_json': encoded, 'result_pages': pages, 'source_text': p['native_text']}
