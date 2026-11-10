"""Chinese confirmation fields with signed identity, evidence and explicit issue decisions."""
from copy import deepcopy
from decimal import Decimal,InvalidOperation
import hashlib,json,re

PARAM_FIELDS=[('额定电流A','rated_current_a','positive'),('极数','pole_count','poles'),('脱扣曲线','trip_curve','text'),
 ('剩余动作电流mA','leakage_current_ma','positive'),('分断能力kA','breaking_capacity_ka','positive'),
 ('额定电压V','voltage_v','positive'),('输出电流A','current_a','positive'),('一次电压V','primary_voltage_v','positive'),
 ('二次电压V','secondary_voltage_v','positive'),('容量VA','apparent_power_va','positive'),('ATS类别','ats_class','ats'),
 ('线圈电压V','coil_voltage_v','positive'),('其他技术要求','engineering_requirements_text','text')]


def parameter_rows(state,additions):
    rows=[]
    keys={key for _,key,_ in PARAM_FIELDS}|{'voltage_pair_v'}
    for item in state['facts']['items']+[{'row_id':r[1],'shown_model':'人工补录；不用请留空','requirements':{}} for r in additions]:
        req=item.get('requirements',{});pair=req.get('voltage_pair_v') or []
        vals=dict(req)
        if isinstance(pair,list) and len(pair)==2:vals.update(primary_voltage_v=pair[0],secondary_voltage_v=pair[1])
        other={k:v for k,v in req.items() if k not in keys}
        rows.append(['PARAM',item['row_id'],item.get('shown_model','')]+[vals.get(key,'') for _,key,_ in PARAM_FIELDS]+[json.dumps(other,ensure_ascii=False) if other else ''])
    return rows


def parameters_from_columns(item,row,records):
    if '确认技术参数JSON' in row:return row
    r=records.get(('PARAM',item['row_id']))
    if r is None:raise ValueError('技术参数行缺失：'+item['row_id'])
    req=deepcopy(item.get('requirements',{}));req.pop('voltage_pair_v',None)
    for _,key,_ in PARAM_FIELDS:req.pop(key,None)
    vals={}
    for label,key,kind in PARAM_FIELDS:
        text=str(r.get(label,'')).strip()
        if not text:continue
        if kind=='positive':
            try:n=Decimal(text)
            except InvalidOperation:raise ValueError(label+'请填实际数字，不带单位：'+item['row_id']) from None
            if not n.is_finite() or n<=0:raise ValueError(label+'应大于0；未知请留空：'+item['row_id'])
            vals[key]=float(n)
        elif kind=='poles':
            text=re.sub(r'\s+','',text).upper()
            if not re.fullmatch(r'[1-4]P(?:\+N)?',text):raise ValueError('极数请按1P、1P+N、2P、3P、4P等填写，不从II推断：'+item['row_id'])
            vals[key]=text
        elif kind=='ats':
            text=text.upper().removesuffix('级')
            if text not in ('PC','CB'):raise ValueError('ATS类别请填PC或CB；未知留空：'+item['row_id'])
            vals[key]=text
        else:vals[key]=text
    pair=[vals.pop('primary_voltage_v',None),vals.pop('secondary_voltage_v',None)]
    if (pair[0] is None)!=(pair[1] is None):raise ValueError('变压器一次、二次电压需一起填写或一起留空：'+item['row_id'])
    if pair[0] is not None:vals['voltage_pair_v']=pair
    role=row.get('确认元件类别','')
    if any(k in vals for k in ('voltage_pair_v','apparent_power_va')) and '变压器' not in role:raise ValueError('变压比与VA容量需归属变压器，请核对类别：'+item['row_id'])
    if 'ats_class' in vals and not ('转换' in role or 'ATS' in role.upper()):raise ValueError('PC/CB类别应归属自动转换开关：'+item['row_id'])
    if 'leakage_current_ma' in vals and role in ('小型断路器','塑壳断路器'):raise ValueError('普通断路器行不能直接填剩余动作电流，请核对器件类别或独立漏保配置：'+item['row_id'])
    req.update(vals)
    return dict(row,确认技术参数JSON=json.dumps(req,ensure_ascii=False,allow_nan=False))


def current_issues(state):
    result=[];seen=set()
    for q in state.get('recognition_issues',[]):
        key=json.dumps(q,ensure_ascii=False,sort_keys=True)
        if key in seen:continue
        seen.add(key);qid='Q'+hashlib.sha256(key.encode()).hexdigest()[:12]
        item=next((i for i in state['facts']['items'] if i['row_id']==q.get('item_id')),None)
        rect=q.get('view_rect') or ((item or {}).get('evidence') or [{}])[0].get('bbox')
        location='第%s页'%q.get('page','?')
        if q.get('view'):location+='；视图'+str(q['view'])
        if rect and len(rect)==4:
            scale=100 if max(rect)<=1 else .1
            location+='；左%.1f%% 上%.1f%% 右%.1f%% 下%.1f%%'%tuple(v*scale for v in rect)
        result.append((qid,q,location))
    return result


def issue_form_rows(state):
    return [['CHECK',qid,q.get('item_id',''),q['message'],location,'',''] for qid,q,location in current_issues(state)]


def validate_issue_returns(original,state,records,approver):
    kept={x['row_id']:x for x in state['facts']['items']};decisions=[]
    for qid,q,_ in current_issues(original):
        if q.get('item_id') and q['item_id'] not in kept:
            decisions.append(dict(issue=q,decision='关联项已明确排除',approver=approver));continue
        row=records.get(('CHECK',qid),{})
        if row.get('处理结论')!='已核对':raise ValueError('仍有待确认问题，请在“待确认问题”填处理结论和依据：'+qid)
        basis=row.get('确认内容或依据','').strip()
        if len(basis)<6 or re.search(r'待确认|待核对|未完成|未核实|不确定|不知道|随便|通过即可',basis):raise ValueError('问题依据需写实际确认结果、资料或图纸修订，不能只填已核对：'+qid)
        item=kept.get(q.get('item_id'))
        if item:
            role=item['role'];req=item.get('requirements',{})
            if q['code']=='item_parameter_pending' and '极数未明确' in q['message'] and not req.get('pole_count'):raise ValueError('ATS极数仍未填写，请先核实实际极数：'+item['row_id'])
            if q['code']=='item_parameter_pending' and '订货类别未核实' in q['message'] and role=='剩余电流保护器件':raise ValueError('请明确剩余电流保护器件的订货类别：'+item['row_id'])
        decisions.append(dict(issue=q,decision='人工核对',basis=basis,approver=approver))
    for d in decisions:
        if d.get('basis'):state['facts'].setdefault('notes',[]).append({'text':'工程核对 '+d['issue']['message']+'；确认依据：'+d['basis'],'kind':'human_issue_decision','reviewer':approver})
    state['recognition_issue_decisions']=decisions
    state['recognition_issues']=[dict(d['issue'],human_reviewed=True,human_basis=d.get('basis','关联项已排除')) for d in decisions]
    state['recognition_summary']['human_reviewed_checks']=len(decisions)
    state['recognition_summary']['recognition_accuracy_verified']=False
    return state
