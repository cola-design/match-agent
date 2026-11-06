"""Stdlib-only Dify nodes. A single File never enters a code node."""
import base64
import json
import math
import zlib


def pack(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def unpack_manifest(value):
    if not isinstance(value, str) or len(value) > 60000:
        raise ValueError('解析说明大小无效')
    decoded = base64.b64decode(value, validate=True)
    inflater = zlib.decompressobj()
    content = inflater.decompress(decoded, 1500001)
    if len(content) > 1500000 or not inflater.eof or inflater.unused_data:
        raise ValueError('解析说明压缩结构无效')
    return json.loads(content)


def check_pdf(files):
    ok = isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict)
    name = str((files[0] if ok else {}).get('filename') or (files[0] if ok else {}).get('name') or '')
    ok = ok and name.lower().endswith('.pdf')
    return {'valid': 'YES' if ok else 'NO', 'message': '' if ok else '请在开始表单上传一份完整PDF，无需拆图或准备TXT。'}


def bind_manifest(tool_json, images, selection_context):
    try:
        if not isinstance(tool_json, list):
            raise ValueError('未收到PDF解析插件的JSON结果')
        records = [x for x in tool_json if isinstance(x, dict) and 'success' in x]
        if len(records) != 1 or records[0]['success'] is not True:
            raise ValueError(str(records[0].get('message', 'PDF解析失败')) if records else 'PDF解析结果缺失')
        manifest = records[0]['manifest']
        if manifest.get('schema_version') != 'electrical-pdf/4':
            raise ValueError('PDF插件版本与本流程不匹配')
        units = manifest['units']
        if not units or len(units) > 24 or not isinstance(images, list):
            raise ValueError('整页/放大图数量无效')
        actual = [str(x.get('filename') or x.get('name') or '') for x in images]
        expected = [x['image_name'] for x in units]
        if len(set(actual)) != len(actual) or len(set(expected)) != len(expected) or set(actual) != set(expected):
            raise ValueError('整页/放大图文件与原文说明不匹配，停止调用模型')
        if len({u['unit_id'] for u in units}) != len(units):
            raise ValueError('图面编号重复')
        manifest['uploaded_image_names'] = actual
        manifest['selection_context'] = str(selection_context or '')[:3000]
        compact = base64.b64encode(zlib.compress(pack(manifest).encode('utf8'))).decode('ascii')
        if len(compact) > 60000:
            raise ValueError('整份PDF说明超过本试用版预算，未删除页面继续')
        return {'valid': 'YES', 'manifest_compact': compact, 'message': ''}
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return {'valid': 'NO', 'manifest_compact': '', 'message': str(exc)}


def bind_image(index, manifest_compact):
    p = unpack_manifest(manifest_compact)
    names = p['uploaded_image_names']
    if isinstance(index, bool) or not isinstance(index, (int, float)) or not math.isfinite(index) or index != int(index) or not 0 <= index < len(names):
        raise ValueError('图片序号无效')
    unit = next(u for u in p['units'] if u['image_name'] == names[int(index)])
    ctx = dict(unit['context'], selection_context=p['selection_context'])
    return {'context_json': pack(ctx)}


def validate_reply(raw, context_json):
    ctx = json.loads(context_json)
    def duplicate(pairs):
        d = {}
        for k, v in pairs:
            if k in d: raise ValueError('JSON字段重复')
            d[k] = v
        return d
    try:
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 12000:
            raise ValueError('模型回复为空或超过短JSON预算')
        s = raw.strip()
        if s.startswith('```') and s.endswith('```'):
            s = '\n'.join(s.splitlines()[1:-1])
        def constant(_): raise ValueError('JSON含非有限数值')
        d = json.loads(s, object_pairs_hook=duplicate, parse_constant=constant)
        if not isinstance(d, dict) or type(d.get('readable')) is not bool or not isinstance(d.get('checks'), list):
            raise ValueError('模型回复结构无效')
        allowed = set(ctx['target_anchor_ids']); seen = set(); checks = []
        categories = {'MCB', 'MCCB', 'RCBO', 'RCCB', 'residual_current_device_unspecified', 'ATS',
                      'SPD', 'controller', 'contactor', 'relay', 'timer', 'transformer', 'dc_power_supply', 'fuse', 'unknown'}
        if len(d['checks']) > 6: raise ValueError('分类条目超过本图目标数')
        for c in d['checks']:
            aid = c['anchor_id']
            if aid not in allowed or aid in seen or c.get('category') not in categories:
                raise ValueError('原文引用、类别或重复条目无效')
            if c.get('confidence') not in ('high', 'medium', 'low', 'unknown'):
                raise ValueError('分类置信等级无效')
            if c.get('symbol_visible') is not None and type(c['symbol_visible']) is not bool:
                raise ValueError('符号可见标记无效')
            seen.add(aid)
            checks.append({'anchor_id': aid, 'category': c['category'], 'symbol_visible': c.get('symbol_visible'),
                           'confidence': c['confidence']})
        extras = d.get('unmapped_symbols', [])
        if not isinstance(extras, list) or len(extras) > 3:
            raise ValueError('未关联图形标识结构无效')
        unmapped = []
        for x in extras:
            if not isinstance(x, dict) or not isinstance(x.get('label'), str) or not isinstance(x.get('reason'), str):
                raise ValueError('未关联标识缺少原文和原因')
            if len(x['label']) > 60 or len(x['reason']) > 80:
                raise ValueError('未关联图形说明过长')
            unmapped.append({'label': x['label'], 'reason': x['reason']})
        record = {'unit_id': ctx['unit_id'], 'source_id': ctx['source_id'], 'status': 'valid',
                  'data': {'readable': d['readable'], 'checks': checks, 'unmapped_symbols': unmapped}}
        encoded = pack(record)
        if len(encoded) > 2000: raise ValueError('本区结构化回复超过合并预算')
        return {'valid': 'YES', 'packet_json': encoded, 'message': ''}
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        failure = {'unit_id': ctx['unit_id'], 'source_id': ctx['source_id'], 'status': 'invalid', 'error': str(exc)}
        return {'valid': 'NO', 'packet_json': pack(failure), 'message': str(exc)}


def collect_records(packets, manifest_compact):
    p = unpack_manifest(manifest_compact)
    records = [json.loads(x) for x in packets]
    ids = [x['unit_id'] for x in records]
    expected = {u['unit_id'] for u in p['units']}
    if len(ids) != len(expected) or len(set(ids)) != len(ids) or set(ids) != expected:
        raise ValueError('图片结果漏失或重复，不能声称整份处理完成')
    encoded = pack(records)
    if len(encoded) > 60000:
        raise ValueError('完整结果超出预算，未截断冒充成功')
    return {'vision_results': encoded}
