"""Generate explicitly labelled offline layout previews from supplied case/API fixtures."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'workflow_tools/tests'))
from case_helpers import selected_case,load_node,fill_output,priced_example


def render():
    selected,_=selected_case()
    writer=load_node('1800000000103')
    out=writer['main'](selected['confirm_markdown'],selected['selection_json'])
    payload=json.loads(out['payload_json'])
    payload['cells']['BOM确认!A1']='排版预览｜50kW资料BOM确认单（尚未人工确认）'
    out['payload_json']=json.dumps(payload,ensure_ascii=False)
    (ROOT/'audit_evidence/v18_2_bom_preview.xlsx').write_bytes(fill_output(out))
    quote=load_node('3400000000006')['main']([priced_example()])
    payload=json.loads(quote['quote_payload_json'])
    payload['cells']['01 报价汇总!A2']='单项材料参考报价｜版式预览'
    payload['cells']['01 报价汇总!A3']='仅展示已验证的1项物料，不代表50kW项目完整BOM或项目总报价。'
    quote['quote_payload_json']=json.dumps(payload,ensure_ascii=False)
    (ROOT/'audit_evidence/v18_2_quote_layout_preview.xlsx').write_bytes(fill_output(quote,'quote_payload_json'))
    return selected,quote


if __name__=='__main__':render()
