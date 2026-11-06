import re
import unicodedata

def norm(s):
    return re.sub('[‐‑‒–—−]', '-', unicodedata.normalize('NFKC', str(s))).strip().replace("×", "x")

def decode_base(raw):
    """Decode explicit literal markings; never infer catalogue properties."""
    s = norm(raw).upper()
    p = {}
    kind = 'other'
    meaning = ''
    m = re.search('(?<![A-Z0-9])(MCCB|MCB|RCBO|RCCB|RCB|RCD)(?=[\\d /-]|$)', s)
    if m:
        kind = 'device_model'
        p['drawing_abbreviation'] = m[1]
        meaning = {'MCCB': '塑壳断路器', 'MCB': '小型断路器', 'RCB': '剩余电流保护器件，订货类别待核对', 'RCBO': '带过电流保护的剩余电流动作断路器', 'RCCB': '不带过电流保护的剩余电流动作断路器', 'RCD': '剩余电流保护器件统称'}[m[1]]
        curve = re.search('(?:^|[/\\s-])([BCD])\\s*(\\d+(?:\\.\\d+)?)\\s*A(?=$|[/\\s(])', s)
        if curve:
            p.update(trip_curve=curve[1], rated_current_a=float(curve[2]))
        trip = re.search('MA\\d+(?:\\.\\d+)?A', s)
        if trip:
            p['trip_marking_raw'] = trip[0]
            p['rated_current_a'] = float(re.search('\\d+(?:\\.\\d+)?', trip[0])[0])
    elif re.search('(?<![A-Z])(?:WDZ[A-Z0-9]*-|NHBV-|ZNBV-|BVR?-|UTP/|CAT\\d)', s):
        kind = 'cable'
        meaning = '电线、线缆或通信线标注'
        if 'WDZB-' in s:
            p.update(halogen_free=True, low_smoke=True, flame_retardant_category='B')
        family = re.search('(?:WDZ[A-Z0-9]*-)(BYJ|YJY|YJV|RYJS|RYJSP|RYJ)(?:-|$)', s)
        if family:
            p['family'] = family[1]
            if family[1] == 'BYJ':
                p['family_meaning'] = '交联聚烯烃绝缘布电线'
            elif family[1] == 'YJY':
                p['family_meaning'] = '交联聚乙烯绝缘、聚烯烃护套电力电缆'
        spec = re.search('\\d+\\s*[X*]\\s*\\d+(?:\\.\\d+)?(?:\\s*\\+\\s*\\d+\\s*[X*]\\s*\\d+(?:\\.\\d+)?)*', s)
        if spec:
            p['conductor_groups'] = [{'count': int(n), 'area_mm2': float(a)} for n, a in re.findall('(\\d+)\\s*[X*]\\s*(\\d+(?:\\.\\d+)?)', spec[0])]
        routes = {'JDG': '套接紧定式钢导管', 'SC': '钢管敷设标识', 'CT': '电缆桥架敷设标识'}
        p['containment'] = [{'code': a + b, 'meaning': routes[a]} for a, b in re.findall('(?<![A-Z])(JDG|SC|CT)(\\d*)', s)]
        cat = re.search('CAT(\\dE?)', s)
        if cat:
            p['category_raw'] = 'CAT' + cat[1]
    elif re.fullmatch('\\d{1,3}\\s*[#号]', s):
        kind = 'circuit_tag'
    elif re.fullmatch('L[123](?:[~～-]L?[123])?(?:[,、]L[123])*|[ABC]相|N|PE|PEN', s):
        kind = 'phase'
    elif re.fullmatch('\\d+(?:\\.\\d+)?\\s*MA', s):
        kind = 'residual_current'
    elif re.fullmatch('(?:AC|DC)?\\s*\\d+(?:\\.\\d+)?\\s*/\\s*\\d+(?:\\.\\d+)?\\s*V', s):
        kind = 'voltage_ratio'
        p['voltage_pair_v'] = [float(x) for x in re.findall('\\d+(?:\\.\\d+)?', s)]
    elif re.fullmatch('\\d+(?:\\.\\d+)?\\s*VA', s):
        kind = 'apparent_power'
        p['apparent_power_va'] = float(re.search('\\d+(?:\\.\\d+)?', s)[0])
    elif re.fullmatch('(?:DC|AC)\\s*\\d+(?:\\.\\d+)?\\s*V(?:\\s+\\d+(?:\\.\\d+)?\\s*A)?', s):
        kind = 'power_marking'
        v = re.search('(\\d+(?:\\.\\d+)?)\\s*V', s)
        a = re.search('(\\d+(?:\\.\\d+)?)\\s*A', s)
        p.update(power_type=s[:2], voltage_v=float(v[1]))
        if a:
            p['current_a'] = float(a[1])
    elif re.match('^ATS[\\s-]', s):
        kind = 'device_model'
        meaning = '自动转换开关型号标注'
        p['drawing_abbreviation'] = 'ATS'
    elif re.fullmatch('(?:K[AMT]|Q[FS]|FU|SPD|TA|PM)\\d*', s):
        kind = 'device_tag'
    elif '时间控制器' in s or '时控开关' in s:
        kind = 'function_label'
    elif re.match('^(?:PE|KX|COS|IJ)\\s*[=＝]', s):
        kind = 'calculation'
    elif re.fullmatch('[A-Z][A-Z0-9]{1,14}-[A-Z0-9][A-Z0-9./+() -]{1,70}', s) and re.search('\\d', s):
        kind = 'unclassified_model'
    if kind in ('device_model', 'unclassified_model'):
        poles = re.search('(?<!\\d)([1-4]P(?:\\+N)?)(?![A-Z0-9])', s)
        if poles:
            p['pole_marking'] = poles[1]
        if 'rated_current_a' not in p:
            pattern = '(?:^|\\s)(\\d+(?:\\.\\d+)?)\\s*A(?=$|[/\\s(-])' if kind == 'unclassified_model' else '(?<![A-Z0-9.])(\\d+(?:\\.\\d+)?)\\s*A(?=$|[/\\s(-])'
            currents = re.findall(pattern, s)
            if len(currents) == 1:
                p['rated_current_a'] = float(currents[0])
    if kind in ('device_model', 'residual_current'):
        leak = re.search('(?<![\\d.])(\\d+(?:\\.\\d+)?)\\s*MA(?=$|[/\\s(])', s)
        if leak:
            p['residual_current_ma'] = float(leak[1])
    return {'kind': kind, 'meaning': meaning, 'explicit_parameters': p}


def decode(raw):
    out = decode_base(raw)
    s = norm(raw).upper(); p = out['explicit_parameters']
    if re.fullmatch(r'(PC|CB)\s*级', s):
        out.update(kind='ats_class', meaning='自动转换开关类别标注')
        p['ats_class'] = re.match(r'PC|CB', s)[0]
    elif re.fullmatch(r'\d+(?:\.\d+)?\s*KA', s):
        out['kind'] = 'breaking_marking'
        p['breaking_capacity_marking_ka'] = float(re.search(r'\d+(?:\.\d+)?', s)[0])
    elif re.fullmatch(r'\d+(?:\.\d+)?\s*V', s):
        out['kind'] = 'voltage_marking'
        p['voltage_v'] = float(re.search(r'\d+(?:\.\d+)?', s)[0])
    if out['kind'] == 'cable':
        if re.search(r'(?<![A-Z])WDZ[A-Z0-9]*-', s):
            p.update(halogen_free=True, low_smoke=True)
        multi = re.match(r'^\s*(\d+)\s*[X*]\s*\(', s)
        if multi: p['explicit_multiplier'] = int(multi[1])
        if re.search(r'(?:^|-)NHBV-', s):
            p.update(family='BV', prefix_raw='NH', fire_resistant_marking=True,
                     family_meaning='铜芯聚氯乙烯绝缘布电线')
        elif re.search(r'(?:^|-)ZNBV-', s):
            p.update(family='BV', prefix_raw='ZN', family_meaning='铜芯聚氯乙烯绝缘布电线；ZN前缀按项目标准核对')
        cat = re.search(r'CAT\s*([5-8](?:E|A)?)(?![A-Z0-9])', s)
        if cat: p['category_raw'] = 'CAT' + cat[1]
        if 'UTP/' in s: p['twisted_pair_type'] = 'UTP'
        elif 'STP/' in s: p['twisted_pair_type'] = 'STP'
    return out


def number_text(value):
    return '%g' % value if isinstance(value, (int, float)) else str(value)


def explain(raw, category, params):
    """Chinese explanations of literal markings, not manufacturer's certification."""
    names = {'MCB': '小型断路器', 'MCCB': '塑壳断路器', 'ATS': '自动转换开关',
             'RCBO': '带过电流保护的剩余电流动作断路器',
             'RCCB': '不带过电流保护的剩余电流动作断路器',
             'residual_current_device_unspecified': '剩余电流保护器件',
             'SPD': '浪涌保护器件候选（根据图面符号，完整配置待核对）',
             'controller': '控制/监测设备候选，厂家具体功能未核实',
             'contactor': '接触器或其图面触点标识', 'relay': '继电器或其触点标识',
             'timer': '时间控制器/时控功能标识', 'transformer': '变压器',
             'dc_power_supply': '直流电源规格标识', 'fuse': '熔断保护器件候选',
             'unknown': '图面型号/标识，器件类别尚未核实', 'cable': '线缆标注'}
    parts = [names.get(category, names['unknown'])]; fields = {}; pending = []
    if category == 'cable':
        parts = []
        if params.get('halogen_free') and params.get('low_smoke'):
            prefix = '无卤低烟阻燃' + (params.get('flame_retardant_category', '') + '类' if params.get('flame_retardant_category') else '')
            parts.append(prefix); fields['燃烧性能标记'] = prefix
        family = params.get('family')
        fm = params.get('family_meaning')
        if fm: parts.append(fm)
        elif family:
            parts.append(family + '线缆系列，具体结构按产品标准核对')
            pending.append(family + '系列的具体结构/产品标准尚未核实')
        if params.get('prefix_raw') == 'NH': parts.append('NH为耐火标记')
        if params.get('prefix_raw') == 'ZN': pending.append('ZN前缀具体等级尚未按项目图例核实')
        groups = params.get('conductor_groups', [])
        if groups:
            if family in ('BYJ', 'BV', 'BVR'):
                spec = '＋'.join('%d根、每根%s mm²' % (g['count'], number_text(g['area_mm2'])) for g in groups)
            else:
                spec = '＋'.join(('%d芯、每芯%s mm²' if family in ('YJY', 'YJV') else '%d×%s mm²') % (g['count'], number_text(g['area_mm2'])) for g in groups)
            parts.append(spec); fields['导体规格'] = spec
        if params.get('twisted_pair_type') == 'UTP': parts.append('非屏蔽双绞线')
        if params.get('twisted_pair_type') == 'STP': parts.append('屏蔽双绞线')
        if params.get('category_raw'):
            cat = params['category_raw']; parts.append({'CAT5': '五类', 'CAT5E': '超五类', 'CAT6': '六类', 'CAT6A': '超六类'}.get(cat, cat) + '通信线标记')
            fields['通信线类别'] = cat
        for c in params.get('containment', []):
            code = c['code']
            if code.startswith('JDG'): parts.append(code + '套接紧定式钢导管')
            elif code.startswith('SC'): parts.append(code + '钢管敷设')
            elif code == 'CT': parts.append('CT电缆桥架敷设')
        routes = {'CE': '沿顶板面敷设', 'WE': '沿墙面敷设', 'WC': '墙内暗敷',
                  'CC': '顶板内暗敷', 'FC': '地面/地板下敷设'}
        found = list(dict.fromkeys(re.findall(r'(?<![A-Z])(CE|WE|WC|CC|FC)(?![A-Z])', norm(raw).upper())))
        if found:
            text = '、'.join(code + '：' + routes[code] for code in found)
            parts.append(text + '（项目图例优先）'); fields['敷设标记'] = text
        if params.get('explicit_multiplier') is not None:
            fields['图面倍数'] = params['explicit_multiplier']
            parts.append('外层标注%s组/路同规格线缆；不代表已确认采购数量' % params['explicit_multiplier'])
        fields['长度_m'] = None
        if not parts: parts = ['线缆型号原文；具体结构待核对']
    else:
        if params.get('trip_curve'):
            parts.append(params['trip_curve'] + '型脱扣特性'); fields['脱扣特性'] = params['trip_curve']
        if params.get('rated_current_a') is not None:
            label = '图面电流标记_A' if params.get('trip_marking_raw') else '额定电流_A'
            fields[label] = params['rated_current_a']; parts.append(('图面电流标记' if params.get('trip_marking_raw') else '额定电流') + number_text(params['rated_current_a']) + 'A')
        if params.get('trip_marking_raw'):
            parts.append('原型号含' + params['trip_marking_raw'] + '脱扣器/系列标记，不作为MCB的B/C/D曲线')
        if params.get('pole_marking'):
            pole = params['pole_marking']
            if category == 'unknown':
                fields['型号P标记'] = pole
                parts.append(pole + '原标记，具体含义按该型号资料核对')
            else:
                fields['极数标记'] = pole
                parts.append(pole + ('：1个相极加中性极N' if pole == '1P+N' else '：' + pole[0] + '极'))
        if params.get('residual_current_ma') is not None:
            fields['剩余动作电流_mA'] = params['residual_current_ma']; parts.append('剩余动作电流' + number_text(params['residual_current_ma']) + 'mA')
        if params.get('ats_class'):
            fields['转换开关类别'] = params['ats_class'] + '级'; parts.append(params['ats_class'] + '级')
        if params.get('voltage_pair_v'):
            fields['电压比_V'] = params['voltage_pair_v']; parts.append('电压比' + '/'.join(number_text(v) for v in params['voltage_pair_v']) + 'V')
        if params.get('apparent_power_va') is not None:
            fields['容量_VA'] = params['apparent_power_va']; parts.append('容量' + number_text(params['apparent_power_va']) + 'VA')
        if params.get('power_type'): fields['电源类型标注'] = params['power_type']
        if params.get('voltage_v') is not None:
            fields['电压标注_V'] = params['voltage_v']; parts.append(params.get('power_type', '') + number_text(params['voltage_v']) + 'V')
        if params.get('current_a') is not None:
            fields['电流标注_A'] = params['current_a']; parts.append(number_text(params['current_a']) + 'A')
        if params.get('breaking_capacity_marking_ka') is not None:
            fields['分断电流标注_kA'] = params['breaking_capacity_marking_ka']; pending.append('Icu/Ics等具体指标含义仍需核对原文')
    if category == 'unknown': pending.append('器件类别未核实，不能仅凭型号猜产品')
    if category in ('controller', 'SPD', 'dc_power_supply'): pending.append('厂家具体功能/输入输出或完整订货参数未核实')
    if category == 'residual_current_device_unspecified': pending.append('RCCB/RCBO等具体订货类别未核实')
    if category == 'ATS' and 'pole_marking' not in params: pending.append('极数未明确；II后缀不等于2P')
    if decode(raw)['kind'] == 'device_tag': pending.append('位号/触点不直接等于另一台采购实体')
    return {'meaning_cn': '；'.join(parts), 'selection_fields_cn': fields, 'pending': pending}
