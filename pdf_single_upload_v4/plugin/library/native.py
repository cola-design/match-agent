from __future__ import annotations
from statistics import median
import math
import re
import unicodedata
import fitz

def norm(s):
    return re.sub('[‐‑‒–—−]', '-', unicodedata.normalize('NFKC', str(s))).strip()

def box(values):
    return [round(float(x), 4) for x in values]

def center(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)

def union(boxes):
    return [min((b[0] for b in boxes)), min((b[1] for b in boxes)), max((b[2] for b in boxes)), max((b[3] for b in boxes))]

def decode(raw):
    """Decode explicit literal markings; never infer catalogue properties."""
    s = norm(raw).upper()
    p = {}
    kind = 'other'
    meaning = ''
    m = re.search('(?<![A-Z0-9])(MCCB|MCB|RCBO|RCCB|RCB|RCD)(?=[\\d /-]|$)', s)
    if m:
        kind = 'device_model'
        p['drawing_abbreviation'] = m[1]
        meaning = {'MCCB': '塑壳断路器', 'MCB': '小型断路器', 'RCB': '剩余电流保护器件，订货类别待核对', 'RCBO': '带过电流保护的剩余电流动作断路器', 'RCCB': '不带过电流保护的剩余电流动作断路器', 'RCD': '剩余电流保护器件统称'}[m[1]]
        curve = re.search('(?:^|[/\\s-])([BCD])\\s*(\\d+(?:\\.\\d+)?)\\s*A(?=$|[/\\s(])', s)
        if curve:
            p.update(trip_curve=curve[1], rated_current_a=float(curve[2]))
        trip = re.search('MA\\d+(?:\\.\\d+)?A', s)
        if trip:
            p['trip_marking_raw'] = trip[0]
            p['rated_current_a'] = float(re.search('\\d+(?:\\.\\d+)?', trip[0])[0])
    elif re.search('(?<![A-Z])(?:WDZ[A-Z0-9]*-|NHBV-|ZNBV-|BVR?-|UTP/|CAT\\d)', s):
        kind = 'cable'
        meaning = '电线、线缆或通信线标注'
        if 'WDZB-' in s:
            p.update(halogen_free=True, low_smoke=True, flame_retardant_category='B')
        family = re.search('(?:WDZ[A-Z0-9]*-)(BYJ|YJY|YJV|RYJS|RYJSP|RYJ)(?:-|$)', s)
        if family:
            p['family'] = family[1]
            if family[1] == 'BYJ':
                p['family_meaning'] = '交联聚烯烃绝缘布电线'
            elif family[1] == 'YJY':
                p['family_meaning'] = '交联聚乙烯绝缘、聚烯烃护套电力电缆'
        spec = re.search('\\d+\\s*[X*]\\s*\\d+(?:\\.\\d+)?(?:\\s*\\+\\s*\\d+\\s*[X*]\\s*\\d+(?:\\.\\d+)?)*', s)
        if spec:
            p['conductor_groups'] = [{'count': int(n), 'area_mm2': float(a)} for n, a in re.findall('(\\d+)\\s*[X*]\\s*(\\d+(?:\\.\\d+)?)', spec[0])]
        routes = {'JDG': '套接紧定式钢导管', 'SC': '钢管敷设标识', 'CT': '电缆桥架敷设标识'}
        p['containment'] = [{'code': a + b, 'meaning': routes[a]} for a, b in re.findall('(?<![A-Z])(JDG|SC|CT)(\\d*)', s)]
        cat = re.search('CAT(\\dE?)', s)
        if cat:
            p['category_raw'] = 'CAT' + cat[1]
    elif re.fullmatch('\\d{1,3}\\s*[#号]', s):
        kind = 'circuit_tag'
    elif re.fullmatch('L[123](?:[~～-]L?[123])?(?:[,、]L[123])*|[ABC]相|N|PE|PEN', s):
        kind = 'phase'
    elif re.fullmatch('\\d+(?:\\.\\d+)?\\s*MA', s):
        kind = 'residual_current'
    elif re.fullmatch('(?:AC|DC)?\\s*\\d+(?:\\.\\d+)?\\s*/\\s*\\d+(?:\\.\\d+)?\\s*V', s):
        kind = 'voltage_ratio'
        p['voltage_pair_v'] = [float(x) for x in re.findall('\\d+(?:\\.\\d+)?', s)]
    elif re.fullmatch('\\d+(?:\\.\\d+)?\\s*VA', s):
        kind = 'apparent_power'
        p['apparent_power_va'] = float(re.search('\\d+(?:\\.\\d+)?', s)[0])
    elif re.fullmatch('(?:DC|AC)\\s*\\d+(?:\\.\\d+)?\\s*V(?:\\s+\\d+(?:\\.\\d+)?\\s*A)?', s):
        kind = 'power_marking'
        v = re.search('(\\d+(?:\\.\\d+)?)\\s*V', s)
        a = re.search('(\\d+(?:\\.\\d+)?)\\s*A', s)
        p.update(power_type=s[:2], voltage_v=float(v[1]))
        if a:
            p['current_a'] = float(a[1])
    elif re.match('^ATS[\\s-]', s):
        kind = 'device_model'
        meaning = '自动转换开关型号标注'
        p['drawing_abbreviation'] = 'ATS'
    elif re.fullmatch('(?:K[AMT]|Q[FS]|FU|SPD|TA|PM)\\d*', s):
        kind = 'device_tag'
    elif '时间控制器' in s or '时控开关' in s:
        kind = 'function_label'
    elif re.match('^(?:PE|KX|COS|IJ)\\s*[=＝]', s):
        kind = 'calculation'
    elif re.fullmatch('[A-Z][A-Z0-9]{1,14}-[A-Z0-9][A-Z0-9./+() -]{1,70}', s) and re.search('\\d', s):
        kind = 'unclassified_model'
    if kind in ('device_model', 'unclassified_model'):
        poles = re.search('(?<!\\d)([1-4]P(?:\\+N)?)(?![A-Z0-9])', s)
        if poles:
            p['pole_marking'] = poles[1]
        if 'rated_current_a' not in p:
            pattern = '(?:^|\\s)(\\d+(?:\\.\\d+)?)\\s*A(?=$|[/\\s(-])' if kind == 'unclassified_model' else '(?<![A-Z0-9.])(\\d+(?:\\.\\d+)?)\\s*A(?=$|[/\\s(-])'
            currents = re.findall(pattern, s)
            if len(currents) == 1:
                p['rated_current_a'] = float(currents[0])
    if kind in ('device_model', 'residual_current'):
        leak = re.search('(?<![\\d.])(\\d+(?:\\.\\d+)?)\\s*MA(?=$|[/\\s(])', s)
        if leak:
            p['residual_current_ma'] = float(leak[1])
    return {'kind': kind, 'meaning': meaning, 'explicit_parameters': p}

def native_annotations(page, sid):
    rows = []
    for block in page.get_text('dict')['blocks']:
        if block.get('type') != 0:
            continue
        for line in block['lines']:
            for span in line['spans']:
                raw = span['text'].strip()
                if not raw:
                    continue
                r = fitz.Rect(span['bbox']) * page.rotation_matrix
                direction = fitz.Point(line['dir']) * fitz.Matrix(page.rotation_matrix.a, page.rotation_matrix.b, page.rotation_matrix.c, page.rotation_matrix.d, 0, 0)
                rows.append({'id': f'{sid}:p{page.number + 1}:t{len(rows) + 1}', 'raw': raw, 'bbox': box(r), 'font_size': round(float(span['size']), 4), 'direction': box(direction), 'source': 'native_pdf_text', **decode(raw)})
    return rows

def segments(page, annotations):
    """Recover wire-sized axis segments from strokes and thin filled strips.

    Native text boxes mask SHX glyph outlines, so those are not treated as wires.
    Thin filled polygons are collapsed to one centreline, not counted twice.
    """
    masked = [fitz.Rect(a['bbox']) for a in annotations]
    native = []
    tiny = []
    sizes = [a['font_size'] for a in annotations if a['font_size'] > 0]
    scale = median(sizes) if sizes else 6
    min_len = max(2, scale * 0.45)

    def add(p, q, pid, strip=False):
        p = p * page.rotation_matrix
        q = q * page.rotation_matrix
        dx, dy = (abs(p.x - q.x), abs(p.y - q.y))
        if max(dx, dy) < 0.12:
            return
        mid = (p + q) / 2
        if any((r.contains(mid) for r in masked)):
            return
        axis = ('h' if dx >= dy else 'v') if min(dx, dy) <= max(0.15, min_len * 0.06) else 'o'
        value, lo, hi = ((p.y + q.y) / 2, min(p.x, q.x), max(p.x, q.x)) if axis == 'h' else ((p.x + q.x) / 2, min(p.y, q.y), max(p.y, q.y))
        data = {'axis': axis, 'value': float(value), 'lo': lo, 'hi': hi, 'paths': [pid], 'filled_strip': strip, 'p1': box(p), 'p2': box(q)}
        if max(dx, dy) < min_len:
            if axis in ('h', 'v'):
                tiny.append(data)
        else:
            native.append(data)
    for pid, path in enumerate(page.get_drawings()):
        rect = path['rect']
        width, height = (rect.width, rect.height)
        if path.get('fill') is not None and min(width, height) <= max(0.8, scale * 0.2) and (max(width, height) >= min_len):
            if width >= height:
                add(fitz.Point(rect.x0, (rect.y0 + rect.y1) / 2), fitz.Point(rect.x1, (rect.y0 + rect.y1) / 2), pid, True)
            else:
                add(fitz.Point((rect.x0 + rect.x1) / 2, rect.y0), fitz.Point((rect.x0 + rect.x1) / 2, rect.y1), pid, True)
            continue
        for item in path['items']:
            if item[0] == 'l':
                add(item[1], item[2], pid)
            elif item[0] == 're':
                r = item[1]
                for p, q in ((r.tl, r.tr), (r.tr, r.br), (r.br, r.bl), (r.bl, r.tl)):
                    add(p, q, pid)
            elif item[0] == 'qu':
                q = item[1]
                pts = [q.ul, q.ur, q.lr, q.ll]
                edges = [(pts[i], pts[(i + 1) % 4]) for i in range(4)]
                lengths = [abs(b - a) for a, b in edges]
                shortest = min(range(4), key=lambda i: lengths[i])
                opposite = (shortest + 2) % 4
                if lengths[shortest] > 0 and max(lengths) / lengths[shortest] > 5 and (max(lengths) > min_len):
                    add(sum(edges[shortest], fitz.Point(0, 0)) / 2, sum(edges[opposite], fitz.Point(0, 0)) / 2, pid, True)
                else:
                    for a, b in edges:
                        add(a, b, pid)
    buckets = {}
    for s in tiny:
        buckets.setdefault((s['axis'], round(s['value'] * 4)), []).append(s)
    for values in buckets.values():
        chain = []

        def emit(parts):
            if len(parts) < 4 or parts[-1]['hi'] - parts[0]['lo'] < min_len:
                return
            lo, hi = (parts[0]['lo'], parts[-1]['hi'])
            value = sum((p['value'] for p in parts)) / len(parts)
            p1, p2 = ([lo, value], [hi, value]) if parts[0]['axis'] == 'h' else ([value, lo], [value, hi])
            native.append({'axis': parts[0]['axis'], 'value': value, 'lo': lo, 'hi': hi, 'p1': p1, 'p2': p2, 'paths': sorted({i for p in parts for i in p['paths']}), 'filled_strip': False, 'dashed_assembly': True})
        for s in sorted(values, key=lambda x: x['lo']):
            if chain and s['lo'] - chain[-1]['hi'] > 0.25:
                emit(chain)
                chain = []
            chain.append(s)
        emit(chain)
    merged = []
    for s in sorted(native, key=lambda x: (x['axis'], round(x['value'], 1), x['lo'])):
        found = next((t for t in merged if t['axis'] == s['axis'] and abs(t['value'] - s['value']) <= 0.35 and (abs(t['lo'] - s['lo']) <= 0.4) and (abs(t['hi'] - s['hi']) <= 0.4)), None)
        if found:
            found['paths'] = sorted(set(found['paths'] + s['paths']))
        else:
            merged.append(s)
    for i, s in enumerate(merged):
        s.update(id=f'p{page.number + 1}:g{i + 1}', value=round(s['value'], 4), lo=round(s['lo'], 4), hi=round(s['hi'], 4))
    return merged

def grouped_tags(annotations):
    groups = []
    tags = sorted((a for a in annotations if a['kind'] == 'circuit_tag'), key=lambda a: center(a['bbox'])[0])
    for a in tags:
        x, y = center(a['bbox'])
        h = a['bbox'][3] - a['bbox'][1]
        group = next((g for g in groups if abs(g['x'] - x) <= max(h * 1.3, 5)), None)
        if group is None:
            group = {'x': x, 'tags': []}
            groups.append(group)
        group['tags'].append(a)
    result = []
    for g in groups:
        tags = sorted(g['tags'], key=lambda a: center(a['bbox'])[1])
        if len(tags) < 3:
            continue
        gaps = [center(b['bbox'])[1] - center(a['bbox'])[1] for a, b in zip(tags, tags[1:])]
        positive = [x for x in gaps if x > 1]
        spacing = median(positive) if positive else 0
        if not spacing:
            continue
        part = []
        for a in tags:
            if part and center(a['bbox'])[1] - center(part[-1]['bbox'])[1] > spacing * 3:
                if len(part) >= 3:
                    result.append({'x': g['x'], 'tags': part})
                part = []
            part.append(a)
        if len(part) >= 3:
            result.append({'x': g['x'], 'tags': part})
    return result

def associate_branches(annotations, geometry):
    """Associate only when literal circuit tags and a vector bus both exist.

    This is a horizontal single-line-diagram recognizer, not a complete netlist
    solver. Open switch contacts are not incorrectly interpreted as wire breaks.
    """
    associations = []
    groups = []
    issues = []
    hlines = [x for x in geometry if x['axis'] == 'h' and (not x.get('dashed_assembly'))]
    vlines = [x for x in geometry if x['axis'] == 'v' and (not x.get('dashed_assembly'))]
    for group in grouped_tags(annotations):
        tags = group['tags']
        heights = [a['bbox'][3] - a['bbox'][1] for a in tags]
        fh = median(heights)
        rows = []
        for tag in tags:
            tb = tag['bbox']
            candidates = [s for s in hlines if s['hi'] >= tb[0] - fh and s['lo'] < tb[0] - fh * 3 and (tb[3] - 0.2 * fh <= s['value'] <= tb[3] + fh * 1.5)]
            if not candidates:
                continue
            best = min(candidates, key=lambda s: abs(s['value'] - tb[3]))
            rows.append({'tag': tag, 'baseline': best['value'], 'tag_wire': best['id']})
        if len(rows) < 3:
            issues.append({'type': 'unresolved_circuit_geometry', 'anchor_ids': [a['id'] for a in tags], 'message': '回路编号已读取，但未找到足够的对应矢量出线；需局部图像识别'})
            continue
        spacing = median([b['baseline'] - a['baseline'] for a, b in zip(rows, rows[1:])])
        candidates = []
        for v in vlines:
            if v['value'] >= group['x'] - fh * 5:
                continue
            touched = []
            for row in rows:
                y = row['baseline']
                if not v['lo'] - 1 <= y <= v['hi'] + 1:
                    continue
                ends = [s for s in hlines if abs(s['value'] - y) <= 1 and abs(s['lo'] - v['value']) <= max(1, fh * 0.2) and (s['hi'] > v['value'] + fh * 0.6)]
                if ends:
                    touched.append(row['tag']['id'])
            if len(touched) >= max(3, math.ceil(len(rows) * 0.6)):
                candidates.append((len(touched), v, touched))
        if not candidates:
            issues.append({'type': 'unresolved_bus', 'anchor_ids': [a['id'] for a in tags], 'message': '未找到与回路出线端点一致的母线，不按文字纵坐标强行关联'})
            continue
        candidates.sort(key=lambda x: (-x[0], x[1]['value']))
        best = candidates[0]
        bus = best[1]
        if any((c[0] == best[0] and abs(c[1]['value'] - bus['value']) > fh for c in candidates[1:])):
            issues.append({'type': 'ambiguous_bus', 'message': '存在多条同等支持的母线，保留回路标注等待补核'})
            continue
        oblique = [g for g in geometry if g['axis'] == 'o']
        symbol_rows = set()
        for ri, row in enumerate(rows):
            for a in annotations:
                if a['kind'] not in ('device_model', 'unclassified_model'):
                    continue
                b = a['bbox']
                fs = a['font_size']
                if not bus['value'] <= center(b)[0] < group['x'] - fh or abs(center(b)[1] - row['baseline']) > spacing * 0.55:
                    continue
                for g in oblique:
                    x = (g['p1'][0] + g['p2'][0]) / 2
                    y = (g['p1'][1] + g['p2'][1]) / 2
                    if b[0] - fs <= x <= b[2] + fs and b[3] - fs * 0.2 <= y <= b[3] + fs * 2 and (abs(y - row['baseline']) < fs * 2):
                        symbol_rows.add(ri)
                        break
        if len(symbol_rows) < 2:
            issues.append({'type': 'circuit_symbol_support_missing', 'message': '编号和直线可能来自表格；未找到至少两行开关符号依据，不作为单线回路图处理'})
            continue
        gid = f'circuit_group_{len(groups) + 1}'
        groups.append({'id': gid, 'bus_geometry': bus['id'], 'bus_x': bus['value'], 'terminal_tag_x': group['x'], 'method': 'literal circuit tags + vector branch endpoints + common bus'})
        for i, row in enumerate(rows):
            y = row['baseline']
            top = (rows[i - 1]['baseline'] + y) / 2 if i else y - spacing / 2
            bottom = (rows[i + 1]['baseline'] + y) / 2 if i + 1 < len(rows) else y + spacing / 2
            items = []
            phases = []
            wires = []
            loads = []
            others = []
            for a in annotations:
                x, cy = center(a['bbox'])
                if not top <= cy < bottom:
                    continue
                if bus['value'] <= x < group['x'] - fh:
                    if a['kind'] in ('device_model', 'unclassified_model', 'voltage_ratio', 'device_tag'):
                        items.append(a['id'])
                    elif a['kind'] == 'phase':
                        phases.append(a['id'])
                    elif a['kind'] == 'cable':
                        wires.append(a['id'])
                    else:
                        others.append(a['id'])
                elif group['x'] + fh * 0.4 <= x < group['x'] + fh * 25 and a['kind'] in ('other', 'calculation'):
                    loads.append(a['id'])
            items.sort(key=lambda aid: center(next((a for a in annotations if a['id'] == aid))['bbox'])[0])
            associations.append({'id': f'{gid}:r{i + 1}', 'group_id': gid, 'circuit_tag': row['tag']['raw'], 'tag_anchor': row['tag']['id'], 'wire_geometry': row['tag_wire'], 'baseline': y, 'band': [top, bottom], 'bus_x': bus['value'], 'tag_x': group['x'], 'device_annotation_ids': items, 'phase_anchor_ids': phases, 'wire_annotation_ids': wires, 'load_annotation_ids': loads, 'other_annotation_ids': others, 'function_annotation_ids': [], 'status': 'native_geometry_association', 'electrical_connectivity_verified': False})
    for caption in [a for a in annotations if a['kind'] == 'function_label']:
        x, y = center(caption['bbox'])
        candidates = []
        for row in associations:
            for aid in row['device_annotation_ids']:
                tag = next((a for a in annotations if a['id'] == aid))
                if tag['kind'] != 'device_tag':
                    continue
                tx, ty = center(tag['bbox'])
                if abs(tx - x) < caption['font_size'] * 2 and 0 < ty - y < caption['font_size'] * 4:
                    candidates.append((math.hypot(tx - x, ty - y), row))
        candidates.sort(key=lambda x: x[0])
        if candidates and (len(candidates) == 1 or candidates[1][0] - candidates[0][0] > caption['font_size']):
            for row in associations:
                if caption['id'] in row['other_annotation_ids']:
                    row['other_annotation_ids'].remove(caption['id'])
            candidates[0][1]['function_annotation_ids'].append(caption['id'])
    return (groups, associations, issues)

def associate_separate_parameters(annotations, branches):
    by_id = {a['id']: a for a in annotations}
    links = []
    issues = []
    for a in annotations:
        if a['kind'] not in ('residual_current', 'apparent_power'):
            continue
        row = next((r for r in branches if a['id'] in r['other_annotation_ids'] or a['id'] in r['device_annotation_ids']), None)
        if row is None:
            continue
        target_kinds = ('device_model',) if a['kind'] == 'residual_current' else ('voltage_ratio',)
        candidates = []
        for aid in row['device_annotation_ids']:
            other = by_id[aid]
            if other['kind'] not in target_kinds:
                continue
            if a['kind'] == 'residual_current' and other['explicit_parameters'].get('drawing_abbreviation') not in ('MCB', 'RCB', 'RCBO', 'RCCB', 'RCD'):
                continue
            x, y = center(a['bbox'])
            ox, oy = center(other['bbox'])
            width = other['bbox'][2] - other['bbox'][0]
            if abs(x - ox) > max(width * 0.8, a['font_size'] * 2):
                continue
            candidates.append((math.hypot(x - ox, y - oy), other))
        candidates.sort(key=lambda c: c[0])
        if not candidates:
            continue
        if len(candidates) > 1 and candidates[1][0] - candidates[0][0] < a['font_size']:
            issues.append({'type': 'ambiguous_parameter_owner', 'anchor_ids': [a['id']], 'message': '独立参数存在多个近邻器件，未强行关联'})
            continue
        links.append({'parameter_anchor': a['id'], 'target_anchor': candidates[0][1]['id'], 'circuit_id': row['id'], 'parameters': a['explicit_parameters'], 'method': 'same vector-supported branch + exclusive spatial owner'})
    return (links, issues)
