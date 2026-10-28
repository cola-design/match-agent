
_V182_MARKUP=Decimal('0.15')
_V182_DISCOUNT=Decimal('0.02')
_v182_quote_base=main
def main(results,confirmed_bom_json='',markup_rate='0.15',discount_rate='0.02'):
    global _V182_MARKUP,_V182_DISCOUNT
    try:
        _V182_MARKUP=Decimal(str(markup_rate));_V182_DISCOUNT=Decimal(str(discount_rate))
        if not _V182_MARKUP.is_finite() or _V182_MARKUP<0:raise ValueError()
        if not _V182_DISCOUNT.is_finite() or not Decimal('0')<=_V182_DISCOUNT<=Decimal('1'):raise ValueError()
    except Exception:
        raise ValueError('报价加成须为非负有限数，折扣须为0到1之间的有限数；请检查环境变量')
    out=_v182_quote_base(results,confirmed_bom_json)
    if isinstance(results,str):
        try:rows=json.loads(results)
        except (TypeError,ValueError):rows=[]
    else:rows=results
    if isinstance(rows,dict):rows=rows.get('output',rows.get('results',[rows]))
    decoded=[]
    for row in rows if isinstance(rows,list) else []:
        try:row=json.loads(row) if isinstance(row,str) else row
        except (TypeError,ValueError):continue
        if isinstance(row,dict):decoded.append(row)
    confirmed={'SUPPLIER_QUOTE','CONTRACT_PRICE','ACTUAL_PURCHASE','APPROVED_PURCHASE_PRICE','VERIFIED_MARKET_PRICE'}
    references=[row for row in decoded if row.get('item_status')=='PRICED' and (row.get('reference_only') is True or str(row.get('price_basis') or '').upper() not in confirmed)]
    out['reference_price_count']=len(references)
    out['quote_title']='材料参考报价（含估算或未核验价格）' if references else '材料报价'
    if out.get('pricing_status')=='READY':
        payload=wire_loads(out['quote_payload_json']);cells=payload['cells']
        cells['01 报价汇总!A2']=out['quote_title']
        cells['01 报价汇总!B7']=out['quote_title']+('｜单柜用量' if '单柜' in out['pricing_message'] else '')
        cells['01 报价汇总!A3']='估算价格不等于真实采购核价；依据、版本、有效期及单位换算见明细。' if references else '价格依据、版本、有效期及计价单位见明细；按企业审核流程使用。'
        priced=[row for row in decoded if row.get('item_status')=='PRICED']
        for number,row in enumerate(priced,4):
            label='参考价格，尚未经采购核验' if row in references else '来源依据见价格版本'
            suffix='；BOM数量单位：'+str(row.get('unit') or '')+'；计价单位：'+str(row.get('pricing_unit') or row.get('unit') or '')
            if row.get('unit_conversion_note'):suffix+='；'+str(row['unit_conversion_note'])
            cells['02 报价明细!L'+str(number)]=str(cells.get('02 报价明细!L'+str(number)) or '')+'；'+label+suffix
        cells.update({'03 缺价与说明!A4':1,'03 缺价与说明!D4':'参考价格说明' if references else '报价范围',
                      '03 缺价与说明!E4':('本批有'+str(len(references))+'项估算或未核验价格；金额按现有价格记录计算，不能据此认定为真实采购价。') if references else '本次价格字段和有效期校验通过；价格依据按明细所列来源执行。',
                      '03 缺价与说明!F4':'仅材料费用；人工、加工、检验和运输等未计入。'})
        payload['output_filename']='成套柜材料参考报价_'+out['quote_no'] if references else '成套柜材料报价_'+out['quote_no']
        out['output_filename']=payload['output_filename'];out['quote_payload_json']=plain_json(payload)
        if references:out['pricing_message']='本批含%d项估算或未核验价格，以下为材料参考计算。'%len(references)+out['pricing_message']
    return out
