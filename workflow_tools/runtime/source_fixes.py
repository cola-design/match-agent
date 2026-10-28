"""Embedded after the requirement validator; relies on its existing source-verification helpers."""


def explicit_source_units(evidence):
    units=set()
    for ev in evidence_leaves(evidence):
        if not is_anchored(ev):continue
        for _,unit in re.findall(r'(?<![A-Za-z\d.])([0-9]+(?:\.[0-9]+)?|[零一二两三四五六七八九十百千]+)\s*(台|只|个|件|套|根|支|组|块|颗|处|米|m)(?![A-Za-z0-9²])',fact_text(ev['text']),re.I):
            units.add('m' if unit in ('米','m','M') else unit)
        for unit in re.findall(r'(?:计量单位|单位)\s*[:：=]\s*(台|只|个|件|套|根|支|组|块|颗|处|米|m)(?![A-Za-z0-9²])',fact_text(ev['text']),re.I):
            units.add('m' if unit in ('米','m','M') else unit)
    return units


def restore_peer_protocol_evidence(peer,protocol_requirement):
    source=protocol_requirement.get('source_evidence') or {}
    verified=[ev for ev in evidence_leaves(source.get('protocol',[])) if is_anchored(ev)]
    target=peer.setdefault('source_evidence',{}).setdefault('protocol',[])
    for ev in verified:
        if ev not in target:target.append(copy.deepcopy(ev))
    if verified and peer.get('mapping_status')=='DRAWING_ONLY':peer['mapping_status']='MATCHED'


def preserve_source_units_and_structure(payload,drawing,protocol,issues):
    groups={g.get('scheme_group_id'):g for g in contract_rows(drawing.get('scheme_groups')) if isinstance(g,dict)}
    for row in contract_rows(payload.get('component_requirements')):
        if row.get('handling_mode') in ('EXCLUDED','SPACE_ONLY'):continue
        sources=row.get('source_evidence') or {}
        protocol_units=explicit_source_units(sources.get('protocol',[]))
        drawing_units=explicit_source_units(sources.get('drawing',[]))
        units=protocol_units | drawing_units
        if len(units)==1:
            row['unit']=next(iter(units));row.setdefault('requirements',{})['unit']=row['unit']
            row['unit_basis']='VERIFIED_DOCUMENT'
        elif len(units)>1:
            add_issue(issues,'BLOCKER','协议与图纸或同项引文的计量单位不一致：'+'、'.join(sorted(units)),row.get('scheme_group_id'),row.get('request_id'),stage='计量单位',blocks_retrieval=True)
            row['unit_basis']='CONFLICT'
        else:
            row['unit_basis']='UNVERIFIED'
            add_issue(issues,'BLOCKER','物料计量单位未在对应的已核验引文中明确，不能用默认台/个替代；请补充单位',row.get('scheme_group_id'),row.get('request_id'),stage='计量单位',blocks_retrieval=True)
        role=str(row.get('component_role') or '')+' '+str(row.get('category_path') or '')
        if not any(word in role for word in ('柜体','箱体')):continue
        params=row.setdefault('requirements',{});group=groups.get(row.get('scheme_group_id'),{})
        structural_evidence=[ev for ev in evidence_leaves(group.get('evidence',[])) if is_anchored(ev)]
        structural_evidence+= [ev for ev in evidence_leaves(sources.get('drawing',[])) if is_anchored(ev)]
        door_values=[]
        for ev in structural_evidence:
            for word,count in (('双门',2),('单门',1)):
                if word in fact_text(ev['text']):door_values.append((count,word,ev))
        if len({value[0] for value in door_values})==1:
            count,label,ev=door_values[0]
            if params.get('door_count') not in (None,'',count,str(count)):
                add_issue(issues,'BLOCKER','柜门数量与柜体已核验引文不一致',row.get('scheme_group_id'),row.get('request_id'),stage='柜体结构',blocks_retrieval=True)
            else:
                params['door_count']=count;params['door_type']=label
                fact={'field':'door_count','value':count,'source_text':ev['text'],'source':copy.deepcopy(ev)}
                if fact not in row.setdefault('parameter_facts',[]):row['parameter_facts'].append(fact)
        elif len({value[0] for value in door_values})>1:
            add_issue(issues,'BLOCKER','柜门形式引文含多个不同值，需确认适用配置',row.get('scheme_group_id'),row.get('request_id'),stage='柜体结构',blocks_retrieval=True)
    return payload
