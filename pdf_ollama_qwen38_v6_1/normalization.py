"""Normalize only explicit metadata conventions; never guess a diagram owner.

Malformed items are preserved as unparsed records, not repaired into facts.
"""
import json
import math
import unicodedata
from literal_decode import decode

ALIASES = {
    'mcb': 'MCB', 'mccb': 'MCCB', 'rcbo': 'RCBO', 'rccb': 'RCCB',
    'rcb': 'residual_current_device_unspecified', 'rcd': 'residual_current_device_unspecified',
    'residual_current_device_unspecified': 'residual_current_device_unspecified',
    'ats': 'ATS', 'spd': 'SPD', 'controller': 'controller', 'contactor': 'contactor',
    'relay': 'relay', 'timer': 'timer', 'transformer': 'transformer',
    'dc_power_supply': 'dc_power_supply', 'fuse': 'fuse', 'cable': 'cable', 'wire': 'cable',
    'unknown': 'unknown', '小型断路器': 'MCB', '塑壳断路器': 'MCCB',
    '剩余电流保护器件': 'residual_current_device_unspecified', '漏电保护器': 'residual_current_device_unspecified',
    '自动转换开关': 'ATS', '双电源转换开关': 'ATS', '浪涌保护器': 'SPD', '电涌保护器': 'SPD',
    '控制器': 'controller', '接触器': 'contactor', '继电器': 'relay', '时间控制器': 'timer',
    '时控开关': 'timer', '变压器': 'transformer', '直流电源': 'dc_power_supply',
    '熔断器': 'fuse', '线缆': 'cable', '电缆': 'cable', '电线': 'cable', '通信线': 'cable', '未知': 'unknown',
}


def primitive_id(value):
    if isinstance(value, str):
        s = unicodedata.normalize('NFKC', value).strip()
        return s if s and len(s) <= 80 else None
    if type(value) is int: return str(value)
    if type(value) is float and math.isfinite(value) and value.is_integer(): return str(int(value))
    return None


def raw_snippet(value, limit=500):
    text = json.dumps(value, ensure_ascii=False, default=str)
    return {'value': text[:limit], 'truncated': len(text) > limit}


def resolve_reference(value, defined):
    s = primitive_id(value)
    if s in defined: return s
    matches = [key for key in defined if s and key.casefold() == s.casefold()]
    return matches[0] if len(matches) == 1 else None


def normalized_category(value, raw):
    text = unicodedata.normalize('NFKC', value).strip().casefold().replace('-', '_').replace(' ', '_') if isinstance(value, str) else ''
    candidate = ALIASES.get(text)
    source = decode(raw)
    abbr = source['explicit_parameters'].get('drawing_abbreviation')
    if abbr: return {'RCB': 'residual_current_device_unspecified', 'RCD': 'residual_current_device_unspecified'}.get(abbr, abbr)
    if source['kind'] == 'cable': return 'cable'
    if source['kind'] == 'voltage_ratio': return 'transformer'
    if source['kind'] == 'function_label': return 'timer'
    return candidate or 'unknown'


def normalize_payload(data, plan):
    """Return schema-safe rows, plus explicit issues and preserved bad rows."""
    if not isinstance(data.get('items'), list): raise ValueError('候选列表items不是数组，无法逐项处理')
    if len(data['items']) > 40: raise ValueError('候选条目超过当前40项协议预算，未截断')
    if not isinstance(data.get('circuits', []), list): raise ValueError('回路列表circuits不是数组')
    if len(data.get('circuits', [])) > 20: raise ValueError('回路列表超过当前20条协议预算')
    notes = []; logs = []; unparsed = []; unbound = []
    def note(code, text, item=None, repairable=True):
        notes.append({'code': code, 'message': text, 'item_id': item, 'repairable': repairable})
    def text_or_none(value, limit, field, owner=None):
        if value is None: return None
        if isinstance(value, str) and len(value) <= limit: return value.strip()
        note('metadata_value_invalid', '%s格式无效，保留其他可读字段，未猜测该值' % field, owner)
        return None
    drawings = data.get('drawings')
    if drawings is None: drawings = plan.get('drawings', [])
    if not isinstance(drawings, list):
        note('drawings_invalid', '子图定义不是数组，候选仍保留，归属需确认')
        drawings = []
    if len(drawings) > 6: raise ValueError('子图数量超过当前6个协议预算，未截断')
    ds = []; defined = set()
    for row in drawings:
        if not isinstance(row, dict) or primitive_id(row.get('id')) is None:
            note('drawing_definition_invalid', '有子图定义缺少可用ID，未自动建立新子图')
            logs.append({'kind': 'invalid_drawing', 'raw': raw_snippet(row)}); continue
        key = primitive_id(row['id'])
        if key in defined:
            note('drawing_definition_duplicate', '子图ID%s重复；重复定义未自动合并成新的图面' % key)
            logs.append({'kind': 'duplicate_drawing', 'raw': raw_snippet(row)}); continue
        defined.add(key)
        tags = row.get('cabinet_tags', [])
        if not isinstance(tags, list):
            note('cabinet_tags_invalid', '子图%s的柜号格式无效，未转成数量' % key); tags = []
        if len(tags) > 8: raise ValueError('柜号清单超过当前协议预算')
        clean_tags = [t.strip() for t in tags if isinstance(t, str) and 0 < len(t) <= 180]
        ds.append({'id': key, 'title': text_or_none(row.get('title'), 140, '子图名称'), 'cabinet_tags': clean_tags})
    if not ds: note('no_confirmed_drawing', '没有确认的子图ID；保留候选文字，不自动把它们绑定到第一幅图')
    declared = {primitive_id(r.get('id')) for r in data['items'] if isinstance(r, dict)} - {None}
    used = set(); ambiguous = set(); items = []
    def generated():
        n = 1
        while 'UNBOUND%d' % n in declared or 'UNBOUND%d' % n in used: n += 1
        return 'UNBOUND%d' % n
    for index, row in enumerate(data['items']):
        if not isinstance(row, dict):
            unparsed.append({'index': index + 1, 'reason': '条目不是对象', 'raw': raw_snippet(row)})
            note('item_not_object', '第%d项不是对象，已保留诊断，其他条目继续' % (index + 1)); continue
        raw = row.get('raw')
        if raw is None: raw = row.get('label', row.get('observed_label'))
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 160:
            unparsed.append({'index': index + 1, 'reason': '型号原文缺失或超出160字预算', 'raw': raw_snippet(row)})
            note('item_raw_unusable', '第%d项的型号文字无法结构化；其他可读条目继续' % (index + 1)); continue
        raw = raw.strip(); original_id = primitive_id(row.get('id')); iid = original_id
        if iid is None or iid in used:
            if iid in used: ambiguous.add(iid)
            iid = generated()
            note('item_id_unresolved', '“%s”的原实例ID缺失或重复；只分配内部记录ID，不据此猜测回路' % raw, iid)
        used.add(iid)
        drawing = resolve_reference(row.get('drawing_id'), defined)
        category = normalized_category(row.get('category'), raw)
        logs.append({'item_id': iid, 'raw_label': raw, 'category_input': raw_snippet(row.get('category'), 120), 'category_used': category,
                     'drawing_input': raw_snippet(row.get('drawing_id'), 120), 'drawing_used': drawing})
        if drawing is None:
            note('item_drawing_unresolved', '“%s”的子图引用%s未在已定义ID中确认；条目保留，归属留空' % (raw, str(row.get('drawing_id'))[:80]), iid)
        items.append({'id': iid, 'drawing_id': drawing, 'raw': raw, 'category': category,
                      'position': text_or_none(row.get('position'), 80, '位置', iid),
                      'phase': text_or_none(row.get('phase'), 40, '相别', iid),
                      'model_id_raw': raw_snippet(row.get('id'), 120), 'model_category_raw': raw_snippet(row.get('category'), 120),
                      'model_drawing_id_raw': raw_snippet(row.get('drawing_id'), 120)})
    if ambiguous:
        note('ambiguous_item_ids', '存在重复原实例ID：%s；引用这些ID的回路暂不绑定' % '、'.join(sorted(ambiguous)))
    item_ids = {x['id'] for x in items}; circuits = []; cids = set(); scopes = set()
    for index, row in enumerate(data.get('circuits', [])):
        if not isinstance(row, dict):
            unbound.append({'reason': '回路不是对象', 'raw': raw_snippet(row)}); note('circuit_not_object', '有回路记录格式无效，其他回路继续'); continue
        cid = primitive_id(row.get('id')); drawing = resolve_reference(row.get('drawing_id'), defined)
        tag = row.get('tag')
        if not isinstance(tag, str) or not tag.strip() or len(tag) > 40 or drawing is None or cid is None:
            unbound.append({'reason': '回路号、ID或子图引用未确认', 'raw': raw_snippet(row)})
            note('circuit_metadata_unresolved', '第%d条回路的编号/子图引用未确认；保留原记录，不猜测归属' % (index + 1)); continue
        tag = tag.strip(); scope = (drawing, tag.casefold())
        if cid in cids or scope in scopes:
            unbound.append({'reason': '重复回路ID或同子图重复编号', 'raw': raw_snippet(row)})
            note('circuit_duplicate', '重复回路%s已列为待核对，未覆盖其他回路' % tag); continue
        cids.add(cid); scopes.add(scope); members = row.get('members', [])
        if not isinstance(members, list):
            note('members_invalid', '回路%s的成员不是数组，暂不绑定候选' % tag); members = []
        if len(members) > 24: raise ValueError('单回路成员超过协议预算')
        clean = []
        for value in members:
            ref = resolve_reference(value, item_ids)
            raw_id = primitive_id(value)
            if raw_id in ambiguous or ref in ambiguous:
                note('ambiguous_member_reference', '回路%s引用了重复ID%s，未选择其中一个候选' % (tag, raw_id)); continue
            if ref is None:
                note('unresolved_member_reference', '回路%s有未确认的成员引用%s' % (tag, str(value)[:80])); continue
            if ref in clean:
                note('duplicate_member_reference', '回路%s重复引用%s，展示中只保留一次引用，不改采购数量' % (tag, ref)); continue
            clean.append(ref)
        circuits.append({'id': cid, 'drawing_id': drawing, 'tag': tag,
                         'phase': text_or_none(row.get('phase'), 40, '回路相别'),
                         'load': text_or_none(row.get('load'), 140, '负荷原文'), 'members': clean})
    marks = data.get('markings', [])
    if not isinstance(marks, list):
        note('markings_invalid', '独立标注列表格式无效，其他候选保留'); marks = []
    if len(marks) > 24: raise ValueError('独立标注超过当前24项预算，未截断')
    clean_marks = []
    for row in marks:
        if not isinstance(row, dict) or not isinstance(row.get('raw'), str) or not 0 < len(row['raw']) <= 100:
            unparsed.append({'reason': '独立标注无法结构化', 'raw': raw_snippet(row)}); note('marking_unusable', '有独立标注格式无效，未把它猜成参数'); continue
        target = resolve_reference(row.get('target_id'), item_ids)
        if primitive_id(row.get('target_id')) in ambiguous: target = None
        clean_marks.append({'raw': row['raw'].strip(), 'target_id': target})
    unresolved = data.get('unresolved', [])
    if not isinstance(unresolved, list):
        note('unresolved_field_invalid', '模型的待确认列表格式无效，程序仍单独检查漏项'); unresolved = []
    if len(unresolved) > 12: raise ValueError('模型待确认列表超过协议预算')
    clean_unresolved = [x for x in unresolved if isinstance(x, str) and len(x) <= 180]
    readable = data.get('readable'); complete = data.get('page_complete')
    if type(readable) is not bool: readable = False; note('readability_flag_missing', '模型没有有效可读状态，未判为已看清')
    if type(complete) is not bool: complete = False; note('complete_flag_missing', '模型没有有效完整状态，未判为完整')
    normalized = {'readable': readable, 'page_complete': complete, 'drawings': ds, 'items': items,
                  'circuits': circuits, 'markings': clean_marks, 'unresolved': clean_unresolved}
    return normalized, {'issues': notes, 'item_metadata': logs, 'unparsed_records': unparsed,
                        'unbound_circuits': unbound, 'defined_drawing_ids': sorted(defined),
                        'normalization_policy': '显式类别别名/原文缩写解码；不猜子图归属，不修补截断JSON'}


def response_diagnostics(raw, stage, error=None, adaptation=None, plan=None):
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
    out = {'stage': stage, 'raw_response_chars': len(text), 'raw_response_excerpt': text[:1800],
           'excerpt_truncated': len(text) > 1800}
    if error is not None:
        out.update(error_type=type(error).__name__, error=str(error))
        pos = getattr(error, 'pos', None)
        if isinstance(pos, int): out['parse_error_context'] = text[max(0, pos - 160):pos + 160]
    if adaptation:
        out['item_metadata'] = adaptation['item_metadata']
        out['unparsed_records'] = adaptation['unparsed_records']
        out['unbound_circuits'] = adaptation['unbound_circuits']
        out['defined_drawing_ids'] = adaptation['defined_drawing_ids']
    if plan:
        out['outline_status'] = plan.get('status')
        if plan.get('error'): out['outline_error'] = plan['error']
        if plan.get('diagnostics'): out['outline_diagnostics'] = plan['diagnostics']
    return out
