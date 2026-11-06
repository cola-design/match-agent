"""Only standard-library functions embedded in Dify code nodes."""
import html
import json
import math
import re

SCHEMA='pdf-recognition-test/3'


def filename(file):
    if not isinstance(file,dict):raise ValueError('未收到Dify文件对象')
    name=str(file.get('filename') or file.get('name') or '').replace('\\','/').rsplit('/',1)[-1]
    if not name:raise ValueError('上传文件缺少文件名')
    return name


def read_manifest(texts,images,notes,overload_scope):
    try:
        if not isinstance(texts,list) or len(texts)!=1:raise ValueError('请只上传本批的一个evidence.txt')
        if not isinstance(texts[0],str) or len(texts[0])>110000:raise ValueError('本批说明过长或为空，请使用本机生成的原文件')
        packet=json.loads(texts[0].lstrip('\ufeff').strip())
        if not isinstance(packet,dict) or packet.get('schema_version')!=SCHEMA:raise ValueError('说明版本不匹配，请用本测试包重新生成材料')
        units=packet.get('units')
        if not isinstance(units,list) or not 1<=len(units)<=4:raise ValueError('每批应有1至4个分区')
        for key in ('batch_index','batch_count','total_units'):
            if type(packet.get(key)) is not int or packet[key]<1:raise ValueError('批次/总分区统计无效')
        if packet['batch_index']>packet['batch_count'] or packet['total_units']<len(units):raise ValueError('批次与全文件统计不一致')
        if not isinstance(images,list) or len(images)!=len(units):raise ValueError('请上传同一batch目录内的全部PNG，数量必须与说明一致')
        actual=[filename(f).casefold() for f in images]
        expected=[u['image_name'].casefold() for u in units]
        if len(set(actual))!=len(actual) or len(set(expected))!=len(expected) or set(actual)!=set(expected):
            raise ValueError('图片文件名不匹配或重复；请不要改名、漏传或混用不同批次的材料')
        # Preserve upload order here, while ArrayFileSegment is converted by
        # Dify 1.8 CodeNode. Never pass an iteration FileSegment into code.
        packet['uploaded_image_names']=[filename(f) for f in images]
        ids=[u['unit_id'] for u in units]
        if len(set(ids))!=len(ids):raise ValueError('分区编号重复')
        for u in units:
            if not isinstance(u.get('context',{}).get('native_or_ocr_anchors'),list):raise ValueError('原文证据结构无效')
            if u['context'].get('unit_id')!=u['unit_id']:raise ValueError('分区归属不一致')
        if overload_scope not in ('未指定','指定回路（在补充说明中写明）','整柜各回路'):raise ValueError('请选择有效的过载要求适用范围')
        packet['user_requirements']={'notes':str(notes or ''),'overload_alarm_only_scope':overload_scope,
                                      'source':'Dify开始表单用户输入','engineering_verified':False}
        text=json.dumps(packet,ensure_ascii=False,separators=(',',':'))
        if len(text)>60000:raise ValueError('本批说明超过Dify安全预算，请用--batch-size 1重新生成材料')
        return {'valid':'YES','manifest_json':text,'message':''}
    except (ValueError,TypeError,KeyError) as exc:
        return {'valid':'NO','manifest_json':'','message':str(exc)+'。尚未调用模型。'}


def bind_image(index,manifest_json):
    packet=json.loads(manifest_json)
    names=packet.get('uploaded_image_names')
    if not isinstance(names,list) or isinstance(index,bool) or not isinstance(index,(int,float)) or not 0<=index<len(names) or index!=int(index):
        raise ValueError('当前图片序号与上传列表不一致，请重新上传本批材料')
    name=names[int(index)].casefold()
    unit=next(u for u in packet['units'] if u['image_name'].casefold()==name)
    context=dict(unit['context'],user_requirements=packet['user_requirements'],image_name=unit['image_name'],
                 source_id=packet['source_id'])
    return {'context_json':json.dumps(context,ensure_ascii=False,separators=(',',':'))}


def checked_model(raw,context_json):
    context=json.loads(context_json)
    try:
        if not isinstance(raw,str) or len(raw.encode())>120000:raise ValueError('模型文本为空或超出分区预算')
        checked=validate(raw,{'unit_id':context['unit_id'],'context':context})
        for item in checked['data']['items']:
            if not isinstance(item.get('meaning'),str):raise ValueError('每个对象必须返回中文含义meaning；不确定时写明不确定')
            if item.get('object_type') not in ('device','cable','interface','symbol','unknown'):raise ValueError('对象类型object_type无效')
        review=checked['data'].get('engineering_review',[])
        if not isinstance(review,list):raise ValueError('engineering_review必须是数组')
        known={a['id'] for a in context['native_or_ocr_anchors']}
        for q in review:
            if not isinstance(q,dict) or not isinstance(q.get('question'),str) or not q['question'].strip() or not isinstance(q.get('basis'),str) or not q['basis'].strip():
                raise ValueError('工程问题必须有具体问题和资料依据')
            ids=q.get('native_anchor_ids')
            if not isinstance(ids,list) or not ids or any(not isinstance(x,str) or x not in known for x in ids):
                raise ValueError('工程问题必须引用当前分区中实际存在的原文；识别缺项请写recognition_gaps')
        record={'status':'response_validated','unit_id':context['unit_id'],'image_name':context['image_name'],**checked}
        text=json.dumps(record,ensure_ascii=False,separators=(',',':'))
        if len(text)>60000:raise ValueError('单区输出过长，需要更细分区，不采用截断结果')
        return {'valid':'YES','packet_json':text,'message':''}
    except (ValueError,TypeError,KeyError) as exc:
        # The original response is retained in the Dify model node execution.
        # A truncated JSON is not repaired into a successful extraction.
        record={'status':'failed','unit_id':context['unit_id'],'image_name':context['image_name'],
                'error':str(exc),'engineering_verified':False}
        return {'valid':'NO','packet_json':json.dumps(record,ensure_ascii=False),'message':str(exc)}


def collect_results(packets,manifest_json):
    manifest=json.loads(manifest_json);units={u['unit_id']:u for u in manifest['units']}
    responses=[json.loads(x) for x in packets]
    if len(responses)!=len(units) or {r['unit_id'] for r in responses}!=set(units):raise ValueError('分区结果不完整或归属错误，未声称处理完毕')
    items=[];connections=[];notes=[];gaps=[];conflicts=[];engineering=[];failed=[];coverage=set();identity={}
    def pagebox(b,u):
        r=u['bbox_page']
        return [r[0]+b[0]*(r[2]-r[0]),r[1]+b[1]*(r[3]-r[1]),r[0]+b[2]*(r[2]-r[0]),r[1]+b[3]*(r[3]-r[1])]
    def overlap(a,b):
        area=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
        small=min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1]))
        return small>0 and area/small>.5
    for response in responses:
        uid=response['unit_id'];u=units[uid]
        if response['status']!='response_validated':failed.append(response);continue
        data=response['data'];conflicts.extend(dict(x,unit_id=uid) for x in response['validation_conflicts'])
        if not data['readable']:gaps.append({'unit_id':uid,'type':'region_not_readable'})
        gaps.extend({'unit_id':uid,'detail':x} for x in data['recognition_gaps'])
        engineering.extend({'unit_id':uid,'detail':x} for x in data.get('engineering_review',[]))
        remap={}
        for original in data['items']:
            item=dict(original,id=uid+':'+original['id'],unit_id=uid,image_name=u['image_name'],page=u['page'],
                      bbox_page=pagebox(original['bbox'],u),physical_quantity=None,engineering_verified=False)
            aids=tuple(sorted(item['native_anchor_ids']));coverage.update(aids)
            key=(item['page'],aids) if aids else None
            old=identity.get(key) if key else None
            if old is not None:
                same=all(old.get(k)==item.get(k) for k in ('observed_label','object_type','kind','explicit_parameters','circuit_tag','phase'))
                if same and overlap(old['bbox_page'],item['bbox_page']):
                    old.setdefault('also_seen_in',[]).append(uid);remap[original['id']]=old['id'];continue
                conflicts.append({'type':'overlap_disagreement','unit_id':uid,'native_anchor_ids':list(aids),
                                  'message':'同一原文在不同局部图的归属或参数不同，保留双方等待核对'})
            items.append(item);remap[original['id']]=item['id']
            if key:identity[key]=item
            gaps.extend({'unit_id':uid,'item_id':item['id'],'detail':x} for x in item.get('recognition_gaps',[]))
        for edge in data['connections']:
            connections.append(dict(edge,unit_id=uid,page=u['page'],from_item=remap[edge['from_item']],
                to_item=remap[edge['to_item']],bbox_page=pagebox(edge['bbox'],u),engineering_verified=False))
        for note in data['notes']:
            coverage.update(note.get('native_anchor_ids',[]))
            notes.append(dict(note,unit_id=uid,page=u['page'],bbox_page=pagebox(note['bbox'],u)))
    expected={a['id']:a for u in units.values() for a in u['context']['native_or_ocr_anchors']
              if a.get('literal_kind') in ('device_model','unclassified_model','device_tag','function_label','voltage_ratio','power_marking')}
    missing=[{'id':aid,'raw':expected[aid]['raw']} for aid in sorted(set(expected)-coverage)]
    requirements=manifest['user_requirements']
    if requirements['overload_alarm_only_scope']=='整柜各回路':
        mcb={a['id']:a['raw'] for u in units.values() for a in u['context']['native_or_ocr_anchors']
             if a['literal_parameters'].get('drawing_abbreviation')=='MCB'}
        if mcb:engineering.append({'type':'overload_requirement_device_review','source':'用户表单与原生MCB标记',
            'requirement':'整柜各回路过载保护只报警、不切断','native_evidence':mcb,
            'message':'常规MCB一般具有过载脱扣功能，需设计人员核对具体保护方案；未改写器件或保护设置，短路与漏电动作另行核对。'})
    summary={'source_id':manifest['source_id'],'source_filename':manifest['source_filename'],
             'batch_index':manifest['batch_index'],'batch_count':manifest['batch_count'],
             'batch_units':len(units),'total_source_units':manifest['total_units'],'failed_units':len(failed),
             'candidate_objects':len(items),'candidate_connections':len(connections),'unassociated_native_markings':len(missing),
             'recognition_gap_count':len(gaps),'conflict_count':len(conflicts),'engineering_review_count':len(engineering),
             'all_batch_responses_validated':not failed,'recognition_accuracy_verified':False,
             'terminal_connectivity_verified':False,'engineering_verified':False}
    result={'schema_version':SCHEMA,'summary':summary,'project_requirements':requirements,'items':items,
            'connections':connections,'notes':notes,'recognition_gaps':gaps,'conflicts':conflicts,
            'engineering_review':engineering,'unassociated_native_markings':missing,'failed_units':failed,
            'source_issues':manifest.get('native_extraction_issues',[]),
            'scope':'局部视觉识图待核对结果；不连接产品库、价格库或绘图服务；未生成批准采购数量'}
    def cell(value):return html.escape(str(value if value is not None else '未明确')).replace('|','&#124;').replace('\n','<br>')
    md=[f"本批 {manifest['batch_index']}/{manifest['batch_count']}：处理 {len(units)} 个局部图。",
        '这些是待核对候选；格式通过不等于图纸已完整看懂。','',
        '|页/分区|原图标注|中文含义|回路/相别|明确参数|', '|---|---|---|---|---|']
    labels={'rated_current_a':'额定电流(A)','residual_current_ma':'剩余动作电流(mA)','leakage_current_ma':'剩余动作电流(mA)',
            'pole_marking':'极数标记','pole_count':'极数','trip_curve':'脱扣特性','voltage_v':'电压(V)',
            'voltage_pair_v':'电压标记(V)','apparent_power_va':'视在功率(VA)','breaking_capacity_ka':'分断能力(kA)',
            'drawing_abbreviation':'图面缩写','trip_marking_raw':'脱扣器原标记'}
    for item in items:
        params='；'.join(labels.get(k,k)+'：'+(json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else str(v)) for k,v in item['explicit_parameters'].items()) or '未明确'
        md.append('|'+ '|'.join(cell(x) for x in [str(item['page'])+'/'+item['unit_id'],item['observed_label'],item['meaning'],str(item.get('circuit_tag') or '')+'/'+str(item.get('phase') or ''),params])+'|')
    md+=['','识别缺项：'+cell(len(gaps))+'；原文未关联：'+cell(len(missing))+'；模型分歧：'+cell(len(conflicts))+'；失败分区：'+cell(len(failed))+'。']
    for label,values in [('识别缺项',gaps),('原文尚未关联',missing),('模型与原文/跨区分歧',conflicts),('工程待核对',engineering),('失败分区',failed)]:
        if values:md+=['',label+'：']+['- '+cell(json.dumps(v,ensure_ascii=False)) for v in values]
    if manifest['batch_count']>1:md+=['','本文件还有其他批次。请新开对话上传下一批材料；本轮未声称整份图纸识别完成。']
    report='\n'.join(md);result_json=json.dumps(result,ensure_ascii=False,separators=(',',':'))
    if len(report)>60000 or len(result_json)>60000:raise ValueError('本批完整结果超过预算，请用--batch-size 1生成更小批次；未截断冒充成功')
    return {'report':report,'result_json':result_json}
