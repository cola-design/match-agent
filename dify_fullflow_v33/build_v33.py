"""Dify 1.8 application: scope-aware quotation plus native vector transformer evidence."""
import ast
from pathlib import Path
import json
import os
import yaml

ROOT=Path(__file__).resolve().parent
BASE=Path(os.environ.get('V33_BASE_YML',ROOT.parent/'downloads/cabinet_project_v32.yml'))
if not BASE.exists():BASE=ROOT.parent/'recognition-v32-20261109/cabinet_project_v32.yml'


def code_for(fn,args):
    body=[];modules={'transport','inventory','literal_decode','spatial','native_workflow','pdf_native','evidence_contract','vector_symbols','line_scopes'}
    for filename in ('transport.py','literal_decode.py','inventory.py','png_crop.py','spatial.py','pdf_native.py','evidence_contract.py','vector_symbols.py','line_scopes.py','native_workflow.py'):
        body.extend(n for n in ast.parse((ROOT/filename).read_text()).body if not(isinstance(n,ast.ImportFrom) and n.module in modules))
    funcs={n.name:n for n in body if isinstance(n,ast.FunctionDef)};needed=set();pending=[fn]
    while pending:
        name=pending.pop()
        if name in needed:continue
        needed.add(name);pending.extend(n.id for n in ast.walk(funcs[name]) if isinstance(n,ast.Name) and n.id in funcs)
    out=[n for n in body if isinstance(n,(ast.Import,ast.ImportFrom,ast.Assign)) or isinstance(n,ast.FunctionDef) and n.name in needed]
    text='\n'.join(ast.unparse(n) for n in out)+'\n\ndef main('+','.join(args)+'):\n    return '+fn+'('+','.join(args)+')\n'
    compile(text,fn,'exec');return text


def helper(fn,args):
    return '\n'.join(ast.unparse(n) for n in ast.parse(code_for(fn,args)).body if not(isinstance(n,ast.FunctionDef) and n.name=='main'))+'\n'


def output_scope_sheets(state):
    refs=state.get('line_references',[])
    if not refs:return []
    result=[{'name':'报价范围说明','headers':['记录类型','行ID','说明'],'rows':[
        ['TECH','SCOPE','本次金额以已确认采购报价表为准；线路参考未纳入采购数量和金额，不代表已确定合同供货责任。'],
        ['TECH','INTERNAL','柜内接线、母排等成本仍需按工艺定额核对或补录，不以柜外线路标注替代。']],
        'editable':set(),'widths':[14,24,130]}]
    rows=[['TECH',x['row_id'],x.get('device_tag',''),x['shown_model'],x['reference_reason']] for x in refs]
    for start in range(0,len(rows),100):
        result.append({'name':'线路参考_未计价'+('' if len(rows)<=100 else '_'+str(start//100+1)),
            'headers':['记录类型','原图行ID','回路或用途','图面线路标注','范围说明'],'rows':rows[start:start+100],
            'editable':set(),'widths':[14,20,24,65,105]})
    return result


def build():
    doc=yaml.safe_load(BASE.read_text());nodes=doc['workflow']['graph']['nodes'];by={n['data']['title']:n['data'] for n in nodes}
    by['读取原生文字及真实位置，不执行PDF内容']['code']=code_for('native_pdf_node',['encoded'])
    by['按真实文字位置定位漏核项，准备定点放大']['code']=code_for('native_repairs',['packets','manifest_compact'])
    by['按原生文字身份合并，交叉核对回路和负荷']['code']=code_for('collect_native',['packets','repair_packets','manifest_compact'])
    bridge=by['把识图结果转为待人工核对的项目清单']
    bridge['code']+='\n_scope_bridge_main=main\n'+helper('partition_line_references',['state'])+'''
def main(state_json,result_json,result_pages):
    value=_scope_bridge_main(state_json,result_json,result_pages)
    if value.get('valid')!='YES':return value
    try:
        state=unpack(value['state_json'])
        # Normalize issue identities to the same confirmation-sheet row IDs.
        result=json.loads(result_json);parts=[json.loads(p) for p in result_pages] if result.get('details_in') else [result]
        origins=[x for p in parts for x in p.get('items',[])]
        identities={x['id']:r['row_id'] for x,r in zip(origins,state['facts']['items'])}
        for q in state.get('recognition_issues',[]):
            if q.get('item_id') in identities:q['item_id']=identities[q['item_id']]
        state=partition_line_references(state)
        value['state_json']=pack(state)
        value['message']+='\\n已将柜外或范围未明确的线路保留为参考；仅明确纳入的项目进入采购报价。'
        return value
    except Exception as exc:
        return {'valid':'NO','state_json':state_json,'message':str(exc)}
'''
    form=by['生成当前阶段确认表，保留项目版本与签名']
    form['code']+=helper('reference_form_rows',['state'])
    needle='append_approver(state, sheets, text)';assert needle in form['code']
    form['code']=form['code'].replace(needle,"""
    if stage=='SOURCE_REVIEW' and state.get('line_references'):
        headers=['记录类型','行ID','图面线路标注','含义','默认范围说明','是否由我方采购并计价','归属柜组','回路或用途','确认单柜采购用量','单位','物料编码','指定厂商','采购方式','外购或非标依据','纳入依据','确认','原图位置']
        rows=reference_form_rows(state)
        for start in range(0,len(rows),100):
            sheets.append(sheet('线路参考_默认不报价'+('' if len(rows)<=100 else '_'+str(start//100+1)),headers,rows[start:start+100],{5,6,7,8,10,11,12,13,14,15},[14,18,55,70,85,25,18,24,24,12,24,24,24,65,65,16,65]))
        text+=' 线路参考默认不报价，不用填写长度和确认。若由我方采购计价，改为是，并填写单柜采购用量、纳入依据和确认；按所选物料计量，多根BYJ电线应核对合计米数。柜内接线和母排成本需按工艺另行核对或补录。'
    """+needle)
    reader=by['校验问题清单的项目、版本、固定列与确认人']
    reader['code']=reader['code'].replace("'SOURCEADD', 'QUOTE', 'TECH')", "'SOURCEADD', 'QUOTE', 'TECH', 'REF')")
    reader['code']=reader['code'].replace("if not kept:\n            raise ValueError('没有保留任何元件，不能进入选型')", "if not kept and not state.get('line_references'):\n            raise ValueError('没有保留任何元件，不能进入选型')")
    reader['code']=reader['code'].replace('def read_stage_return(', 'def _previous_read_stage_return(',1)
    reader['code']+=helper('apply_line_reference_choices',['state','original_refs','records','approver','review_source'])+'''
def read_stage_return(state_json,encoded,kind):
    original=unpack(state_json)
    records,approver=parse_form(original,xlsx_markdown(encoded),kind)
    value=_previous_read_stage_return(state_json,encoded,kind)
    state=unpack(value['state_json'])
    state=apply_line_reference_choices(state,original.get('line_references',[]),records,approver,reviewed_source_item)
    if not state['facts']['items']:raise ValueError('当前没有确认纳入报价的项目；请核对器件，或明确纳入线路参考项。')
    stamp(state,'line_scope_confirmed',approver)
    value['state_json']=pack(state);return value
'''
    production=by['仅在报价确认后计算采购报价与排产文件']
    tree=ast.parse(production['code'])
    for function in tree.body:
        if isinstance(function,ast.FunctionDef) and function.name=='production_files':
            for n in ast.walk(function):
                if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='workbook':
                    n.args[0]=ast.BinOp(left=n.args[0],op=ast.Add(),right=ast.Call(func=ast.Name(id='output_scope_sheets',ctx=ast.Load()),args=[ast.Name(id='state',ctx=ast.Load())],keywords=[]))
    production['code']=ast.unparse(ast.fix_missing_locations(tree))+'\n'+ast.unparse(ast.parse(__import__('inspect').getsource(output_scope_sheets)))+'\n'
    doc['app']['name']='成套柜项目助手 V33（成套供货范围与矢量符号核对）'
    doc['app']['description']='柜外及范围未明确线路默认仅参考，可人工明确纳入；原生双线圈、引线、电压比及容量联合核对变压器。'
    doc['dependencies']=[]
    doc['workflow']['features']['opening_statement']+=' 柜外或范围未明确的线路默认保留参考、不自动报价；需要我方供货时在确认表明确纳入。'
    for n in nodes:
        if n['data']['type']=='code':compile(n['data']['code'],n['data']['title'],'exec')
    (ROOT/'cabinet_project_v33.yml').write_text(yaml.safe_dump(doc,allow_unicode=True,sort_keys=False))
    print(json.dumps({'nodes':len(nodes),'edges':len(doc['workflow']['graph']['edges'])}));return doc

if __name__=='__main__':build()
