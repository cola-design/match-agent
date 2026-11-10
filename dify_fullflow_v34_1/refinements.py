"""Evidence reconciliation that preserves ambiguous controls and unresolved coverage."""
from copy import deepcopy
import re
from spatial import *


def normalize_named_controls(result):
    for x in result.get('items',[]):
        if x.get('symbol_observed') is False:continue
        if re.fullmatch(r'时间控制器|时控开关|时间控制开关',x['raw'].strip()) and x.get('category') in ('controller','unknown','timer'):
            if x['category']!='timer':
                result.setdefault('validation_audit',[]).append(dict(kind='named_function',item_id=x['id'],raw=x['raw'],message='按明确的时控功能名称分类；不据此推断额定电流、触点容量或采购数量'))
            x.update(category='timer',kind='device')
    return result


def supplement_class_markers(result,manifest):
    for page in manifest.get('native_pages',{}).values():
        classes=[s for s in page.get('spans',[]) if s['text'].strip().upper() in ('PC','CB')]
        grades=[s for s in page.get('spans',[]) if s['text'].strip()=='级']
        for label in classes:
            candidates=[]
            for grade in grades:
                a,b=label['bbox'],grade['bbox'];ac,bc=center(a),center(b)
                h=max(a[3]-a[1],b[3]-b[1]);w=max(a[2]-a[0],b[2]-b[0])
                horizontal=abs(ac[1]-bc[1])<h*.6 and min(abs(a[2]-b[0]),abs(b[2]-a[0]))<h*.6
                vertical=abs(ac[0]-bc[0])<w*.6 and min(abs(a[3]-b[1]),abs(b[3]-a[1]))<w*.6
                if horizontal or vertical:candidates.append(grade)
            if len(candidates)!=1:continue
            grade=candidates[0];identity=label['id']+'+'+grade['id']
            if any(n.get('native_ref')==identity for n in result.get('document_annotations',[])):continue
            box=[min(label['bbox'][0],grade['bbox'][0]),min(label['bbox'][1],grade['bbox'][1]),max(label['bbox'][2],grade['bbox'][2]),max(label['bbox'][3],grade['bbox'][3])]
            note={'id':'NATIVE-'+identity,'raw':label['text'].strip().upper()+'级','native_ref':identity,'native_refs':[label['id'],grade['id']],'page':page['page'],'bbox':box,'kind':'parameter'}
            note['drawing_id']=assign_drawing(note,result.get('drawings',[]))
            result.setdefault('document_annotations',[]).append(note)
    return result


def refine_controls(result,manifest,branch_finder):
    result=supplement_class_markers(normalize_named_controls(deepcopy(result)),manifest);items=result.get('items',[]);anchors=result.get('circuits',[])
    for x in items:
        if x.get('category')!='contactor' or x.get('symbol_observed') is False or x.get('circuit_id'):continue
        page=manifest.get('native_pages',{}).get(str(x['page']),{})
        a=branch_finder(x,anchors,page)
        if a:x.update(circuit_id=a['id'],circuit=a['tag'],binding_status='candidate_geometry_checked')
    for timer in items:
        if timer.get('category')!='timer' or timer.get('symbol_observed') is False or not timer.get('bbox'):continue
        possible=[]
        for contact in items:
            if contact.get('category')!='contactor' or contact.get('page')!=timer['page'] or contact.get('drawing_id')!=timer.get('drawing_id') or not contact.get('native_ref') or not contact.get('bbox'):continue
            a=next((a for a in anchors if a['id']==contact.get('circuit_id')),None)
            if not a or a.get('direction') not in ('horizontal','vertical'):continue
            across=1 if a['direction']=='horizontal' else 0;along=1-across
            tc,cc=center(timer['bbox']),center(contact['bbox'])
            height=timer['bbox'][across+2]-timer['bbox'][across]
            width=timer['bbox'][along+2]-timer['bbox'][along]
            if abs(tc[along]-cc[along])<=max(12,width*.7) and 0<abs(tc[across]-cc[across])<=max(30,min(50,height*3)):
                possible.append((contact,a))
        if len(possible)!=1:continue
        contact,a=possible[0]
        if timer.get('circuit_id') not in (None,a['id']):
            result.setdefault('issues',[]).append(problem('control_relation_conflict','时控功能与接触器关系和已有回路归属有冲突，请对照控制连接核对',page=timer['page'],item_id=timer['id']));continue
        timer.update(circuit_id=a['id'],circuit=a['tag'],binding_status='control_relation_candidate',control_target_id=contact['id'])
        result.setdefault('issues',[]).append(problem('control_relation_candidate','时控器与同位置接触器已关联到该支路候选，请核对实际控制关系及完整采购规格',page=timer['page'],item_id=timer['id']))
        result.setdefault('validation_audit',[]).append(dict(kind='control_relation',item_id=timer['id'],raw=timer['raw'],message='时控功能与相邻接触器位置关联为回路候选，未代替控制接线设计和采购数量确认'))
    return result


def reconcile_observations(result,manifest):
    resolved=result.setdefault('resolved_observations',[]);kept=[];items=result.get('items',[])
    for observation in result.get('quarantined_observations',[]):
        reason=observation.get('quarantine_reason');matches=[]
        if reason=='native_label_symbol_not_verified':
            matches=[x for x in items if x['page']==observation.get('page') and x.get('native_ref')==observation.get('native_ref') and x.get('symbol_observed',True)]
        elif reason=='ambiguous_visual_duplicate_of_native_text' and observation.get('bbox'):
            same=[x for x in items if x['page']==observation.get('page') and x.get('native_ref') and x.get('symbol_observed',True) and label_key(x['raw'])==label_key(observation.get('raw',''))]
            page=manifest.get('native_pages',{}).get(str(observation.get('page')),{})
            spans=[s for s in page.get('spans',[]) if label_key(s['text'])==label_key(observation.get('raw',''))]
            if len(same)==len(spans)==1 and max(abs(a-b) for a,b in zip(center(same[0]['bbox']),center(observation['bbox'])))<30:matches=same
        if len(matches)==1:
            resolved.append(dict(observation,resolution='最终已有唯一的原生位置与器件证据；该观测不再作为未解决漏项',resolved_item_id=matches[0]['id']))
        else:kept.append(observation)
    result['quarantined_observations']=kept
    return result
