import json
import re
from decimal import Decimal, InvalidOperation
CODE_RE = re.compile('物料编码\\s*[:：]\\s*([A-Z][A-Z0-9_-]{5,}|\\d{8,16})(?![A-Z0-9_-])', re.I)
FIELDS = ('物料编码', '物料描述', '产品类别', '产品系列', '规格参数', '状态', '数据质量')

def clean(v):
    return str(v).strip().replace('\u200b', '') if v is not None else ''

def json_obj(v):
    if isinstance(v, dict):
        return v
    t = re.sub('^```(?:json)?\\s*|\\s*```$', '', clean(v), flags=re.I)
    try:
        x = json.loads(t)
        return x if isinstance(x, dict) else {}
    except (TypeError, ValueError):
        return {}

def docs(result):
    if isinstance(result, str):
        return [result]
    if isinstance(result, list):
        out = []
        for item in result:
            out += docs(item)
        return out
    if isinstance(result, dict):
        for key in ('content', 'text', 'page_content'):
            if isinstance(result.get(key), str) and result[key].strip():
                return [result[key]]
    return []

def parse_field(segment, key):
    stop = '|'.join((re.escape(x) for x in FIELDS if x != key))
    m = re.search('(?:^|\\s)\\*{0,2}' + re.escape(key) + '\\*{0,2}\\s*[:：]\\s*(.*?)(?=\\s*(?:' + stop + ')\\s*[:：]|\\Z)', segment, re.S | re.I)
    return re.sub('\\s+', ' ', m.group(1)).strip() if m else ''

def parse_products(result):
    rows = []
    for text in docs(result):
        text = text.replace('**', '')
        hits = list(CODE_RE.finditer(text))
        for i, hit in enumerate(hits):
            body = text[hit.start():hits[i + 1].start() if i + 1 < len(hits) else len(text)]
            row = {'物料编码': hit.group(1)}
            for key in FIELDS[1:]:
                row[key] = parse_field(body, key)
            if row['物料描述'] and (row['规格参数'] or row['产品类别']):
                rows.append(row)
    return rows

def norm(v):
    return re.sub('[^A-Z0-9]', '', clean(v).upper())

def model_match(wanted, desc):
    if not wanted:
        return True
    if clean(wanted).isdigit():
        return bool(re.search('(?<!\\d)' + re.escape(clean(wanted)) + '(?!\\d)', clean(desc)))
    tokens = re.findall('[A-Z]+[A-Z0-9]*(?:[-_/][A-Z0-9]+)*|[0-9]+(?:[.][0-9]+)?', clean(wanted).upper())
    return bool(tokens) and all((norm(token) in norm(desc) for token in tokens))

def pole_value(text):
    t = clean(text).upper()
    m = re.search('([1-4])\\s*P\\s*\\+\\s*N', t)
    if m:
        return (int(m.group(1)) + 1, True, m.group(0).replace(' ', ''))
    m = re.search('(?<!\\d)([1-4])\\s*P(?!\\s*\\+\\s*N)', t)
    if m:
        return (int(m.group(1)), False, m.group(0).replace(' ', ''))
    return None

def decimal(v):
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None

def explicit_trip_requirements(value):
    curves = set()
    currents = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in ('forbidden_models', 'excluded_models', 'prohibited_models', 'forbidden_accessories'):
                continue
            if key in ('trip_curve', 'tripping_curve', '脱扣曲线', '脱扣特性'):
                text = clean(child).upper()
                if text in ('B', 'C', 'D'):
                    curves.add(text)
            c, a = explicit_trip_requirements(child)
            curves.update(c)
            currents.update(a)
        return (curves, currents)
    if isinstance(value, (list, tuple)):
        for child in value:
            c, a = explicit_trip_requirements(child)
            curves.update(c)
            currents.update(a)
        return (curves, currents)
    text = clean(value).upper()
    text = re.sub('(?:不允许|不得|禁止|不能|不采用|不选用|不使用|排除|不含)[^;；。\\n,，]*', '', text)
    for match in re.finditer('(?<![A-Z0-9])([BCD])\\s*(\\d{1,3})(?:\\s*A)?(?![A-Z0-9])', text):
        curves.add(match.group(1))
        currents.add(int(match.group(2)))
    for match in re.finditer('(?:脱扣(?:特性|曲线)|TRIP_CURVE|TRIPPING_CURVE)\\s*[:：=为]?\\s*([BCD])(?![A-Z0-9/／])|(?<![A-Z0-9/／])([BCD])\\s*型?\\s*脱扣', text):
        curves.add(next((x for x in match.groups() if x)))
    return (curves, currents)

def requirement_trip(req):
    fields = ('requirements', 'shown_model', 'requirement_summary', 'drawing_parameters', 'protocol_parameters')
    return explicit_trip_requirements([req.get(k) for k in fields])

def trip_mismatch(requirement, product):
    wanted_curves, wanted_currents = explicit_trip_requirements(requirement)
    actual_curves, actual_currents = explicit_trip_requirements(product)
    if len(wanted_curves) > 1 or len(wanted_currents) > 1:
        return '需求中的脱扣特性或对应电流存在冲突，请核实原始资料，不能直接采用'
    if wanted_curves:
        if len(actual_curves) != 1:
            return '产品记录或确认型号未唯一证实所要求的脱扣特性：' + '/'.join(sorted(wanted_curves))
        if wanted_curves != actual_curves:
            return '脱扣特性不一致：要求' + '/'.join(sorted(wanted_curves)) + '型，当前为' + '/'.join(sorted(actual_curves)) + '型；请更正型号及对应物料编码'
    if wanted_currents and actual_currents and (wanted_currents != actual_currents):
        return '脱扣型号额定电流不一致：要求%sA，当前%sA' % (sorted(wanted_currents), sorted(actual_currents))
    return ''

def chinese_parameters__v16_before_1(value):
    if not isinstance(value, dict):
        return clean(value)
    labels = [('system_voltage_v', '系统电压', 'V'), ('rated_voltage_v', '额定电压', 'V'), ('rated_current_a', '额定电流', 'A'), ('rated_current_min_a', '额定电流最低要求', 'A'), ('pole_count', '极数', ''), ('breaking_capacity_ka', '分断能力', 'kA'), ('breaking_capacity_min_ka', '分断能力最低要求', 'kA'), ('continuous_operating_voltage_v', '最大持续运行电压Uc', 'V'), ('max_continuous_operating_voltage_v', '最大持续运行电压Uc', 'V'), ('uc_v', '最大持续运行电压Uc', 'V'), ('spd_uc_v', '最大持续运行电压Uc', 'V'), ('trip_curve', '脱扣特性', ''), ('tripping_curve', '脱扣特性', ''), ('leakage_required', '是否配置漏电保护', ''), ('leakage_current_ma', '漏电动作电流', 'mA'), ('installation_mode', '安装或进出线要求', ''), ('operation_mode', '操作方式', ''), ('control_voltage', '控制电压', ''), ('required_accessories', '要求附件', ''), ('forbidden_accessories', '禁止附件', ''), ('nominal_discharge_current_ka', '标称放电电流In', 'kA'), ('max_discharge_current_ka', '最大放电电流Imax', 'kA'), ('test_class', '试验类别', ''), ('brand_constraints', '品牌限制', '')]
    parts = []
    for key, label, unit in labels:
        val = value.get(key)
        if val is None or val == '' or val == [] or (val == {}):
            continue
        if isinstance(val, bool):
            text = '需要' if val else '不需要'
        elif isinstance(val, list):
            text = '、'.join((str(x) for x in val))
        else:
            text = str(val)
        parts.append(label + '：' + text + unit)
    return '；'.join(dict.fromkeys(parts))

def spd_ambiguous(req):
    role = clean(req.get('component_role')) + ' ' + clean(req.get('category_path'))
    spec = req.get('requirements') if isinstance(req.get('requirements'), dict) else {}
    return any((x in role.upper() for x in ('SPD', '电涌', '浪涌'))) and '后备' not in role and (spec.get('rated_voltage_v') not in (None, '')) and all((spec.get(k) in (None, '') for k in ('continuous_operating_voltage_v', 'max_continuous_operating_voltage_v', 'uc_v', 'spd_uc_v')))

def readable_requirement(req, item=None):
    item = item or {}
    spec = req.get('requirements') if isinstance(req.get('requirements'), dict) else item.get('requirements', {})
    spec = dict(spec) if isinstance(spec, dict) else {}
    ambiguous = spd_ambiguous(dict(req, requirements=spec))
    voltage = spec.pop('rated_voltage_v', None) if ambiguous else None
    parts = []
    model = clean(req.get('shown_model') or item.get('shown_model'))
    if model:
        parts.append('文件指定型号：' + model)
    if spec:
        parts.append(chinese_parameters(spec))
    summary = clean(item.get('requirement_summary') or req.get('requirement_summary'))
    if summary.startswith('{') and '}' in summary:
        summary = summary[summary.rfind('}') + 1:].strip('；; ')
    if summary and summary not in parts:
        parts.append(summary)
    if ambiguous:
        parts.append('电压参数含义待确认：' + str(voltage) + 'V（需明确额定工作电压还是Uc，不可互相替代）')
    segments = []
    for part in parts:
        for segment in part.split('；'):
            if segment and segment not in segments:
                segments.append(segment)
    return '；'.join(segments)

def candidate_check__v16_before_1(req, rec):
    spec = req.get('requirements') if isinstance(req.get('requirements'), dict) else {}
    attrs = rec.get('规格参数', '')
    desc = rec.get('物料描述', '')
    conflicts = []
    warnings = []
    score = 0
    if not category_match(req, rec):
        return (['产品类别不一致：需求%s，候选%s' % (clean(req.get('category_path') or req.get('component_role')), rec.get('产品类别'))], [], 0)
    score += 30
    forbidden = spec.get('forbidden_accessories') or []
    if '辅助触点' in forbidden and re.search('(?<![A-Z])OF\\d*|配[^;；]*辅助触|带[^;；]*辅助触', attrs + ' ' + desc, re.I):
        conflicts.append('明确禁止辅助触点，候选带有附加触点')
    for field, label in (('nominal_discharge_current_ka', '标称放电电流'), ('max_discharge_current_ka', '最大放电电流')):
        wanted = decimal(spec.get(field))
        if wanted is not None:
            hit = re.search(label + '\\s*(?:[（(]\\s*kA\\s*[）)])?\\s*[:=：]\\s*(\\d+(?:\\.\\d+)?)\\s*kA', attrs, re.I)
            if not wanted.is_finite():
                conflicts.append(label + '要求不是有效数值')
            elif not hit:
                warnings.append('候选记录未证实' + label)
            elif decimal(hit.group(1)) < wanted:
                conflicts.append(label + '不足')
    if spec.get('test_class'):
        wanted = clean(spec['test_class']).upper().replace('类', '').replace('Ⅱ', 'II')
        hit = re.search('试验(?:分类|类型|类别)\\s*[:=：]\\s*([^;；,，]+)', attrs)
        actual = clean(hit.group(1)).upper().replace('类', '').replace('Ⅱ', 'II') if hit else ''
        if not actual:
            warnings.append('候选记录未证实试验类别')
        elif actual != wanted:
            conflicts.append('试验类别不一致')
    required_pole = pole_value(spec.get('pole_count'))
    found_pole = pole_value(re.search('极数\\s*[:=：]\\s*[^;；,，]+', attrs, re.I).group(0) if re.search('极数\\s*[:=：]\\s*[^;；,，]+', attrs, re.I) else desc)
    if required_pole:
        if not found_pole:
            warnings.append('候选记录未证实极数')
        elif required_pole[0] != found_pole[0] or (required_pole[1] and (not found_pole[1]) and (found_pole[0] != 4)):
            conflicts.append('极数不一致：要求%s，候选%s' % (required_pole[2], found_pole[2]))
        elif required_pole[2] == found_pole[2]:
            score += 30
        else:
            score += 24
            warnings.append('4P与3P+N的端子及中性线开断方式须工程师确认')
    current_req = decimal(spec.get('rated_current_a', spec.get('rated_current_min_a')))
    current_hit = re.search('额定电流\\s*[:=：]\\s*(\\d+(?:\\.\\d+)?)\\s*A', attrs, re.I)
    if not current_hit:
        current_hit = re.search('(?<![A-Z0-9])(?:[BCD])?(\\d+(?:\\.\\d+)?)A\\b', desc, re.I)
    current = decimal(current_hit.group(1)) if current_hit else None
    if current_req is not None:
        if current is None:
            warnings.append('候选记录未证明额定电流')
        elif current < current_req:
            conflicts.append('额定电流不足：要求%sA，候选%sA' % (current_req, current))
        elif current == current_req:
            score += 25
        else:
            score += 5
            warnings.append('候选额定电流高于文件指定值，需校核保护配合')
    required_curves, required_currents = requirement_trip(req)
    if len(required_curves) > 1 or len(required_currents) > 1:
        conflicts.append('原始需求中的脱扣特性或对应电流相互冲突，不能自动选择')
    else:
        trip_error = trip_mismatch([req.get(k) for k in ('requirements', 'shown_model', 'requirement_summary', 'drawing_parameters', 'protocol_parameters')], attrs + ' ' + desc)
        if trip_error:
            conflicts.append(trip_error)
        elif required_curves:
            score += 20
    uc_req = decimal(spec.get('continuous_operating_voltage_v') or spec.get('max_continuous_operating_voltage_v') or spec.get('uc_v') or spec.get('spd_uc_v'))
    if uc_req is not None and ('电涌保护器' in clean(req.get('component_role')) or '浪涌保护器' in clean(req.get('component_role'))):
        uc_hit = re.search('(?:最大持续(?:工作|运行)电压|Uc)\\s*[:=：]\\s*(\\d+(?:\\.\\d+)?)\\s*V?', attrs, re.I)
        uc_found = decimal(uc_hit.group(1)) if uc_hit else None
        if uc_found is None:
            warnings.append('候选记录未证明SPD持续运行电压Uc')
        elif uc_found != uc_req:
            conflicts.append('SPD持续运行电压Uc不一致：要求%sV，候选%sV' % (uc_req, uc_found))
        else:
            score += 20
    capacity = decimal(spec.get('breaking_capacity_min_ka'))
    if capacity is not None and '断路器' in clean(req.get('component_role')):
        m = re.search('分断能力\\s*[:=：]\\s*(\\d+(?:\\.\\d+)?)\\s*kA', attrs, re.I)
        found = decimal(m.group(1)) if m else None
        if found is None:
            warnings.append('候选记录未证明分断能力')
        elif found < capacity:
            conflicts.append('分断能力不足：要求%skA' % capacity)
        else:
            score += 10
    model = clean(req.get('shown_model'))
    if model and model_match(model, desc):
        score += 25
    if code_is_formal(rec.get('物料编码')):
        score += 5
    if rec.get('状态') in ('有效', '在售', 'ACTIVE'):
        score += 8
    return (conflicts, warnings, score)

def code_is_formal(code):
    return clean(code).isdigit()

def required_model(req):
    model = clean(req.get('shown_model'))
    if model:
        return model
    m = re.search('(?:文件指定型号\\s*[:：]\\s*)?\\b([A-Z]{2,}\\d[A-Z0-9_-]*(?:-\\d+[A-Z0-9_-]*)?)\\b', clean(req.get('requirement_summary')), re.I)
    return m.group(1) if m else ''

def main(selection_text: str, requirement_json: str, retrieval_result) -> dict:
    req = json_obj(requirement_json)
    model = required_model(req)
    rows = parse_products(retrieval_result)
    by_code = {}
    for row in rows:
        by_code.setdefault(row['物料编码'], []).append(row)
    candidates = []
    rejected = []
    for code, records in by_code.items():
        if len({(r['物料描述'], r['规格参数'], r['状态']) for r in records}) > 1:
            rejected.append('同一编码存在不同产品规格或状态：' + str(code))
            continue
        rec = records[0]
        if model and (not model_match(model, rec['物料描述'])):
            continue
        failures, warnings, score = candidate_check(req, rec)
        if failures:
            rejected.extend(failures)
            continue
        if any((x in rec['状态'] for x in ('停用', '禁用', '冻结', '停产', '淘汰', '作废'))):
            continue
        conflict = len({(x['物料描述'], x['规格参数'], x['状态']) for x in records}) > 1
        candidates.append((score, code, rec, warnings, conflict))
    candidates.sort(key=lambda x: (-x[0], not code_is_formal(x[1]), x[1]))
    spec = req.get('requirements') if isinstance(req.get('requirements'), dict) else {}
    required_curves, required_currents = requirement_trip(req)
    requested_curve = next(iter(required_curves)) if len(required_curves) == 1 else ''
    curve_variants = set()
    uc_variants = set()
    for _, _, record, _, _ in candidates:
        description = clean(record.get('物料描述'))
        hit = re.search('(?<![A-Z0-9])([BCD])\\d{1,3}A?\\b', description, re.I)
        if hit:
            curve_variants.add(hit.group(1).upper())
        hit = re.search('(?<!\\d)(275|320|385|440|460)\\s*V', description, re.I)
        if hit:
            uc_variants.add(hit.group(1))
    breaker_curve_ambiguous = '断路器' in clean(req.get('component_role')) and requested_curve not in {'B', 'C', 'D'} and (len(curve_variants) > 1)
    requested_uc = decimal(spec.get('continuous_operating_voltage_v') or spec.get('max_continuous_operating_voltage_v') or spec.get('uc_v') or spec.get('spd_uc_v'))
    spd_uc_ambiguous = ('电涌保护器' in clean(req.get('component_role')) or '浪涌保护器' in clean(req.get('component_role'))) and requested_uc is None and (len(uc_variants) > 1)
    ambiguity_reasons = candidate_ambiguities(req, candidates)
    status = 'NOT_RETRIEVED'
    candidate = {}
    alternatives = []
    warnings = []
    reasons = []
    code = ''
    if spd_ambiguous(req):
        warnings.append('SPD电压参数含义待确认：请核实文件电压是Uc还是额定工作电压，未确认不得报价')
    if candidates:
        score, code, rec, unknown, conflicting = candidates[0]
        candidate = {'material_code': code, 'material_name': rec['物料描述'], 'series': rec['产品系列'], 'source_quality': rec['数据质量'], 'product_status': rec['状态'], 'matched_parameters': []}
        warnings += unknown
        if breaker_curve_ambiguous:
            warnings.append('脱扣曲线未明确，候选存在%s差异；当前推荐项仅为暂定，须工程师确认' % '/'.join(sorted(curve_variants)))
        if spd_uc_ambiguous:
            warnings.append('SPD持续运行电压Uc未明确，候选存在%sV差异；当前推荐项仅为暂定，须工程师确认' % '/'.join(sorted(uc_variants, key=int)))
        if conflicting:
            warnings.append('同一编码检索到不同规格或状态，禁止自动报价')
        if not rec['状态'] or '待核实' in rec['状态'] or '待确认' in rec['状态']:
            warnings.append('产品状态待核实，需工程师确认当前有效性')
        if code.upper().startswith('TMP-'):
            warnings.append('临时演示物料编码，不得作为企业正式采购编码')
        status = 'MATCHED' if not unknown and (not conflicting) and (not breaker_curve_ambiguous) and (not spd_uc_ambiguous) and (not spd_ambiguous(req)) and (rec['状态'] in ('有效', '在售', 'ACTIVE')) and code_is_formal(code) else 'NEED_REVIEW'
        alternatives = [{'material_code': c, 'material_name': r['物料描述'], 'difference': '同类别同型号的其他合格候选，需工程师比较'} for _, c, r, _, _ in candidates[1:6]]
        selection_reason = '从本次PRODUCT检索结果中按类别、型号、极数、电流、脱扣曲线和分断能力逐项校验后选出。'
    else:
        reasons = ['本次检索未找到同时满足产品类别和关键规格的候选']
        if rejected:
            reasons.append(rejected[0])
        warnings.append('未召回不等于整个产品库不存在；请核对API检索词、候选过滤与产品发布状态，或补充产品数据')
        selection_reason = '没有通过确定性规则校验的候选，系统未写入物料编码。'
    if candidates and ambiguity_reasons:
        candidate = {}
        code = ''
        status = 'NEED_REVIEW'
        alternatives = [{'material_code': c, 'material_name': r['物料描述'], 'difference': '待工程师选定；' + '；'.join(ambiguity_reasons)} for _, c, r, _, _ in candidates[:6]]
        warnings += ambiguity_reasons
        warnings.append('请在修改意见写明最终型号、物料编码及缺失参数；仅写采用不能代替选型')
        selection_reason = '关键技术要求未唯一明确，仅列候选，不预填推荐物料编码。'
    mapping = clean(req.get('mapping_status')).upper()
    missing = req.get('missing_parameters') if isinstance(req.get('missing_parameters'), list) else []
    if status == 'MATCHED' and (mapping not in ('MATCHED', 'USER_CONFIRMED', 'USER_CONFIRMED_ADDITION') or any(('待产品库匹配' not in clean(x) for x in missing))):
        status = 'NEED_REVIEW'
        warnings.append('上游需求尚未全部确认')
    stable_summary = readable_requirement(req)
    if len(required_curves) == 1:
        stable_summary += '；明确脱扣特性：' + next(iter(required_curves)) + '型脱扣'
    if len(required_currents) == 1:
        stable_summary += '；明确脱扣规格：' + next(iter(required_curves), '') + str(next(iter(required_currents)))
    item = {'request_id': clean(req.get('request_id')), 'scheme_group_id': clean(req.get('scheme_group_id')), 'circuit_id': clean(req.get('circuit_id')), 'symbol': clean(req.get('symbol')), 'component_role': clean(req.get('component_role')), 'requirement_summary': stable_summary, 'requirements': req.get('requirements') if isinstance(req.get('requirements'), dict) else {}, 'shown_model': clean(req.get('shown_model')), 'quantity_per_cabinet': req.get('quantity_per_cabinet', ''), 'cabinet_quantity': req.get('cabinet_quantity', ''), 'project_quantity': req.get('project_quantity', req.get('quantity', '')), 'quantity': req.get('project_quantity', req.get('quantity', '')), 'unit': clean(req.get('unit') or '台'), 'recommended': candidate, 'alternatives': alternatives, 'match_status': status, 'mismatch_or_missing': reasons, 'selection_reason': selection_reason, 'warnings': list(dict.fromkeys((clean(x) for x in warnings if clean(x)))), 'source_evidence': [{'material_code': c, 'material_name': r['物料描述']} for _, c, r, _, _ in candidates[:6]], 'upstream_mapping_status': mapping, 'upstream_missing_parameters': missing, 'matched_protocol_rule_ids': req.get('matched_protocol_rule_ids') if isinstance(req.get('matched_protocol_rule_ids'), list) else [], 'human_confirmation': '待确认', 'final_material_code': '', 'confirmation_note': ''}
    item['quantity_basis'] = req.get('quantity_basis', 'PROJECT')
    item['quantity_per_cabinet'] = req.get('quantity_per_cabinet')
    item['project_cabinet_quantity'] = req.get('project_cabinet_quantity', req.get('cabinet_quantity'))
    if item['quantity_basis'] == 'PER_CABINET':
        item['project_quantity'] = None
        item['quantity'] = req.get('quantity_per_cabinet')
        item['warnings'].append('单柜用量，仅供单柜材料参考，不代表项目总量')
    item['ambiguity_reasons'] = ambiguity_reasons
    item = preserve_selection_trace(item, req)
    return {'item_json': json.dumps(item, ensure_ascii=False), 'item_status': status, 'exact_material_code': code}
import unicodedata

def canonical_category_name(value):
    raw = unicodedata.normalize('NFKC', clean(value))
    key = re.sub('[\\s_／/\\-]+', '', raw).lower()
    english = {'circuitbreaker': '断路器', 'breaker': '断路器', 'mcb': '小型断路器', 'miniaturecircuitbreaker': '小型断路器', 'mccb': '塑壳断路器', 'moldedcasecircuitbreaker': '塑壳断路器', 'mouldedcasecircuitbreaker': '塑壳断路器', 'isolatorswitch': '隔离开关', 'isolator': '隔离开关', 'disconnector': '隔离开关', 'surgeprotectivedevicebackup': 'SPD后备保护器', 'spdbackup': 'SPD后备保护器', 'scb': 'SPD后备保护器', 'surgeprotectivedevice': '电涌保护器', 'surgeprotector': '电涌保护器', 'spd': '电涌保护器', 'cablegland': '电缆防水接头', 'terminallug': '铜铝接线鼻', 'cablelug': '铜铝接线鼻', 'mountingbracket': '表位支架', 'meterbracket': '表位支架', 'groundingbar': '接地排', 'pebar': '接地排', 'fastener': '紧固件', 'fasteners': '紧固件', 'enclosure': '柜体箱体', 'cabinetenclosure': '柜体箱体', 'recloser': '自动重合闸', 'rcbo': '漏电断路器'}
    if key in english:
        return english[key]
    aliases = [(('SPD后备保护器', '防雷后备保护器', '浪涌后备保护器'), 'SPD后备保护器'), (('表位支架', '安装支架', '电表支架', '表架'), '表位支架'), (('铜铝接线鼻', '接线鼻'), '铜铝接线鼻'), (('电缆防水接头', '防水接头'), '电缆防水接头'), (('PE接地排', '接地排'), '接地排'), (('膨胀螺栓', '膨胀螺丝', '紧固件'), '紧固件'), (('成套柜体', '柜体箱体', '柜体'), '柜体箱体'), (('电涌保护器', '浪涌保护器'), '电涌保护器'), (('小型隔离开关', '交流隔离开关', '隔离开关'), '隔离开关'), (('小型断路器', '微型断路器'), '小型断路器'), (('塑壳断路器',), '塑壳断路器'), (('漏电断路器',), '漏电断路器'), (('自动重合闸',), '自动重合闸')]
    for words, category in aliases:
        if any((word in raw for word in words)):
            return category
    if raw in ('断路器', '明确型号断路器'):
        return '断路器'
    return ''

def category_key(value):
    return canonical_category_name(value) or re.sub('[\\s/／_-]', '', clean(value))

def category_match(req, rec):
    role = canonical_category_name(req.get('component_role'))
    path = canonical_category_name(req.get('category_path'))
    actual = canonical_category_name(rec.get('产品类别'))
    breaker_types = {'断路器', '小型断路器', '塑壳断路器', '漏电断路器'}
    if role and path and (role != path) and (not (path == '断路器' and role in breaker_types)):
        return False
    required = role or path
    if not required:
        return category_key(req.get('category_path') or req.get('component_role')) == category_key(rec.get('产品类别'))
    if required == '断路器' and clean(req.get('shown_model')):
        return actual in breaker_types - {'断路器'}
    return required == actual
import copy

def generic_parameter_display(previous, value):
    if isinstance(value, dict):
        value = copy.deepcopy(value)
        for exact, minimum in [('rated_current_a', 'rated_current_min_a'), ('breaking_capacity_ka', 'breaking_capacity_min_ka')]:
            if value.get(exact) not in (None, '') and str(value.get(exact)) == str(value.get(minimum)):
                value.pop(minimum, None)
        if value.get('trip_curve') and value.get('trip_curve') == value.get('tripping_curve'):
            value.pop('tripping_curve', None)
    text = previous(value)
    if not isinstance(value, dict):
        return text
    existing = {'system_voltage_v', 'rated_voltage_v', 'rated_current_a', 'rated_current_min_a', 'pole_count', 'breaking_capacity_ka', 'breaking_capacity_min_ka', 'continuous_operating_voltage_v', 'max_continuous_operating_voltage_v', 'uc_v', 'spd_uc_v', 'trip_curve', 'tripping_curve', 'leakage_required', 'leakage_current_ma', 'installation_mode', 'operation_mode', 'control_voltage', 'required_accessories', 'forbidden_accessories', 'nominal_discharge_current_ka', 'max_discharge_current_ka', 'test_class', 'brand_constraints'}
    metadata = {'model', 'required_model', 'designated_model', 'specified_model', 'bit_number', 'position', 'symbol', 'unit', 'quantity_per_cabinet'}
    labels = {'material': '材质', 'thickness_mm': '板厚（mm）', 'color': '颜色', 'cross_section_mm2': '截面积（mm²）', 'specification': '规格原文', 'cabinet_cable_entry_exit': '柜体电缆进出线', 'cabinet_cable_entry_exit_source_text': '柜体电缆进出线原文（待核对）'}
    parts = [text]
    for key, val in value.items():
        if key in existing or key in metadata or val in (None, '', [], {}):
            continue
        parts.append(labels.get(key, str(key)) + '：' + clean(val))
    return '；'.join((p for p in parts if p))
_v16_parameter_display = chinese_parameters__v16_before_1

def chinese_parameters(value):
    return generic_parameter_display(_v16_parameter_display, value)
_v16_candidate_check = candidate_check__v16_before_1

def _v163_candidate_base(req, rec):
    conflicts, warnings, score = _v16_candidate_check(req, rec)
    validated = {'system_voltage_v', 'rated_voltage_v', 'rated_current_a', 'rated_current_min_a', 'pole_count', 'breaking_capacity_ka', 'breaking_capacity_min_ka', 'continuous_operating_voltage_v', 'max_continuous_operating_voltage_v', 'uc_v', 'spd_uc_v', 'trip_curve', 'tripping_curve', 'leakage_required', 'leakage_current_ma', 'installation_mode', 'operation_mode', 'control_voltage', 'required_accessories', 'forbidden_accessories', 'nominal_discharge_current_ka', 'max_discharge_current_ka', 'test_class', 'brand_constraints', 'model', 'required_model', 'designated_model', 'unit', 'quantity_per_cabinet', 'bit_number', 'position', 'note', 'brand', 'shown_specification'}
    extra = [str(k) for k, v in req.get('requirements', {}).items() if k not in validated and v not in (None, '', [], {})]
    if extra:
        warnings.append('扩展参数已保留，需工程师核验：' + '、'.join(extra))
    return (conflicts, warnings, score)

def candidate_check(req, rec):
    conflicts, warnings, score = _v163_candidate_base(req, rec)
    labels = {'cabinet_cable_entry_exit_source_text': '柜体电缆进出线原文（待核对）', 'cabinet_cable_entry_exit': '柜体电缆进出线', 'thickness_mm': '板厚', 'cross_section_mm2': '截面积', 'material': '材质', 'color': '颜色'}
    result = []
    for text in warnings:
        for key, label in labels.items():
            text = text.replace(key, label)
        result.append(text)
    extra_conflicts, extra_warnings = extension_product_checks(req.get('requirements') or {}, rec)
    conflicts += extra_conflicts
    result += extra_warnings
    return (conflicts, result, score)

def product_discriminators(record):
    attrs = str(record.get('规格参数') or '')
    description = str(record.get('物料描述') or '')
    curves, _ = explicit_trip_requirements(attrs + ' ' + description)
    result = {}
    if len(curves) == 1:
        result['trip_curve'] = next(iter(curves))
    patterns = {'continuous_operating_voltage_v': '(?:最大持续(?:工作|运行)电压|\\bUc)\\s*[:=：]\\s*([0-9.]+)\\s*V?', 'control_voltage': '(?:线圈电压|控制电压)\\s*[:=：]\\s*((?:AC|DC)?\\s*[0-9.]+\\s*V)', 'pole_count': '极数\\s*[:=：]\\s*([1-5]P(?:\\+N)?)'}
    for field, pattern in patterns.items():
        hit = re.search(pattern, attrs, re.I)
        if hit:
            result[field] = re.sub('\\s+', '', hit.group(1)).upper()
    return result

def candidate_ambiguities(req, candidates):
    params = req.get('requirements') if isinstance(req.get('requirements'), dict) else {}
    curves, _ = requirement_trip(req)
    signatures = [product_discriminators(row[2]) for row in candidates]
    reasons = []
    role = str(req.get('component_role') or '') + ' ' + str(req.get('category_path') or '')
    if '断路器' in role and (not curves) and any(('trip_curve' in s for s in signatures)):
        reasons.append('脱扣曲线未明确，不能由检索先后或编码排序替代技术要求')
    if any((word in role for word in ('电涌', '浪涌', 'SPD'))) and '后备' not in role:
        uc = next((params[k] for k in ('continuous_operating_voltage_v', 'max_continuous_operating_voltage_v', 'uc_v', 'spd_uc_v') if params.get(k) not in (None, '')), None)
        if uc is None:
            reasons.append('SPD持续运行电压Uc未明确，需核对资料或由工程师明确')
    for field, label in [('control_voltage', '线圈/控制电压'), ('pole_count', '极数')]:
        variants = {s[field] for s in signatures if s.get(field)}
        if not params.get(field) and len(variants) > 1:
            reasons.append(label + '存在不同候选值，需求尚未明确')
    return reasons

def preserve_selection_trace(item, req):
    item['document_evidence'] = copy.deepcopy(req.get('source_evidence') or {})
    item['parameter_facts'] = copy.deepcopy(req.get('parameter_facts') or [])
    item['selection_decision'] = 'CANDIDATES_ONLY' if item.get('ambiguity_reasons') else 'RECOMMENDED_PENDING_ENGINEER' if item.get('recommended') else 'NO_VERIFIED_CANDIDATE'
    return item

def product_field_token(value):
    return re.sub('[\\s_()（）²]+', '', unicodedata.normalize('NFKC', str(value or ''))).casefold()

def product_attribute_map(record):
    attrs = record.get('规格参数') or ''
    if isinstance(attrs, dict):
        return {product_field_token(k): v for k, v in attrs.items()}
    result = {}
    for part in re.split('[;；\\n]', str(attrs)):
        m = re.match('\\s*([^=:：]{1,45})\\s*[=:：]\\s*(.+?)\\s*$', part)
        if m:
            result[product_field_token(m.group(1))] = m.group(2)
    for k, v in record.items():
        if v not in (None, '', [], {}):
            result.setdefault(product_field_token(k), v)
    materials = re.findall('(?<!\\d)(201|304|316L?)\\s*不锈钢', str(record.get('物料描述') or ''), re.I)
    if len(set(materials)) == 1:
        result.setdefault(product_field_token('材质'), materials[0] + '不锈钢')
    return result

def extension_product_checks(requirements, record):
    attrs = product_attribute_map(record)
    conflicts = []
    warnings = []
    fields = {'control_voltage': ('控制/线圈电压', ('控制电压', '线圈电压', 'control_voltage'), 'voltage'), 'leakage_current_ma': ('漏电动作电流', ('额定剩余动作电流', '漏电动作电流', '剩余动作电流', 'leakage_current_ma'), 'ma'), 'material': ('材质', ('材质', '柜体材质', 'material'), 'material'), 'thickness_mm': ('板厚', ('板厚', '柜体板厚', '厚度', 'thickness_mm'), 'mm'), 'cross_section_mm2': ('截面积', ('截面积', '导体截面', 'cross_section_mm2'), 'mm2'), 'color': ('颜色', ('颜色', '表面颜色', 'color'), 'text'), 'installation_mode': ('安装方式', ('安装方式', 'installation_mode'), 'text'), 'operation_mode': ('操作方式', ('操作方式', 'operation_mode'), 'text')}

    def atom(value, kind):
        text = unicodedata.normalize('NFKC', str(value or '')).strip().upper().replace(' ', '')
        if kind == 'material':
            found = set(re.findall('(?<!\\d)(201|304|316L?)(?!\\d)', text))
            return next(iter(found)) if len(found) == 1 else None
        if kind == 'voltage':
            m = re.fullmatch('(AC|DC)?([0-9.]+)(KV|V)?', text)
            return (m.group(1) or '', float(m.group(2)) * (1000 if m.group(3) == 'KV' else 1)) if m else None
        if kind in ('ma', 'mm', 'mm2'):
            m = re.fullmatch('([0-9.]+)(MA|A|MM|MM2|平方毫米)?', text)
            if not m:
                return None
            unit = m.group(2) or ''
            if kind == 'ma' and unit not in ('', 'MA', 'A'):
                return None
            if kind == 'mm' and unit not in ('', 'MM'):
                return None
            if kind == 'mm2' and unit not in ('', 'MM2', '平方毫米'):
                return None
            return float(m.group(1)) * (1000 if kind == 'ma' and unit == 'A' else 1)
        return text
    for field, (label, aliases, kind) in fields.items():
        wanted = requirements.get(field)
        if wanted in (None, '', [], {}):
            continue
        known = next((attrs[product_field_token(k)] for k in aliases if product_field_token(k) in attrs), None)
        if known is None:
            warnings.append('候选记录未证实' + label + '，需工程师核验')
            continue
        left, right = (atom(wanted, kind), atom(known, kind))
        if left is None or right is None:
            warnings.append(label + '文字无法自动等同，需工程师核验')
            continue
        if kind == 'voltage':
            different = left[1] != right[1] or bool(left[0] and right[0] and (left[0] != right[0]))
            if left[0] and (not right[0]):
                warnings.append('候选记录未证实控制电压的AC/DC类型')
        else:
            different = left != right
        if different:
            if kind == 'text':
                warnings.append(label + '描述不同，需工程师核验：' + str(wanted) + ' / ' + str(known))
            else:
                conflicts.append(label + '不一致：要求' + str(wanted) + '，候选' + str(known))

    def model_atom(value):
        value = unicodedata.normalize('NFKC', str(value or '')).upper().replace('×', 'X')
        return re.sub('[^A-Z0-9\\u4e00-\\u9fff]', '', value)
    for field in ('model', 'required_model', 'designated_model', 'specified_model'):
        wanted = requirements.get(field)
        if not wanted:
            continue
        description = model_atom(record.get('物料描述'))
        parts = [model_atom(part) for part in re.split('\\s+', str(wanted)) if model_atom(part)]
        if parts and (not all((part in description for part in parts))):
            conflicts.append('文件指定型号与产品记录不一致：' + str(wanted))
    brands = requirements.get('brand_constraints') or []
    if brands:
        known = next((attrs[product_field_token(k)] for k in ('品牌', '制造商', '产品品牌', 'brand') if product_field_token(k) in attrs), None)
        if known is None:
            warnings.append('候选记录未证实品牌，需工程师核验')
        elif not any((product_field_token(b) in product_field_token(known) for b in brands)):
            conflicts.append('品牌不符合资料限定范围')
    leakage = requirements.get('leakage_required')
    if isinstance(leakage, bool):
        known = next((attrs[product_field_token(k)] for k in ('漏电保护', '漏电功能', 'leakage_required') if product_field_token(k) in attrs), None)
        token = str(known or '').strip().lower()
        actual = True if token in ('是', '有', '带', 'true', '1') else False if token in ('否', '无', '不带', 'false', '0') else None
        if actual is None:
            warnings.append('候选记录未证实漏电保护功能，需工程师核验')
        elif actual != leakage:
            conflicts.append('漏电保护功能与明确要求不一致')
    for field, label in [('required_accessories', '必需附件')]:
        if requirements.get(field) not in (None, '', [], {}, False):
            warnings.append(label + '需工程师核验，系统未自动证明')
    return (conflicts, warnings)


# V18 MongoDB document adapters. PRODUCT/PRICE retrieval may return JSON documents
# from the formal MongoDB-backed Dify datasets; legacy Markdown remains supported.
def _v18_iter_objects(value):
    if isinstance(value, dict):
        if value.get('material_code') or value.get('物料编码'):
            yield value
            return
        for key in ('content', 'text', 'page_content', 'body', 'items', 'result', 'results', 'documents', 'chunks', 'data'):
            if key in value:
                yield from _v18_iter_objects(value[key])
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _v18_iter_objects(item)
        return
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(('{', '[')):
            try:
                yield from _v18_iter_objects(json.loads(text))
                return
            except (ValueError, TypeError):
                pass
        yield value

def _v18_category(obj):
    c = obj.get('category') or {}
    if isinstance(c, dict):
        return str(c.get('middle') or c.get('root') or c.get('leaf') or '').strip(), str(c.get('leaf') or '').strip()
    return str(c).strip(), ''

def _v18_specs(obj):
    text = obj.get('specification_text') or ''
    if text:
        return str(text)
    specs = obj.get('standard_specs') or {}
    parts = []
    if isinstance(specs, dict):
        for key, val in specs.items():
            if isinstance(val, dict):
                value = val.get('value_text') or val.get('value_num') or ''
                unit = val.get('unit') or ''
                parts.append(str(val.get('raw_name') or key) + '=' + str(value) + str(unit))
            elif val not in (None, ''):
                parts.append(str(key) + '=' + str(val))
    attrs = obj.get('source_attributes') or []
    if isinstance(attrs, list):
        for attr in attrs:
            if isinstance(attr, dict) and attr.get('name') and attr.get('value') not in (None, ''):
                parts.append(str(attr['name']) + '=' + str(attr['value']))
    return '；'.join(dict.fromkeys(parts))

def _v18_product(obj):
    code = str(obj.get('material_code') or obj.get('物料编码') or '').strip().upper()
    if not code:
        return None
    if 'material_code' in obj:
        if str(obj.get('data_status') or '').upper() != 'ACTIVE':
            return None
        if str(obj.get('source_status') or '').strip() not in ('有效', 'ACTIVE', '在售'):
            return None
        category, series = _v18_category(obj)
        quality = '；'.join(str(x) for x in (obj.get('quality_flags') or []) if x)
        return {'物料编码': code, '物料描述': str(obj.get('description') or '').strip(),
                '产品类别': category, '产品系列': series, '规格参数': _v18_specs(obj),
                '状态': 'ACTIVE', '数据质量': quality or 'source_status=有效; data_status=ACTIVE'}
    return None

def _v18_price(obj):
    code = str(obj.get('material_code') or obj.get('物料编码') or '').strip().upper()
    if not code:
        return None
    if 'material_code' in obj:
        if str(obj.get('status') or '').upper() != 'ACTIVE':
            return None
        category = str(obj.get('category') or '').strip()
        start = str(obj.get('valid_from') or '')[:10]
        end = str(obj.get('valid_to') or '')[:10]
        return {'物料编码': code, '物料描述': '', '产品类别': category,
                '未税单价': str(obj.get('unit_price_ex_tax') or ''),
                '计价单位': str(obj.get('pricing_unit') or ''),
                '税率': str(obj.get('tax_rate') if obj.get('tax_rate') is not None else ''),
                '有效期': start + '至' + end, '价格版本': str(obj.get('price_version') or ''),
                '审批状态': 'ACTIVE', '价格依据': str(obj.get('price_basis') or '')}
    return None

def _v18_legacy_records(value, fields):
    out = []
    labels = '|'.join(re.escape(x) for x in fields)
    code_re = re.compile(r'(?:物料编码|material_code)\s*[:：]\s*([A-Z][A-Z0-9_-]{5,}|\d{8,16})(?![A-Z0-9_-])', re.I)
    for raw in _v18_iter_objects(value):
        if not isinstance(raw, str):
            continue
        matches = list(code_re.finditer(raw))
        for i, hit in enumerate(matches):
            block = raw[hit.start():matches[i+1].start() if i + 1 < len(matches) else len(raw)]
            row = {'物料编码': hit.group(1).upper()}
            for key in fields[1:]:
                m = re.search(r'(?:^|\s)' + re.escape(key) + r'\s*[:：]\s*(.*?)(?=\s*(?:' + labels + r')\s*[:：]|\Z)', block, re.S | re.I)
                row[key] = re.sub(r'\s+', ' ', m.group(1)).strip() if m else ''
            if row.get('物料描述') and (row.get('规格参数') if fields == PRODUCT_FIELDS else row.get('未税单价')):
                out.append(row)
    return out

def parse_records(value, fields):
    out = []
    for obj in _v18_iter_objects(value):
        if isinstance(obj, dict):
            row = _v18_product(obj) if fields == PRODUCT_FIELDS else _v18_price(obj)
            if row:
                out.append(row)
    out.extend(_v18_legacy_records(value, fields))
    return out

_legacy_parse_products_v18 = parse_products
def parse_products(result):
    rows = []
    for obj in _v18_iter_objects(result):
        if isinstance(obj, dict):
            row = _v18_product(obj)
            if row:
                rows.append(row)
    # Preserve compatibility with existing Markdown PRODUCT records.
    rows.extend(_legacy_parse_products_v18(result))
    unique = {}
    for row in rows:
        unique.setdefault((row.get('物料编码'), row.get('物料描述'), row.get('规格参数'), row.get('状态')), row)
    return list(unique.values())
_v182_category_base=canonical_category_name
def canonical_category_name(value):
    known=_v182_category_base(value)
    if known:return known
    text=re.sub(r'[\s/／_-]','',clean(value))
    # Position and AC/DC qualifiers are checked separately from the product family.
    text=re.sub(r'^(?:(?:进线|出线|主回路|支路|交流|直流|低压|高压|三相|单相|通用))+','',text)
    return text[:-1] if text.endswith('类') else text
def v182_accessory_signature(record):
    text=str(record.get('物料描述') or '')+' '+str(record.get('规格参数') or '')
    tokens=set(re.findall(r'(?<![A-Z0-9])(?:MX\d*|OF\d*|SD\d*|MN\d*)(?![A-Z0-9])',text.upper()))
    if re.search(r'(?<![A-Z0-9])F(?![A-Z0-9])',str(record.get('物料描述') or '').upper()):tokens.add('摇信F')
    if '一体式' in text:tokens.add('一体式')
    return tuple(sorted(tokens))


_v182_candidate_base=candidate_check
def candidate_check(req,rec):
    params=req.get('requirements') or {}
    copy_req=dict(req);copy_req['requirements']=dict(params);copy_req['requirements'].pop('accessories_explicit_none',None)
    failures,warnings,score=_v182_candidate_base(copy_req,rec)
    exact=decimal(params.get('rated_current_a'))
    if exact is not None:
        attrs=product_attribute_map(rec)
        value=next((attrs.get(product_field_token(key)) for key in ('额定电流','额定工作电流','rated_current_a') if attrs.get(product_field_token(key)) not in (None,'')),None)
        match=re.fullmatch(r'\s*([0-9]+(?:\.[0-9]+)?)\s*A?\s*',str(value or ''),re.I)
        if match and decimal(match.group(1))!=exact:
            failures.append('文件明确额定电流为%sA，候选为%sA，不能仅按不低于要求替代'%(exact,match.group(1)))
    if params.get('accessories_explicit_none') is True and v182_accessory_signature(rec):
        failures.append('资料明确不带附件，候选带附件或附加功能')
    return failures,warnings,score


_v182_ambiguity_base=candidate_ambiguities
def candidate_ambiguities(req,candidates):
    reasons=_v182_ambiguity_base(req,candidates)
    params=req.get('requirements') or {}
    signatures={v182_accessory_signature(row[2]) for row in candidates}
    if len(signatures)>1:
        # A positive accessory requirement does not resolve other optional extra accessories.
        reasons.append('候选的附件或附加功能不同（基础款、辅助触点、分励脱扣、摇信或一体式等）；需明确最终型号和编码，不能按检索顺序默认选定')
    return list(dict.fromkeys(reasons))

"""Keep an explicit pole configuration intact; 4P and 3P+N are distinct."""
import unicodedata as _v1821_unicode


def v1821_pole_token(value):
    text = _v1821_unicode.normalize('NFKC', str(value or '')).upper()
    text = re.sub(r'\s+', '', text).replace('极', 'P')
    return text if re.fullmatch(r'[1-9][0-9]?P(?:\+N)?', text) else ''


def v1821_pole_error(required, record):
    if required in (None, ''):
        return ''
    wanted = v1821_pole_token(required)
    if not wanted:
        return '资料极数格式无法自动核验，需明确完整极数及中性线配置'
    attrs = product_attribute_map(record)
    values = [attrs.get(product_field_token(key)) for key in ('极数', 'pole_count')]
    actual = {v1821_pole_token(value) for value in values if v1821_pole_token(value)}
    description = _v1821_unicode.normalize('NFKC', str(record.get('物料描述') or '')).upper()
    actual.update(re.sub(r'\s+', '', hit) for hit in re.findall(
        r'(?<![A-Z0-9])([1-9][0-9]?\s*P(?:\s*\+\s*N)?)(?![A-Z0-9+])', description))
    if not actual:
        return '候选记录未证实完整极数配置，不能默认满足' + wanted
    if len(actual) != 1:
        return '候选极数记录相互冲突：' + '/'.join(sorted(actual))
    found = next(iter(actual))
    if wanted != found:
        return '极数不一致：要求%s，候选%s；4P与3P+N不能自动互换' % (wanted, found)
    return ''

_v1821_category_base = canonical_category_name


def canonical_category_name(value):
    # This is a specific family alias, not a substring/fuzzy category bypass.
    text = re.sub(r'\s+', '', _v1821_unicode.normalize('NFKC', str(value or '')))
    if text in ('后备保护器', '后备保护器类'):
        return 'SPD后备保护器'
    return _v1821_category_base(value)


_v1821_candidate_base = candidate_check


def candidate_check(req, rec):
    failures, warnings, score = _v1821_candidate_base(req, rec)
    error = v1821_pole_error((req.get('requirements') or {}).get('pole_count'), rec)
    if error:
        failures.append(error)
    return failures, warnings, score
