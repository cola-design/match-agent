"""Conservative transformer rule using native cubic contours, labels and a wire.

No inference from a voltage ratio alone. Unsupported or ambiguous geometry
stays unresolved and is available for visual/human review.
"""
from copy import deepcopy
import math
from spatial import *


def physical_point(point,size):return [point[0]*size[0]/1000,point[1]*size[1]/1000]


def native_circle(path,size,spans):
    if path.get('paint') not in ('S','s','B','B*'):return None
    curves=path.get('curves',[])
    if len(curves)!=4 or any(len(c)!=4 for c in curves):return None
    points=[physical_point(p,size) for c in curves for p in c]
    bounds=[min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]
    w,h=bounds[2]-bounds[0],bounds[3]-bounds[1]
    if not 2<=min(w,h)<=max(w,h)<=60 or abs(w-h)>max(w,h)*.12:return None
    cx,cy=center(bounds);radius=(w+h)/4
    for curve in curves:
        p=[physical_point(x,size) for x in curve]
        if abs(math.dist(p[0],[cx,cy])-radius)>radius*.08 or abs(math.dist(p[3],[cx,cy])-radius)>radius*.08:return None
        if not 1.2*radius<math.dist(p[0],p[3])<1.6*radius:return None
        for endpoint,control in ((p[0],p[1]),(p[3],p[2])):
            tangent=[control[0]-endpoint[0],control[1]-endpoint[1]];length=math.hypot(*tangent)
            if not .4*radius<length<.7*radius:return None
            radial=[endpoint[0]-cx,endpoint[1]-cy]
            if abs(sum(a*b for a,b in zip(radial,tangent)))/(radius*length)>.12:return None
    norm_center=[cx*1000/size[0],cy*1000/size[1]]
    # Do not mistake outlined font glyphs such as '00' for winding symbols.
    if any(s['bbox'][0]<=norm_center[0]<=s['bbox'][2] and s['bbox'][1]<=norm_center[1]<=s['bbox'][3] for s in spans):return None
    return {'center':[cx,cy],'diameter':2*radius,'bounds':bounds,'native_bounds':[bounds[0]*1000/size[0],bounds[1]*1000/size[1],bounds[2]*1000/size[0],bounds[3]*1000/size[1]]}


def transformer_vector_proofs(page):
    size=page.get('display_size_points')
    if not size or len(size)!=2 or min(size)<=0:return []
    spans=page.get('spans',[]);circles=[];seen=set()
    for path in page.get('closed_cubic_paths',[]):
        circle=native_circle(path,size,spans)
        if circle:
            key=tuple(round(v,2) for v in circle['bounds'])
            if key not in seen:circles.append(circle);seen.add(key)
    ratios=[s for s in spans if decode(s['text'])['kind']=='voltage_ratio']
    capacities=[s for s in spans if decode(s['text'])['kind']=='apparent_power']
    proposals={}
    for i,left in enumerate(circles):
        for right in circles[i+1:]:
            d=(left['diameter']+right['diameter'])/2
            if abs(left['diameter']-right['diameter'])>d*.15:continue
            delta=[abs(a-b) for a,b in zip(left['center'],right['center'])];distance=math.hypot(*delta)
            if not .4*d<distance<.95*d or min(delta)>.2*d:continue
            axis=0 if delta[0]>delta[1] else 1;other=1-axis
            middle=[(a+b)/2 for a,b in zip(left['center'],right['center'])]
            bounds=[min(left['bounds'][0],right['bounds'][0]),min(left['bounds'][1],right['bounds'][1]),max(left['bounds'][2],right['bounds'][2]),max(left['bounds'][3],right['bounds'][3])]
            wires=[]
            for raw in page.get('stroke_segments',[]):
                a,b=physical_point(raw[:2],size),physical_point(raw[2:],size)
                if abs(a[other]-b[other])>.1 or abs(a[other]-middle[other])>.2*d:continue
                ends=sorted([a[axis],b[axis]])
                if ends[0]<=bounds[axis+2]+.3*d and ends[1]>=bounds[axis]-.3*d and (ends[0]<bounds[axis] or ends[1]>bounds[axis+2]):wires.append(raw)
            if not wires:continue
            nearby_ratios=[s for s in ratios if math.dist(physical_point(center(s['bbox']),size),middle)<3*d and abs(physical_point(center(s['bbox']),size)[axis]-middle[axis])<1.5*d]
            nearby_va=[s for s in capacities if math.dist(physical_point(center(s['bbox']),size),middle)<3*d and abs(physical_point(center(s['bbox']),size)[axis]-middle[axis])<1.5*d]
            if len(nearby_ratios)!=1 or len(nearby_va)!=1:continue
            ratio,capacity=nearby_ratios[0],nearby_va[0]
            if (physical_point(center(ratio['bbox']),size)[other]-middle[other])*(physical_point(center(capacity['bbox']),size)[other]-middle[other])>=0:continue
            proof={'ratio_ref':ratio['id'],'capacity_ref':capacity['id'],'ratio_text':ratio['text'],'capacity_text':capacity['text'],
                   'page':page['page'],'ratio_bbox':ratio['bbox'],'coil_bounds':[left['native_bounds'],right['native_bounds']],
                   'wire_segments':wires,'evidence_type':'native_two_closed_cubic_windings_with_wire_ratio_and_VA'}
            proposals.setdefault(ratio['id'],[]).append(proof)
    return [rows[0] for rows in proposals.values() if len(rows)==1]


def apply_vector_transformers(result,manifest,branch_finder):
    result=deepcopy(result);items=result.setdefault('items',[])
    for page in manifest.get('native_pages',{}).values():
        for proof in transformer_vector_proofs(page):
            matches=[x for x in items if x.get('native_ref')==proof['ratio_ref'] and x['page']==page['page']]
            if len(matches)>1:continue
            if matches:target=matches[0]
            else:
                target={'id':'VECTOR-'+proof['ratio_ref'],'raw':proof['ratio_text'],'page':page['page'],'bbox':proof['ratio_bbox'],
                        'native_ref':proof['ratio_ref'],'observations':[],'view_ids':[]}
                target['drawing_id']=assign_drawing(target,result.get('drawings',[]));items.append(target)
            associated=dict(target.get('associated_parameters',{}))
            capacity=decode(proof['capacity_text'])['explicit_parameters']['apparent_power_va']
            if associated.get('apparent_power_va') not in (None,capacity):
                result.setdefault('issues',[]).append(problem('parameter_conflict','原生容量文字与已有视觉容量读法不一致，请对照原图核对',page=page['page'],item_id=target['id']))
            associated['apparent_power_va']=capacity
            target.update(kind='device',category='transformer',symbol_observed=True,
                          literal_parameters=decode(proof['ratio_text'])['explicit_parameters'],
                          associated_parameters=associated,
                          symbol_evidence=proof,position_source='native_pdf_text_and_vector_symbol')
            a=branch_finder(target,result.get('circuits',[]),page)
            target.update(circuit_id=a['id'] if a else None,circuit=a['tag'] if a else None,
                          load=a.get('load') if a else None,binding_status='candidate_geometry_checked' if a else 'unbound')
            result.setdefault('validation_audit',[]).append({'kind':'native_vector_symbol','item_id':target['id'],'raw':target['raw'],
                'page':page['page'],'bbox':target['bbox'],'message':'已结合原生矢量双线圈、引线、电压比和VA容量核对为变压器候选，采购数量仍待人工确认'})
    result.setdefault('summary',{})['native_missing_symbols']=sum(x.get('symbol_observed') is False for x in items)
    result['summary']['native_vector_transformers']=sum(x.get('symbol_evidence',{}).get('evidence_type')=='native_two_closed_cubic_windings_with_wire_ratio_and_VA' for x in items)
    return result
