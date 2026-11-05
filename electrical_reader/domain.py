"""Conservative electrical annotation decoding. No catalogue lookup or design inference."""
import re
import unicodedata

VERSION = '0.1.0'
SOURCES = {
    'MCB': 'https://electrification.us.abb.com/back-to-basics/circuit-breaker-basics',
    'RCD': 'https://www.se.com/us/en/faqs/FA89258/',
    'WDZB': 'https://www.qrunning.com/products/view?id=1795&lang=cn',
}

CATEGORY_NAMES = {'unclassified':'保留原文，尚未分类','protective_device':'断路器/保护器件','wire_or_cable':'电线/电缆标注',
                  'phase':'相别','circuit_number':'回路编号','transfer_switch':'转换开关','voltage_ratio':'两侧电压',
                  'apparent_power':'视在功率','power_supply_marking':'电源参数','device_tag':'器件位号',
                  'calculation_marking':'计算参数','residual_current_marking':'剩余电流动作值'}


def parameter_text(p):
    names={'drawing_abbreviation':'原图缩写','pole_count':'极数','trip_curve':'脱扣曲线','rated_current_a':'额定电流(A)',
           'residual_operating_current_ma':'剩余电流动作值(mA)','trip_marking_raw':'原图脱扣标识','fire_marking_raw':'阻燃标识',
           'halogen_free':'无卤','low_smoke':'低烟','flame_retardant':'阻燃','combustion_performance_grade':'燃烧性能等级',
           'bundled_flame_retardant_category':'成束阻燃类别','fire_resistant_marked':'标有耐火要求','wire_family':'线缆类型',
           'family_meaning':'线型含义','conductor_spec_raw':'原图线数/截面积','phase_raw':'相别原文','voltage_pair_v':'两侧电压(V)',
           'apparent_power_va':'视在功率(VA)','power_type':'电源类型','voltage_v':'电压(V)','current_a':'电流(A)',
           'rated_current_marked_a':'标注电流(A)'}
    rows=[]
    for key,value in p.items():
        if key=='conductor_groups':value=' + '.join(str(x['count'])+'×'+str(x['cross_section_mm2'])+'mm²' for x in value)
        elif key=='containment':value='、'.join(x['code']+x['size_marking_raw']+'（'+x['meaning']+'）' for x in value)
        elif key=='installation_markings':value='、'.join(x['code']+'（'+x['common_meaning']+'）' for x in value)
        elif isinstance(value,bool):value='是' if value else '否'
        elif isinstance(value,list):value='/'.join(str(x) for x in value)
        rows.append({'conductor_groups':'规格组','containment':'导管/桥架','installation_markings':'常见敷设含义'}.get(key,names.get(key,key))+'='+str(value))
    return '；'.join(rows)


def normalized(text):
    text = unicodedata.normalize('NFKC', str(text)).strip()
    return re.sub(r'[‐‑‒–—−]', '-', text).replace('×', 'x').replace('＊', '*')


def decode(text):
    raw = str(text)
    s = normalized(raw)
    upper = s.upper()
    result = {'raw': raw, 'normalized': s, 'category': 'unclassified', 'meaning': '', 'parameters': {},
              'uncertainties': [], 'basis': '原图文字；规则解读不等于完成产品选型'}
    p = result['parameters']
    protection = re.search(r'(?<![A-Z0-9])(MCCB|MCB|RCBO|RCCB|RCB|RCD)(?=[\d\s/\-]|$)', upper)
    if protection:
        prefix = protection.group(1)
        names = {'MCCB': '塑壳断路器', 'MCB': '小型断路器', 'RCB': '剩余电流保护器件（保留图纸缩写RCB）',
                 'RCCB': '不带过电流保护的剩余电流动作断路器', 'RCBO': '带过电流保护的剩余电流动作断路器',
                 'RCD': '剩余电流保护器件统称'}
        result.update(category='protective_device', meaning=names[prefix])
        p['drawing_abbreviation'] = prefix
        poles = re.search(r'(?<!\d)([1-4]P(?:\+N)?)(?![A-Z0-9])', upper)
        if poles:p['pole_count'] = poles.group(1)
        curve = re.search(r'(?:^|[/\-\s])([BCD])\s*(\d+(?:\.\d+)?)\s*A(?=$|[/\s])', upper)
        if curve:p.update(trip_curve=curve.group(1), rated_current_a=float(curve.group(2)))
        else:
            currents = re.findall(r'(?<![\d.])(\d+(?:\.\d+)?)\s*A(?=$|[/\s])', upper)
            if len(currents) == 1:p['rated_current_a'] = float(currents[0])
        leak = re.search(r'(?<![\d.])(\d+(?:\.\d+)?)\s*MA(?=$|[/\s])', upper)
        if leak:p['residual_operating_current_ma'] = float(leak.group(1))
        if prefix == 'RCB':result['uncertainties'].append('RCB缩写不能单独确定RCCB/RCBO订货类别；结合图例、完整型号和器件资料核对')
        if prefix == 'MCB' and leak:result['uncertainties'].append('同一标注同时出现MCB和剩余电流值，保留原文，待确认保护类型；不自动改写为RCBO')
        if prefix == 'MCCB' and re.search(r'-MA\d', upper):
            p['trip_marking_raw'] = re.search(r'MA\d+(?:\.\d+)?A', upper).group()
            result['uncertainties'].append('MA为图纸脱扣标识；不得按MCB的B/C/D曲线解释，具体保护功能查原型号资料')
    elif re.search(r'(?<![A-Z0-9])(?:WDZ[A-D1]*N?|NHBV|ZR[BNC]*BV|ZNBV|BV[RV]?|RVV[P]?|UTP/CAT\d)', upper):
        result.update(category='wire_or_cable', meaning='电线、电缆或通信线标注')
        prefix = re.search(r'WDZB1|WDZBN|WDZB|WDZAN|WDZA|WDZCN|WDZC|WDZN|WDZ', upper)
        if prefix:
            p['fire_marking_raw'] = prefix.group()
            p['halogen_free'] = True;p['low_smoke'] = True;p['flame_retardant'] = True
            if prefix.group() == 'WDZB1':p['combustion_performance_grade'] = 'B1';result['meaning'] = '无卤低烟、燃烧性能B1级电线电缆标识'
            else:
                grade = re.search(r'WDZ([ABC])', prefix.group())
                if grade:p['bundled_flame_retardant_category'] = grade.group(1)
                if 'N' in prefix.group():p['fire_resistant_marked'] = True
                result['meaning'] = '无卤低烟阻燃'+(p.get('bundled_flame_retardant_category', '')+'类' if grade else '')+'电线电缆标识'
        family = re.search(r'(?:WDZ[A-Z0-9]*-)(BYJ|YJY|YJV|RYJS|RYJSP|RYJ|KYJY|KYJYP)(?=-|$)', upper)
        if family:
            p['wire_family'] = family.group(1)
            if family.group(1) == 'BYJ':p['family_meaning'] = '交联聚烯烃绝缘布电线；线数规格不直接解释成多芯电缆'
            elif family.group(1) == 'YJY':p['family_meaning'] = '交联聚乙烯绝缘、聚烯烃护套电力电缆；导体及电压按完整型号/资料核对'
        specs = re.search(r'(?<![\d.])(\d+\s*[X*]\s*\d+(?:\.\d+)?(?:\s*\+\s*\d+\s*[X*]\s*\d+(?:\.\d+)?)*)', upper)
        if specs:
            p['conductor_groups'] = [{'count': int(n), 'cross_section_mm2': float(a)} for n,a in re.findall(r'(\d+)\s*[X*]\s*(\d+(?:\.\d+)?)', specs.group())]
            p['conductor_spec_raw'] = specs.group()
        routes = []
        for match in re.finditer(r'(?<![A-Z])(JDG|SC|CT)(\d+(?:\.\d+)?)?(?![A-Z])', upper):
            names = {'JDG': '套接紧定式钢导管', 'SC': '钢管敷设标识', 'CT': '电缆桥架敷设标识'}
            routes.append({'code':match.group(1), 'meaning':names[match.group(1)], 'size_marking_raw':match.group(2) or ''})
        if routes:p['containment'] = routes
        locations = {'CC': '顶板内暗敷', 'WC': '墙内暗敷', 'FC': '地板或地坪内暗敷', 'CE': '沿天棚面或顶板面敷设', 'WE': '沿墙面敷设'}
        p['installation_markings'] = [{'code':x,'common_meaning':locations[x]} for x in locations if re.search(r'(?<![A-Z])'+x+r'(?![A-Z])', upper)]
        result['uncertainties'].append('敷设代号含义按项目图例优先；未标注的线路长度、电压等级和N/PE分配不推算')
        if upper.startswith('ZNBV'):result['uncertainties'].append('ZNBV保持原文，需核对项目图例；不擅自纠正为其他电线型号')
    elif re.fullmatch(r'L[123](?:\s*[~\-]\s*L[123])?', upper):
        result.update(category='phase', meaning='三相交流电相别标识；不作为唯一元件位号')
        p['phase_raw'] = s
    elif re.fullmatch(r'\d+\s*[#＃]', s):
        result.update(category='circuit_number', meaning='图中回路编号；需结合图面确定所属柜体')
    elif re.search(r'\bATS[-\s]', upper):
        result.update(category='transfer_switch', meaning='自动转换开关标识')
        m = re.search(r'(\d+(?:\.\d+)?)A', upper)
        if m:p['rated_current_marked_a'] = float(m.group(1))
    elif re.fullmatch(r'\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?V', upper):
        result.update(category='voltage_ratio', meaning='两侧电压标注；结合附近变压器符号确认归属')
        p['voltage_pair_v'] = [float(x) for x in re.findall(r'\d+(?:\.\d+)?', upper)]
    elif re.fullmatch(r'\d+(?:\.\d+)?\s*VA', upper):
        result.update(category='apparent_power', meaning='额定视在功率标注，通常用于变压器等设备')
        p['apparent_power_va'] = float(re.search(r'\d+(?:\.\d+)?', upper).group())
    elif re.fullmatch(r'(?:DC|AC)\s*\d+(?:\.\d+)?V(?:\s*\d+(?:\.\d+)?A)?', upper):
        result.update(category='power_supply_marking', meaning='电源电压/电流标注；保留AC/DC类型，不作为SPD持续运行电压')
        p['power_type'] = upper[:2];p['voltage_v'] = float(re.search(r'(\d+(?:\.\d+)?)V', upper).group(1))
        m = re.search(r'(\d+(?:\.\d+)?)A$', upper)
        if m:p['current_a'] = float(m.group(1))
    elif re.fullmatch(r'KM\d*', upper):
        result.update(category='device_tag', meaning='通常为接触器位号；具体器件和控制参数需对照符号')
    elif re.search(r'(?:PE|KX|IJ|COS[ΦØφ]|COSPHI)\s*=', upper):
        result.update(category='calculation_marking', meaning='设计容量、需要系数、功率因数或计算电流；不当作器件数量')
    elif re.search(r'\d+(?:\.\d+)?\s*mA\b', s):
        result.update(category='residual_current_marking', meaning='剩余电流动作值标注；所属器件需通过位置与符号核对')
        p['residual_operating_current_ma'] = float(re.search(r'(\d+(?:\.\d+)?)\s*mA', s).group(1))
    return result
