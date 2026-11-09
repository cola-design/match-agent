"""Pure standard-library engineering/state functions embedded in Dify code nodes."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import gzip
import hashlib
import hmac
import io
import json
import re
import uuid

SCOPES={'OUR_PURCHASE','CUSTOMER_SUPPLIED','UTILITY_SUPPLIED','RESERVED'}
YES={'是','确认','已确认','同意','采用','YES','yes'}
SCOPE_NAMES={'OUR_PURCHASE':'我方采购','CUSTOMER_SUPPLIED':'甲供','UTILITY_SUPPLIED':'供电局安装','RESERVED':'仅预留'}
MAX_ITEMS=100
MAX_PAGES=20


def j(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def obj(value):
    if isinstance(value,(dict,list)):return deepcopy(value)
    text=re.sub(r'^```(?:json)?\s*|\s*```$','',str(value or '').strip())
    return json.loads(text)


def number(value,positive=False):
    try:value=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):raise ValueError('请填写有效数字') from None
    if not value.is_finite() or (positive and value<=0):raise ValueError('数字超出允许范围')
    return value


def pack(state):
    raw=j(state).encode()
    if len(raw)>700000:raise ValueError('项目数据过大，请按柜号拆分；未保留半份数据')
    result='N20:'+base64.b64encode(gzip.compress(raw,mtime=0)).decode()
    if len(result)>55000:raise ValueError('项目上下文超过Dify单变量预算，请按柜号拆分')
    return result


def unpack(value):
    if not value:return {}
    if not str(value).startswith('N20:'):raise ValueError('旧版本项目不能混用，请新建项目')
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(value[4:],validate=True))) as stream:raw=stream.read(700001)
    if len(raw)>700000:raise ValueError('上下文超出处理上限')
    state=json.loads(raw)
    if not isinstance(state,dict):raise ValueError('项目上下文无效')
    return state


def stamp(state,action,approver=''):
    state.setdefault('history',[]).append({'action':action,'approver':approver,'revision':state['revision'],
        'at':datetime.now(timezone.utc).isoformat(timespec='seconds')})
    state['history']=state['history'][-25:]


def new_state(name,files,technical_files):
    def refs(values,role):
        return [{'id':str(f.get('upload_file_id') or f.get('id') or ''),'filename':str(f.get('filename') or f.get('name') or ''),
            'role':role} for f in values if isinstance(f,dict)]
    return {'schema':'dify-native-v20','project_id':uuid.uuid4().hex,'name':str(name or '电气成套项目')[:100],
        'revision':1,'stage':'READING','sign_key':uuid.uuid4().hex+uuid.uuid4().hex,
        'documents':refs(files,'drawing')+refs(technical_files,'technical_agreement'),
        'pages':[],'facts':{'cabinets':[],'items':[],'notes':[],'rules':[],'connections':[],'recognition_issues':[]},
        'issues':[],'bom':[],'source_reviewed':False,'history':[]}


def issue(message,row_id='',field='',kind='engineering_note',options=None):
    return {'id':'Q'+hashlib.sha256(j([row_id,field,message]).encode()).hexdigest()[:12],
        'row_id':row_id,'field':field,'kind':kind,'message':message,'options':options or [],'resolved':False}


def conversion_manifest(stats,images,input_files,role):
    values=obj(stats)
    if isinstance(values,dict):values=[values]
    candidates=[x for x in values if isinstance(x,dict) and x.get('success') is True and isinstance(x.get('images'),list)]
    if len(candidates)!=1:raise ValueError('PDF转图片未返回完整结果，请核对插件是否安装和文件是否可读')
    result=candidates[0];meta=result['images']
    if len(images)!=len(meta) or result.get('total_images')!=len(meta) or result.get('total_pages')!=len(meta):
        raise ValueError('PDF转换页数不完整，本次不使用缺页图纸')
    if not 1<=len(meta)<=MAX_PAGES:raise ValueError('每批图片页数应为1至20页，请拆分项目')
    groups={}
    for row in meta:
        f=row.get('file_index');p=row.get('page_number')
        if not isinstance(f,int) or not isinstance(p,int):raise ValueError('转换页码格式无效')
        groups.setdefault(f,[]).append(p)
    if set(groups)!=set(range(1,len(input_files)+1)):
        raise ValueError('有PDF未完成转换，请重新导出或分开上传')
    for pages in groups.values():
        if sorted(pages)!=list(range(1,len(pages)+1)):raise ValueError('PDF转换存在漏页或重复页')
    return [{'document_id':('T' if role=='technical_agreement' else 'D')+str(x['file_index']),
        'page':x['page_number'],'image_index':i,'filename':x['filename'],'role':role} for i,x in enumerate(meta)]


def bind_page(raw,origin):
    data=obj(raw)
    if not isinstance(data,dict):raise ValueError('视觉模型输出不是JSON对象')
    data['_origin']=origin
    if data.get('page_understood') is not True:
        data.setdefault('issues',[]).append({'message':'该页未完成可靠识别，需重新导出清晰图纸','reupload':True})
    for key in ('cabinets','items','connections','critical_notes','rules','issues'):
        if not isinstance(data.get(key,[]),list):raise ValueError('视觉结果字段不是数组：'+key)
    return data


def valid_evidence(evidence,pages):
    allowed={(p['document_id'],p['page']) for p in pages}
    if not isinstance(evidence,list) or not evidence:return False
    for entry in evidence:
        if not isinstance(entry,dict) or (entry.get('document_id'),entry.get('page')) not in allowed:return False
        box=entry.get('bbox')
        if not str(entry.get('quote') or '').strip() or not isinstance(box,list) or len(box)!=4:return False
        if not all(isinstance(x,(int,float)) and not isinstance(x,bool) and 0<=x<=1 for x in box):return False
        if box[0]>=box[2] or box[1]>=box[3]:return False
    return True


def merge_pages(state,pages):
    facts=state['facts'];cabs={c['cabinet_id']:c for c in facts['cabinets']}
    items={(x.get('cabinet_id'),x.get('device_tag')):x for x in facts['items'] if x.get('device_tag')}
    mappings={};known={(x['document_id'],x['page']) for x in state['pages']}
    for raw in pages:
        data=obj(raw);origin=data['_origin'];key=(origin['document_id'],origin['page'])
        if key in known:raise ValueError('同一来源页被重复提交')
        state['pages'].append(origin);known.add(key)
        if len(state['pages'])>MAX_PAGES:raise ValueError('项目总页数超过20页，请按柜号拆分')
        for cab in data.get('cabinets',[]):
            cid=str(cab.get('cabinet_id') or '').strip()
            if not cid:
                facts['recognition_issues'].append(issue('柜体缺少明确编号，需补充编号后重新识别',kind='reupload'));continue
            if cid not in cabs:
                cab=deepcopy(cab);cabs[cid]=cab;facts['cabinets'].append(cab)
            else:
                old=cabs[cid]
                for field,value in cab.items():
                    if field in ('evidence','notes'):old.setdefault(field,[]).extend(value or []);continue
                    if value in (None,'',[],{}):continue
                    if old.get(field) not in (None,'',[],{}) and old[field]!=value:
                        facts['recognition_issues'].append(issue('柜体资料的字段存在冲突，请确认实际值：'+field,cid,'cabinet.'+field,'text'))
                    else:old[field]=value
        for source in data.get('items',[]):
            source=deepcopy(source);old_id=str(source.get('row_id') or '')
            source.setdefault('requirements',{});source.setdefault('unit','');source.setdefault('supply_scope','UNKNOWN')
            source.setdefault('procurement_mode','CATALOG');source.setdefault('evidence',[])
            tag=str(source.get('device_tag') or '').strip();identity=(source.get('cabinet_id'),tag)
            if tag and identity in items:
                target=items[identity]
                for field in ('shown_model','model_family','requirements','quantity_per_cabinet','unit','supply_scope'):
                    value=source.get(field)
                    if value in (None,'',[],{}):continue
                    if target.get(field) not in (None,'',[],{}) and target[field]!=value:
                        facts['recognition_issues'].append(issue('同一位号的来源字段冲突，请填写解决后的实际值：'+field,target['row_id'],field,'text'))
                    else:target[field]=value
                target['evidence'].extend(source.get('evidence') or [])
            else:
                source['row_id']='I'+str(len(facts['items'])+1).zfill(4);target=source;facts['items'].append(target)
                if tag:items[identity]=target
            mappings[(key,old_id)]=target['row_id']
        for item in data.get('issues',[]):
            if isinstance(item,dict) and item.get('message'):
                facts['recognition_issues'].append(issue(str(item['message']),kind='reupload' if item.get('reupload') else 'engineering_note'))
        facts['notes'].extend(data.get('critical_notes') or [])
        facts['rules'].extend(data.get('rules') or [])
    for raw in pages:
        data=obj(raw);origin=data['_origin'];key=(origin['document_id'],origin['page'])
        for edge in data.get('connections',[]):
            a=mappings.get((key,str(edge.get('from'))));b=mappings.get((key,str(edge.get('to'))))
            if a and b and a!=b:
                link={'from':a,'to':b,'circuit':str(edge.get('circuit') or ''),'evidence':edge.get('evidence') or []}
                if not any(x['from']==a and x['to']==b for x in facts['connections']):facts['connections'].append(link)
    if len(facts['items'])>MAX_ITEMS:raise ValueError('物料超过100项，请按柜号拆分；未输出截断BOM')
    state['stage']='REVIEWING'
    return state


def apply_rules(state):
    facts=state['facts']
    for rule in facts['rules']:
        if rule.get('_applied'):continue
        rule['_applied']=True
        scoped=any(rule.get(k) for k in ('cabinet_ids','device_tags','applicable_roles'))
        targets=[x for x in facts['items'] if (not rule.get('cabinet_ids') or x.get('cabinet_id') in rule['cabinet_ids'])
            and (not rule.get('device_tags') or x.get('device_tag') in rule['device_tags'])
            and (not rule.get('applicable_roles') or x.get('role') in rule['applicable_roles'])]
        if not scoped or not targets or not valid_evidence(rule.get('evidence'),state['pages']):
            facts['recognition_issues'].append(issue('技术协议约束的作用范围/来源不明确，请修订资料后重传：'+str(rule.get('text','')),kind='reupload'));continue
        for item in targets:
            for key,value in (rule.get('requirements') or {}).items():
                old=item['requirements'].get(key)
                if old not in (None,'',[],{}) and old!=value:
                    facts['recognition_issues'].append(issue('协议与图纸参数冲突，请填写解决后的完整requirements JSON：'+str(rule.get('text','')),item['row_id'],'requirements','json'))
                else:item['requirements'][key]=value
        facts['notes'].append({'text':rule.get('text',''),'requires_confirmation':True,'evidence':rule.get('evidence',[])})


def review_projection(state):
    value={k:state['facts'][k] for k in ('cabinets','items','notes','rules','connections')}
    text=j(value)
    if len(text)>48000:raise ValueError('工程复核输入超过48KB，请按柜号分批')
    return text


def apply_text_review(state,raw):
    result=obj(raw);expected={x['row_id'] for x in state['facts']['items']}
    if result.get('review_complete') is not True or set(result.get('checked_row_ids',[]))!=expected:
        state['facts']['recognition_issues'].append(issue('文本模型未完成全部物料的工程复核，请工程师逐项核对或重新识别',kind='engineering_note'))
    for row in result.get('issues',[]):
        if isinstance(row,dict) and row.get('message'):
            rid=row.get('row_id','') if row.get('row_id') in expected else ''
            state['facts']['recognition_issues'].append(issue(str(row['message']),rid,'','engineering_note'))
    state['stage']='ASSESS'
    return state


def product_hash(product):
    data={k:product.get(k) for k in ('material_code','description','category','manufacturer','unit','standard_specs','source_status','data_status','quality_flags')}
    data['source_attributes']=sorted([{'name':x.get('name'),'value':x.get('value'),'source':x.get('source')}
        for x in product.get('source_attributes',[])],key=j)
    return hashlib.sha256(j(data).encode()).hexdigest()


def required_parameters(item):
    role=str(item.get('role') or '');keys=[]
    if any(x in role for x in ('断路器','隔离开关')):keys=['rated_current_a','pole_count']
    if '小型断路器' in role:keys.append('trip_curve')
    if '漏电' in role:keys.append('leakage_current_ma')
    if '电涌' in role or '浪涌' in role:keys=['continuous_operating_voltage_v','pole_count']
    return [k for k in keys if (item.get('requirements') or {}).get(k) in (None,'',[],{})]


def query_inputs(state):
    output=[]
    for item in state['facts']['items']:
        if item.get('supply_scope')!='OUR_PURCHASE' or item.get('procurement_mode')=='NON_CATALOG':continue
        model=str(item.get('model_family') or item.get('shown_model') or item.get('role') or '').strip()
        if not model:continue
        specs=item.get('requirements') or {};amps=specs.get('rated_current_a')
        terms=[model,str(item.get('shown_model') or ''),str(specs.get('pole_count') or '')]
        if amps is not None:terms.extend([str(amps)+'A',str(specs.get('trip_curve') or '')+str(amps)])
        q=' '.join(' '.join(dict.fromkeys(x for x in terms if x)).split())[:240]
        code=str(item.get('material_code') or '')
        if code and not re.fullmatch(r'\d{8,16}',code):
            continue
        output.append({'item':item,'path':'/v1/products/'+code if code else '/v1/products/search',
            'q':q,'model':' '.join(str(item.get('model_family') or '').split())[:180]})
    return output


def assess(state,selection_results):
    apply_rules(state);facts=state['facts'];issues=[deepcopy(x) for x in facts['recognition_issues'] if not x.get('resolved')]
    results={x['row_id']:x for x in map(obj,selection_results)}
    cabinets={x['cabinet_id']:x for x in facts['cabinets']}
    if not cabinets or not facts['items']:issues.append(issue('没有完整的柜体和元件清单，请提供清晰完整图纸',kind='reupload'))
    for cid,cab in cabinets.items():
        try:
            value=number(cab.get('quantity'),True)
            if value!=value.to_integral_value():raise ValueError()
        except ValueError:issues.append(issue('请确认项目中该配置的实际柜体数量，填写正整数',cid,'cabinet.quantity','integer'))
        if not valid_evidence(cab.get('evidence'),state['pages']) and not cab.get('evidence_confirmed'):
            issues.append(issue('柜体缺少有效页码/图像位置，请补充核对依据',cid,'cabinet.evidence','engineering_note'))
    for item in facts['items']:
        rid=item['row_id']
        if item.get('cabinet_id') not in cabinets:issues.append(issue('请填写元件所属的明确柜号',rid,'cabinet_id','text'))
        try:number(item.get('quantity_per_cabinet'),True)
        except ValueError:issues.append(issue('请确认该元件单柜实际用量，填写数字',rid,'quantity_per_cabinet','number'))
        if not item.get('unit'):issues.append(issue('请填写计量单位',rid,'unit','text'))
        if item.get('supply_scope') not in SCOPES:issues.append(issue('请确认供货范围，使用指定英文代码',rid,'supply_scope','scope',sorted(SCOPES)))
        if not valid_evidence(item.get('evidence'),state['pages']) and not item.get('evidence_confirmed'):
            issues.append(issue('该元件缺少有效的图像来源定位，请逐项核对后补充依据',rid,'evidence','engineering_note'))
        brand=str(item.get('manufacturer_requested') or '')
        if brand and '天正' not in brand and 'TENGEN' not in brand.upper() and not item.get('substitution_approved'):
            issues.append(issue('客户指定其他品牌，是否批准用符合参数的天正产品替代？',rid,'substitution_approved','yes'))
        if item.get('supply_scope')!='OUR_PURCHASE':item['selected']=None;continue
        if item.get('procurement_mode')=='NON_CATALOG':
            item['selected']=None
            if not item.get('non_catalog_approved') or not item.get('non_catalog_reason') or not item.get('shown_model'):
                issues.append(issue('非标项需确认完整规格，并在修订JSON填写non_catalog_reason',rid,'non_catalog_approved','yes'))
            continue
        missing=required_parameters(item)
        if missing:issues.append(issue('关键参数缺失，请填写完整requirements JSON：'+','.join(missing),rid,'requirements','json'))
        result=results.get(rid,{})
        item['selected']=result.get('selected')
        item['price_references']=result.get('price_references',[])
        if not item['selected']:
            issues.append(issue('未找到唯一且规格相符的正式产品，填写物料编码；确属非标可填NON_CATALOG并在修订JSON说明依据',rid,'material_code','code',result.get('alternatives',[])))
    for idx,note in enumerate(facts['notes']):
        if note.get('requires_confirmation') and not note.get('confirmed'):
            issues.append(issue('请确认关键技术要求：'+str(note.get('text','')),'NOTE'+str(idx),'note','engineering_note'))
    if not state.get('source_reviewed'):
        issues.append(issue('请对照原PDF核对本表“识别清单”中的柜号、物料、单柜数量、柜体数量、供货范围及漏项，填写具体核对结论',field='source_review',kind='engineering_note'))
    state['issues']=list({x['id']:x for x in issues}.values())
    state['revision']+=1
    if state['issues']:state['stage']='NEEDS_CLARIFICATION';state['bom']=[]
    else:
        state['bom']=make_bom(state);state['stage']='BOM_REVIEW'
    stamp(state,'assess');return state


def make_bom(state):
    cabinets={x['cabinet_id']:x for x in state['facts']['cabinets']};rows=[]
    for item in state['facts']['items']:
        cab=cabinets[item['cabinet_id']];q=number(item['quantity_per_cabinet'],True);c=number(cab['quantity'],True)
        product=item.get('selected') or {}
        rows.append({'row_id':item['row_id'],'cabinet_id':item['cabinet_id'],'device_tag':item.get('device_tag',''),
            'role':item.get('role',''),'description':product.get('description') or item.get('shown_model') or item.get('role'),
            'material_code':product.get('material_code',''),'manufacturer':'天正电气' if product else item.get('manufacturer_requested',''),
            'quantity_per_cabinet':float(q),'cabinet_quantity':int(c),'quantity':float(q*c),'unit':item['unit'],
            'supply_scope':item['supply_scope'],'procurement_mode':item.get('procurement_mode','CATALOG'),
            'catalog_hash':product.get('catalog_hash',''),'requirements':item.get('requirements',{}),
            'price_references':item.get('price_references',[]),'price':{'confirmed':False,'unit_price_ex_tax':None,'tax_rate':None,'basis':''},'approval':''})
    return rows


def apply_issue_answers(state,rows,approver,interpreted=None):
    items={x['row_id']:x for x in state['facts']['items']};cabs={x['cabinet_id']:x for x in state['facts']['cabinets']}
    resolved=set()
    for entry in state['issues']:
        row=rows.get(entry['id'],{});value=str(row.get('答复') or '').strip()
        if str(row.get('确认') or '').strip() not in YES or not value:continue
        if re.search(r'不确定|待确认|待补|不清楚|暂不|不能确认|[?？]',value):continue
        if re.search(r'请(?:删除|修正|补充|修改|更换)|发现(?:重复|漏项)|存在(?:重复|漏项)|需要(?:删除|修改|补充)',value) and not row.get('修订JSON'):continue
        if interpreted is not None:
            opinion=interpreted.get(entry['id'],{})
            if opinion.get('raw_answer')!=value or opinion.get('meaning_clear') is not True:continue
        field=entry['field'];rid=entry['row_id'];kind=entry['kind'];target=items.get(rid)
        if kind=='reupload':continue
        if kind in ('number','integer'):
            value=number(value,True)
            if kind=='integer' and value!=value.to_integral_value():raise ValueError('柜体数量必须为整数')
            value=float(value)
        elif kind=='scope':
            if value not in SCOPES:raise ValueError('供货范围请使用表中英文代码')
        elif kind=='yes':
            if value not in YES:continue
            value=True
        elif kind=='json':
            value=obj(value)
            if not isinstance(value,dict):raise ValueError('requirements须为JSON对象')
        elif kind=='engineering_note' and len(value)<4:continue
        if field=='source_review':state['source_reviewed']=True
        elif rid.startswith('NOTE') and field=='note':state['facts']['notes'][int(rid[4:])].update(confirmed=True,confirmation=value,approver=approver)
        elif rid in cabs and field.startswith('cabinet.'):
            key=field.split('.',1)[1]
            if key=='evidence':cabs[rid]['evidence_confirmed']=True
            elif key in ('quantity','width_mm','height_mm','depth_mm','thickness_mm'):
                v=number(value,True)
                if key=='quantity' and v!=v.to_integral_value():raise ValueError('柜体数量必须为整数')
                cabs[rid][key]=float(v)
            else:cabs[rid][key]=str(value)
        elif target is not None:
            if field=='evidence':target['evidence_confirmed']=True
            elif field=='material_code' and str(value).upper()=='NON_CATALOG':target.update(procurement_mode='NON_CATALOG',non_catalog_approved=True,material_code='')
            elif field in ('requirements','cabinet_id','unit','quantity_per_cabinet','supply_scope','material_code','shown_model','model_family','substitution_approved','non_catalog_approved'):target[field]=value
            else:target.setdefault('engineer_notes',[]).append(str(value))
            if row.get('修订JSON'):
                corrections=obj(row['修订JSON']);allowed={'role','device_tag','shown_model','model_family','requirements','unit','cabinet_id','quantity_per_cabinet','supply_scope','non_catalog_reason'}
                if not isinstance(corrections,dict) or not set(corrections)<=allowed:raise ValueError('修订JSON含不允许的字段')
                for k,v in corrections.items():
                    if k=='requirements' and not isinstance(v,dict):raise ValueError('requirements必须为JSON对象')
                    if k=='quantity_per_cabinet':number(v,True)
                    elif k!='requirements' and not isinstance(v,str):raise ValueError('型号、单位等应填写文本')
                target.update(corrections)
                target.setdefault('engineer_corrections',[]).append({'approver':approver,'changes':corrections})
        else:state['facts'].setdefault('engineer_notes',[]).append(str(value))
        resolved.add(entry['id'])
    for entry in state['facts']['recognition_issues']:
        if entry['id'] in resolved:entry['resolved']=True
    state['revision']+=1;state['stage']='ASSESS';stamp(state,'issue_answers',approver);return state


def apply_preview_changes(state,records,approver):
    changed=False;excluded=set();items={x['row_id']:x for x in state['facts']['items']};cabs={x['cabinet_id']:x for x in state['facts']['cabinets']}
    for (typ,rid),row in records.items():
        if typ not in ('PREVIEW','CAB') or not row.get('修订JSON'):continue
        value=obj(row['修订JSON'])
        if not isinstance(value,dict):raise ValueError('识别清单/柜体修订需填写JSON对象')
        if typ=='CAB':
            if not set(value)<={'quantity','width_mm','height_mm','depth_mm','thickness_mm'}:raise ValueError('柜体修订含未知字段')
            target=cabs[rid]
            for key,number_value in value.items():
                numeric=number(number_value,True)
                if key=='quantity' and numeric!=numeric.to_integral_value():raise ValueError('柜体数量必须为正整数')
                if target.get(key) is None or numeric!=number(target[key]):target[key]=float(numeric);changed=True
            continue
        allowed={'role','device_tag','shown_model','model_family','requirements','unit','cabinet_id','quantity_per_cabinet','supply_scope','material_code','exclude','reason'}
        if not set(value)<=allowed:raise ValueError('元件修订含未知字段；不能借修订JSON直接批准价格或替代品牌')
        if value.get('exclude') is True:
            if len(str(value.get('reason') or ''))<4:raise ValueError('剔除重复项须填写具体依据')
            excluded.add(rid);state.setdefault('excluded_items',[]).append({'row_id':rid,'reason':value['reason'],'approver':approver});changed=True;continue
        target=items[rid]
        for key,actual in value.items():
            if key in ('exclude','reason'):continue
            if key=='requirements':
                if not isinstance(actual,dict):raise ValueError('requirements必须为JSON对象')
            elif key=='quantity_per_cabinet':number(actual,True)
            elif not isinstance(actual,str):raise ValueError('柜号、型号、单位等字段须填文本')
            if target.get(key)!=actual:target[key]=actual;changed=True
    if excluded:
        state['facts']['items']=[x for x in state['facts']['items'] if x['row_id'] not in excluded]
        state['facts']['connections']=[x for x in state['facts']['connections'] if x['from'] not in excluded and x['to'] not in excluded]
        for x in state['facts']['recognition_issues']:
            if x['row_id'] in excluded:x['resolved']=True
    if changed:
        state['source_reviewed']=False;stamp(state,'preview_corrections',approver)
    return changed


def confirm_bom(state,records,approver):
    if state['stage']!='BOM_REVIEW':raise ValueError('当前不在BOM确认阶段，请先完成问题确认')
    facts=deepcopy(state['facts']);changes=False
    for item in facts['items']:
        row=records[('BOM',item['row_id'])]
        if row.get('确认') not in YES:raise ValueError('所有BOM行都需要工程师明确确认，包括甲供和预留项')
        raw=records[('CHANGE',item['row_id'])].get('修订JSON')
        if raw:
            correction=obj(raw);allowed={'material_code','shown_model','model_family','role','device_tag','cabinet_id','requirements','quantity_per_cabinet','unit','supply_scope'}
            if not isinstance(correction,dict) or not set(correction)<=allowed:raise ValueError('元件修订JSON包含未知字段')
            for key,value in correction.items():
                if key=='requirements' and not isinstance(value,dict):raise ValueError('requirements应为JSON对象')
                elif key=='quantity_per_cabinet':number(value,True)
                elif key!='requirements' and not isinstance(value,str):raise ValueError('编码、型号等修订字段应为文本')
                if item.get(key)!=value:item[key]=value;changes=True
    for cab in facts['cabinets']:
        raw=records[('CAB',cab['cabinet_id'])].get('修订JSON')
        if not raw:continue
        correction=obj(raw)
        if not isinstance(correction,dict) or not set(correction)<={'quantity','width_mm','height_mm','depth_mm','thickness_mm'}:raise ValueError('柜体修订字段不正确')
        for key,value in correction.items():
            value=number(value,True)
            if key=='quantity' and value!=value.to_integral_value():raise ValueError('柜体数量必须为正整数')
            if cab.get(key) is None or number(cab[key])!=value:cab[key]=float(value);changes=True
    if changes:
        state['facts']=facts;state['source_reviewed']=False;state['bom']=[];state['revision']+=1;state['stage']='ASSESS'
        stamp(state,'bom_revision',approver);return state
    for row in state['bom']:
        row['approval']=approver
        if row['supply_scope']=='OUR_PURCHASE':
            p=records[('PRICE',row['row_id'])]
            row['price']={'unit_price_ex_tax':p.get('确认未税采购单价'),'tax_rate':p.get('税率'),
                'basis':p.get('价格依据',''),'confirmed':p.get('确认') in YES,'reference_only':False}
    q=records[('SETTINGS','QUOTE')]
    state['quote_settings']={'markup':q.get('材料加成率'),'discount':q.get('折扣率'),'confirmed':q.get('确认') in YES,
        'fees':q.get('其他费用未税金额'),'fees_tax':q.get('其他费用税率'),'fees_basis':q.get('其他费用依据',''),'fees_confirmed':q.get('费用确认') in YES,'approver':approver}
    engineering=obj(records[('CAD','ENGINEERING')].get('工程JSON') or '{}')
    if not isinstance(engineering,dict):raise ValueError('CAD工程JSON必须为对象')
    state['engineering']=engineering;state['stage']='CONFIRMED';state['revision']+=1
    state['confirmed_bom_hash']=hashlib.sha256(j(state['bom']).encode()).hexdigest()
    stamp(state,'bom_confirmed',approver);return state


def verify_catalog(state,results):
    rows={x['row_id']:x for x in map(obj,results)}
    for row in state['bom']:
        if row['supply_scope']!='OUR_PURCHASE' or row['procurement_mode']=='NON_CATALOG':continue
        current=rows.get(row['row_id'],{})
        if not current.get('selected') or current['selected']['material_code']!=row['material_code'] or current['selected']['catalog_hash']!=row['catalog_hash']:
            state['stage']='ASSESS';state['bom']=[];state['source_reviewed']=False;state['revision']+=1
            state['message']='产品数据已变化或停用；请发送“继续处理”重新匹配并确认，当前未生成正式文件。'
            return False
    return True


def status_message(state):
    if not state:return '请上传图纸PDF，发送“新建项目”；技术协议可选。'
    labels={'READING':'正在读取资料','REVIEWING':'待工程复核','ASSESS':'待产品匹配，可发送“继续处理”',
        'NEEDS_CLARIFICATION':'待问题确认单回传','BOM_REVIEW':'待BOM逐项确认','CONFIRMED':'BOM已确认，可生成交付文件','DELIVERED':'交付文件已生成'}
    return '**'+state['name']+'**\n\n项目：`'+state['project_id']+'`；版本：'+str(state['revision'])+'；'+labels.get(state['stage'],state['stage'])+'。'
