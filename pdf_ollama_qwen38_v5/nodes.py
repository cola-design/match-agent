"""Dify stdlib nodes for page-image recognition with the user's Ollama model."""
from collections import Counter
import base64
import html
import json
import math
import re
import unicodedata
import zlib

# build.py adds the pure literal decoder here when exporting code nodes.
from literal_decode import decode, norm

MODEL_NAME = 'TenGen-ZhiHan:F16'
CATEGORIES = {'MCB', 'MCCB', 'RCBO', 'RCCB', 'residual_current_device_unspecified',
              'ATS', 'SPD', 'controller', 'contactor', 'relay', 'timer',
              'transformer', 'dc_power_supply', 'fuse', 'cable', 'unknown'}
MEANING = {'MCB': '小型断路器', 'MCCB': '塑壳断路器',
           'RCBO': '带过电流保护的剩余电流动作断路器',
           'RCCB': '不带过电流保护的剩余电流动作断路器',
           'residual_current_device_unspecified': '剩余电流保护器件，具体订货类别未明确',
           'ATS': '自动转换开关，II后缀不能作为2P依据', 'SPD': '浪涌保护器件候选',
           'controller': '控制/监测设备，具体功能待核对', 'contactor': '接触器或其图面触点标识',
           'relay': '继电器或其触点标识', 'timer': '时控器件/功能标识', 'transformer': '变压器',
           'dc_power_supply': '直流电源规格标识', 'fuse': '熔断保护器件候选',
           'cable': '电线、电缆或通信线标注', 'unknown': '类别待核对'}
ROUTES = {'CE': '沿顶板面敷设', 'WE': '沿墙面敷设', 'WC': '墙内暗敷',
          'CC': '顶板内暗敷', 'FC': '地面/地板下敷设，以项目图例为准'}


def j(v):
    return json.dumps(v, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def canonical(s):
    return re.sub(r'\s+', '', norm(s)).upper().replace('×', 'X')


def uncompress(value):
    obj = zlib.decompressobj()
    raw = obj.decompress(base64.b64decode(value, validate=True), 600001)
    if len(raw) > 600000 or not obj.eof or obj.unused_data:
        raise ValueError('整份说明超出预算或结构无效')
    return json.loads(raw)


def check_input(files):
    valid = isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict)
    name = str((files[0] if valid else {}).get('filename') or (files[0] if valid else {}).get('name') or '')
    valid = valid and name.lower().endswith('.pdf')
    return {'valid': 'YES' if valid else 'NO', 'message': '' if valid else '请在表单中上传一份完整PDF，不需要拆图或TXT。'}


def bind_pages(tool_json, images, document_text, notes):
    try:
        records = [x for x in (tool_json or []) if isinstance(x, dict) and 'images' in x]
        if len(records) != 1 or records[0].get('success') is not True:
            raise ValueError('未收到PDF-to-Images成功记录，请查看现有转图节点')
        stats = records[0]; pages = stats['images']
        if type(stats.get('total_pages')) is not int or not 1 <= stats['total_pages'] <= 12:
            raise ValueError('本次试用支持1至12页，未截取部分页冒充整份识别')
        if stats.get('total_images') != stats['total_pages'] or len(pages) != stats['total_pages']:
            raise ValueError('PDF页数与转图数量不一致，停止识别')
        names = [str(x.get('filename') or x.get('name') or '') for x in (images or [])]
        expected = [x['filename'] for x in pages]
        if len(set(names)) != len(names) or len(set(expected)) != len(expected) or set(names) != set(expected):
            raise ValueError('PDF页图文件缺失或重复，停止识别')
        numbers = [x.get('page_number') for x in pages]
        if any(type(n) is not int for n in numbers) or set(numbers) != set(range(1, len(pages) + 1)):
            raise ValueError('PDF页码缺失或重复')
        if any(x.get('file_index') != 1 for x in pages):
            raise ValueError('本次仅允许一份PDF，不混合不同文件的页图')
        if isinstance(document_text, list):
            if any(not isinstance(x, str) for x in document_text): raise ValueError('文字辅助结构无效')
            text = '\n'.join(document_text)
        elif isinstance(document_text, str): text = document_text
        else: text = ''
        if len(text) > 60000:
            raise ValueError('PDF全文超过本次上下文预算，未删去后半部分继续识别')
        if len(text) + len(str(notes or '')[:3000]) + 1000 > 60000:
            raise ValueError('全文和补充说明超过单页上下文预算，尚未调用模型')
        packet = {'schema_version': 'pdf-ollama-page/5', 'images': pages, 'uploaded_names': names,
                  'native_text': text, 'notes': str(notes or '')[:3000], 'page_count': len(pages)}
        encoded = base64.b64encode(zlib.compress(j(packet).encode('utf8'))).decode('ascii')
        if len(encoded) > 60000: raise ValueError('整份说明超过节点传输预算')
        return {'valid': 'YES', 'manifest_compact': encoded, 'message': ''}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return {'valid': 'NO', 'manifest_compact': '', 'message': str(exc)}


def page_context(index, manifest_compact):
    p = uncompress(manifest_compact)
    if isinstance(index, bool) or not isinstance(index, (int, float)) or not math.isfinite(index) or index != int(index) or not 0 <= index < len(p['uploaded_names']):
        raise ValueError('页图序号无效')
    name = p['uploaded_names'][int(index)]
    image = next(x for x in p['images'] if x['filename'] == name)
    ctx = {'page': image['page_number'], 'image_name': name, 'page_count': p['page_count'],
           'whole_document_text': p['native_text'], 'project_notes': p['notes'],
           'native_text_scope': '全文件文字，仅用于辅助转写，不证明某标注属于当前页，也不提供文字坐标'}
    encoded = j(ctx)
    if len(encoded) > 60000: raise ValueError('单页辅助上下文超过预算，未截断')
    return {'context_json': encoded}


def checked(raw, context_json):
    ctx = json.loads(context_json)
    try:
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 45000:
            raise ValueError('模型回复为空或过长，请核对Ollama模型和输出预算')
        s = raw.strip()
        if s.startswith('<think>'):
            end = s.find('</think>')
            if end < 0: raise ValueError('模型仅输出未结束的思考内容，请确认think=false')
            s = s[end + len('</think>'):].strip()
            if not s: raise ValueError('思考结束后没有JSON正文')
        if s.startswith('```') and s.endswith('```'):
            s = '\n'.join(s.splitlines()[1:-1]).strip()
        def duplicate(pairs):
            d = {}
            for k, v in pairs:
                if k in d: raise ValueError('JSON存在重复字段')
                d[k] = v
            return d
        def constant(_): raise ValueError('JSON含非有限数值')
        d = json.loads(s, object_pairs_hook=duplicate, parse_constant=constant)
        if not isinstance(d, dict) or type(d.get('readable')) is not bool or type(d.get('page_complete')) is not bool:
            raise ValueError('模型缺少readable/page_complete状态')
        items = d.get('items'); markings = d.get('markings', []); unresolved = d.get('unresolved', [])
        if not isinstance(items, list) or len(items) > 40 or not isinstance(markings, list) or len(markings) > 20:
            raise ValueError('器件/独立标注结构或数量超过协议预算')
        if not isinstance(unresolved, list) or len(unresolved) > 12 or any(not isinstance(x, str) or len(x) > 160 for x in unresolved):
            raise ValueError('待核对内容结构无效')
        output = []; annotations = []; ids = set()
        native = canonical(ctx['whole_document_text'])
        for row in items:
            if not isinstance(row, dict): raise ValueError('候选条目不是对象')
            iid = row.get('id'); label = row.get('label'); cat = row.get('category')
            if not isinstance(iid, str) or not re.fullmatch(r'I\d{1,3}', iid) or iid in ids:
                raise ValueError('本页实例ID缺失、重复或格式错误')
            ids.add(iid)
            if not isinstance(label, str) or not label.strip() or len(label) > 140 or cat not in CATEGORIES:
                raise ValueError('候选原文或类别无效')
            for key in ('circuit', 'phase', 'position'):
                value = row.get(key)
                if value is not None and (not isinstance(value, str) or len(value) > 60):
                    raise ValueError('回路/相别/位置描述结构无效')
            literal = decode(label)
            if literal['kind'] in ('phase', 'circuit_tag', 'residual_current', 'apparent_power', 'calculation'):
                annotations.append({'raw': label, 'reason': '该标注不是独立器件，未作为候选计数'})
                continue
            rawcat = literal['explicit_parameters'].get('drawing_abbreviation')
            warnings = []
            if rawcat:
                intrinsic = {'RCB': 'residual_current_device_unspecified', 'RCD': 'residual_current_device_unspecified'}.get(rawcat, rawcat)
                if cat != intrinsic: warnings.append('视觉类别与图面缩写不一致，保留原文类别')
                cat = intrinsic
            elif literal['kind'] == 'cable': cat = 'cable'
            elif literal['kind'] == 'voltage_ratio': cat = 'transformer'
            elif literal['kind'] == 'function_label': cat = 'timer'
            params = dict(literal['explicit_parameters'])
            if cat == 'cable':
                params['installation_codes'] = [{'code': x, 'common_meaning': ROUTES[x], 'project_legend_priority': True}
                    for x in ROUTES if re.search(r'(?<![A-Z])' + x + r'(?![A-Z])', label.upper())]
            if rawcat == 'ATS' and 'pole_marking' not in params: warnings.append('ATS极数未明确；II不能推定为2P')
            if cat == 'contactor': warnings.append('触点/位号与实体数量、线圈规格仍需核对')
            support = 'document_text_present' if native and canonical(label) in native else 'image_transcription_only'
            if support != 'document_text_present': warnings.append('此转写未在内置提取文字中完整匹配，需对照原图核对')
            output.append({'id': iid, 'label': label.strip(), 'category': cat, 'meaning': MEANING[cat],
                           'circuit': row.get('circuit'), 'phase': row.get('phase'), 'position': row.get('position'),
                           'literal_parameters': params, 'associated_parameters': {}, 'branch_requirements': {},
                           'text_support': support, 'warnings': warnings, 'candidate_quantity': None})
        by_id = {o['id']: o for o in output}
        for mark in markings:
            if not isinstance(mark, dict) or not isinstance(mark.get('raw'), str) or len(mark['raw']) > 80:
                raise ValueError('独立参数结构无效')
            value = decode(mark['raw']); target = by_id.get(mark.get('target_id'))
            if target is None:
                annotations.append({'raw': mark['raw'], 'reason': '独立标注没有有效的候选归属'})
                continue
            if value['kind'] == 'residual_current': target['branch_requirements'].update(value['explicit_parameters'])
            elif value['kind'] in ('apparent_power', 'voltage_ratio', 'power_marking'):
                target['associated_parameters'].update(value['explicit_parameters'])
            else:
                annotations.append({'raw': mark['raw'], 'reason': '未将无法明确解码的标注当作选型参数'})
            target.setdefault('associated_markings', []).append({'raw': mark['raw'],
                'document_text_present': bool(native and canonical(mark['raw']) in native),
                'association_source': 'visual_model_unverified'})
        record = {'page': ctx['page'], 'image_name': ctx['image_name'], 'status': 'parsed',
                  'readable': d['readable'], 'model_reported_page_complete': d['page_complete'],
                  'items': output, 'annotations': annotations, 'unresolved': unresolved}
        encoded = j(record)
        if len(encoded) > 45000: raise ValueError('本页结构化候选超过预算')
        return {'valid': 'YES', 'packet_json': encoded, 'message': ''}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        record = {'page': ctx['page'], 'image_name': ctx['image_name'], 'status': 'failed', 'error': str(exc)}
        return {'valid': 'NO', 'packet_json': j(record), 'message': str(exc)}


def collect(packets, manifest_compact):
    p = uncompress(manifest_compact); records = [json.loads(x) for x in packets]
    expected = {(x['page_number'], x['filename']) for x in p['images']}
    actual = [(x['page'], x['image_name']) for x in records]
    if len(actual) != len(expected) or len(set(actual)) != len(actual) or set(actual) != expected:
        raise ValueError('页图结果缺失或重复，停止整份汇总')
    items = []; failed = []; incomplete = []; annotations = []; unresolved = []
    for r in sorted(records, key=lambda x: x['page']):
        if r['status'] != 'parsed': failed.append(r); continue
        if not r['readable'] or not r['model_reported_page_complete']: incomplete.append(r['page'])
        for o in r['items']:
            items.append(dict(o, id='P%d-%s' % (r['page'], o['id']), page=r['page'], image_name=r['image_name']))
        annotations.extend(dict(a, page=r['page']) for a in r['annotations'])
        unresolved.extend({'page': r['page'], 'text': x} for x in r['unresolved'])
    ambiguous = Counter((o['page'], canonical(o['label']), o['circuit'], o['position']) for o in items)
    for o in items:
        if ambiguous[(o['page'], canonical(o['label']), o['circuit'], o['position'])] > 1:
            o['warnings'].append('同页存在未区分位置的相同条目；未据此相加为采购数量')
    summary = {'model': MODEL_NAME, 'provider': 'ollama', 'pages': p['page_count'],
               'candidate_objects': len(items), 'failed_pages': len(failed),
               'model_reported_incomplete_pages': incomplete, 'unresolved_notes': len(unresolved),
               'native_text_available': bool(p['native_text'].strip()),
               'recognition_accuracy_verified': False, 'native_vector_coordinates_available': False,
               'automatic_zoom_enabled': False, 'product_database_matched': False, 'selection_approved': False}
    result = {'schema_version': 'pdf-ollama-page/5', 'summary': summary, 'items': items,
              'annotations': annotations, 'unresolved': unresolved, 'failed_pages': failed,
              'source_text': p['native_text'], 'project_notes': p['notes'],
              'scope': '整页视觉识图试验，文字提取仅辅助；不构成全图精准识别、产品选型或采购数量批准'}
    def cell(v): return html.escape(str(v if v is not None else '未明确')).replace('|', '&#124;').replace('\n', '<br>')
    lines = ['模型：Ollama / ' + MODEL_NAME,
             '共%d页，得到%d项图面候选；模型回复失败%d页；模型自报未完整页：%s。' % (
                 p['page_count'], len(items), len(failed), '、'.join(map(str, incomplete)) or '无'),
             '本版为整页识图试验，未使用安装失败的自定义PDF解析插件；没有原生矢量坐标或自动放大。',
             '参数由图像转写/全文辅助解码；即使文字在全文中存在，也不能证明当前页归属或参数关联正确。',
             '', '|页/实例|原图标注|含义|回路/相别|标注参数|关联参数/支路要求|待核对|', '|---|---|---|---|---|---|---|']
    for o in items[:50]:
        lines.append('|' + '|'.join(cell(x) for x in [str(o['page']) + '/' + o['id'], o['label'], o['meaning'],
            str(o['circuit'] or '') + '/' + str(o['phase'] or ''), j(o['literal_parameters']),
            j({'关联': o['associated_parameters'], '支路要求': o['branch_requirements']}), '；'.join(o['warnings'])]) + '|')
    if failed:
        lines += ['', '模型回复失败：'] + ['- 第%d页：%s' % (r['page'], cell(r['error'])) for r in failed]
    if unresolved:
        lines += ['', '识图待核对（不是设计问题单）：'] + ['- 第%d页：%s' % (r['page'], cell(r['text'])) for r in unresolved]
    if not p['native_text'].strip(): lines.append('未获得内置提取文字，本次完全依赖视觉转写，需逐项对照原图。')
    if len(items) > 50:
        lines.append('聊天仅展示前50项；全部候选仍保存在结构化输出中，未删除其余条目。')
    lines.append('\n候选数量尚未确认，未调用产品库、价格或CAD服务。结构化数据在“统一汇总整份识图结果”的result_json；大文件详情按页保存在result_pages，全文保存在source_text。')
    encoded = j(result); report = '\n'.join(lines)
    detail_pages = [j({'page': r['page'], 'image_name': r['image_name'], 'model_record': r,
                      'normalized_items': [o for o in items if o['page'] == r['page']]}) for r in records]
    if any(len(x) > 60000 for x in detail_pages) or len(report) > 60000:
        raise ValueError('单页详情或汇总说明超过节点预算，未截断后声称完整')
    if len(encoded) > 60000:
        encoded = j({'schema_version': result['schema_version'], 'summary': summary,
                     'details_location': 'result_pages', 'source_text_location': 'source_text',
                     'message': '完整结果按页输出，不在单个字符串中截断；请同时保留result_pages和source_text'})
    return {'report': report, 'result_json': encoded, 'result_pages': detail_pages, 'source_text': p['native_text']}
