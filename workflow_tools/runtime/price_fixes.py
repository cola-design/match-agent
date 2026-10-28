
_V182_VERIFIED_BASES={'SUPPLIER_QUOTE','CONTRACT_PRICE','ACTUAL_PURCHASE','APPROVED_PURCHASE_PRICE','VERIFIED_MARKET_PRICE'}
_v182_price_base=main
def main(item_json,material_code,product_result,price_result,primary_product_result=None,primary_price_result=None,product_gate='',price_gate=''):
    out=_v182_price_base(item_json,material_code,product_result,price_result,primary_product_result,primary_price_result,product_gate,price_gate)
    row=json.loads(out['quote_item_json'])
    if row.get('item_status')=='PRICED':
        source_item=json.loads(item_json) if isinstance(item_json,str) else item_json
        requirements=source_item.get('requirements_json') or '{}'
        requirements=json.loads(requirements) if isinstance(requirements,str) else requirements
        wanted=dec(requirements.get('rated_current_a')) if isinstance(requirements,dict) else None
        if wanted is not None:
            records=parse_records(product_result,PRODUCT_FIELDS)+parse_records(primary_product_result,PRODUCT_FIELDS)
            for product in records:
                if str(product.get('物料编码') or '')!=str(material_code):continue
                attrs=product_attribute_map(product)
                value=next((attrs.get(product_field_token(key)) for key in ('额定电流','额定工作电流','rated_current_a') if attrs.get(product_field_token(key)) not in (None,'')),None)
                hit=re.fullmatch(r'\s*([0-9]+(?:\.[0-9]+)?)\s*A?\s*',str(value or ''),re.I)
                if hit and dec(hit.group(1))!=wanted:
                    row['item_status']='PRODUCT_PARAMETER_MISMATCH'
                    row['issue']='产品额定电流与原始明确要求不一致，需重新选型，不能直接报价'
        out['item_status']=row['item_status']
        basis=str(row.get('price_basis') or '').upper()
        row['reference_only']=basis not in _V182_VERIFIED_BASES
        row['price_basis_label']='估算或未核验来源，仅作材料参考' if row['reference_only'] else '已声明的供应商/采购核价来源，仍按企业审核流程使用'
    out['quote_item_json']=json.dumps(row,ensure_ascii=False)
    return out
