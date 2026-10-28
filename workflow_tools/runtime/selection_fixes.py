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
