"""Patch the tested full business graph with the new recognition functions."""
import ast,json,sys,os
from pathlib import Path
from copy import deepcopy
import yaml
from graph import Graph
from prompts import VISION
ROOT=Path(__file__).resolve().parent
BASE_YML=Path(os.environ.get('V29_BASE_YML',ROOT.parent/'downloads/cabinet_project_v28_2_workflow_files.yml'))
if not BASE_YML.exists():BASE_YML=Path('/workspace/work/dify-fullflow-v28-20261108/cabinet_project_v28_2_existing_plugins.yml')

def code_for(fn,args):
 body=[]
 for file in ('transport.py','literal_decode.py','inventory.py','png_crop.py','spatial.py'):
  body.extend(n for n in ast.parse((ROOT/file).read_text()).body if not (isinstance(n,ast.ImportFrom) and n.module in ('transport','inventory','literal_decode')))
 funcs={n.name:n for n in body if isinstance(n,ast.FunctionDef)};needed=set();pending=[fn]
 while pending:
  name=pending.pop()
  if name in needed:continue
  needed.add(name);pending.extend(n.id for n in ast.walk(funcs[name]) if isinstance(n,ast.Name) and n.id in funcs)
 out=[n for n in body if isinstance(n,(ast.Import,ast.ImportFrom,ast.Assign)) or isinstance(n,ast.FunctionDef) and n.name in needed]
 src='\n'.join(ast.unparse(n) for n in out)+'\n\ndef main('+','.join(args)+'):\n    return '+fn+'('+','.join(args)+')\n'
 compile(src,fn,'exec');return src

def build():
 d=yaml.safe_load(BASE_YML.read_text());g=Graph();g.nodes=d['workflow']['graph']['nodes'];g.edges=d['workflow']['graph']['edges'];g.counter=4100000000000
 titles={n['data']['title']:n for n in g.nodes}
 for title,fn in [('绑定页码和裁切范围，代码不接收单个File','job_context'),('按真实像素裁切局部PNG，保留颜色','crop_node'),('映射回原页坐标，备注与器件分开','parse_view'),('按原页位置合并实例，校验回路归属','collect_spatial'),('核对图片身份，不将原图冒充局部图','check_view_files')]:
  n=titles[title];n['data']['code']=code_for(fn,[v['variable'] for v in n['data']['variables']])
 model=titles['qwen3-vl-plus：逐位置原文、符号和回路证据'];parent=model['parentId']
 ctx=model['data']['prompt_template'][1]['text'];sel=ctx[3:-3].rsplit('.',1)
 request=g.node('code','仅传当前视图任务，隔离全页文字和全页坐标',code_language='python3',code='import json\ndef main(context_json):\n x=json.loads(context_json)\n return {"request_json":json.dumps({"mode":x["mode"],"page":x["page"],"view_id":x["view_id"],"coordinate_system":"当前图片左上0,0，右下1000,1000；不得输出整页坐标"},ensure_ascii=False)}',variables=[{'variable':'context_json','value_selector':sel}],outputs={'request_json':{'type':'string','children':None}})
 g.inside(request,parent,model['position']['x']);next(n for n in g.nodes if n['id']==request)['position']['y']=250
 for e in g.edges:
  if e['target']==model['id']:e['target']=request;e['data']['targetType']='code';e['id']+='-request'
 g.edge(request,model['id']);model['data']['prompt_template'][1]['text']='{{#'+request+'.request_json#}}'
 model['data']['prompt_template'][0]['text']=VISION+'\n程序强制分工：overview只输出drawings和非器件标注，不输出任何器件、线缆、anchors或links。detail只读当前真实裁切图，输出局部0-1000坐标，缺少位置不得把源文字当图上器件。类别无法确定时unknown。'
 gate=titles['当前视图图片是否正确'];case=deepcopy(gate['data']['cases'][0]);case['id']=case['case_id']='BLANK';case['conditions'][0]['value']='BLANK';gate['data']['cases'].append(case)
 blank=g.node('code','像素确认空白区域，跳过模型并保留覆盖记录',code_language='python3',code=code_for('blank_view',['context_json']),variables=[{'variable':'context_json','value_selector':sel}],outputs={'packet_json':{'type':'string','children':None}})
 g.inside(blank,parent,gate['position']['x']);next(n for n in g.nodes if n['id']==blank)['position']['y']=420
 g.edge(gate['id'],blank,'BLANK');packet=titles['本视图结果或明确失败记录'];g.edge(blank,packet['id']);packet['data']['variables'].append([blank,'packet_json'])
 bridge=titles['把识图结果转为待人工核对的项目清单']['data'];bridge['code']=bridge['code'].replace('\ndef main(', '\ndef previous_main(',1)+'''
def main(state_json,result_json,result_pages):
    value=previous_main(state_json,result_json,result_pages)
    if value.get('valid')!='YES':return value
    result=json.loads(result_json)
    packets=[json.loads(s) for s in result_pages] if result.get('details_in')=='result_pages' else [result]
    groups={}
    for p in packets:
      for row in p.get('quarantined_observations',[]):
        if row.get('quarantine_reason')=='overview_not_inventory':continue
        key=row['raw'].strip();groups.setdefault(key,[]).append({'page':row.get('page'),'reason':row.get('quarantine_reason'),'bbox':row.get('bbox')})
    state=unpack(value['state_json']);state['recognition_unplaced']=groups
    notes={}
    for row in state['facts']['notes']:
        key=(row.get('kind'),''.join(row['text'].split()))
        if key not in notes:notes[key]=dict(row)
        else:notes[key].setdefault('evidence',[]).extend(row.get('evidence',[]))
    state['facts']['notes']=list(notes.values())
    if state.get('notes'):
        state['facts']['notes'].append({'text':'项目补充说明：'+state['notes'],'kind':'project_notes','evidence':[]})
    value['state_json']=pack(state);return value
'''
 form=titles['生成当前阶段确认表，保留项目版本与签名']['data'];needle='append_approver(state, sheets, text)'
 assert needle in form['code']
 form['code']=form['code'].replace(needle,"\n    if state.get('recognition_unplaced'):\n        sheets.append(sheet('识图待定位', ['记录类型','行ID','待核对原文','原因及原位置'], [['TECH','UNPLACED_'+str(i),raw,j(evidence)] for i,(raw,evidence) in enumerate(state['recognition_unplaced'].items(),1)], widths=[14,24,80,100]))\n        text+=' 请对照识图待定位表核对漏项；这些读法尚未列为采购器件，不代表可以忽略。'\n    "+needle)
 form['code']=form['code'].replace(needle,"\n    expanded=[]\n    for info in sheets:\n        if len(info['rows'])>100:\n            for start in range(0,len(info['rows']),100):\n                expanded.append(dict(info,name=info['name'][:25]+'_'+str(start//100+1),rows=info['rows'][start:start+100]))\n        else:expanded.append(info)\n    sheets=expanded\n    "+needle)
 d['dependencies']=[];d['app']['name']='成套柜项目助手 V29（定位与合并修正）'
 for n in g.nodes:
  if n['data']['type']=='code':
   n['data']['code']=n['data']['code'].replace('len(template) > 55000 or len(payload) > 55000','len(template) > 2000000 or len(payload) > 2000000')
   compile(n['data']['code'],n['data']['title'],'exec')
 g.layout();g.nodes.sort(key=lambda n:bool(n.get('parentId')));d['workflow']['graph'].update(nodes=g.nodes,edges=g.edges)
 out=ROOT/'cabinet_project_v29.yml';out.write_text(yaml.safe_dump(d,allow_unicode=True,sort_keys=False));print('Built',len(g.nodes),'nodes');return d
if __name__=='__main__':build()
