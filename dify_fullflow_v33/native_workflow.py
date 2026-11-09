"""Native text identities support visual observations; text alone never proves a symbol."""
import base64
import json
import re
import zlib
from collections import Counter
from spatial import *
from evidence_contract import finalize_evidence
from vector_symbols import apply_vector_transformers, transformer_vector_proofs


def native_seed(span):
    decoded=decode(span['text']);kind=decoded['kind'];p=decoded['explicit_parameters']
    if kind in ('device_model','unclassified_model','cable','voltage_ratio','function_label'):return True
    if kind=='power_marking' and p.get('current_a') is not None:return True
    if kind=='device_tag' and re.fullmatch(r'K[AMT]\d*|FU\d*|SPD\d*',canonical(span['text'])):return True
    return False


def native_manifest(tool_json,images,document_text,notes,native_pages,native_status,native_message):
    value=prepare_jobs(tool_json,images,document_text,notes)
    if value['valid']!='YES':return value
    packet=uncompress(value['manifest_compact']);pages={}
    for text in native_pages:
        page=uncompress(text);pages[str(page['page'])]=page
    packet['native_pages']=pages;packet['native_status']=native_status;packet['native_message']=native_message
    encoded=base64.b64encode(zlib.compress(j(packet).encode())).decode()
    if len(encoded)>55000:raise ValueError('原生文字定位超过项目预算，请按柜组拆分；未截断原文')
    value['manifest_compact']=encoded
    return value


def native_context(job_json,manifest_compact):
    job=json.loads(job_json);manifest=uncompress(manifest_compact)
    page=manifest.get('native_pages',{}).get(str(job['page']),{})
    job['native_page_compact']=base64.b64encode(zlib.compress(j(page).encode())).decode()
    return {'file_serial':str(job['file_serial']),'mode':job['mode'],'context_json':j(job),'job_json':job_json}


def native_visible(ctx):
    rect=ctx.get('crop_audit',{}).get('actual_rect',ctx['rect']);out=[]
    page=uncompress(ctx['native_page_compact']) if ctx.get('native_page_compact') else {}
    for span in page.get('spans',[]):
        b=span['bbox'];intersection=max(0,min(b[2],rect[2])-max(b[0],rect[0]))*max(0,min(b[3],rect[3])-max(b[1],rect[1]))
        if intersection/max(1e-8,area(b))<0.7:continue
        local=[(b[0]-rect[0])*1000/(rect[2]-rect[0]),(b[1]-rect[1])*1000/(rect[3]-rect[1]),
               (b[2]-rect[0])*1000/(rect[2]-rect[0]),(b[3]-rect[1])*1000/(rect[3]-rect[1])]
        out.append(dict(span,local_bbox=[min(1000,max(0,v)) for v in local]))
    return out


def native_request(context_json):
    ctx=json.loads(context_json)
    request={'mode':ctx['mode'],'page':ctx['page'],'view_id':ctx['view_id'],
             'coordinate_system':'当前图片左上0,0，右下1000,1000。native_texts的bbox也已转换为当前图片局部坐标。',
             'native_texts':[{'id':s['id'],'text':s['text'],'bbox':[round(v,2) for v in s['local_bbox']],'selection_label_candidate':native_seed(s)} for s in native_visible(ctx)],
             'focus_native_ids':ctx.get('focus_native_ids',[])}
    if ctx['mode']=='repair':request['instruction']='只复核focus_native_ids对应的独立符号、完整型号和所属回路。电压比与旁边断路器必须按各自原生ID分列。看不清符号时明确未核实，不按出现次数补造器件。'
    text=j(request)
    if len(text)>55000:raise ValueError('当前局部原生文字过多，请分柜；未截断原文')
    return {'request_json':text}


def native_parse(raw,context_json):
    ctx=json.loads(context_json);visible={s['id']:s for s in native_visible(ctx)}
    try:data=read_object(raw)
    except (ValueError,TypeError):return parse_view(raw,context_json)
    chosen={};extra=[];rect=ctx.get('crop_audit',{}).get('actual_rect',ctx['rect'])
    for index,obj in enumerate(data.get('objects',[])):
        if not isinstance(obj,dict) or not isinstance(obj.get('raw'),str):continue
        refs=obj.get('native_refs',obj.get('native_id',[]));refs=[refs] if isinstance(refs,str) else refs
        refs=[r for r in refs if r in visible] if isinstance(refs,list) else []
        if not refs:
            matches=[s['id'] for s in visible.values() if label_key(s['text'])==label_key(obj['raw'])]
            focused=[r for r in matches if r in ctx.get('focus_native_ids',[])]
            if len(focused)==1:matches=focused
            if len(matches)==1:refs=matches
        primary=[r for r in refs if native_seed(visible[r])]
        if len(primary)>1:
            # Never invent two symbols by splitting a combined text box.
            obj['bbox']=None;extra.append(problem('native_combined_entities','一个视觉对象引用了多个独立型号/电压比位置，已转入定点复核：'+obj['raw']))
            continue
        if len(refs)==1 or len(primary)==1:
            ref=primary[0] if primary else refs[0];span=visible[ref]
            model_key,native_key=label_key(obj['raw']),label_key(span['text'])
            if model_key!=native_key and model_key not in native_key and native_key not in model_key:
                obj['_visual_raw_conflict']=obj['raw']
            # A wrong reading is corrected from the actual glyph map, not model spelling.
            obj['raw']=span['text'];obj['bbox']=span['local_bbox'];chosen[obj.get('id','')]=(ref,span)
            if decode(span['text'])['kind']=='voltage_ratio':
                # A voltage ratio alone remains an annotation unless a transformer symbol is observed.
                if obj.get('category')!='transformer' or not obj.get('symbol'):
                    obj['kind']='parameter';obj['category']='unknown'
            if ctx.get('focus_native_ids') and primary and ref not in ctx['focus_native_ids']:
                obj['kind']='note';obj['category']='unknown'
    for anchor in data.get('anchors',[]):
        if not isinstance(anchor,dict):continue
        ref=anchor.get('native_ref')
        if ref not in visible:
            options=[s['id'] for s in visible.values() if canonical(s['text'])==canonical(anchor.get('tag',''))]
            ref=options[0] if len(options)==1 else None
        if ref:
            span=visible[ref];anchor['tag']=span['text'];anchor['bbox']=span['local_bbox'];anchor['_native_ref']=ref
    result=parse_view(j(data),context_json);packet=json.loads(result['packet_json'])
    packet['job'].pop('native_page_compact',None)
    for row in packet.get('observations',[])+packet.get('quarantined_observations',[]):
        if row['local_id'] in chosen:
            ref,span=chosen[row['local_id']];row['native_ref']=ref;row['bbox']=span['bbox'];row['position_source']='native_pdf_text'
            original=next((o for o in data['objects'] if o.get('id')==row['local_id']),{})
            if original.get('_visual_raw_conflict'):row['visual_raw_conflict']=original['_visual_raw_conflict']
    for row in packet.get('anchors',[]):
        # Match by local ID rather than list order, including invalid/duplicate anchor cases.
        match=next((a for a in data['anchors'] if a.get('id')==row['local_id'] and a.get('_native_ref')),None)
        if match:row['native_ref']=match['_native_ref'];row['bbox']=visible[match['_native_ref']]['bbox']
    packet['issues'].extend(extra)
    return {'packet_json':j(packet)}


def native_repairs(packets,manifest_compact):
    manifest=uncompress(manifest_compact);records=[json.loads(p) for p in packets];jobs=[]
    for image in manifest['images']:
        number=image['page_number'];page=manifest.get('native_pages',{}).get(str(number),{})
        candidates=[s for s in page.get('spans',[]) if native_seed(s)]
        vector_refs={p['ratio_ref'] for p in transformer_vector_proofs(page)}
        observed=vector_refs|{o.get('native_ref') for p in records if p['job']['page']==number and p['job']['mode']!='overview'
                  for o in p.get('observations',[]) if o['kind'] in ('device','cable') and o.get('symbol')}
        missing=[s for s in candidates if s['id'] not in observed]
        if not candidates:
            # Image-only fallback gets a full-content check, not fabricated native coordinates.
            missing=[{'id':'IMAGE_ONLY','bbox':[0,0,1000,1000]}]
        if len(missing)>24:raise ValueError('本页有超过24处未完成符号复核，需按子图拆分；未忽略后续位置')
        xs=[s['bbox'][i] for s in page.get('spans',[]) for i in (0,2)]
        for index,span in enumerate(missing,1):
            b=span['bbox'];pad=max(20,min(65,(b[3]-b[1])*3.5))
            rect=[max(0,min(xs)-20) if xs else 0,max(0,b[1]-pad),min(1000,max(xs)+20) if xs else 1000,min(1000,b[3]+pad)]
            if span['id']=='IMAGE_ONLY':rect=[0,0,1000,1000]
            jobs.append(j({'job_id':'P%d-F%d'%(number,index),'page':number,'file_serial':manifest['uploaded_names'].index(image['filename'])+1,
                           'image_name':image['filename'],'view_id':'F%d'%index,'mode':'repair','rect':rect,'absolute_rect':True,
                           'focus_native_ids':[] if span['id']=='IMAGE_ONLY' else [span['id']]}))
    return {'jobs':jobs}


def native_circuit_geometry(page,drawings):
    """Printed IDs and wire-end lanes; no ordinal renumbering or load-name guessing."""
    spans=page.get('spans',[]);segments=page.get('straight_segments',[]);anchors=[]
    for span in spans:
        if decode(span['text'])['kind']!='circuit_tag':continue
        box=span['bbox'];pt=center(box);options=[]
        for line in segments:
            a,b=line[:2],line[2:];dx,dy=abs(a[0]-b[0]),abs(a[1]-b[1])
            if dx>=40 and dy<0.2:
                distance=min(abs(a[0]-pt[0]),abs(b[0]-pt[0]));axis=abs(a[1]-box[3])
                if distance<15 and axis<max(12,box[3]-box[1]):options.append((distance+axis,'horizontal',line))
            elif dy>=40 and dx<0.2:
                distance=min(abs(a[1]-pt[1]),abs(b[1]-pt[1]));axis=abs(a[0]-box[2])
                if distance<15 and axis<max(12,box[2]-box[0]):options.append((distance+axis,'vertical',line))
        if not options:
            anchor={'id':'NC-'+span['id'],'native_ref':span['id'],'tag':span['text'],'raw':span['text'],'kind':'circuit','page':page['page'],
                    'bbox':box,'direction':'unknown','wire_segment':None,'phase':None,'load':None,'load_bbox':None,'members':[],
                    'load_position_checked':False,'observations':[],'view_ids':['native_pdf']}
            anchor['drawing_id']=assign_drawing(anchor,drawings);anchors.append(anchor)
            continue
        _,direction,line=min(options,key=lambda x:x[0])
        anchor={'id':'NC-'+span['id'],'native_ref':span['id'],'tag':span['text'],'raw':span['text'],'kind':'circuit','page':page['page'],
                'bbox':box,'direction':direction,'wire_segment':line,'phase':None,'load':None,'load_bbox':None,'members':[],
                'load_position_checked':False,'observations':[],'view_ids':['native_pdf']}
        along=0 if direction=='horizontal' else 1
        endpoint=min((line[along],line[along+2]),key=lambda v:abs(v-pt[along]))
        anchor['label_end']=1 if endpoint==max(line[along],line[along+2]) else -1
        anchor['tip_coordinate']=endpoint
        anchor['drawing_id']=assign_drawing(anchor,drawings);anchors.append(anchor)
    for anchor in anchors:
        if anchor['drawing_id'] is None or not anchor.get('wire_segment'):continue
        axis=1 if anchor['direction']=='horizontal' else 0;along=1-axis;line=anchor['wire_segment'];wire=line[axis]
        neighbors=[a for a in anchors if a is not anchor and a['drawing_id']==anchor['drawing_id'] and a['direction']==anchor['direction'] and a.get('wire_segment')]
        distances=[abs(a['wire_segment'][axis]-wire) for a in neighbors if abs(a['wire_segment'][axis]-wire)>1]
        tolerance=min(distances)*0.45 if distances else 18
        tip=anchor['tip_coordinate'];side=anchor['label_end'];near=[]
        for span in spans:
            box=span['bbox'];pt=center(box);gap=box[along]-tip if side==1 else tip-box[along+2]
            if not 0<gap<80 or abs(pt[axis]-wire)>tolerance:continue
            if native_seed(span) or decode(span['text'])['kind'] in ('circuit_tag','phase','calculation','apparent_power','residual_current'):continue
            if span['text'].strip() in ('I/O','N','PE'):continue
            # The label must be closest to this wire end, not the next printed circuit.
            if any(abs(pt[axis]-a['wire_segment'][axis])<abs(pt[axis]-wire) for a in neighbors):continue
            near.append(span)
        if near:
            near.sort(key=lambda s:(round(s['bbox'][axis]/5),s['bbox'][along]))
            anchor['load']=' '.join(s['text'] for s in near)
            anchor['load_bbox']=[min(s['bbox'][0] for s in near),min(s['bbox'][1] for s in near),max(s['bbox'][2] for s in near),max(s['bbox'][3] for s in near)]
            anchor['load_native_refs']=[s['id'] for s in near];anchor['load_position_checked']=True
    return anchors


def native_branch_candidate(item,anchors,page):
    if not item.get('native_ref'):return None
    if item.get('drawing_id') is None:return None
    if decode(item['raw'])['kind'] in ('function_label','device_tag'):return None
    options=[];pt=center(item['bbox'])
    for a in anchors:
        if not a.get('wire_segment') or a['direction'] not in ('horizontal','vertical'):continue
        if a['page']!=item['page'] or a.get('drawing_id')!=item.get('drawing_id'):continue
        axis=1 if a['direction']=='horizontal' else 0;along=1-axis;wire=a['wire_segment'];row=wire[axis]
        neighbors=[v for v in anchors if v['page']==a['page'] and v.get('drawing_id')==a.get('drawing_id') and v['direction']==a['direction'] and v['id']!=a['id'] and v.get('wire_segment')]
        spacing=[abs(v['wire_segment'][axis]-row) for v in neighbors if abs(v['wire_segment'][axis]-row)>1]
        tolerance=min(18,min(spacing)*0.45) if spacing else 15
        if abs(pt[axis]-row)>tolerance:continue
        start,end=sorted([wire[along],wire[along+2]])
        side=a.get('label_end',1)
        # The distribution bus must span at least three numbered branches.
        buses=[]
        for line in page.get('straight_segments',[]):
            on_source_side=line[along]<start if side==1 else line[along]>end
            if abs(line[along]-line[along+2])<.2 and on_source_side and min(line[axis],line[axis+2])<=row<=max(line[axis],line[axis+2]):
                crossed=sum(min(line[axis],line[axis+2])<=v['wire_segment'][axis]<=max(line[axis],line[axis+2]) for v in [a]+neighbors)
                if crossed>=3:buses.append(line[along])
        left,right=(max(buses) if buses else start-15,end) if side==1 else (start,min(buses) if buses else end+15)
        if not left<=pt[along]<=right:continue
        options.append(a)
    return options[0] if len(options)==1 else None


def collect_native(packets,repair_packets,manifest_compact):
    manifest=uncompress(manifest_compact);records=[json.loads(v) for v in packets+repair_packets]
    # All views retain observations; no whole-image inventory can erase a native identity.
    value=collect_spatial([j(v) for v in records],manifest_compact)
    result=json.loads(value['result_json'])
    if result.get('details_in'):
        joined={'schema_version':result['schema_version'],'summary':result['summary']}
        for part in value['result_pages']:
            for k,v in json.loads(part).items():
                if isinstance(v,list):joined.setdefault(k,[]).extend(v)
        result=joined
    for key in ('items','issues','drawings','circuits','document_annotations','diagnostics','quarantined_observations','validation_audit','connection_annotations','resolved_checks'):
        result.setdefault(key,[])
    items=result.get('items',[]);issues=result['issues'];drawings=result['drawings']
    # Visual-only readings matching native labels without an exact native identity cannot multiply entities.
    native_keys={(p['page'],label_key(s['text'])) for p in manifest.get('native_pages',{}).values() for s in p.get('spans',[]) if native_seed(s)}
    kept=[]
    for x in items:
        page=manifest.get('native_pages',{}).get(str(x['page']),{})
        key=label_key(x['raw']);reason=None
        if canonical(x['raw']) in ('I/O','IO','L1','L2','L3','N','PE'):reason='port_or_phase_not_purchase_entity'
        if page.get('status')=='available' and not x.get('native_ref'):
            references=[label_key(s['text']) for s in page.get('spans',[]) if native_seed(s)]
            if native_seed({'text':x['raw']}) and (x['page'],key) in native_keys:reason='ambiguous_visual_duplicate_of_native_text'
            elif len(key)>=3 and any(v.startswith(key) and v!=key for v in references):reason='native_partial_reading'
            elif re.search(r'\d+(?:\.\d+)?/\d+(?:\.\d+)?V',key) and re.search(r'MCCB|MCB|RCB',key):reason='combined_transformer_breaker_reading'
            elif re.fullmatch(r'(?:MCB|MCCB|RCB|RCCB|RCBO)(?:[-/][A-Z0-9]*)?',key):reason='clipped_protection_label'
        if reason:
            result['quarantined_observations'].extend(dict(o,quarantine_reason=reason) for o in x['observations'])
        else:kept.append(x)
    items=kept;result['items']=items
    for item in items:
        conflicts=sorted({o['visual_raw_conflict'] for o in item.get('observations',[]) if o.get('visual_raw_conflict')})
        if conflicts:
            issues.append(problem('native_visual_text_conflict','原生文字“%s”与视觉读法%s不一致，请对照原图核对；未用视觉读法覆盖原文'%(item['raw'],'、'.join(conflicts)),page=item['page'],item_id=item['id']))
    for item in items:
        page=manifest.get('native_pages',{}).get(str(item['page']),{})
        if page.get('status')!='available':
            issues.append(problem('native_position_unavailable','识图待复核：本页没有完整原生文字定位，当前原文和位置来自视觉识别，需核对误读与重复项',page=item['page'],item_id=item['id']))
    native_anchors=[];missing=[]
    for key,page in manifest.get('native_pages',{}).items():
        if page.get('status') not in ('available','partial'):continue
        page_anchors=native_circuit_geometry(page,drawings);native_anchors.extend(page_anchors)
        for span in page['spans']:
            if not re.fullmatch(r'L[123](?:[~～-]L?[123])?',canonical(span['text'])):continue
            candidate=dict(span,raw=span['text'],page=page['page'],native_ref=span['id'])
            candidate['drawing_id']=assign_drawing(candidate,drawings)
            a=native_branch_candidate(candidate,page_anchors,page)
            if a:
                phase=canonical(span['text'])
                if a['phase'] in (None,phase):a['phase']=phase
                else:a['phase']=None;issues.append(problem('phase_conflict','同一支路的相别标注不一致，未自动采用',page=page['page']))
        for item in items:
            if item['page']!=page['page']:continue
            a=native_branch_candidate(item,page_anchors,page)
            if a:
                item.update(circuit_id=a['id'],circuit=a['tag'],load=a['load'],binding_status='candidate_geometry_checked',
                            binding_evidence='native printed tag, wire endpoint, distribution bus and text lane')
                a['members'].append(item['id'])
            elif page_anchors:
                item.update(circuit_id=None,circuit=None,load=None,binding_status='unbound')
        observed={x.get('native_ref') for x in items if x['page']==page['page']}
        # A VA label may be linked only to one nearby, visually observed transformer.
        for span in page['spans']:
            rating=decode(span['text'])
            if rating['kind']!='apparent_power':continue
            center_va=center(span['bbox'])
            transformers=[x for x in items if x['page']==page['page'] and x['category']=='transformer' and
                x.get('native_ref') and abs(center(x['bbox'])[0]-center_va[0])<max(15,(x['bbox'][2]-x['bbox'][0])*.7) and
                abs(center(x['bbox'])[1]-center_va[1])<max(15,(span['bbox'][3]-span['bbox'][1])*3)]
            if len(transformers)==1:
                target=transformers[0];params=target.setdefault('associated_parameters',{})
                va=rating['explicit_parameters']['apparent_power_va'];old=params.get('apparent_power_va')
                if old in (None,va):params['apparent_power_va']=va
                else:issues.append(problem('parameter_conflict','同一变压器的VA容量关联有冲突，未覆盖原值',page=page['page'],item_id=target['id']))
        for span in page['spans']:
            if native_seed(span) and span['id'] not in observed:
                missing.append(dict(span,page=page['page']))
        # Preserve cabinet identifiers found in text inside each overview region.
        for d in drawings:
            if d['page']!=page['page']:continue
            candidates=[]
            for s in page['spans']:
                if assign_drawing(dict(s,page=page['page']),[d])!=d['id']:continue
                text=canonical(s['text'])
                if re.fullmatch(r'(?:\d{1,3})?[A-Z]{0,3}(?:AP|AL|AT|PDT|PD|XL|XM|PZ)[A-Z]?\d{1,4}|(?:AP|AL|AT|XL|XM|PZ)\d{1,4}',text):candidates.append(s['text'])
            if candidates:d['cabinet_tags']=list(dict.fromkeys(candidates))
        # Native technical requirements remain visible even if a model omits them.
        existing_refs={n.get('native_ref') for n in result['document_annotations']}
        for span in page['spans']:
            if span['id'] not in existing_refs and re.search(r'过载|过负载|报警|不切断|监测|故障|运行时间',span['text']):
                result['document_annotations'].append(dict(raw=span['text'],kind='note',page=page['page'],bbox=span['bbox'],native_ref=span['id']))
    if native_anchors:
        replaced_pages={a['page'] for a in native_anchors}
        native_ids={a['native_ref'] for a in native_anchors}
        remaining=[]
        for a in result['circuits']:
            if a['page'] not in replaced_pages:remaining.append(a)
            elif a.get('native_ref') not in native_ids:
                a['load_candidate']=a.get('load');a['load']=None;a['load_position_checked']=False
                remaining.append(a)
        result['circuits']=remaining+native_anchors
    # Correct count hints compare against independent native text positions, never purchase quantities.
    unresolved=[]
    for span in missing:
        unresolved.append({'raw':span['text'],'page':span['page'],'bbox':span['bbox'],'native_ref':span['id'],
                           'quarantine_reason':'native_label_symbol_not_verified','kind':'unknown','job_id':'native_pdf'})
        identity='U-'+span['id']
        candidate={'id':identity,'raw':span['text'],'page':span['page'],'bbox':span['bbox'],'native_ref':span['id'],
                   'category':'unknown','kind':'unknown','literal_parameters':{},'associated_parameters':{},
                   'symbol_observed':False,'circuit':None,'circuit_id':None,'load':None,'binding_status':'unbound',
                   'meaning_cn':'原生文字已定位；独立器件符号未核实，尚未确认为采购实体',
                   'position_source':'native_pdf_text','observations':[],'view_ids':[]}
        candidate['drawing_id']=assign_drawing(candidate,drawings);items.append(candidate)
        issues.append(problem('native_symbol_unresolved','原生文字位置已找到，但独立符号尚未核实；请核对该处是器件、标注还是图例：'+span['text'],page=span['page'],item_id=identity))
    result['quarantined_observations'].extend(unresolved)
    # Remove stale model-coordinate warnings only where exact native branch evidence replaced them.
    native_bound={x['id'] for x in items if x.get('binding_evidence')}
    obsolete={'binding_geometry_conflict','binding_unverified','multiple_circuit_owners','cable_scope_unconfirmed','load_position_unconfirmed','load_label_conflict','view_missing'}
    issues[:]=[q for q in issues if not(q['code'] in obsolete and (q.get('item_id') in native_bound or q['code'] in ('load_position_unconfirmed','load_label_conflict','view_missing')))]
    result['schema_version']='pdf-spatial-circuit/31.0'
    result['summary'].update(workflow_version='31.0',candidate_objects=len(items),circuits=len(result['circuits']),pending_checks=len(issues),
        views_expected=7*manifest['page_count']+len(repair_packets),views_received=len(records),
        native_positioned_pages=[int(k) for k,p in manifest.get('native_pages',{}).items() if p.get('status')=='available'],
        native_missing_symbols=len(missing),targeted_repair_views=len(repair_packets),
        native_vector_coordinates_available=any(p.get('spans') for p in manifest.get('native_pages',{}).values()),
        native_status=manifest.get('native_status'),native_message=manifest.get('native_message'),
        recognition_accuracy_verified=False,inventory_basis='native_text_identity_and_local_symbol_evidence')
    result=apply_vector_transformers(result,manifest,native_branch_candidate)
    result=finalize_evidence(result,manifest)
    items=result['items'];issues=result['issues']
    # Bounded serialized chunks, preserving all list entries.
    parts=[];current={'schema_version':result['schema_version'],'summary':result['summary']}
    for key in ('drawings','items','circuits','document_annotations','issues','diagnostics','quarantined_observations','validation_audit','connection_annotations','resolved_checks'):
        for row in result.get(key,[]):
            size=len(j(row))
            if size>55000:raise ValueError('单条原生证据超过预算，未截断')
            if len(j(current))+size+100>58000:parts.append(j(current));current={'schema_version':result['schema_version']}
            current.setdefault(key,[]).append(row)
    parts.append(j(current));text=j(result)
    if len(text)>60000:text=j({'schema_version':result['schema_version'],'summary':result['summary'],'details_in':'result_pages'})
    return {'result_json':text,'result_pages':parts,'source_text':manifest.get('native_text',''),
            'report':'V33：原生文字位置与局部符号联合核对；%d项候选，%d处文字已定位但符号未核实。候选数量不是采购数量。'%(len(items),len(missing))}
