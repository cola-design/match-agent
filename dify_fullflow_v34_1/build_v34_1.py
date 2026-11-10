"""Build a Dify 1.8 revision from the public V33 baseline and pure Python modules."""
import ast,inspect,json,os
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parent
BASE=Path(os.environ.get('V34_BASE_YML',str(ROOT.parent/'downloads/cabinet_project_v33.yml')))
if not BASE.exists():BASE=ROOT.parent/'recognition-v33-20261109/cabinet_project_v33.yml'


def flatten(names):
    modules={Path(n).stem for n in names};body=[]
    for filename in names:
        body += [n for n in ast.parse((ROOT/filename).read_text()).body if not(isinstance(n,ast.ImportFrom) and n.module in modules)]
    return '\n'.join(ast.unparse(n) for n in body)+'\n'


def recognition_code(fn,args):
    text=flatten(['transport.py','literal_decode.py','inventory.py','png_crop.py','spatial.py','pdf_native.py','refinements.py','evidence_contract.py','vector_symbols.py','native_workflow.py'])
    tree=ast.parse(text);funcs={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)};needed=set();pending=[fn]
    while pending:
        name=pending.pop()
        if name in needed:continue
        needed.add(name);pending.extend(n.id for n in ast.walk(funcs[name]) if isinstance(n,ast.Name) and n.id in funcs)
    body=[n for n in tree.body if not isinstance(n,ast.FunctionDef) or n.name in needed]
    return '\n'.join(ast.unparse(n) for n in body)+'\ndef main('+','.join(args)+'):\n    return '+fn+'('+','.join(args)+')\n'


def build():
    doc=yaml.safe_load(BASE.read_text());nodes=doc['workflow']['graph']['nodes'];by={n['data']['title']:n['data'] for n in nodes}
    specs=[('读取原生文字及真实位置，不执行PDF内容','native_pdf_node',['encoded']),('按真实文字位置定位漏核项，准备定点放大','native_repairs',['packets','manifest_compact']),('按原生文字身份合并，交叉核对回路和负荷','collect_native',['packets','repair_packets','manifest_compact'])]
    # native_parse is embedded in several iteration code nodes.
    for node in nodes:
        data=node['data']
        if data.get('type')=='code' and 'def native_parse(' in data.get('code',''):
            data['code']=recognition_code('native_parse',['raw','context_json'])
    for title,fn,args in specs:by[title]['code']=recognition_code(fn,args)
    for node in nodes:
        data=node['data']
        if data.get('type')=='code' and '文件序号超出范围' in data.get('code','') and any(v['variable']=='serial' for v in data.get('variables',[])):
            # The upstream list operator already extracted the requested page into a one-file list.
            data['variables']=[v for v in data['variables'] if v['variable']!='serial']
            data['code']=data['code'].replace('def main(files,base_url,serial=1):','def main(files,base_url):\n    serial=1\n    if not isinstance(files,list) or len(files)!=1:raise ValueError("本页图片选择结果应恰好一份，未用其他页替代")')
    bridge=by['把识图结果转为待人工核对的项目清单']
    bridge['code']+='''
_v34_bridge_main=main
def main(state_json,result_json,result_pages):
    result=_v34_bridge_main(state_json,result_json,result_pages)
    if result.get('valid')=='YES':
        state=unpack(result['state_json']);state['schema']='cabinet-native-v34'
        state['recognition_summary'].update(pending_checks=len(state.get('recognition_issues',[])),reference_pending_checks=len(state.get('reference_line_checks',[])))
        result['state_json']=pack(state)
    return result
'''
    form=by['生成当前阶段确认表，保留项目版本与签名']
    helper=flatten(['review_forms.py'])
    form['code']=form['code'].replace('影响选型的技术要求必须写入对应元件的确认技术参数JSON','影响选型的技术要求请在技术参数确认页的对应中文列填写')
    form['code']+=helper
    insert="""
    if stage=='SOURCE_REVIEW':
        source=next(s for s in sheets if s['name']=='识图与问题清单')
        additions=next(s for s in sheets if s['name']=='手工补录')
        prows=parameter_rows(state,additions['rows'])
        for table in (source,additions):
            table['headers']=table['headers'][:9]+table['headers'][10:]
            for r in table['rows']:del r[9]
            if table.get('widths'):table['widths']=table['widths'][:9]+table['widths'][10:]
            table['editable']={i if i<9 else i-1 for i in table['editable'] if i!=9}
        headers=['记录类型','行ID','原图型号']+[h for h,k,t in PARAM_FIELDS]+['其他原参数_只读']
        for start in range(0,len(prows),100):
            sheets.append(sheet('技术参数确认'+('' if len(prows)<=100 else '_'+str(start//100+1)),headers,prows[start:start+100],set(range(3,len(headers)-1)),[14,18,40]+[22]*(len(headers)-3)))
        issues=issue_form_rows(state)
        for start in range(0,len(issues),100):
            sheets.append(sheet('待确认问题'+('' if len(issues)<=100 else '_'+str(start//100+1)),['记录类型','行ID','关联清单行ID','问题说明','原图位置','处理结论','确认内容或依据'],issues[start:start+100],{5,6},[14,24,20,90,70,22,100]))
        text+=' 技术参数在技术参数确认页填写中文列，不用编辑JSON；未知参数留空，不填0。待确认问题逐项写已核对及实际依据，未解决时保留待确认；不能仅凭整体确认进入选型。'
    """
    needle='append_approver(state, sheets, text)';assert needle in form['code']
    form['code']=form['code'].replace(needle,insert+needle)
    reader=by['校验问题清单的项目、版本、固定列与确认人'];tree=ast.parse(reader['code'])
    for n in tree.body:
        if isinstance(n,ast.FunctionDef) and n.name=='reviewed_source_item':n.name='_reviewed_source_json'
        if isinstance(n,ast.FunctionDef) and n.name=='_previous_read_stage_return':
            for call in ast.walk(n):
                if isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=='reviewed_source_item':call.args.append(ast.Name(id='records',ctx=ast.Load()))
    code=ast.unparse(ast.fix_missing_locations(tree))
    code=code.replace("'QUOTE', 'TECH', 'REF')", "'QUOTE', 'TECH', 'REF', 'PARAM', 'CHECK')")
    needle="stamp(state, 'line_scope_confirmed', approver)";assert needle in code
    code=code.replace(needle,"state=validate_issue_returns(original,state,records,approver)\n    "+needle)
    code+='\n'+helper+'''
def reviewed_source_item(item,row,cabinets,records=None):
    row=parameters_from_columns(item,row,records or {})
    return _reviewed_source_json(item,row,cabinets)
'''
    reader['code']=code
    doc['app']['name']='成套柜项目助手 V34.1（中文参数与逐项问题确认）'
    doc['app']['description']='明确参数自动归属，时控与接触器关系候选保留核对；中文参数确认、定位问题和签名回传。'
    doc['dependencies']=[]
    doc['workflow']['features']['opening_statement']+=' 参数在中文列核对；未解决的问题填写实际依据后再进入选型。'
    for node in nodes:
        if node['data']['type']=='code':compile(node['data']['code'],node['data']['title'],'exec')
    (ROOT/'cabinet_project_v34_1.yml').write_text(yaml.safe_dump(doc,allow_unicode=True,sort_keys=False))
    print(json.dumps({'nodes':len(nodes),'edges':len(doc['workflow']['graph']['edges'])}));return doc
if __name__=='__main__':build()
