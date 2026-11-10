"""Finalize recognition as one consistent set of entities, parameters and checks.

Rejected observations and superseded checks remain evidence; only current,
applicable questions are sent to the human confirmation sheet.
"""
from copy import deepcopy
import re
from spatial import *
from literal_decode import number_text


def parameter_owner_allowed(key,category):
    owners={'voltage_pair_v':{'transformer'},'apparent_power_va':{'transformer'},
            'ats_class':{'ATS'},'residual_current_ma':{'RCBO','RCCB','residual_current_device_unspecified'}}
    return key not in owners or category in owners[key]


def outgoing_tag(tag,anchor,drawings,manifest):
    key=canonical(tag)
    cabinet_tags={canonical(t) for d in drawings for t in d.get('cabinet_tags',[])}
    if not key or key in cabinet_tags:return False
    kind=decode(tag)['kind']
    if kind=='circuit_tag':return True
    if kind in ('phase','cable','voltage_ratio','power_marking','calculation'):return False
    if key in ('I/O','IO','N','PE','主用','备用','进线','出线','回路总线','总线'):return False
    if re.search(r'WDZ|NHBV|ZNBV|BVR?-|UTP/|STP/|CAT[5-8]|JDG|SC\d|\d+[*X×]\d+',key):return False
    # Custom printed identifiers are supported, but require their own text evidence.
    if not re.fullmatch(r'[A-Z]{1,8}[-_]?[0-9]{1,5}(?:[.\-/][A-Z0-9]{1,5})*|\d+(?:[-.]\d+)+',key):return False
    page=manifest.get('native_pages',{}).get(str(anchor['page']),{})
    native=any(s['id']==anchor.get('native_ref') and canonical(s['text'])==key for s in page.get('spans',[]))
    visual=bool(anchor.get('bbox') and anchor.get('drawing_id') and anchor.get('direction') in ('horizontal','vertical') and anchor.get('observations'))
    return native or visual


def marker_owners(note,params,items,circuits,drawings):
    key=next(iter(params),None)
    if not key:return []
    candidates=[x for x in items if x['page']==note['page'] and parameter_owner_allowed(key,x['category']) and
                x.get('symbol_observed',True) and x['category']!='unknown']
    if key=='residual_current_ma':
        branch=marker_branch(note,circuits,drawings)
        if branch:candidates=[x for x in candidates if x.get('circuit_id')==branch['id']]
    direct=[x for x in candidates if x['id']==note.get('target_id') and parameter_geometry_compatible(note,x)]
    if direct:return direct
    if key=='residual_current_ma' and not marker_branch(note,circuits,drawings):return []
    matching=[]
    for x in candidates:
        own=dict(x.get('literal_parameters',{}));own.update(x.get('associated_parameters',{}))
        if all(own.get(k)==v for k,v in params.items()) and parameter_geometry_compatible(note,x):matching.append(x)
    if matching:return matching
    if key=='ats_class' and note.get('bbox'):
        candidates=[x for x in candidates if x.get('drawing_id')==note.get('drawing_id') and parameter_geometry_compatible(note,x) and max(abs(a-b) for a,b in zip(center(note['bbox']),center(x['bbox'])))<=70]
        return candidates
    return []


def marker_branch(note,circuits,drawings):
    if not note.get('bbox'):return None
    drawing=note.get('drawing_id') or assign_drawing(note,drawings)
    if not drawing:return None
    pt=center(note['bbox']);options=[]
    for a in circuits:
        if a['page']!=note['page'] or a.get('drawing_id')!=drawing or not a.get('wire_segment'):continue
        axis=1 if a.get('direction')=='horizontal' else 0
        if a.get('direction') not in ('horizontal','vertical'):continue
        wire=a['wire_segment'];row=wire[axis];along=1-axis
        neighbors=[b for b in circuits if b is not a and b['page']==a['page'] and b.get('drawing_id')==drawing and b.get('direction')==a['direction'] and b.get('wire_segment')]
        spacing=[abs(b['wire_segment'][axis]-row) for b in neighbors if abs(b['wire_segment'][axis]-row)>1]
        tolerance=min(18,min(spacing)*.45) if spacing else 15
        if abs(pt[axis]-row)<=tolerance and min(wire[along],wire[along+2])-50<=pt[along]<=max(wire[along],wire[along+2]):options.append(a)
    return options[0] if len(options)==1 else None


def audit_record(kind,message,row=None,**extra):
    row=row or {}
    return dict(kind=kind,message=message,item_id=row.get('id'),raw=row.get('raw',''),page=row.get('page'),bbox=row.get('bbox'),**extra)


def finalize_evidence(source,manifest):
    result=deepcopy(source)
    if result.get('summary',{}).get('evidence_contract_version')=='34.0':return result
    for key in ('items','circuits','document_annotations','issues','quarantined_observations'):result.setdefault(key,[])
    old_checks=result['issues'];active=[];resolved=[];audit=list(result.get('validation_audit',[]))
    items=result['items'];drawings=result.get('drawings',[]);notes=result['document_annotations']
    # A time/function glyph beside a named timer belongs to that symbol, not another order line.
    kept=[];removed_ids=set()
    for item in items:
        timer=[x for x in items if x is not item and x['page']==item['page'] and x.get('drawing_id')==item.get('drawing_id') and
               (x.get('category')=='timer' or re.search(r'时间控制|时控|延时',x['raw'])) and len(x['raw'])>1 and x.get('bbox') and item.get('bbox') and
               max(abs(a-b) for a,b in zip(center(x['bbox']),center(item['bbox'])))<=max(25,(x['bbox'][3]-x['bbox'][1])*2)]
        if item['raw'].strip() in ('t','θ','Δ','~','～') and item.get('category') in ('unknown','timer') and len(timer)==1:
            note=dict(item,kind='symbol_mark',related_item_id=timer[0]['id'],status='not_procurement_entity')
            notes.append(note);removed_ids.add(item['id'])
            audit.append(audit_record('symbol_mark','该处为时间/功能符号标记，已作为注释关联到时控器，不单列采购器件',item))
        else:kept.append(item)
    items=kept;result['items']=items;by_id={x['id']:x for x in items}
    # Connection roles remain visible as notes; they cannot appear as numbered outgoing circuits.
    circuits=[];excluded=[]
    for a in result['circuits']:
        if outgoing_tag(a['tag'],a,drawings,manifest):circuits.append(a)
        else:
            excluded.append(a['id']);note=dict(a,raw=a['tag'],kind='connection_annotation',status='not_outgoing_number')
            result.setdefault('connection_annotations',[]).append(note)
    if excluded:audit.append(audit_record('circuit_classification','已将%d条相别、线缆或进线角色标注移出出线回路表，原文与位置仍保留'%len(excluded)))
    circuits.sort(key=lambda a:(a['page'],a.get('drawing_id') or '',tuple((0,int(p)) if p.isdigit() else (1,p) for p in re.split(r'(\d+)',canonical(a['tag'])))))
    result['circuits']=circuits;valid_circuits={a['id']:a for a in circuits}
    for a in circuits:
        page=manifest.get('native_pages',{}).get(str(a['page']),{})
        verified=any(s['id']==a.get('native_ref') and canonical(s['text'])==canonical(a['tag']) for s in page.get('spans',[]))
        if not verified and a.get('observations'):
            active.append(problem('outgoing_number_visual_only','回路编号“%s”目前仅有视觉位置证据，请对照原图核对'%a['tag'],page=a['page']))
    for item in items:
        a=valid_circuits.get(item.get('circuit_id'))
        if a and a['page']==item['page'] and a.get('drawing_id')==item.get('drawing_id'):
            item['circuit']=a['tag'];item['load']=a.get('load') if a.get('load_position_checked') else None
        else:item.update(circuit_id=None,circuit=None,load=None,binding_status='unbound')
        item['candidate_circuit_ids']=[v for v in item.get('candidate_circuit_ids',[]) if v in valid_circuits]
    for a in circuits:a['members']=[x['id'] for x in items if x.get('circuit_id')==a['id']]
    # Rebuild literal meanings and applicable selection parameters after every association change.
    rejected=[]
    for item in items:
        literal=decode(item['raw'])['explicit_parameters'];previous=dict(item.get('literal_parameters',{}));previous.update(item.get('associated_parameters',{}))
        associated={}
        for key,value in previous.items():
            if key in literal:
                if literal[key]!=value:active.append(problem('parameter_conflict','图面原文参数与关联参数不一致：'+key,page=item['page'],item_id=item['id'],field=key))
                continue
            if parameter_owner_allowed(key,item['category']):associated[key]=value
            else:
                rejected.append(dict(item_id=item['id'],field=key,value=value,page=item['page']))
                audit.append(audit_record('parameter_ownership','已移除不属于该器件类别的关联参数“%s”，保留为参数证据'%key,item,field=key,value=value))
        # An explicit conflicting literal remains source evidence, not a silently accepted spec.
        effective=dict(literal);effective.update(associated)
        for key in list(effective):
            if not parameter_owner_allowed(key,item['category']):
                item.setdefault('unresolved_markings',{})[key]=effective.pop(key)
        item['literal_parameters']=literal;item['associated_parameters']=associated
        item['selection_parameters']={} if item['category']=='unknown' else effective
        if item['category'] in ('MCB','MCCB') and 'residual_current_ma' in item.get('unresolved_markings',{}):
            value=item['unresolved_markings']['residual_current_ma']
            active.append(problem('protective_function_conflict','图面“%s”同时含%s mA标记，请核对剩余电流保护要求和完整器件配置；未擅自修改类别'%(item['raw'],number_text(value)),page=item['page'],item_id=item['id'],field='leakage_current_ma',marker_raw=str(value)+'mA'))
        display=dict(literal);display.update(associated)
        # Inapplicable literal markings stay in raw text and unresolved_markings, not meaning/specs.
        for key in list(display):
            if not parameter_owner_allowed(key,item['category']):display.pop(key)
        explanation=explain(item['raw'],item['category'],display);item.update(explanation)
        if item.get('symbol_observed') is False:item['meaning_cn']='原文已定位，独立符号未核实；'+item['meaning_cn']
        for text in item.get('pending',[]):active.append(problem('item_parameter_pending','“%s”：%s'%(item['raw'],text),page=item['page'],item_id=item['id']))
    for rejection in rejected:
        owner=[x for x in items if x['page']==rejection['page'] and parameter_owner_allowed(rejection['field'],x['category']) and
               x.get('selection_parameters',{}).get(rejection['field'])==rejection['value'] and x.get('symbol_observed',True)]
        if not owner:
            active.append(problem('parameter_target_unresolved','已阻止不适用的参数进入该器件选型，但尚未找到其实际归属：'+rejection['field'],page=rejection['page'],item_id=rejection['item_id'],field=rejection['field']))
    # Supplement independent native parameter labels even when a visual response omitted one.
    existing={n.get('native_ref') for n in notes if n.get('native_ref')}
    for p in manifest.get('native_pages',{}).values():
        for span in p.get('spans',[]):
            if span['id'] not in existing and decode(span['text'])['kind'] in ('voltage_ratio','apparent_power','residual_current','ats_class'):
                note=dict(span,id='NATIVE-'+span['id'],native_ref=span['id'],raw=span['text'],kind='parameter',page=p['page'],target_id=None)
                note['drawing_id']=assign_drawing(note,drawings);notes.append(note);existing.add(span['id'])
    resolved_raw=set();conflict_raw=set()
    for note in notes:
        params=decode(note['raw'])['explicit_parameters'];kind=decode(note['raw'])['kind']
        if kind not in ('voltage_ratio','apparent_power','residual_current','ats_class'):continue
        note.pop('status',None)
        owners=marker_owners(note,params,items,circuits,drawings)
        if len(owners)==1:
            target=owners[0]
            previous=dict(target['literal_parameters']);previous.update(target['associated_parameters'])
            conflicts=[k for k,v in params.items() if previous.get(k) not in (None,v)]
            if conflicts:
                note.update(target_id=target['id'],status='requires_engineering_confirmation')
                active.append(problem('parameter_conflict','独立参数与该器件已有参数冲突，未自动覆盖',page=target['page'],item_id=target['id'],field=conflicts[0]))
            else:
                target['associated_parameters'].update(params)
                note['target_id']=target['id'];note['status']='assigned';resolved_raw.add((note['page'],canonical(note['raw'])))
        elif kind=='residual_current':
            branch=marker_branch(note,circuits,drawings)
            protective=[x for x in items if branch and x.get('circuit_id')==branch['id'] and x['category'] in ('MCB','MCCB','RCBO','RCCB','residual_current_device_unspecified')]
            if len(protective)==1:
                target=protective[0];value=params.get('residual_current_ma')
                if target['category'] in ('MCB','MCCB'):
                    note.update(target_id=target['id'],status='requires_engineering_confirmation')
                    conflict_raw.add((note['page'],canonical(note['raw'])))
                    active.append(problem('protective_function_conflict','图面“%s”所在支路另有%s mA标记，请核对剩余电流保护要求和器件类型；未擅自改为RCBO'%(target['raw'],number_text(value)),page=target['page'],item_id=target['id'],field='leakage_current_ma',marker_raw=note['raw']))
                else:
                    old=target['selection_parameters'].get('residual_current_ma')
                    if old in (None,value):
                        target['associated_parameters']['residual_current_ma']=value;target['selection_parameters']['residual_current_ma']=value
                        note.update(target_id=target['id'],status='assigned');resolved_raw.add((note['page'],canonical(note['raw'])))
                    else:active.append(problem('parameter_conflict','同一支路的剩余动作电流标注有冲突，未覆盖原值',page=target['page'],item_id=target['id'],field='leakage_current_ma'))
        if note.get('status') not in ('assigned','requires_engineering_confirmation'):
            active.append(problem('parameter_unassigned','独立参数“%s”尚未取得唯一、适用的器件归属，原文保留待核对'%note['raw'],page=note['page'],marker_raw=note['raw'],annotation_id=note.get('id')))
    def quoted_raw(q):
        if q.get('marker_raw'):return q['marker_raw']
        m=re.search(r'[“「](.*?)[”」]',q.get('message',''));return m[1] if m else ''
    # Replace superseded view-level guesses with checks against the final facts.
    derived={'binding_geometry_conflict','binding_unverified','multiple_circuit_owners','cable_scope_unconfirmed',
             'item_parameter_pending','classification_unknown','parameter_unassigned','not_outgoing_circuit',
             'load_position_unconfirmed','load_label_conflict'}
    for q in old_checks:
        code=q['code'];target=by_id.get(q.get('item_id'));raw=quoted_raw(q)
        if code=='native_combined_entities' and not raw:raw=q.get('message','').rsplit('：',1)[-1].strip()
        if q.get('item_id') and q['item_id'] not in by_id:
            resolved.append(dict(q,resolution='该观测未作为最终采购候选，原始证据仍保留'));continue
        if q.get('item_id') in removed_ids:
            resolved.append(dict(q,resolution='该观测已归为符号注释'));continue
        refs=set(q.get('focus_native_ids',[]))
        if code=='view_incomplete' and q.get('view_mode')=='repair' and refs and all(any(x.get('native_ref')==ref and x.get('symbol_observed',True) for x in items) for ref in refs):
            resolved.append(dict(q,resolution='该定点任务的全部原文位置已取得独立器件证据；仅关闭此项定点任务，不代表整页准确率已验证'));continue
        if code in derived:
            resolved.append(dict(q,resolution='已按最终器件、有效回路和参数归属重新核对'));continue
        if code=='annotation_unclassified' and any(n.get('kind')=='symbol_mark' and canonical(n['raw'])==canonical(raw) for n in notes):
            resolved.append(dict(q,resolution='已作为功能符号关联到实际器件'));continue
        if code=='visual_uncertainty':
            refs=set(re.findall(r'P\d+-T\d+',q.get('message','')))
            found=[x for x in items if x.get('native_ref') in refs and x.get('symbol_observed',True)]
            if refs and {x.get('native_ref') for x in found}==refs and all(x['category']=='cable' for x in found) and not re.search(r'冲突|矛盾|不一致|模糊|看不清|无法识别|无法辨认',q['message']):
                resolved.append(dict(q,resolution='线缆原文已作为候选保留；不要求线缆标注必须带独立元件符号或出线编号，长度与供货范围仍由人工核对'));continue
        if code=='native_symbol_unresolved' and target and target.get('symbol_observed',True):
            resolved.append(dict(q,resolution='已取得独立符号证据并完成类别核对'));continue
        if code=='classification_conflict' and target and target['category']!='unknown':
            resolved.append(dict(q,resolution='最终类别已有明确型号字面依据'));continue
        if code in ('parameter_association_unverified','parameter_target_incompatible') and (q.get('page'),canonical(raw)) in resolved_raw|conflict_raw:
            resolved.append(dict(q,resolution='已按最终唯一归属或保护功能矛盾重新核对'));continue
        if code=='native_combined_entities' and raw:
            spans=[s for p in manifest.get('native_pages',{}).values() for s in p.get('spans',[]) if label_key(s['text'])==label_key(raw)]
            verified={x.get('native_ref') for x in items if x.get('symbol_observed',True)}
            if spans and all(s['id'] in verified for s in spans):
                resolved.append(dict(q,resolution='首轮合并读法已按独立原生位置复核'));continue
        if code in ('source_label_unplaced','instance_count_pending') and raw:
            found=[x for x in items if label_key(x['raw'])==label_key(raw) and x.get('symbol_observed',True)]
            if not found and decode(raw)['kind']=='cable':
                expected=decode(raw)['explicit_parameters']
                if expected.get('category_raw') and expected.get('twisted_pair_type'):
                    found=[x for x in items if x['category']=='cable' and x.get('symbol_observed',True) and
                           all(decode(x['raw'])['explicit_parameters'].get(k)==expected[k] for k in ('category_raw','twisted_pair_type'))]
            if found:
                if code=='source_label_unplaced':resolved.append(dict(q,resolution='最终清单已有该原文位置'));continue
                spans=[s for p in manifest.get('native_pages',{}).values() for s in p.get('spans',[]) if label_key(s['text'])==label_key(raw)]
                if spans and len({x.get('native_ref') for x in found})==len(spans):resolved.append(dict(q,resolution='独立原生位置已对齐；不作为采购数量'));continue
        active.append(q)
    # Explain pending binding only when competing valid outgoing candidates remain.
    for item in items:
        final_params=dict(item['literal_parameters']);final_params.update(item['associated_parameters'])
        final_params={k:v for k,v in final_params.items() if parameter_owner_allowed(k,item['category'])}
        item['selection_parameters']={} if item['category']=='unknown' else final_params
        item.update(explain(item['raw'],item['category'],final_params))
        if item.get('symbol_observed') is False:item['meaning_cn']='原文已定位，独立符号未核实；'+item['meaning_cn']
        options=item.get('candidate_circuit_ids',[])
        if item.get('circuit_id') is None and len(options)>1:
            active.append(problem('circuit_binding_unresolved','器件仍对应多个有效出线候选，需核对实际导线关系',page=item['page'],item_id=item['id']))
    unique={}
    for q in active:
        key=(q['code'],q.get('item_id'),q.get('field'),q.get('page'),q.get('marker_raw'),q.get('message'))
        if q['code']=='protective_function_conflict':key=(q['code'],q.get('item_id'),q.get('field'))
        unique.setdefault(key,q)
    result['issues']=list(unique.values());result['resolved_checks']=resolved
    result['validation_audit']=audit
    result['parameter_rejections']=rejected
    summary=result.setdefault('summary',{})
    summary.update(workflow_version='34.0',evidence_contract_version='34.0',candidate_objects=len(items),
                   circuits=len(circuits),cable_candidates=sum(x.get('category')=='cable' for x in items),
                   document_annotations=len(notes),pending_checks=len(result['issues']),resolved_checks_count=len(resolved),
                   excluded_connection_labels=len(excluded),non_procurement_symbol_marks=len(removed_ids),
                   unbound_cable_candidates=sum(x.get('category')=='cable' and not x.get('circuit_id') for x in items),
                   status='needs_review' if result['issues'] else 'no_rule_issue_detected',recognition_accuracy_verified=False)
    errors=[]
    for x in items:
        if any(not parameter_owner_allowed(k,x['category']) for k in x.get('selection_parameters',{})):errors.append('器件参数归属不一致')
    for a in circuits:
        expected=[x['id'] for x in items if x.get('circuit_id')==a['id']]
        if a['members']!=expected:errors.append('回路成员与最终清单不一致')
    summary['validation_errors']=list(dict.fromkeys(errors))
    result['schema_version']='pdf-spatial-circuit/34.0'
    return result
