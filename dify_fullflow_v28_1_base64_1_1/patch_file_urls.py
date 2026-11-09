"""Adapt file parameters inside the workflow, preserving Dify signed URLs."""
from copy import deepcopy
from pathlib import Path
import re,uuid,yaml
from graph import Graph
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'cabinet_project_v28_2_workflow_files.yml'
CODE='''from urllib.parse import urlsplit
def main(files,base_url,serial=1):
    if not isinstance(files,list) or not files:raise ValueError("没有收到待读取附件")
    index=int(serial or 1)-1
    if not 0<=index<len(files):raise ValueError("文件序号超出范围")
    def normalize(f):
      url=str(f.get("url") or f.get("remote_url") or "")
      if url.startswith("/files/"):
        base=str(base_url or "").strip().rstrip("/");p=urlsplit(base)
        if p.scheme not in ("http","https") or not p.netloc or p.path or p.query or p.fragment or p.username or p.password:
            raise ValueError("请在流程环境变量 DIFY_FILES_BASE_URL 填写Dify文件服务根地址")
        url=base+url
      elif urlsplit(url).scheme not in ("http","https"):
        raise ValueError("附件地址不完整，不能读取")
      return dict(f,dify_model_identity="__dify__file__",url=url,remote_url=url,transfer_method="remote_url")
    return {"file_parameter":normalize(files[index]),"file_parameters":[normalize(f) for f in files]}
'''
def build():
 d=yaml.safe_load((ROOT/'cabinet_project_v28_1_base64_1_1.yml').read_text());w=d['workflow'];g=Graph();g.nodes=w['graph']['nodes'];g.edges=w['graph']['edges'];g.counter=3900000000000
 for n in list(g.nodes):
  x=n['data']
  if x['type']!='tool':continue
  pdf=any(p.get('name')=='pdf_files' for p in x.get('paramSchemas',[]))
  if not pdf and x.get('tool_name') not in ('base64_file_encoder','base64_image_encoder'):continue
  key='pdf_files' if pdf else 'input_file' if x['tool_name']=='base64_file_encoder' else 'input_image'
  original=x['tool_parameters'][key]['value'];selected=original[0];sel=g.data(selected)
  variables=[{'variable':'files','value_selector':original if pdf else [selected,'result']},{'variable':'base_url','value_selector':['env','DIFY_FILES_BASE_URL']}]
  extract=sel.get('extract_by',{})
  if extract.get('enabled'):
   m=re.fullmatch(r'\{\{#(.+)\.([^.#]+)#\}\}',extract['serial']);assert m
   variables.append({'variable':'serial','value_selector':[m[1],m[2]]})
  a=g.node('code','补全Dify附件地址：'+x['title'],code_language='python3',code=CODE,variables=variables,outputs={'file_parameter':{'type':'object','children':None},'file_parameters':{'type':'array[object]','children':None}})
  if n.get('parentId'):g.inside(a,n['parentId'],n['position']['x']-50)
  for edge in g.edges:
   if edge['target']==n['id']:edge['target']=a;edge['data']['targetType']='code';edge['id']+='-url'
  g.edge(a,n['id']);x['tool_parameters'][key]['value']=[a,'file_parameters' if pdf else 'file_parameter']
 # Relabel six visible actions; keep retry and revision machinery available internally.
 w['features']['suggested_questions']=['开始选型','提交问题清单','提交选型确认','提交报价表','查看项目','生成排产资料']
 w['features']['opening_statement']='填写项目名称并上传图纸PDF，技术协议可选，发送“开始选型”。下载问题清单核对后上传并发送“提交问题清单”；随后依次回传“提交选型确认”“提交报价表”。报价确认后使用“生成排产资料”获取表格。可随时“查看项目”。'
 w['environment_variables'].append({'id':str(uuid.uuid4()),'name':'DIFY_FILES_BASE_URL','value_type':'string','value':'http://api:5001','selector':['env','DIFY_FILES_BASE_URL'],
  'description':'插件读取Dify自身文件的根地址，默认标准Docker内部地址；不是产品查询API地址。'})
 # Regenerate actionable messages without removed commands. Keep factual reasons intact.
 for n in g.nodes:
  x=n['data']
  if x['type']=='code':
   code=x['code'].replace('发送“继续处理”重新选型，随后重新确认报价','点击“开始选型”重新选型，随后重新确认报价').replace('修正配置后发送“继续处理”','修正配置后点击“开始选型”重试')
   x['code']=code
   if x['title']=='识别明确操作，严格区分项目阶段':
    x['code']=x['code'].replace('\ndef main(', '\ndef original_main(',1)+'''
def main(query,state_json,chat_files,pdf_files,technical_files,project_name,notes):
    try: state=unpack(state_json)
    except Exception: state={}
    if query=="查看项目" and state.get("stage") in ("SOURCE_REVIEW","SELECTION_REVIEW","QUOTE_REVIEW"):
        query="继续处理"
    if query=="开始选型" and state.get("stage") in ("MATCH_PENDING","PRICE_PENDING"):
        query="继续处理"
    return original_main(query,state_json,chat_files,pdf_files,technical_files,project_name,notes)
'''
 # Remove unused project archive branch (no remaining upstream dependency).
 removed={n['id'] for n in g.nodes if n['data']['title'] in ('导出项目状态和确认记录','生成项目归档JSON','项目资料归档')}
 for n in g.nodes:
  if n['data']['type']=='if-else':n['data']['cases']=[c for c in n['data']['cases'] if c.get('id')!='ARCHIVE']
 changed=True
 while changed:
  changed=False
  for n in g.nodes:
   incoming=[e for e in g.edges if e['target']==n['id']]
   if n['id'] not in removed and incoming and all(e['source'] in removed for e in incoming):removed.add(n['id']);changed=True
 g.nodes=[n for n in g.nodes if n['id'] not in removed];g.edges=[e for e in g.edges if e['source'] not in removed and e['target'] not in removed]
 g.layout();g.nodes.sort(key=lambda n:bool(n.get('parentId')));w['graph'].update(nodes=g.nodes,edges=g.edges)
 d['app']['name']='成套柜项目助手 V28.2（流程内文件地址修复）'
 OUT.write_text(yaml.safe_dump(d,allow_unicode=True,sort_keys=False));print('Built',len(g.nodes),'nodes');return d
if __name__=='__main__':build()
