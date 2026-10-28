import re
def v182_position(item,items,many_groups):
    def generated(value):return bool(re.fullmatch(r'[A-Za-z]+-[a-f0-9]{12,}',str(value or ''),re.I))
    parts=[]
    if many_groups:
        groups=list(dict.fromkeys(str(row.get('scheme_group_id') or '') for row in items))
        label=item.get('scheme_label')
        parts.append(str(label) if label else '方案'+str(groups.index(str(item.get('scheme_group_id') or ''))+1))
    symbol=clean(item.get('symbol'));circuit=clean(item.get('circuit_id'))
    if symbol:parts.append(symbol)
    if circuit and not generated(circuit) and circuit!=symbol:parts.append(circuit)
    return '/'.join(parts) or clean(item.get('component_role'))


def v182_requirement_display(text):
    text=str(text or '')
    text=re.sub(r'(?:方案组|回路)\s*[:：]\s*[A-Za-z]+-[a-f0-9]{12,}\s*[；;]?','',text,flags=re.I).strip('；; ')
    text=text.replace('door_count：','柜门数量：').replace('door_type：','柜门形式：').replace('protection_level：','防护等级：')
    for curve in ('B','C','D'):
        if '脱扣特性：'+curve in text or '脱扣曲线：'+curve in text:
            text=text.replace('；明确脱扣特性：'+curve+'型脱扣','')
    return '；'.join(dict.fromkeys(part.strip() for part in text.split('；') if part.strip()))


_v182_parameter_display=chinese_parameters
def chinese_parameters(value):
    return _v182_parameter_display(value).replace('door_count：','柜门数量：').replace('door_type：','柜门形式：')
