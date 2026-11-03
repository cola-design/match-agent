from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import re

import httpx

from . import legacy_selection
from .core import Problem, canonical, number
from .pdf_vision import validate_evidence

SCOPES = {'OUR_PURCHASE','CUSTOMER_SUPPLIED','UTILITY_SUPPLIED','RESERVED','UNKNOWN'}


def new_issue(message, row_id='', field='', answer_type='text', evidence=None, options=None):
    identity = canonical([row_id, field, message])
    return {'id':'Q'+hashlib.sha256(identity.encode()).hexdigest()[:12], 'row_id':row_id,
            'field':field,'answer_type':answer_type,'message':message,'evidence':evidence or [],
            'options':options or [],'answer':'','resolved':False}


def catalog_hash(product):
    content = {key:product.get(key) for key in ('material_code','description','category','standard_specs','source_status','data_status','quality_flags')}
    content['source_attributes'] = sorted([{'name':x.get('name'),'value':x.get('value'),'source':x.get('source')}
        for x in product.get('source_attributes',[])], key=canonical)
    return hashlib.sha256(canonical(content).encode()).hexdigest()


class ProductClient:
    def __init__(self, settings):
        self.settings = settings

    def get(self, path, params=None):
        cfg = self.settings
        if not cfg.product_base or not cfg.product_key:
            raise Problem('PRODUCT_API_NOT_CONFIGURED', '请配置已部署的产品查询API地址和查询密钥。', 503)
        try:
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                response = client.get(cfg.product_base+path, params=params, headers={'X-API-Key':cfg.product_key})
            if response.status_code != 200:
                raise Problem('PRODUCT_API_FAILED', '产品查询API未返回成功结果，请核对连接和权限。', 502)
            rows = response.json()
            if not isinstance(rows,list):
                raise ValueError()
            return rows
        except Problem:
            raise
        except Exception:
            raise Problem('PRODUCT_API_FAILED', '产品查询API不可用，未用猜测数据替代。', 502) from None

    def exact(self, code):
        if not re.fullmatch('[A-Za-z0-9_.-]{1,80}', str(code)):
            raise Problem('MATERIAL_CODE_INVALID', '物料编码格式不正确。', 422)
        rows = self.get('/v1/products/'+str(code))
        if len(rows)!=1:
            raise Problem('MATERIAL_CODE_UNAVAILABLE', '指定物料编码没有唯一的有效产品。', 422)
        return rows[0]

    def candidates(self, item):
        if item.get('material_code'):
            return [self.exact(item['material_code'])]
        model = item.get('model_family') or item.get('shown_model') or item.get('role')
        if not model:
            return []
        specs=item.get('requirements') or {}
        terms=[model,item.get('shown_model') or '',str(specs.get('pole_count') or '')]
        if specs.get('rated_current_a') is not None:
            amps=str(specs['rated_current_a'])
            terms.extend([amps+'A',str(specs.get('trip_curve') or '')+amps])
        params = {'q':' '.join(dict.fromkeys(x for x in terms if x))[:240],'limit':50}
        if item.get('model_family'):
            params['model'] = item['model_family']
        return self.get('/v1/products/search', params)

    def prices(self, code):
        return self.get('/v1/prices/'+str(code))


def merge_pages(extracted, prepared):
    facts = {'cabinets':[], 'items':[], 'connections':[], 'notes':[], 'rules':[], 'recognition_issues':[]}
    cabinets, items, mappings = {}, {}, {}
    for page in extracted:
        origin = page.get('_page', {})
        for cab in page.get('cabinets', []):
            if not isinstance(cab,dict):
                continue
            cid = str(cab.get('cabinet_id') or '').strip()
            if not cid:
                cid = 'CAB'+str(len(cabinets)+1)
            cab['cabinet_id'] = cid
            if cid not in cabinets:
                cabinets[cid] = deepcopy(cab)
            else:
                old = cabinets[cid]
                for key,value in cab.items():
                    if key=='evidence':
                        old.setdefault('evidence',[]).extend(value or [])
                    elif value not in (None,'',[]) and old.get(key) not in (None,'',[]) and old[key]!=value:
                        facts['recognition_issues'].append(new_issue('不同资料的柜体字段冲突：'+key, cid, 'cabinet.'+key, 'text', cab.get('evidence')))
                    elif old.get(key) in (None,'',[]):
                        old[key] = value
        for item in page.get('items', []):
            if not isinstance(item,dict):
                continue
            cid = str(item.get('cabinet_id') or '').strip()
            old_id = str(item.get('row_id') or '')
            tag = str(item.get('device_tag') or '').strip()
            identity = (cid,tag) if tag else ('unassigned',origin.get('document_id'),origin.get('page'),old_id,len(items))
            if identity not in items:
                item = deepcopy(item)
                item['row_id'] = 'I'+str(len(items)+1).zfill(4)
                item.setdefault('requirements',{})
                item.setdefault('notes',[])
                item.setdefault('evidence',[])
                item.setdefault('procurement_mode','CATALOG')
                item.setdefault('supply_scope','UNKNOWN')
                items[identity] = item
            else:
                current = items[identity]
                for key in ('shown_model','model_family','quantity_per_cabinet','supply_scope','requirements'):
                    value = item.get(key)
                    if value not in (None,'',{},[]) and current.get(key) not in (None,'',{},[]) and value!=current[key]:
                        facts['recognition_issues'].append(new_issue('同位号在不同视图/资料中的字段冲突：'+key,
                            current['row_id'], key, 'text', item.get('evidence')))
                    elif current.get(key) in (None,'',{},[]):
                        current[key] = value
                current['evidence'].extend(item.get('evidence') or [])
            mappings[(origin.get('document_id'),origin.get('page'),old_id)] = items[identity]['row_id']
        for value in page.get('issues',[]):
            if isinstance(value,dict) and value.get('message'):
                facts['recognition_issues'].append(new_issue(str(value['message']), '', '', 'engineering_note', value.get('evidence')))
        for value in page.get('critical_notes',[]):
            if isinstance(value,dict) and value.get('text'):
                facts['notes'].append(value)
        for rule in page.get('rules',[]):
            if isinstance(rule,dict) and rule.get('text'):
                facts['rules'].append(deepcopy(rule))
        if page.get('unreadable_regions'):
            facts['recognition_issues'].append(new_issue('图纸存在未识别清楚的区域，请补充清晰图或逐项转写说明。',answer_type='engineering_note'))
    for page in extracted:
        origin = page.get('_page',{})
        for edge in page.get('connections',[]):
            if not isinstance(edge,dict):
                continue
            a = mappings.get((origin.get('document_id'),origin.get('page'),str(edge.get('from'))))
            b = mappings.get((origin.get('document_id'),origin.get('page'),str(edge.get('to'))))
            if a and b and a!=b:
                normalized = {'from':a,'to':b,'circuit':str(edge.get('circuit') or ''),'evidence':edge.get('evidence') or []}
                if not any(x['from']==a and x['to']==b for x in facts['connections']):
                    facts['connections'].append(normalized)
    facts['cabinets'] = list(cabinets.values())
    facts['items'] = list(items.values())
    for rule in facts['rules']:
        scope=any(rule.get(k) for k in ('cabinet_ids','device_tags','applicable_roles'))
        targets=[item for item in facts['items'] if
            (not rule.get('cabinet_ids') or item.get('cabinet_id') in rule['cabinet_ids']) and
            (not rule.get('device_tags') or item.get('device_tag') in rule['device_tags']) and
            (not rule.get('applicable_roles') or item.get('role') in rule['applicable_roles'])]
        if not scope or not targets or not validate_evidence(rule.get('evidence'),prepared):
            facts['recognition_issues'].append(new_issue('技术协议约束的适用范围或证据不明确，请补充到修订后的PDF后重新识别：'+rule['text'],answer_type='reupload'))
            continue
        for item in targets:
            for key,value in (rule.get('requirements') or {}).items():
                old=item['requirements'].get(key)
                if old not in (None,'',[],{}) and value!=old:
                    facts['recognition_issues'].append(new_issue('技术协议与图纸参数存在冲突，请填写解决后的完整requirements JSON：'+rule['text'],item['row_id'],'requirements','text',rule.get('evidence')))
                else:item['requirements'][key]=value
        facts['notes'].append({'text':rule['text'],'requires_confirmation':True,'evidence':rule.get('evidence')})
    facts['documents'] = [{'document_id':doc['document_id'],'sha256':doc['sha256'],'page_count':doc['page_count']} for doc in prepared]
    return facts


def requirement(item):
    return {'request_id':item['row_id'],'scheme_group_id':item.get('cabinet_id',''),
            'component_role':item.get('role',''),'symbol':item.get('device_tag',''),
            'circuit_id':item.get('device_tag',''),'shown_model':item.get('shown_model',''),
            'requirements':item.get('requirements') or {},'mapping_status':'USER_CONFIRMED' if item.get('engineer_confirmed') else 'MATCHED',
            'missing_parameters':[], 'project_quantity':item.get('quantity_per_cabinet'),
            'unit':item.get('unit') or '台'}


def choose_product(item, products):
    output = legacy_selection.main('', canonical(requirement(item)), products)
    selection = json.loads(output['item_json'])
    code = selection.get('recommended',{}).get('material_code')
    if selection.get('match_status') != 'MATCHED' or not code:
        return None, selection
    matched = [row for row in products if str(row.get('material_code'))==str(code)]
    if len(matched)!=1:
        return None, selection
    return matched[0], selection


def assess(facts, prepared, product_client):
    issues = [deepcopy(x) for x in facts.get('recognition_issues',[]) if not x.get('resolved')]
    if not facts.get('items') or not facts.get('cabinets'):
        issues.append(new_issue('没有形成完整的柜体与元件清单，需补充清晰图纸并重新识别。', answer_type='reupload'))
    cabinets = {c['cabinet_id']:c for c in facts.get('cabinets',[])}
    for cid,cab in cabinets.items():
        try:
            count=number(cab.get('quantity'),positive=True)
            if count!=count.to_integral_value():raise Problem('INVALID_COUNT','柜体数量必须为整数。')
        except Problem:
            issues.append(new_issue('请确认柜体数量。',cid,'cabinet.quantity','positive_integer',cab.get('evidence')))
        if not cab.get('engineer_confirmed') and not validate_evidence(cab.get('evidence'),prepared):
            issues.append(new_issue('柜体信息缺少有效来源证据，请核对。',cid,'cabinet.evidence','engineering_note'))
    for item in facts.get('items',[]):
        rid = item['row_id']
        if item.get('cabinet_id') not in cabinets:
            issues.append(new_issue('请填写该元件所属柜体编号。',rid,'cabinet_id','text',item.get('evidence')))
        try:
            number(item.get('quantity_per_cabinet'),positive=True)
        except Problem:
            issues.append(new_issue('请确认该元件单柜用量。',rid,'quantity_per_cabinet','positive_number',item.get('evidence')))
        if not item.get('unit'):
            issues.append(new_issue('请确认数量单位。',rid,'unit','text'))
        if item.get('supply_scope') not in SCOPES or item.get('supply_scope')=='UNKNOWN':
            issues.append(new_issue('请确认供货范围。',rid,'supply_scope','scope',item.get('evidence'),sorted(SCOPES-{'UNKNOWN'})))
        if not item.get('engineer_confirmed') and not validate_evidence(item.get('evidence'),prepared):
            issues.append(new_issue('元件事实缺少有效页码/文字锚点或图像定位，请核对。',rid,'evidence','engineering_note'))
        brand = str(item.get('manufacturer_requested') or '').strip()
        if brand and not any(x in brand.upper() for x in ('天正','TENGEN')) and not item.get('tengen_substitution_approved'):
            issues.append(new_issue('资料指定其他品牌，是否批准改用天正产品？',rid,'tengen_substitution_approved','yes',item.get('evidence')))
        if item.get('supply_scope') != 'OUR_PURCHASE':
            item['selected_product'] = None
            continue
        if item.get('procurement_mode')=='NON_CATALOG':
            item['selected_product']=None;item['catalog_hash']=''
            if not item.get('non_catalog_approved') or not item.get('shown_model') or len(str(item.get('non_catalog_reason') or ''))<4:
                issues.append(new_issue('非标采购项需要确认完整规格与采购方式；J列修订JSON请填写non_catalog_reason说明具体依据。',rid,'non_catalog_approved','yes'))
            continue
        role=legacy_selection.canonical_category_name(item.get('role',''))
        required=[]
        if role in ('小型断路器','塑壳断路器','断路器','漏电断路器','隔离开关'):
            required=['rated_current_a','pole_count']
        if role=='小型断路器':required.append('trip_curve')
        if role=='漏电断路器':required.append('leakage_current_ma')
        if role=='电涌保护器':required=['continuous_operating_voltage_v','pole_count']
        missing=[k for k in required if (item.get('requirements') or {}).get(k) in (None,'',[],{})]
        if missing:
            issues.append(new_issue('关键参数不全，请填写完整requirements JSON对象，缺少：'+','.join(missing),rid,'requirements','text',item.get('evidence')))
        try:
            rows = product_client.candidates(item)
            product,selection = choose_product(item,rows)
        except Problem as exc:
            if exc.code in ('PRODUCT_API_NOT_CONFIGURED','PRODUCT_API_FAILED'):
                raise
            product,selection = None, {'warnings':[exc.message]}
        item['selection_review'] = selection
        if not product:
            options = selection.get('alternatives',[])[:6]
            issues.append(new_issue('没有唯一、符合要求的正式产品。请填写正式物料编码；确属非标制作可填写NON_CATALOG并说明依据。',
                rid,'material_code','material_code',item.get('evidence'),options))
            item['selected_product'] = None
        else:
            item['material_code'] = str(product['material_code'])
            item['selected_product'] = product
            item['catalog_hash'] = catalog_hash(product)
    for index,note in enumerate(facts.get('notes',[])):
        if note.get('requires_confirmation') and not note.get('confirmed'):
            issues.append(new_issue('请确认关键工程要求：'+note['text'],'NOTE'+str(index),'critical_note','engineering_note',note.get('evidence')))
    unique = {issue['id']:issue for issue in issues}
    return list(unique.values())


def apply_answers(facts, issues, answers, approver):
    items = {x['row_id']:x for x in facts.get('items',[])}
    cabinets = {x['cabinet_id']:x for x in facts.get('cabinets',[])}
    resolved = set()
    for issue in issues:
        reply = answers.get(issue['id'],{})
        value = str(reply.get('answer') or '').strip()
        if not value or str(reply.get('confirmed') or '').strip() not in ('是','YES','yes','确认','已确认'):
            continue
        kind, field, rid = issue['answer_type'], issue['field'], issue['row_id']
        if kind=='reupload':
            continue
        target = items.get(rid)
        if kind in ('positive_number','positive_integer'):
            numeric = number(value,positive=True)
            if kind=='positive_integer' and numeric != numeric.to_integral_value():
                raise Problem('ANSWER_INVALID','柜体数量必须为正整数。',422)
            value = int(numeric) if numeric==numeric.to_integral_value() else float(numeric)
        elif kind=='scope':
            if value not in SCOPES-{'UNKNOWN'}:
                raise Problem('ANSWER_INVALID','供货范围必须使用表中指定的英文代码。',422)
        elif kind=='yes':
            if value not in ('是','YES','yes','批准','同意'):
                continue
            value = True
        elif kind=='engineering_note' and len(value)<4:
            continue
        if rid.startswith('NOTE') and field=='critical_note':
            index=int(rid[4:]);facts['notes'][index].update(confirmed=True,confirmation=value,approver=approver)
        elif field.startswith('cabinet.') and rid in cabinets:
            actual=field.split('.',1)[1]
            if actual=='evidence':
                cabinets[rid]['engineer_confirmed']=True
            elif actual=='quantity':
                cabinets[rid][actual]=value
            else:
                # Conflict answers are explicit engineer-authored replacement values.
                cabinets[rid][actual]=value
                cabinets[rid]['engineer_confirmed']=True
        elif target is not None:
            if field=='evidence':
                target['engineer_confirmed']=True
            elif field=='material_code' and str(value).upper()=='NON_CATALOG':
                target.update(procurement_mode='NON_CATALOG',non_catalog_approved=True,engineer_confirmed=True,material_code='')
            elif field=='requirements':
                try:
                    replacement=json.loads(str(value))
                except ValueError:
                    raise Problem('ANSWER_INVALID','参数冲突请按表中说明填写JSON字段值，或重新提供已修订图纸。',422) from None
                if not isinstance(replacement,dict):
                    raise Problem('ANSWER_INVALID','参数必须为JSON对象。',422)
                target['requirements']=replacement
            elif field in ('material_code','cabinet_id','quantity_per_cabinet','unit','supply_scope','tengen_substitution_approved','non_catalog_approved','shown_model','model_family'):
                target[field]=value
            else:
                target.setdefault('engineer_notes',[]).append(str(value))
            if reply.get('corrections'):
                try:corrections=json.loads(str(reply['corrections']))
                except ValueError:raise Problem('ANSWER_CORRECTIONS_INVALID','关联元件修订必须是JSON对象。',422) from None
                allowed={'role','shown_model','model_family','requirements','unit','supply_scope','quantity_per_cabinet','cabinet_id','device_tag','non_catalog_reason'}
                if not isinstance(corrections,dict) or not set(corrections)<=allowed:
                    raise Problem('ANSWER_CORRECTIONS_INVALID','关联修订只允许元件型号、参数、数量、柜号、单位、供货范围与非标依据。',422)
                if 'requirements' in corrections and not isinstance(corrections['requirements'],dict):
                    raise Problem('ANSWER_CORRECTIONS_INVALID','requirements必须为JSON对象。',422)
                for key,corrected in corrections.items():
                    if key=='requirements':continue
                    if key=='quantity_per_cabinet':number(corrected,positive=True)
                    elif not isinstance(corrected,str):raise Problem('ANSWER_CORRECTIONS_INVALID','型号、柜号、单位等字段需要文本值。',422)
                target.update(corrections)
                target.setdefault('engineer_corrections',[]).append({'changes':corrections,'approver':approver})
        else:
            facts.setdefault('engineer_notes',[]).append(str(value))
        issue.update(resolved=True,answer=value,approver=approver)
        resolved.add(issue['id'])
    for issue in facts.get('recognition_issues',[]):
        if issue['id'] in resolved:
            issue['resolved']=True


def build_bom(facts):
    cabinets={x['cabinet_id']:x for x in facts['cabinets']}
    rows=[]
    for item in facts['items']:
        cab=cabinets[item['cabinet_id']]
        q=number(item['quantity_per_cabinet'],positive=True)
        c=number(cab['quantity'],positive=True)
        product=item.get('selected_product') or {}
        rows.append({'row_id':item['row_id'],'cabinet_id':item['cabinet_id'],'device_tag':item.get('device_tag',''),
            'role':item.get('role',''),'shown_model':item.get('shown_model',''),
            'description':product.get('description') or item.get('shown_model') or item.get('role'),
            'material_code':str(product.get('material_code') or ''),
            'manufacturer':product.get('manufacturer') or ('天正电气' if product else item.get('manufacturer_requested','')),
            'quantity_per_cabinet':float(q),'cabinet_quantity':int(c),'quantity':float(q*c),
            'unit':item['unit'],'supply_scope':item['supply_scope'], 'procurement_mode':item.get('procurement_mode','CATALOG'),
            'requirements':item.get('requirements',{}),'catalog_hash':item.get('catalog_hash',''),
            'evidence':item.get('evidence',[]),'approval':'','engineer_note':'',
            'price':{'unit_price_ex_tax':None,'tax_rate':None,'basis':'','confirmed':False,'reference_only':True}})
    return rows
