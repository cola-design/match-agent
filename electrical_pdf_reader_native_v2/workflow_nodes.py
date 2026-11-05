import json
import math
import re


def route(files, project_id, query, base_url):
    root=str(base_url or '').strip().rstrip('/')
    if not root.startswith(('http://','https://')):
        return {'route':'HELP','base_url':root,'message':'请先部署配套图纸解析工具，并配置DRAWING_READER_BASE_URL和DRAWING_READER_API_KEY。该工具用于PDF分区、文字定位和本地OCR。'}
    if any(not str(f.get('filename') or f.get('name') or '').lower().endswith(('.pdf',)) for f in files or []):
        return {'route':'HELP','base_url':root,'message':'本识图流程仅接收PDF；其他业务文件请后续整合。'}
    if files:
        action='NEW' if not project_id or re.search('新建|重新开始',str(query)) else 'ADD'
    elif project_id:action='REPORT' if re.search('查看|下载|结果',str(query)) else 'PROCESS'
    else:action='HELP'
    return {'route':action,'base_url':root,'message':'在下方聊天框上传PDF并发送“开始识图”。多页多图会分区处理；发送“继续识图”处理下一批，发送“查看结果”下载当前结果。新项目请附文件发送“新建识图项目”。'}


def checked_body(status_code, body):
    if int(status_code)!=200:raise ValueError('图纸解析工具请求失败，HTTP '+str(status_code)+'；请查看该HTTP节点的响应，未伪造识别结果。')
    value=json.loads(body)
    if not isinstance(value,dict):raise ValueError('解析工具未返回JSON对象')
    return value


def created(status_code, body):
    value=checked_body(status_code,body);pid=value.get('project_id')
    if not isinstance(pid,str) or not re.fullmatch('[a-f0-9]{32}',pid):raise ValueError('解析工具没有返回有效项目编号')
    return {'project_id':pid}


def uploads(results):
    errors=[]
    for r in results:
        if r.get('status_code')!=200:errors.append('有图纸未完成上传或解析，HTTP '+str(r.get('status_code')))
    return {'message':'；'.join(errors),'valid':'NO' if errors else 'YES'}


def batch(status_code, body):
    value=checked_body(status_code,body)
    if not isinstance(value.get('batch'),list) or len(value['batch'])>12:raise ValueError('解析工具返回的分区批次无效')
    return {'units':[json.dumps(x,ensure_ascii=False,separators=(',',':')) for x in value['batch']]}


def unit(item):
    data=json.loads(item)
    return {'unit_id':data['unit_id'],'context_json':item}


def checked_model(raw, context_json):
    context=json.loads(context_json)
    try:
        if not isinstance(raw,str) or len(raw.encode())>120000:
            raise ValueError('模型文本缺失或超过分区预算')
        validate(raw, {'unit_id':context['unit_id'],'context':context})
        packet={'unit_id':context['unit_id'],'raw':raw,'failed':False}
        return {'valid':'YES','packet_json':json.dumps(packet,ensure_ascii=False),'message':''}
    except (ValueError,TypeError,KeyError) as exc:
        packet={'unit_id':context['unit_id'],'raw':'','failed':True}
        return {'valid':'NO','packet_json':json.dumps(packet,ensure_ascii=False),'message':str(exc)+'；该区未视为识别成功。'}


def saved(status_code, body):
    value=checked_body(status_code,body)
    if value.get('saved') is not True:raise ValueError('分区结果未保存，不能跳过该区')
    return {'result':json.dumps(value,ensure_ascii=False)}


def status(status_code, body):
    value=checked_body(status_code,body);s=value.get('summary',{})
    pending=s.get('pending_units',0);failed=s.get('failed_units',0);unbound=s.get('unassociated_native_labels',0)
    message=value.get('message','')+'\n'
    if pending:message+='发送“继续识图”处理下一批；当前下载文件是阶段性结果。\n'
    if failed:message+='有'+str(failed)+'个分区识别失败，已列入结果，不能据此认为图纸已完整识别。\n'
    if unbound:message+='有'+str(unbound)+'条原生标注尚未与视觉条目关联，已保留在原文及待核对表。\n'
    message+='本轮仅提取与解释图纸信息；AI归属和数量需对照原图核对。原文记录数不等于实物数量。'
    return {'message':message}


def _bbox(value):
    return isinstance(value, list) and len(value) == 4 and all((isinstance(x, (int, float)) and (not isinstance(x, bool)) and math.isfinite(x) and (0 <= x <= 1) for x in value)) and (value[0] < value[2]) and (value[1] < value[3])

def validate(raw, unit):
    if not isinstance(raw, str):
        raise ValueError('模型未返回文本')
    s = raw.strip()
    if s.startswith('```') and s.endswith('```'):
        lines = s.splitlines()
        s = '\n'.join(lines[1:-1])

    def bad_constant(value):
        raise ValueError('模型JSON含非有限数值')
    obj = json.loads(s, parse_constant=bad_constant)
    if not isinstance(obj, dict) or type(obj.get('readable')) is not bool:
        raise ValueError('模型JSON结构错误')
    coordinate_system = obj.get('coordinate_system')
    if coordinate_system not in ('normalized_1000', 'normalized'):
        raise ValueError('模型未明确声明受支持的坐标单位')
    divisor = 1000 if coordinate_system == 'normalized_1000' else 1
    anchors = {a['id']: a for a in unit['context']['native_or_ocr_anchors']}
    conflicts = []
    ids = set()
    for group in ('items', 'notes', 'connections', 'recognition_gaps'):
        if not isinstance(obj.get(group), list):
            raise ValueError('模型JSON缺少数组：' + group)
    if obj.get('drawing_type') not in ('single_line', 'multiline_power', 'secondary_wiring', 'assembly', 'schedule', 'mixed', 'unknown'):
        raise ValueError('模型未声明受支持的图面类型')
    for group in ('items', 'notes', 'connections'):
        for row in obj[group]:
            if not isinstance(row, dict) or not isinstance(row.get('bbox'), list):
                raise ValueError('模型位置结构错误')
            rawbox = row['bbox']
            if len(rawbox) != 4 or not all((isinstance(x, (int, float)) and (not isinstance(x, bool)) for x in rawbox)):
                raise ValueError('模型位置非数值')
            row['bbox_raw'] = list(rawbox)
            row['bbox'] = [x / divisor for x in rawbox]
            if not _bbox(row['bbox']):
                raise ValueError('模型位置超出声明单位范围')
    obj['coordinate_system'] = 'normalized'
    for item in obj['items']:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or (not item['id']) or (item['id'] in ids):
            raise ValueError('器件实例编号缺失或重复')
        ids.add(item['id'])
        if not _bbox(item.get('bbox')):
            raise ValueError('器件位置无效')
        if not isinstance(item.get('kind'), str) or not item['kind'] or (not isinstance(item.get('observed_label'), str)) or (not isinstance(item.get('native_anchor_ids'), list)):
            raise ValueError('器件原文或原文引用无效')
        for aid in item['native_anchor_ids']:
            if not isinstance(aid, str) or aid not in anchors:
                raise ValueError('引用了当前分区外的原文编号')
        params = item.get('explicit_parameters')
        if not isinstance(params, dict):
            raise ValueError('器件参数不是对象')
        if not isinstance(item.get('recognition_gaps', []), list):
            raise ValueError('器件识别缺项不是数组')
        for aid in item['native_anchor_ids']:
            a = anchors[aid]
            if a['source'] != 'native_pdf_text':
                continue
            for key, value in a['literal_parameters'].items():
                if key in params and params[key] != value:
                    conflicts.append({'type': 'model_native_parameter_conflict', 'item_id': item['id'], 'anchor_id': aid, 'field': key, 'native_value': value, 'model_value': params[key]})
    for note in obj['notes']:
        if not isinstance(note, dict) or not _bbox(note.get('bbox')) or (not isinstance(note.get('raw'), str)):
            raise ValueError('说明原文或位置无效')
        aids = note.get('native_anchor_ids', [])
        if not isinstance(aids, list) or any((not isinstance(aid, str) or aid not in anchors for aid in aids)):
            raise ValueError('说明引用了分区外的原文')
    for edge in obj['connections']:
        if not isinstance(edge, dict) or edge.get('from_item') not in ids or edge.get('to_item') not in ids or (not _bbox(edge.get('bbox'))):
            raise ValueError('连接引用或线路位置无效')
    return {'data': obj, 'validation_conflicts': conflicts, 'engineering_verified': False, 'unit_id': unit['unit_id']}
