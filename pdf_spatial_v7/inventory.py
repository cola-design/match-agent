import re
from literal_decode import norm, decode
from transport import canonical

def source_inventory(text):
    """Unordered source-text mentions, not geometry or purchase quantities."""
    source = norm(text); candidates = []; markers = []; occupied = []
    patterns = [
        r'(?<![A-Z0-9])(?:\d+\s*[*x×]\s*\()?WDZ[A-Z0-9]*-[A-Z0-9.*x×/+(),\-]+(?:\s+(?:CE|WE|WC|CC|FC)\b)*',
        r'(?<![A-Z0-9])(?:NHBV|ZNBV|BVR?)-[A-Z0-9.*x×/+,\-]+',
        r'(?<![A-Z0-9])(?:UTP|STP)\s*/\s*CAT\s*[5-8](?:E|A)?(?![A-Z0-9])',
        r'(?<![A-Z0-9])(?:RCBO|RCCB|RCB|RCD)[A-Z0-9/+.()\-]*(?:\s+\d+(?:\.\d+)?\s*mA)?',
        r'(?<![A-Z0-9])(?:MCCB|MCB|ATS)[A-Z0-9/+.()\-]*',
        r'(?<![A-Z0-9])[A-Z][A-Z0-9]{1,14}-[A-Z0-9][A-Z0-9./+()\-]{1,70}',
        r'(?<![A-Z0-9])(?:AC|DC)\s*\d+(?:\.\d+)?\s*V\s+\d+(?:\.\d+)?\s*A',
        r'(?<![A-Z0-9])\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\s*V',
        r'时间控制器|时控开关|(?<![A-Z0-9])(?:KM|KA|KT|FU|SPD)\d*(?![A-Z0-9])',
    ]
    found = []
    for pattern in patterns:
        for m in re.finditer(pattern, source, re.I):
            if any(m.start() < b and m.end() > a for a, b in occupied): continue
            raw = m[0].strip()
            if decode(raw)['kind'] in ('other', 'calculation', 'phase', 'circuit_tag'): continue
            found.append((m.start(), raw)); occupied.append((m.start(), m.end()))
    for start, raw in sorted(found):
        old = next((x for x in candidates if canonical(x['raw']) == canonical(raw)), None)
        if old: old['text_mentions'] += 1
        else: candidates.append({'id': 'S%d' % (len(candidates) + 1), 'raw': raw,
                                 'kind': decode(raw)['kind'], 'text_mentions': 1})
    marker_pattern = r'(?:PC|CB)\s*级|(?<![A-Z0-9])\d+(?:\.\d+)?\s*(?:mA|kA|VA)\b'
    for m in re.finditer(marker_pattern, source, re.I):
        if any(m.start() < b and m.end() > a for a, b in occupied): continue
        raw = m[0]
        if not any(canonical(x['raw']) == canonical(raw) for x in markers):
            markers.append({'id': 'M%d' % (len(markers) + 1), 'raw': raw, 'kind': decode(raw)['kind']})
    tags = sorted(set(m[0] for m in re.finditer(r'(?<![0-9A-Z])\d{1,3}#(?![A-Z\u4e00-\u9fff])', source)),
                  key=lambda x: int(x[:-1]))
    return {'candidates': candidates, 'independent_markers': markers, 'circuit_tag_mentions': tags,
            'scope': '全文件无序文字提示；出现次数不是采购数量；不是当前页定位证据'}


