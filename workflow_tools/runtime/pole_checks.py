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
