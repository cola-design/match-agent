"""Apply one final evidence contract to the tested Dify 1.8 V31 application."""
import ast
from pathlib import Path
import os
import json
import yaml

ROOT=Path(__file__).resolve().parent
BASE=Path(os.environ.get('V32_BASE_YML',ROOT.parent/'downloads/cabinet_project_v31.yml'))
if not BASE.exists():BASE=ROOT.parent/'recognition-v31-20261109/cabinet_project_v31.yml'


def code_for(fn,args):
    body=[];modules={'transport','inventory','literal_decode','spatial','native_workflow','pdf_native','evidence_contract'}
    for filename in ('transport.py','literal_decode.py','inventory.py','png_crop.py','spatial.py','pdf_native.py','evidence_contract.py','native_workflow.py'):
        body.extend(n for n in ast.parse((ROOT/filename).read_text()).body if not
                    (isinstance(n,ast.ImportFrom) and n.module in modules))
    funcs={n.name:n for n in body if isinstance(n,ast.FunctionDef)};needed=set();pending=[fn]
    while pending:
        name=pending.pop()
        if name in needed:continue
        needed.add(name);pending.extend(n.id for n in ast.walk(funcs[name]) if isinstance(n,ast.Name) and n.id in funcs)
    out=[n for n in body if isinstance(n,(ast.Import,ast.ImportFrom,ast.Assign)) or isinstance(n,ast.FunctionDef) and n.name in needed]
    text='\n'.join(ast.unparse(n) for n in out)+'\n\ndef main('+','.join(args)+'):\n    return '+fn+'('+','.join(args)+')\n'
    compile(text,fn,'exec');return text


def build():
    doc=yaml.safe_load(BASE.read_text());nodes=doc['workflow']['graph']['nodes'];titles={n['data']['title']:n['data'] for n in nodes}
    collector=titles['按原生文字身份合并，交叉核对回路和负荷']
    collector['code']=code_for('collect_native',['packets','repair_packets','manifest_compact'])
    for node in nodes:
        d=node['data']
        if d['type']=='llm':
            d['prompt_template'][0]['text']+='\n最终校验补充：变压比/VA容量不属于邻近MCB；相别、线缆、主用备用等连接说明不能作为出线编号。时控符号中的t为功能标记，放note并关联时控器，不单列器件。30mA等标记保留原文与对应支路，不擅自修改图面器件类别。'
    bridge=titles['把识图结果转为待人工核对的项目清单']
    old="p = dict(x.get('literal_parameters') or {})\n        p.update(x.get('associated_parameters') or {})"
    assert old in bridge['code']
    bridge['code']=bridge['code'].replace(old,"p = dict(x['selection_parameters']) if 'selection_parameters' in x else dict(x.get('literal_parameters') or {})\n        if 'selection_parameters' not in x:p.update(x.get('associated_parameters') or {})")
    bridge['code']=bridge['code'].replace("raw_result=json.loads(result_json)","raw_result=json.loads(result_json)\n    if raw_result.get('summary',{}).get('validation_errors'):\n        return {'valid':'NO','state_json':state_json,'message':'识图交付校验未通过，本轮未覆盖原项目：'+'；'.join(raw_result['summary']['validation_errors'])}")
    marker="value['state_json']=pack(state);return value"
    assert marker in bridge['code']
    bridge['code']=bridge['code'].replace(marker,"""
    state['recognition_audit']=[r for p in packets for r in p.get('validation_audit',[])]
    state['connection_annotations']=[r for p in packets for r in p.get('connection_annotations',[])]
    for r in state['recognition_audit']:
        if r.get('item_id') in identity_map:r['item_id']=identity_map[r['item_id']]
    value['state_json']=pack(state);return value""")
    form=titles['生成当前阶段确认表，保留项目版本与签名'];needle='append_approver(state, sheets, text)'
    assert needle in form['code']
    form['code']=form['code'].replace(needle,"""
    if stage=='SOURCE_REVIEW':
        audit_rows=[['TECH','AUDIT_'+str(i),r.get('item_id') or '',r.get('raw',''),r['message']] for i,r in enumerate(state.get('recognition_audit',[]),1)]
        if audit_rows:
            for start in range(0,len(audit_rows),100):
                sheets.append(sheet('校验修正记录'+('' if len(audit_rows)<=100 else '_'+str(start//100+1)),['记录类型','行ID','关联清单行','资料原文','处理说明'],audit_rows[start:start+100],widths=[14,22,20,50,110]))
        roles={}
        for r in state.get('connection_annotations',[]):roles[(r.get('page'),r['raw'])]=roles.get((r.get('page'),r['raw']),0)+1
        role_rows=[['TECH','CONNECTION_'+str(i),page,raw,'作为相别、连接或来源标注保留，不当作出线编号；'+str(count)+'处观测'] for i,((page,raw),count) in enumerate(roles.items(),1)]
        for start in range(0,len(role_rows),100):
            sheets.append(sheet('连接与来源标注'+('' if len(role_rows)<=100 else '_'+str(start//100+1)),['记录类型','行ID','页码','原文','说明'],role_rows[start:start+100],widths=[14,26,14,55,100]))
    """+needle)
    doc['app']['name']='成套柜项目助手 V32（统一证据校验）'
    doc['app']['description']='保留原生PDF定位与定点补漏，统一校验参数归属、器件身份、回路及当前疑点；选型确认后再报价。'
    doc['dependencies']=[]
    for n in nodes:
        if n['data']['type']=='code':compile(n['data']['code'],n['data']['title'],'exec')
    (ROOT/'cabinet_project_v32.yml').write_text(yaml.safe_dump(doc,allow_unicode=True,sort_keys=False))
    print(json.dumps({'nodes':len(nodes),'edges':len(doc['workflow']['graph']['edges'])}));return doc

if __name__=='__main__':build()
