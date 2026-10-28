"""Pure product normalization shared by the API and the explicit database repair tool.

No database writes. Original specification text is never replaced by guessed values.
"""
from copy import deepcopy
import json
import math
import re
import unicodedata


class ProductContractError(ValueError):
    def __init__(self, field):
        self.field = field
        super().__init__('Invalid product field: ' + field)


STRUCTURED_FIELDS = {
    'category': dict, 'standard_specs': dict, 'source_attributes': list,
    'quality_flags': list, 'provenance': dict,
}
NON_NUMERIC_FIELDS = {'model', 'accessory_code', 'special_note', 'ip_rating', 'controller_type',
                      'trip_mechanism', 'protection_type', 'mounting_method'}
SCALAR = re.compile(
    r'(?:(AC|DC)\s*)?([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*'
    r'(mm2|mm²|kVA|VA|kvar|kW|W|kWh|kHz|Hz|mA|kA|kV|V|A|P|mm|ms|s|MΩ|Ω|μF|uF|%|℃|°C)?',
    re.I,
)
UNIT_NAMES = {name.casefold(): name for name in ('mm2','kVA','VA','kvar','kW','W','kWh','kHz','Hz',
              'mA','kA','kV','V','A','P','mm','ms','s','MΩ','Ω','μF','uF','%','℃','°C')}


def decode_structured(value, expected_type, field):
    for _ in range(2):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            raise ProductContractError(field) from None
    if not isinstance(value, expected_type):
        raise ProductContractError(field)
    return value


def normalize_spec(value, field):
    if isinstance(value, list):
        return [normalize_spec(item, field) for item in value]
    if not isinstance(value, dict):
        raise ProductContractError('standard_specs.' + field)
    item = deepcopy(value)
    text = str(item.get('value_text') or '')
    # Derived numbers are rebuilt from a complete scalar, never a substring of a model/range.
    item.pop('value_num', None)
    item.pop('unit', None)
    normalized = unicodedata.normalize('NFKC', text).strip()
    if normalized in ('', '/'):
        item['is_empty'] = True
        return item
    item.pop('is_empty', None)
    match = SCALAR.fullmatch(normalized) if field not in NON_NUMERIC_FIELDS else None
    if match:
        number = float(match.group(2))
        if math.isfinite(number):
            item['value_num'] = int(number) if number.is_integer() else number
            if match.group(3):
                item['unit'] = UNIT_NAMES.get(match.group(3).casefold(), match.group(3))
    return item


def normalize_product(document):
    result = deepcopy(document)
    if not isinstance(result.get('material_code'), str) or not result['material_code'].strip():
        # Never guess leading zeros for codes imported as numbers.
        raise ProductContractError('material_code')
    for field, expected in STRUCTURED_FIELDS.items():
        if field not in result:
            if field == 'provenance':
                continue
            raise ProductContractError(field)
        result[field] = decode_structured(result[field], expected, field)
    if any(not isinstance(a, dict) for a in result['source_attributes']):
        raise ProductContractError('source_attributes')
    if any(not isinstance(flag, str) for flag in result['quality_flags']):
        raise ProductContractError('quality_flags')
    result['standard_specs'] = {key: normalize_spec(value, key)
                                for key, value in result['standard_specs'].items()}
    if 'item_id' in result and result['item_id'] is not None:
        result['item_id'] = str(result['item_id'])
    return result
