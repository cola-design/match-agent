"""One PDF in, page-grounded evidence and one selection workbook out.

No model credentials, database access, CAD writes, or external parsing service.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import html
import io
import json
import math
import re

import fitz
from . import native

SCHEMA = 'electrical-pdf/4'
MAX_PAGES = 12
MAX_UNITS = 24
MAX_BYTES = 30 * 1024 * 1024
MAX_TARGETS = 6
CATEGORIES = {
    'MCB', 'MCCB', 'RCBO', 'RCCB', 'residual_current_device_unspecified', 'ATS',
    'SPD', 'controller', 'contactor', 'relay', 'timer', 'transformer',
    'dc_power_supply', 'fuse', 'unknown',
}
TARGET_KINDS = {'unclassified_model', 'device_tag', 'function_label'}
OBJECT_KINDS = {'device_model', 'unclassified_model', 'cable', 'voltage_ratio'}
GLOSSARY = {
    'MCB': '小型断路器', 'MCCB': '塑壳断路器',
    'RCBO': '带过电流保护的剩余电流动作断路器',
    'RCCB': '不带过电流保护的剩余电流动作断路器',
    'residual_current_device_unspecified': '剩余电流保护器件；订货类别未明确',
    'ATS': '自动转换开关', 'SPD': '浪涌保护器件候选，完整配置待核对',
    'controller': '控制/监测设备候选，具体功能按图纸或产品资料核对',
    'contactor': '接触器或其图面触点标识，实体与线圈规格尚待核对',
    'relay': '继电器或其图面触点标识', 'timer': '时间控制器/时控功能标识',
    'transformer': '变压器电压标注', 'dc_power_supply': '直流电源规格标注',
    'fuse': '熔断保护器件候选', 'unknown': '类别待核对', 'cable': '电线/电缆/通信线标注',
}
ROUTES = {'CE': '沿顶板面敷设', 'WE': '沿墙面敷设', 'WC': '墙内暗敷',
          'CC': '顶板内暗敷', 'FC': '地面/地板下敷设；以项目图例为准'}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def strict_json(raw):
    def unique(pairs):
        row = {}
        for k, v in pairs:
            if k in row:
                raise ValueError('JSON字段重复：' + k)
            row[k] = v
        return row
    def invalid(v):
        raise ValueError('JSON含非有限数值：' + v)
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def open_pdf(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise ValueError('请上传不超过30MB的PDF文件')
    try:
        doc = fitz.open(stream=data, filetype='pdf')
    except Exception as exc:
        raise ValueError('文件不是可读取的PDF') from exc
    if doc.needs_pass:
        doc.close()
        raise ValueError('PDF有密码，请先提供可读取的PDF')
    if not 1 <= len(doc) <= MAX_PAGES:
        doc.close()
        raise ValueError('本试用版单份支持1至12页；超出会停止，不会只处理前12页')
    return doc


def analyze(data, filename):
    sid = hashlib.sha256(data).hexdigest()[:16]
    result = {'schema_version': SCHEMA, 'source_id': sid,
              'source_filename': filename, 'pages': [], 'issues': []}
    with open_pdf(data) as doc:
        for pg in doc:
            anchors = native.native_annotations(pg, sid)
            if len(anchors) > 3000 or len(pg.get_drawings()) > 20000:
                raise ValueError('第%d页超过当前原生解析预算；未跳过该页' % (pg.number + 1))
            geometry = native.segments(pg, anchors)
            groups, branches, issues = native.associate_branches(anchors, geometry)
            links, extra = native.associate_separate_parameters(anchors, branches)
            for a in anchors:
                a['bbox_page'] = a.pop('bbox')
                if a['kind'] == 'cable':
                    a['explicit_parameters']['installation_codes'] = [
                        {'code': x, 'common_meaning': ROUTES[x], 'project_legend_priority': True}
                        for x in ROUTES if re.search(r'(?<![A-Z])' + x + r'(?![A-Z])', a['raw'].upper())]
                    multi = re.match(r'^\s*(\d+)\s*[*×]\s*\(', a['raw'])
                    if multi:
                        a['explicit_parameters']['explicit_multiplier'] = int(multi[1])
            branch_map = {b['id']: '%s:p%d:%s' % (sid, pg.number + 1, b['id']) for b in branches}
            for b in branches:
                b['id'] = branch_map[b['id']]
            for link in links:
                link['circuit_id'] = branch_map[link['circuit_id']]
            if not anchors:
                issues.append({'type': 'native_text_unavailable',
                               'message': '本页没有原生文字；当前插件未包含OCR，视觉建议不能替代完整转写'})
            content_boxes = [a['bbox_page'] for a in anchors]
            content_boxes += [list(d['rect'] * pg.rotation_matrix) for d in pg.get_drawings()
                              if not d['rect'].is_empty]
            content_boxes += [list(fitz.Rect(i['bbox']) * pg.rotation_matrix) for i in pg.get_image_info()]
            ink = fitz.Rect(native.union(content_boxes)) if content_boxes else fitz.Rect(pg.rect)
            ink = fitz.Rect(ink.x0 - 8, ink.y0 - 8, ink.x1 + 8, ink.y1 + 8) & pg.rect
            page = {'page': pg.number + 1, 'size': list(pg.rect), 'rotation': pg.rotation,
                    'content_bbox_page': list(ink),
                    'anchors': anchors, 'branches': branches, 'parameter_links': links,
                    'issues': issues + extra, 'vector_segments': len(geometry)}
            result['pages'].append(page)
            result['issues'].extend(dict(x, page=pg.number + 1) for x in page['issues'])
    return result


def candidate_anchor(a):
    return a['kind'] in OBJECT_KINDS | TARGET_KINDS or (
        a['kind'] == 'power_marking' and 'current_a' in a['explicit_parameters'])


def crop_plan(page, targets):
    """Native font size and unresolved symbols trigger zoom; IDs stay global."""
    bounds = fitz.Rect(page['size'])
    content = fitz.Rect(page['content_bbox_page'])
    scale = min(2000 / max(content.width, content.height), 300 / 72)
    needed = [a for a in page['anchors'] if candidate_anchor(a) and (
        a['id'] in targets or a.get('font_size', 8) * scale < 13)]
    bins = defaultdict(list)
    for a in needed:
        x, y = native.center(a['bbox_page'])
        bins[(int((x - bounds.x0) / 340), int((y - bounds.y0) / 280))].append(a)
    clips = []
    for key in sorted(bins, key=lambda k: (k[1], k[0])):
        rows = bins[key]
        # Dense unknown labels get separate review tasks, never silently skipped.
        unclear = [a for a in rows if a['id'] in targets]
        groups = [unclear[i:i + MAX_TARGETS] for i in range(0, len(unclear), MAX_TARGETS)] or [[]]
        for group in groups:
            owned = group if len(groups) > 1 else rows
            rect = fitz.Rect(native.union([a['bbox_page'] for a in owned]))
            rect = fitz.Rect(rect.x0 - 45, rect.y0 - 65, rect.x1 + 45, rect.y1 + 75) & bounds
            clips.append((list(rect), [a['id'] for a in group]))
    return clips


def prepare(data, filename, render=True, native_data=None):
    native_data = native_data or analyze(data, filename)
    manifest = {'schema_version': SCHEMA, 'source_id': native_data['source_id'],
                'source_filename': filename, 'page_count': len(native_data['pages']),
                'native_anchor_count': sum(len(p['anchors']) for p in native_data['pages']),
                'units': [], 'source_issues': native_data['issues']}
    images = []
    for p in native_data['pages']:
        targets = {a['id'] for a in p['anchors'] if a['kind'] in TARGET_KINDS}
        full_targets = sorted(targets)[:MAX_TARGETS]
        # Remove empty paper margins only; all drawing content stays in the overview.
        plan = [('full', p['content_bbox_page'], full_targets)]
        plan += [('zoom', r, t) for r, t in crop_plan(p, targets)]
        for idx, (view, rect, target_ids) in enumerate(plan):
            unit_id = 'p%d-%s%d' % (p['page'], view, idx)
            image_name = '%s-%s.png' % (manifest['source_id'], unit_id)
            frame = fitz.Rect(rect)
            local = [a for a in p['anchors'] if frame.intersects(fitz.Rect(a['bbox_page']))]
            context_anchors = [{'id': a['id'], 'raw': a['raw'], 'kind': a['kind'],
                                'bbox_page': a['bbox_page']} for a in local
                               if candidate_anchor(a) or a['kind'] in ('phase', 'circuit_tag')]
            context = {'source_id': manifest['source_id'], 'page': p['page'], 'view': view,
                       'unit_id': unit_id, 'image_bbox_page': rect, 'page_size': p['size'],
                       'coordinate_system': 'display_page_points_top_left',
                       'target_anchor_ids': target_ids, 'anchors': context_anchors,
                       'page_context': {
                           'branches': [{'id': b['id'], 'circuit_tag': b['circuit_tag'],
                                         'device_anchor_ids': b['device_annotation_ids']}
                                        for b in p['branches']],
                           'notes': [{'id': a['id'], 'raw': a['raw']} for a in p['anchors']
                                     if a['kind'] == 'other'],
                       },
                       'task_scope': '仅辅助核验target_anchor_ids的类别与图面符号；原文与参数由程序保留'}
            if len(dumps(context)) > 50000:
                raise ValueError('第%d页上下文超过当前预算，未截断后假称完整' % p['page'])
            manifest['units'].append({'unit_id': unit_id, 'page': p['page'], 'view': view,
                                      'image_name': image_name, 'context': context})
    if len(manifest['units']) > MAX_UNITS:
        raise ValueError('本文件需要%d张整页/放大图，超过当前24张预算；未处理部分页面冒充完成' % len(manifest['units']))
    if len(dumps(manifest)) > 500000:
        raise ValueError('整份证据超过当前节点预算，未删除原文继续运行')
    if not render:
        return manifest, []
    # All parsing and budget validation finishes before any image is emitted.
    with open_pdf(data) as doc:
        total_image_bytes = 0
        for u in manifest['units']:
            c = u['context']; pg = doc[u['page'] - 1]; rect = fitz.Rect(c['image_bbox_page'])
            scale = min(300 / 72, 2000 / max(rect.width, rect.height))
            pix = pg.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
            blob = pix.tobytes('png')
            total_image_bytes += len(blob)
            if total_image_bytes > 40 * 1024 * 1024:
                raise ValueError('整份图片超过40MB预算，未省略后续图片')
            u['pixels'] = [pix.width, pix.height]
            images.append((u['image_name'], blob))
    return manifest, images


def category_of(a):
    p = a['explicit_parameters']; kind = a['kind']
    if kind == 'device_model':
        return {'RCB': 'residual_current_device_unspecified', 'RCD': 'residual_current_device_unspecified'}.get(
            p.get('drawing_abbreviation'), p.get('drawing_abbreviation', 'unknown'))
    if kind == 'cable': return 'cable'
    if kind == 'voltage_ratio': return 'transformer'
    if kind == 'power_marking': return 'dc_power_supply'
    if kind == 'function_label': return 'timer'
    return 'unknown'


def merge(native_data, manifest, records, selection_context=''):
    units = {u['unit_id']: u for u in manifest['units']}
    ids = [r.get('unit_id') for r in records]
    if len(ids) != len(units) or set(ids) != set(units) or len(set(ids)) != len(ids):
        raise ValueError('整页/放大图的结果数量或编号不一致，停止合并')
    if any(r.get('source_id') != native_data['source_id'] for r in records):
        raise ValueError('视觉结果与当前PDF来源不一致')
    suggestions = defaultdict(list); failures = []; review_gaps = []; unmapped = []
    assessed = set(); unreadable = []
    for r in records:
        u = units[r['unit_id']]
        allowed = set(u['context']['target_anchor_ids'])
        if r.get('status') != 'valid':
            failures.append({'unit_id': r['unit_id'], 'error': r.get('error', '模型输出无效')})
            continue
        d = r['data']
        if not d['readable']:
            unreadable.append(r['unit_id'])
            continue
        for check in d['checks']:
            if check['anchor_id'] not in allowed or check['category'] not in CATEGORIES:
                raise ValueError('存在未授权的原文引用或类别')
            suggestions[check['anchor_id']].append(dict(check, unit_id=r['unit_id']))
            assessed.add(check['anchor_id'])
        review_gaps.extend({'unit_id': r['unit_id'], 'anchor_id': aid}
                           for aid in allowed - {c['anchor_id'] for c in d['checks']})
        unmapped.extend(dict(x, unit_id=r['unit_id']) for x in d.get('unmapped_symbols', []))
    objects = []; branches = []; evidence = []; conflicts = []; issues = deepcopy(native_data['issues'])
    for p in native_data['pages']:
        anchors = {a['id']: a for a in p['anchors']}
        evidence.extend(dict(a, page=p['page']) for a in p['anchors'])
        branches.extend(dict(b, page=p['page']) for b in p['branches'])
        owners = defaultdict(list)
        for b in p['branches']:
            for k in ('device_annotation_ids', 'wire_annotation_ids', 'function_annotation_ids'):
                for aid in b[k]: owners[aid].append(b)
        links = defaultdict(list)
        for link in p['parameter_links']:
            links[link['target_anchor']].append(link)
        for a in p['anchors']:
            if not candidate_anchor(a): continue
            cat = category_of(a); decisions = suggestions[a['id']]
            supported = [d for d in decisions if d['confidence'] in ('high', 'medium')
                         and d['symbol_visible'] is True and d['category'] != 'unknown']
            alternatives = {d['category'] for d in supported}
            if a['kind'] in TARGET_KINDS and supported:
                if a['kind'] == 'function_label' and cat == 'timer' and alternatives != {'timer'}:
                    conflicts.append({'anchor_id': a['id'], 'categories': sorted(alternatives | {'timer'}),
                                      'message': '视觉建议与明确时控文字含义不一致，保留原文含义，不改成其他器件'})
                elif len(alternatives) == 1:
                    cat = next(iter(alternatives))
                else:
                    cat = 'unknown'
                    conflicts.append({'anchor_id': a['id'], 'categories': sorted(alternatives),
                                      'message': '整页/局部分类不一致，未按首个结果批准类别'})
            params = deepcopy(a['explicit_parameters']); associated = []; requirements = {}
            for link in links[a['id']]:
                ref = link['parameter_anchor']; associated.append(ref)
                # A separately printed 30mA stays a branch selection requirement.
                if anchors[ref]['kind'] == 'residual_current':
                    requirements.update(link['parameters'])
                else:
                    params.update(link['parameters'])
            pending = []
            if cat == 'unknown': pending.append('器件类别未核实')
            if a['kind'] == 'device_tag': pending.append('位号/触点不能直接确定采购实体及数量')
            if cat == 'dc_power_supply': pending.append('输入/输出方向须结合模块资料核对，未擅自补交流输入电压')
            if params.get('trip_marking_raw'): pending.append('保留MA脱扣器原标记，不作为MCB的B/C/D曲线')
            if not supported and a['kind'] in TARGET_KINDS: pending.append('视觉符号尚未确认，原文已保留')
            if len(owners[a['id']]) > 1: pending.append('存在多个初步回路归属，未强行择一')
            obj = {'id': a['id'], 'page': p['page'], 'observed_label': a['raw'],
                   'category': cat, 'meaning': GLOSSARY.get(cat, '类别待核对'),
                   'object_type': 'cable' if cat == 'cable' else 'device_candidate',
                   'explicit_parameters': params, 'branch_selection_requirements': requirements,
                   'associated_parameter_anchor_ids': associated,
                   'circuit_ids': [b['id'] for b in owners[a['id']]],
                   'circuit_tags': [b['circuit_tag'] for b in owners[a['id']]],
                   'phases': sorted({anchors[i]['raw'] for b in owners[a['id']] for i in b['phase_anchor_ids']}),
                   'bbox_page': a['bbox_page'], 'source': 'native_pdf_text',
                   'visual_checks': decisions, 'candidate_quantity': None,
                   'quantity_basis': '原文位置实例；尚未确认采购数量', 'pending': pending,
                   'selection_approved': False}
            objects.append(obj)
    summary = {'source_filename': native_data['source_filename'], 'pages': len(native_data['pages']),
               'native_text_spans': len(evidence), 'image_units': len(units),
               'full_page_units': sum(u['view'] == 'full' for u in units.values()),
               'automatic_zoom_units': sum(u['view'] == 'zoom' for u in units.values()),
               'candidate_objects': len(objects), 'device_candidates': sum(o['object_type'] != 'cable' for o in objects),
               'cable_candidates': sum(o['object_type'] == 'cable' for o in objects),
               'visual_response_failures': len(failures), 'unreadable_units': len(unreadable),
               'classification_conflicts': len(conflicts), 'unmapped_visual_symbols': len(unmapped),
               'unknown_category_candidates': sum(o['category'] == 'unknown' for o in objects),
               'native_text_preserved': True, 'recognition_accuracy_verified': False,
               'complete_symbol_coverage_verified': False, 'product_database_matched': False,
               'selection_approved': False}
    return {'schema_version': SCHEMA, 'source_id': native_data['source_id'], 'summary': summary,
            'selection_context': {'text': selection_context, 'source': '用户表单，不是图纸原文'},
            'objects': objects, 'branches': branches, 'source_evidence': evidence,
            'classification_conflicts': conflicts, 'failed_visual_units': failures,
            'unreadable_units': unreadable, 'unreturned_visual_targets': review_gaps,
            'unmapped_visual_symbols': unmapped, 'source_issues': issues,
            'scope': '完整PDF原文与选型候选提取；不执行设计审查、数据库选型、报价或生产出图'}


def workbook_bytes(result):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook(); ws = wb.active; ws.title = '选型信息'
    ws.append(['实例ID', '页码', '回路号', '相别', '图面原文', '类别', '中文含义', '明确参数',
               '支路选型要求', '采购数量', '待确认', '图面位置'])
    for o in result['objects']:
        ws.append([o['id'], o['page'], ' / '.join(o['circuit_tags']), ' / '.join(o['phases']),
                   o['observed_label'], o['category'], o['meaning'], dumps(o['explicit_parameters']),
                   dumps(o['branch_selection_requirements']), '未确认', '；'.join(o['pending']), dumps(o['bbox_page'])])
    ws = wb.create_sheet('全部原文')
    ws.append(['原文ID', '页码', '原文', '文字类别', '明确参数', '图面位置'])
    for a in result['source_evidence']:
        ws.append([a['id'], a['page'], a['raw'], a['kind'], dumps(a['explicit_parameters']), dumps(a['bbox_page'])])
    ws = wb.create_sheet('识别状态')
    ws.append(['类型', '内容'])
    ws.append(['汇总', dumps(result['summary'])]); ws.append(['选型约束', dumps(result['selection_context'])])
    for key in ('failed_visual_units', 'unreadable_units', 'unreturned_visual_targets',
                'classification_conflicts', 'unmapped_visual_symbols', 'source_issues'):
        for row in result[key]: ws.append([key, dumps(row)])
    for ws in wb:
        ws.freeze_panes = 'A2'; ws.auto_filter.ref = ws.dimensions
        for c in ws[1]:
            c.font = Font(color='FFFFFF', bold=True); c.fill = PatternFill('solid', fgColor='234F6B')
        for col in ws.columns: ws.column_dimensions[col[0].column_letter].width = 26
        for row in ws:
            for c in row:
                if isinstance(c.value, str): c.data_type = 's'
                c.alignment = Alignment(vertical='top', wrap_text=True)
    out = io.BytesIO(); wb.save(out); return out.getvalue()


def report(result):
    s = result['summary']
    lines = ['已读取整份PDF：%d页，保留%d处原文；整页图%d张，内部自动放大%d张。' % (
        s['pages'], s['native_text_spans'], s['full_page_units'], s['automatic_zoom_units']),
        '提取%d项选型候选，已按原文ID合并放大图重复项。完整结果见下方Excel和JSON。' % s['candidate_objects'],
        '视觉回复失败%d处；不可读%d处；分类分歧%d处。原文保留不代表符号已全部识别。' % (
            s['visual_response_failures'], s['unreadable_units'], s['classification_conflicts']),
        '', '|页码|回路|原文|类别|', '|---|---|---|---|']
    for o in result['objects'][:30]:
        fields = [o['page'], ' / '.join(o['circuit_tags']), o['observed_label'], o['meaning']]
        lines.append('|' + '|'.join(html.escape(str(v)).replace('|', '&#124;').replace('\n', ' ') for v in fields) + '|')
    if len(result['objects']) > 30: lines.append('聊天仅展示前30项，完整候选和全部原文均在附件中。')
    lines.append('类别仍未核实：%d项，详见Excel待确认列。' % s['unknown_category_candidates'])
    missing_native = [x['page'] for x in result['source_issues'] if x['type'] == 'native_text_unavailable']
    if missing_native:
        lines.append('第%s页没有可提取的原生文字：本插件未包含OCR，这些页尚未完成转写，不能作为完整识别结果。' % '、'.join(map(str, missing_native)))
    lines.append('\n当前输出用于核对选型输入；尚未进行产品库匹配，数量与选型未批准。')
    return '\n'.join(lines)
