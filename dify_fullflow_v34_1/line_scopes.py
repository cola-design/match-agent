"""Quotation scope is separate from recognition. External/unclear lines remain reference."""
from copy import deepcopy
import json
import re


def line_reference_reason(item):
    if item.get('role')!='线缆':return ''
    texts=[item.get('shown_model','')]+[e.get('quote','') for e in item.get('evidence',[])]
    text=' '.join(texts)
    internal=bool(re.search(r'柜内|箱内|屏内|盘内|内部接线',text))
    external=bool(re.search(r'(?<![A-Z])(?:SC\d+|JDG\d+|CT|WC|CC|FC|CE|WE)(?![A-Z])|柜外|箱外|引自|外部',text,re.I))
    if internal and not external:return ''
    if internal and external:return '内外范围标注需核对；默认仅参考，纳入前请确认实际供货范围'
    if external:return '含建筑穿管、桥架或敷设标记；默认作为柜外线路参考，不计入本次成套柜报价'
    return '线路供货范围尚未明确；默认仅参考，需人工明确纳入后才采购计价'


def partition_line_references(state):
    state=deepcopy(state);kept=[];refs=list(state.get('line_references',[]));ids={r['row_id'] for r in refs}
    for item in state['facts']['items']:
        reason=line_reference_reason(item)
        if reason and not item.get('scope_explicitly_included'):
            if item['row_id'] not in ids:
                refs.append(dict(item,reference_reason=reason,default_quote_included=False));ids.add(item['row_id'])
        else:kept.append(item)
    state['facts']['items']=kept;state['line_references']=refs
    checks=[];reference_checks=[]
    for q in state.get('recognition_issues',[]):
        (reference_checks if q.get('item_id') in ids else checks).append(q)
    state['recognition_issues']=checks;state['reference_line_checks']=reference_checks
    state.setdefault('recognition_summary',{}).update(quotation_candidates=len(kept),line_reference_rows=len(refs),default_external_line_pricing=False)
    return state


def reference_form_rows(state):
    return [['REF',x['row_id'],x['shown_model'],x.get('recognition_meaning',''),x['reference_reason'],'否',x.get('cabinet_id',''),
             x.get('device_tag',''),'', 'm',x.get('material_code',''),x.get('manufacturer_requested') or '待核对','CATALOG','','','', '；'.join(('第%s页；左%.2f%% 上%.2f%%'%(e.get('page','?'),e['bbox'][0]*100,e['bbox'][1]*100)) if e.get('bbox') else '位置未明确' for e in x.get('evidence',[]))]
            for x in state.get('line_references',[])]


def apply_line_reference_choices(state,original_refs,records,approver,review_source):
    state=deepcopy(state);remaining=[];cabinets={c['cabinet_id']:c for c in state['facts']['cabinets']}
    yes={'是','YES','yes','纳入'};no={'','否','NO','no','不纳入','仅参考'}
    for ref in original_refs:
        key=('REF',ref['row_id'])
        if key not in records:raise ValueError('线路参考行缺失，不能直接删行：'+ref['row_id'])
        row=records[key];choice=row['是否由我方采购并计价']
        if choice in no:
            if any(row.get(k,'').strip() for k in ('确认单柜采购用量','物料编码','纳入依据')):
                raise ValueError('线路参考已填写采购信息，请明确选择是否纳入报价：'+ref['row_id'])
            remaining.append(ref);continue
        if choice not in yes:raise ValueError('线路是否计价请填写“是”或“否”：'+ref['row_id'])
        basis=row['纳入依据'].strip()
        if len(basis)<4 or re.search(r'待确认|未确认|尚未|不纳入',basis):raise ValueError('纳入线路采购需填写明确供货依据：'+ref['row_id'])
        # Quantities explicitly use the same per-cabinet basis as the existing BOM engine.
        converted={'确认':row['确认'],'确认柜组':row['归属柜组'],'确认回路':row['回路或用途'],'确认元件类别':'线缆',
            '确认型号':ref['shown_model'],'确认技术参数JSON':json.dumps(ref.get('requirements',{}),ensure_ascii=False),
            '确认单柜数量':row['确认单柜采购用量'],'计量单位':'m','供货范围':'OUR_PURCHASE','采购方式':row['采购方式'],
            '物料编码':row['物料编码'],'非标依据':row['外购或非标依据'],'指定厂商':row['指定厂商'],'同意替代':'否'}
        item=review_source(deepcopy(ref),converted,cabinets)
        item.update(scope_explicitly_included=True,quote_inclusion_basis=basis,scope_confirmed_by=approver)
        state['facts']['items'].append(item)
        state.setdefault('included_line_history',[]).append({'row_id':ref['row_id'],'basis':basis,'approver':approver})
    if len(state['facts']['items'])>100:raise ValueError('纳入后超过100项，请按柜组分项目；未提交部分清单')
    state['line_references']=remaining
    state.setdefault('recognition_summary',{}).update(quotation_candidates=len(state['facts']['items']),line_reference_rows=len(remaining))
    return state
