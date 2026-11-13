"""Two independent project entries: native PDF recognition or existing issue workbook."""
import ast,json
from pathlib import Path
import yaml
from graph import Graph
ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/'downloads/cabinet_project_v34_2.yml'
if not BASE.exists():BASE=ROOT.parent/'submission-help-v34-2-20261110/cabinet_project_v34_2.yml'


def build():
 d=yaml.safe_load(BASE.read_text());g=Graph();g.nodes=d['workflow']['graph']['nodes'];g.edges=d['workflow']['graph']['edges'];g.counter=5500000000000
 by={n['data']['title']:n for n in g.nodes};router=by['识别明确操作，严格区分项目阶段'];start=next(n for n in g.nodes if n['data']['type']=='start')
 router['data']['code']+='''
_v35_route_action=route_action
def route_action(query,state_json,chat_files,pdf_files,technical_files,project_name,notes,action):
    try:state=unpack(state_json)
    except ValueError:return _v35_route_action(query,state_json,chat_files,pdf_files,technical_files,project_name,notes,action)
    text=re.sub(r'\\s+','',str(query or action or '')).casefold()
    starts={'开始选型','提交问题清单','提交问题确认清单','提交问题确认','提交问题确认单','导入问题清单','导入已有清单','提交'}
    source='CHAT' if chat_files else 'FORM';files=chat_files or pdf_files or []
    one=isinstance(files,list) and len(files)==1
    extension=str(files[0].get('filename') or files[0].get('name') or '').lower() if one else ''
    pending=state.get('stage')=='SOURCE_IMPORT_PENDING'
    if (not state or pending) and text in starts and extension.endswith('.xlsx'):
        if not state:
            state=new_state(project_name or str(files[0].get('filename') or files[0].get('name') or '已有清单项目')[:-5],[],technical_files or [])
            state.update(schema='cabinet-native-v35',stage='SOURCE_IMPORT_PENDING',entry_mode='ISSUE_LIST',notes=str(notes or '')[:3000],technical_text='',technical_read_status='not_extracted_in_list_entry')
        else:state['documents']=[x for x in state['documents'] if x.get('role')!='issue_list']
        f=files[0];state['documents'].append({'id':str(f.get('upload_file_id') or f.get('id') or ''),'filename':str(f.get('filename') or f.get('name') or ''),'role':'issue_list'})
        return {'route':'SOURCE_RETURN','state_json':pack(state),'message':'已接收已有问题清单，正在读取需求与确认信息；无需从PDF识图开始。','upload_source':source,'has_technical':'NO'}
    if (not state or pending) and text in starts and not (text=='开始选型' and extension.endswith('.pdf')):
        return {'route':'STATUS','state_json':state_json,'message':'可以直接从已有问题确认单开始。请在聊天附件或上方项目资料中上传一份XLSX，再发送“提交问题清单”或“开始选型”；使用图纸入口则上传PDF后发送“开始选型”。','upload_source':source,'has_technical':'NO'}
    if state and text in {'导入问题清单','导入已有清单'}:
        return {'route':'STATUS','state_json':state_json,'message':'本对话已有项目。另开对话上传已有问题单即可创建新项目并从选型开始，原项目会保留。','upload_source':source,'has_technical':'NO'}
    return _v35_route_action(query,state_json,chat_files,pdf_files,technical_files,project_name,notes,action)
'''
 reader=by['校验问题清单的项目、版本、固定列与确认人']
 tree=ast.parse(reader['data']['code']);fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='xlsx_markdown');fn.name='external_workbook_markdown'
 for loop in ast.walk(fn):
    if isinstance(loop,ast.For) and isinstance(loop.target,ast.Name) and loop.target.id=='sh':
        loop.body[0:0]=ast.parse("if sh.attrib.get('state','visible')!='visible':continue").body
 helper=(ROOT/'external_issue_import.py').read_text()
 reader['data']['code']+='\n'+ast.unparse(ast.fix_missing_locations(fn))+'\n'+helper+'''
_v35_source_main=main
def main(state_json,encoded):
    try:
        if unpack(state_json).get('stage')=='SOURCE_IMPORT_PENDING':return import_issue_workbook(state_json,encoded)
    except Exception as exc:
        return {'state_json':state_json,'valid':'NO','route':'STATUS','message':'清单尚未导入：'+str(exc)[:2800]+'。可在本对话修正后重新上传，无需先传PDF。'}
    return _v35_source_main(state_json,encoded)
'''
 # Select a real Dify File through one of two mutually exclusive file branches.
 source_operator=by['读取唯一待处理附件：原样读取回传问题清单XLSX，保留编码前导零'];adapter=by['补全Dify附件地址：原样读取回传问题清单XLSX，保留编码前导零'];route_switch=by['按当前业务阶段进入流程']
 form_operator=g.node('list-operator','读取上方上传的已有问题清单',**{k:v for k,v in source_operator['data'].items() if k not in ('type','title','desc','selected','variable')},variable=[start['id'],'pdf_files'])
 choose=g.branch('已有清单来自聊天附件还是上方资料',[router['id'],'upload_source'],['CHAT'])
 aggregate=g.node('variable-aggregator','本轮问题清单附件',output_type='array[file]',variables=[[source_operator['id'],'result'],[form_operator,'result']])
 g.edges=[e for e in g.edges if not(e['source']==route_switch['id'] and e['sourceHandle']=='SOURCE_RETURN') and not(e['source']==source_operator['id'] and e['target']==adapter['id'])]
 g.edge(route_switch['id'],choose,'SOURCE_RETURN');g.edge(choose,source_operator['id'],'CHAT');g.edge(choose,form_operator,'false');g.edge(source_operator['id'],aggregate);g.edge(form_operator,aggregate);g.edge(aggregate,adapter['id'])
 next(v for v in adapter['data']['variables'] if v['variable']=='files')['value_selector']=[aggregate,'output']
 after=next(n for n in g.nodes if n['data']['type']=='if-else' and any(c.get('variable_selector')==[reader['id'],'route'] for case in n['data'].get('cases',[]) for c in case.get('conditions',[])));form=by['生成当前阶段确认表，保留项目版本与签名']
 after['data']['cases'].append({'id':'FORM','case_id':'FORM','logical_operator':'and','conditions':[{'id':'v35-import-needs-fields','comparison_operator':'is','variable_selector':[reader['id'],'route'],'value':'FORM','varType':'string'}]})
 g.edge(after['id'],form['id'],'FORM')
 # Keep completed fields when only a subset of imported data needs correction.
 preserve=ast.parse(helper);names={'preserve_imported_inputs'}
 funcs=[n for n in preserve.body if isinstance(n,ast.FunctionDef) and n.name in names]
 form['data']['code']+='\n'+'\n'.join(ast.unparse(f) for f in funcs)+'\n'
 form['data']['code']=form['data']['code'].replace("state.get('source_reviewed')", "(state.get('source_reviewed') or state.get('imported_prefill'))")
 needle='append_approver(state, sheets, text)';assert needle in form['data']['code'];form['data']['code']=form['data']['code'].replace(needle,'''preserve_imported_inputs(state,sheets)
    if stage=='SOURCE_REVIEW' and state.get('entry_mode')=='ISSUE_LIST':
        title='导入问题清单_待补充'
        text='已有清单已导入，已填写的型号、参数、数量与确认依据保留。仅需补齐本表列出的缺失信息，无需重新上传PDF。填好后在本对话上传XLSX并发送提交问题清单。'
        sheets[:]=[s for s in sheets if s['name'] not in ('识图覆盖与待核对','回路识别候选','识图待复核','原图位置核对','校验修正记录','连接与来源标注')]
        for table in sheets:
            table['headers']=['导入型号' if h=='原图型号' else '来源说明' if h=='原图解释' else h for h in table['headers']]
    '''+needle)
 status='''
_v35_full_status=full_status
def full_status(state):
    if not state:return '两种起点任选：上传PDF并开始选型；或上传已有问题确认单XLSX，直接开始选型或提交问题清单。'
    text=_v35_full_status(state)
    if state.get('entry_mode')=='ISSUE_LIST':text='入口：已有问题清单，未执行PDF识图。\\n'+text.replace('待图纸和问题清单核对','待导入清单补充确认').replace('SOURCE_IMPORT_PENDING','读取已有问题清单')
    return text
'''
 for node in g.nodes:
    data=node['data']
    if data['type']=='code' and 'def full_status(' in data.get('code',''):data['code']+='\n'+status
    if data['type']=='code':compile(data['code'],data['title'],'exec')
 start['data']['title']='可从PDF或已有问题清单开始'
 v=next(v for v in start['data']['variables'] if v['variable']=='pdf_files');v.update(label='项目资料：PDF图纸或已有问题清单XLSX（任选一份）',allowed_file_extensions=['.pdf','.xlsx'])
 d['app']['name']='成套柜项目助手 V35（已有问题单直达选型）'
 d['workflow']['features']['opening_statement']='可从任一起点开始：\\n①有图纸：上传PDF，点“开始选型”；\\n②已有问题确认清单：上传XLSX，点“提交问题清单”或“开始选型”，无需重新识图。\\n清单完整会直接查库；缺字段会保留已填内容并给出补充表。选型完成后回传BOM确认，再进行报价确认和排产资料。确认表都可从聊天附件（回形针或＋）上传。'.replace('\\n','\n')
 d['workflow']['graph']={'nodes':g.nodes,'edges':g.edges}
 (ROOT/'cabinet_project_v35.yml').write_text(yaml.safe_dump(d,allow_unicode=True,sort_keys=False));print(json.dumps({'nodes':len(g.nodes),'edges':len(g.edges)}));return d
if __name__=='__main__':build()
