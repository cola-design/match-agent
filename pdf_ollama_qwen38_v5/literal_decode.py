import re
import unicodedata

def norm(s):
    return re.sub('[‐‑‒–—−]', '-', unicodedata.normalize('NFKC', str(s))).strip().replace("×", "x")

def decode(raw):
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
