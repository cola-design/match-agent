"""Import visible requirement data as a fresh project; never restore old approvals or keys."""
from copy import deepcopy
import hashlib,json,re


def table_rows(markdown):
    tables=[];headers=None;rows=[]
    for line in markdown.splitlines():
        if not line.strip().startswith('|'):continue
        cells=[c.strip() for c in line.strip()[1:-1].split('|')]
        if cells and all(re.fullmatch(r':?-+:?',c) for c in cells):continue
        # Headers are emitted once per worksheet by the bounded XLSX reader.
        if cells and (cells[0]=='记录类型' or any(x in cells for x in ('型号','元件型号','确认型号','产品型号'))):
            if headers:tables.append((headers,rows))
            headers=cells;rows=[];continue
        if headers and len(cells)==len(headers):rows.append(dict(zip(headers,cells)))
    if headers:tables.append((headers,rows))
    return tables


def imported_pending(message,rid='',field=''):
    return {'code':'external_data_pending','message':message,'item_id':rid or None,'field':field,'page':'导入表'}


def generic_records(markdown):
    result={};found=False
    for headers,rows in table_rows(markdown):
        model_key=next((k for k in ('型号','元件型号','产品型号') if k in headers),None)
        if not model_key:continue
        if found:raise ValueError('通用清单存在多个型号明细页，请先合并为一张需求明细，避免重复导入')
        found=True
        def field(row,names,default=''):
            return next((row[n].strip() for n in names if row.get(n,'').strip()),default)
        for i,row in enumerate(rows,1):
            if not row.get(model_key,'').strip():continue
            rid=field(row,['行ID','元件ID'],'EXT%04d'%i)
            if len(rid)>40:raise ValueError('外部清单行ID过长')
            key=('SOURCE',rid)
            if key in result:raise ValueError('外部清单行ID重复')
            cid=field(row,['柜组','柜组ID','柜号'],'G1')
            quantity=field(row,['单柜数量','单柜用量'])
            scope=field(row,['供货范围'],'OUR_PURCHASE')
            scope={'我方采购':'OUR_PURCHASE','甲供':'CUSTOMER_SUPPLIED','仅预留':'RESERVED','供电局安装':'UTILITY_SUPPLIED'}.get(scope,scope)
            result[key]={'记录类型':'SOURCE','行ID':rid,'原图型号':row[model_key],'原图解释':'外部需求清单导入','待核对内容':'',
                '确认柜组':cid,'确认回路':field(row,['回路','回路号']),'确认元件类别':field(row,['元件类别','器件类别','类别'],'待确认器件'),
                '确认型号':row[model_key],'确认单柜数量':quantity,'计量单位':field(row,['单位','计量单位']),
                '供货范围':scope,'采购方式':field(row,['采购方式'],'CATALOG'),'物料编码':field(row,['物料编码']),
                '非标依据':field(row,['非标依据','外购依据']),'指定厂商':field(row,['指定厂商','厂商','品牌'],'未指定'),
                '同意替代':field(row,['同意替代'],'否'),'确认':field(row,['确认'],'是'),'处理方式':'KEEP','剔除或补录依据':''}
            p={'记录类型':'PARAM','行ID':rid,'原图型号':row[model_key]}
            for label,_,_ in PARAM_FIELDS:p[label]=field(row,[label])
            result[('PARAM',rid)]=p
            c={'记录类型':'CAB','行ID':cid,'原图柜号':cid,'确认柜号':cid,'确认柜体数量':field(row,['柜体数量','柜组数量']),'宽mm':field(row,['宽mm']),'高mm':field(row,['高mm']),'深mm':field(row,['深mm']),'确认':'是'}
            old=result.get(('CAB',cid))
            if old and any(old[k] and c[k] and old[k]!=c[k] for k in ('确认柜体数量','宽mm','高mm','深mm')):raise ValueError('同一柜组的数量或尺寸填写不一致：'+cid)
            if not old:result[('CAB',cid)]=c
            elif not old['确认柜体数量'] and c['确认柜体数量']:result[('CAB',cid)]=c
    if not found:return {}
    result[('SETTINGS','APPROVER')]={'确认人':'用户上传清单'}
    result[('SETTINGS','SOURCE_COMPLETE')]={'确认':'是','核对结论':'用户主动提交完整需求明细进行新项目选型；不继承原项目的产品选型或报价批准'}
    return result


def import_issue_workbook(state_json,encoded):
    state=unpack(state_json)
    if state.get('stage')!='SOURCE_IMPORT_PENDING':raise ValueError('当前不是新项目清单导入状态')
    text=external_workbook_markdown(encoded)
    records={k:v for k,v in parse_markdown(text).items() if k[0]!='META'}
    sources=[(key,row) for key,row in records.items() if key[0]=='SOURCE']
    generated=bool(sources)
    if not sources:
        if any(k[0] in ('BOM','QUOTE','PRICE') for k in records):raise ValueError('当前入口接收问题确认清单或需求明细，不把旧BOM、报价及审批状态当作新项目需求。')
        records=generic_records(text);sources=[(key,row) for key,row in records.items() if key[0]=='SOURCE']
    if not sources:raise ValueError('没有找到元件需求。请上传原问题确认清单，或包含型号、元件类别、单柜数量、单位、柜组和柜体数量的XLSX明细。')
    if len(sources)>100:raise ValueError('外部清单超过100项，请按柜组分项目；未导入截断明细。')
    # Every field below comes from visible cells; old META, selected products and prices are ignored.
    pending=[];items=[];notes=[];cabs={};excluded=[]
    for (typ,cid),row in records.items():
        if typ!='CAB':continue
        if not row.get('确认柜号','').strip() and cid.startswith('NEWG'):continue
        c={'cabinet_id':cid,'source_tags':[s for s in re.split(r'[,，、;/\s]+',row.get('确认柜号') or row.get('原图柜号') or cid) if s],
           'quantity':None,'width_mm':None,'height_mm':None,'depth_mm':None,'evidence':[],'notes':['外部清单导入，未重新识图']}
        try:
            q=number(row.get('确认柜体数量'),True)
            if q!=q.to_integral_value():raise ValueError('柜体数量必须是正整数')
            c['quantity']=int(q)
            for key,label in [('width_mm','宽mm'),('height_mm','高mm'),('depth_mm','深mm')]:
                if row.get(label,''):c[key]=float(number(row[label],True))
            c['_imported_confirmation']=row.get('确认','')
            if row.get('确认') not in YES:raise ValueError('柜组数量未确认')
        except ValueError as exc:pending.append(imported_pending('柜组%s：%s；请补充实际柜体数量。'%(cid,exc),field='cabinet_quantity'))
        cabs[cid]=c
    for (typ,rid),row in records.items():
        if typ=='SOURCEADD':
            if not row.get('确认型号','').strip() and row.get('确认') not in YES:continue
            sources.append(((typ,rid),row))
    if len(sources)>100:raise ValueError('含人工补录后超过100项，请按柜组拆分')
    for (typ,rid),row in sources:
        if row.get('处理方式')=='EXCLUDE':
            if row.get('确认') not in YES or not approval_text(row.get('剔除或补录依据','')):raise ValueError('外部清单剔除行缺少明确依据或确认：'+rid)
            excluded.append({'row_id':rid,'reason':row['剔除或补录依据']});continue
        if typ=='SOURCEADD' and not approval_text(row.get('剔除或补录依据','')):pending.append(imported_pending('人工补录需说明图纸位置或计价依据：'+rid,rid))
        model=row.get('确认型号') or row.get('原图型号') or ''
        cid=row.get('确认柜组') or 'G1'
        if cid not in cabs:
            cabs[cid]={'cabinet_id':cid,'source_tags':[cid],'quantity':None,'width_mm':None,'height_mm':None,'depth_mm':None,'notes':[],'evidence':[]}
            pending.append(imported_pending('柜组%s缺少实际柜体数量。'%cid,field='cabinet_quantity'))
        item={'row_id':rid,'cabinet_id':cid,'device_tag':row.get('确认回路',''),'role':row.get('确认元件类别') or '待确认器件',
            'shown_model':model,'model_family':'','requirements':{},'quantity_per_cabinet':None,'unit':row.get('计量单位',''),
            'supply_scope':row.get('供货范围',''),'procurement_mode':row.get('采购方式','CATALOG'),'material_code':row.get('物料编码',''),
            'manufacturer_requested':row.get('指定厂商',''),'recognition_pending':[],
            'recognition_meaning':'外部清单需求，原文未重新识图','evidence':[{'source':'external_issue_workbook','quote':row.get('原图型号') or model,'row_id':rid}]}
        try:
            converted=parameters_from_columns(item,row,records)
            req=json.loads(converted['确认技术参数JSON'])
            if not isinstance(req,dict):raise ValueError('技术参数必须为对象')
            if any(k in req for k in ('voltage_pair_v','apparent_power_va')) and '变压器' not in item['role']:raise ValueError('变压比及VA容量与器件类别不符')
            if req.get('ats_class') and not ('转换' in item['role'] or 'ATS' in item['role'].upper()):raise ValueError('PC或CB级应属于ATS')
            if req.get('leakage_current_ma') and item['role'] in ('小型断路器','塑壳断路器'):raise ValueError('普通断路器与剩余动作电流存在类别冲突')
            item['requirements']=req
            try:item['quantity_per_cabinet']=float(number(row.get('确认单柜数量'),True))
            except ValueError:pass
            item=reviewed_source_item(item,converted,cabs,records)
        except (ValueError,KeyError,TypeError) as exc:
            item['recognition_pending'].append(str(exc));pending.append(imported_pending('清单%s：%s'%(rid,exc),rid))
        item['_imported_confirmation']=row.get('确认','')
        item['_manufacturer_explicitly_unspecified']=row.get('指定厂商')=='未指定'
        items.append(item)
    if not items:raise ValueError('外部清单没有保留任何元件或计价项目。')
    original_checks=[]
    for (typ,rid),row in records.items():
        if typ=='TECH':
            value=row.get('识图覆盖或未定位内容','')
            if rid.startswith('CHECK_') and value:
                try:q=json.loads(value)
                except ValueError:continue
                if isinstance(q,dict) and q.get('code') and q.get('message'):original_checks.append(q)
            if row.get('原文'):notes.append({'text':row['原文'],'kind':'external_source_note','evidence':[]})
            if row.get('补充说明'):notes.append({'text':row['补充说明'],'kind':'external_source_supplement','evidence':[]})
    state['facts']={'items':items,'cabinets':list(cabs.values()),'notes':notes,'rules':[],'connections':[],'recognition_issues':[]}
    state['recognized_circuits']=[];state['recognition_summary']={'entry_mode':'uploaded_issue_list','recognition_accuracy_verified':False,'source_pdf_requested':False}
    state['recognition_issues']=original_checks
    # Visible question rows also work without the old program's internal TECH records.
    calculated={qid for qid,_,_ in current_issues(state)}
    for (typ,rid),row in list(records.items()):
        if typ=='CHECK' and rid not in calculated:
            q={'code':'external_confirmation_required','message':row.get('问题说明','外部清单问题需确认'),'item_id':row.get('关联清单行ID') or None,'page':'导入表'}
            state['recognition_issues'].append(q)
            qid=next(qid for qid,old,_ in current_issues(state) if old is q)
            records[('CHECK',qid)]=row
    for item in items:
        if ('转换' in item['role'] or 'ATS' in item['role'].upper()) and not item['requirements'].get('pole_count'):
            if not any(q.get('item_id')==item['row_id'] and '极数未明确' in q['message'] for q in state['recognition_issues']):
                state['recognition_issues'].append({'code':'item_parameter_pending','item_id':item['row_id'],'message':'ATS极数未明确，请补充实际极数；不从II推断。','page':'导入表'})
    approver=records.get(('SETTINGS','APPROVER'),{}).get('确认人','').strip()
    if generated and len(approver)<2:pending.append(imported_pending('请填写本次清单的确认人。',field='approver'))
    complete=records.get(('SETTINGS','SOURCE_COMPLETE'),{})
    if generated and (complete.get('确认') not in YES or not approval_text(complete.get('核对结论',''))):pending.append(imported_pending('请填写完整性核对结论并确认。',field='completeness'))
    # Preserve default unpriced references and process explicit inclusions using the same source rules.
    refs=[]
    for (typ,rid),row in records.items():
        if typ!='REF':continue
        ref={'row_id':rid,'shown_model':row.get('图面线路标注',''),'role':'线缆','cabinet_id':row.get('归属柜组',''),
             'device_tag':row.get('回路或用途',''),'requirements':{},'recognition_meaning':row.get('含义',''),
             'reference_reason':row.get('默认范围说明','外部清单线路参考，不自动计价'),'evidence':[],'model_family':''}
        refs.append(ref)
    state['line_references']=refs
    try:state=apply_line_reference_choices(state,refs,records,approver or '用户上传清单',reviewed_source_item)
    except (ValueError,KeyError) as exc:pending.append(imported_pending('线路范围：'+str(exc),field='line_scope'))
    try:state=validate_issue_returns(deepcopy(state),state,records,approver or '用户上传清单')
    except (ValueError,KeyError) as exc:pending.append(imported_pending(str(exc),field='issue_decisions'))
    notes=state['facts']['notes']
    notes.append({'text':'本项目由用户上传的已有问题清单创建，未重新读取PDF。旧会话签名、已选产品、价格和报价审批未作为新项目批准状态继承。','kind':'import_provenance','evidence':[]})
    if state.get('notes'):notes.append({'text':'导入时项目补充说明：'+state['notes'],'kind':'project_notes','evidence':[]})
    state['imported_issue_answers']={rid:{'处理结论':row.get('处理结论',''),'确认内容或依据':row.get('确认内容或依据','')} for (typ,rid),row in records.items() if typ=='CHECK'}
    state['entry_mode']='ISSUE_LIST';state['source_origin']='external_visible_workbook';state['bom']=[];state['excluded_items']=excluded
    state['revision']+=1;state['imported_prefill']=True;state['schema']='cabinet-native-v35'
    state['recognition_summary'].update(quotation_candidates=len(state['facts']['items']),line_reference_rows=len(state.get('line_references',[])))
    if pending:
        state['stage']='SOURCE_REVIEW';state['source_reviewed']=False
        existing=state['recognition_issues'];seen={json.dumps(q,sort_keys=True,ensure_ascii=False) for q in existing}
        for q in pending:
            key=json.dumps(q,sort_keys=True,ensure_ascii=False)
            if key not in seen:existing.append(q);seen.add(key)
        # Old answers are not pre-approved in a newly issued incomplete confirmation form.
        for q in state['recognition_issues']:q.pop('human_reviewed',None);q.pop('human_basis',None)
        route='FORM';message='已有清单已导入并保留已填内容；请补齐本轮列出的问题后选型，无需重新上传PDF。'
    else:
        state['stage']='MATCH_PENDING';state['source_reviewed']=True;state['source_approver']=approver or '用户上传清单'
        route='MATCH';message='已直接从填写完整的问题清单建立新项目，进入数据库选型；没有执行PDF识图。'
    state['recognition_summary']['pending_checks']=len(pending)
    stamp(state,'external_issue_list_imported',approver or '用户上传清单')
    return {'state_json':pack(state),'route':route,'valid':'YES','message':message}


def preserve_imported_inputs(state,sheets):
    if not state.get('imported_prefill'):return
    items={x['row_id']:x for x in state['facts']['items']};cabs={x['cabinet_id']:x for x in state['facts']['cabinets']}
    answers=state.get('imported_issue_answers',{})
    for table in sheets:
        positions={k:i for i,k in enumerate(table['headers'])}
        for row in table['rows']:
            if row[0]=='SOURCE' and row[1] in items:
                item=items[row[1]]
                row[positions['确认']]=item.get('_imported_confirmation','')
                row[positions['同意替代']]='是' if item.get('substitution_approved') else '否'
                if item.get('_manufacturer_explicitly_unspecified'):row[positions['指定厂商']]='未指定'
            elif row[0]=='CAB' and row[1] in cabs:row[positions['确认']]=cabs[row[1]].get('_imported_confirmation','')
            elif row[0]=='CHECK' and row[1] in answers:
                for k,v in answers[row[1]].items():row[positions[k]]=v
