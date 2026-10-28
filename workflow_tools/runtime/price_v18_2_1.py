_v1821_price_base = main


def main(item_json, material_code, product_result, price_result,
         primary_product_result=None, primary_price_result=None, product_gate='', price_gate=''):
    out = _v1821_price_base(item_json, material_code, product_result, price_result,
                          primary_product_result, primary_price_result, product_gate, price_gate)
    row = json.loads(out['quote_item_json'])
    if row.get('item_status') == 'PRICED':
        item = json.loads(item_json) if isinstance(item_json, str) else item_json
        requirements = item.get('requirements_json') or '{}'
        requirements = json.loads(requirements) if isinstance(requirements, str) else requirements
        records = parse_records(product_result, PRODUCT_FIELDS) + parse_records(primary_product_result, PRODUCT_FIELDS)
        for product in records:
            if str(product.get('物料编码') or '') != str(material_code):
                continue
            error = v1821_pole_error(requirements.get('pole_count'), product)
            if error:
                stop(row, 'TECH_SPEC_MISMATCH', error)
                break
    out['item_status'] = row['item_status']
    out['quote_item_json'] = json.dumps(row, ensure_ascii=False)
    return out
