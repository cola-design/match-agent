
_v182_bom_payload_base=main
def main(markdown,selection_json):
    out=_v182_bom_payload_base(markdown,selection_json)
    context=wire_loads(selection_json)
    if context.get('capacity_blocked'):return out
    payload=json.loads(out['payload_json'])
    payload['output_filename']='BOM人工确认单_V18.2'
    cells=payload['cells']
    labels={'MATCHED':'参数可匹配','NEED_REVIEW':'待复核','MISSING':'未找到','NOT_RETRIEVED':'未召回'}
    number=4
    for item in context.get('items',[]):
        notes=list(dict.fromkeys(str(x) for x in (item.get('warnings') or [])+(item.get('mismatch_or_missing') or []) if x))
        alternatives=item.get('alternatives') or []
        if not notes and not alternatives and item.get('match_status')=='MATCHED':continue
        choices='\n'.join(str(row.get('material_name') or '')+' ['+str(row.get('material_code') or '')+']' for row in alternatives)
        row=[item.get('position_display') or item.get('symbol') or item.get('request_id'),item.get('shown_model'),
             labels.get(item.get('match_status'),item.get('match_status')),
             '\n'.join(notes) or '参数检查通过，待工程师确认项目适用性。',choices or '—']
        for column,value in enumerate(row,1):cells['选型说明!'+column_name(column)+str(number)]=safe(value)
        number+=1
    out['payload_json']=plain_json(payload)
    if len(out['payload_json'].encode('utf-8'))>65000:
        raise CapacityError('完整BOM及选型说明超过65KB传递预算，请按柜号分批；本次未生成部分确认单')
    out['summary']='已按实际条数生成确认表；参数自动换行并计算行高，核对原因与候选差异见“选型说明”。'
    return out
