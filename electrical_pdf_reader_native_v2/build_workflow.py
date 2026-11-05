import ast
from copy import deepcopy
import json
from pathlib import Path
import sys
import uuid
import yaml

ROOT=Path(__file__).resolve().parent
from graph import Graph
base=yaml.safe_load((ROOT/'electrical_pdf_reader_native_v2.yml').read_text())
g=Graph();g.counter=2600000000000


def code(title, fn, inputs, outputs):
    tree=ast.parse((ROOT/'workflow_nodes.py').read_text())
    shared=[n for n in ast.parse((ROOT/'vision.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name in ('_bbox','validate')]
    tree.body=[n for n in tree.body if not isinstance(n,ast.FunctionDef) or n.name not in ('_bbox','validate')]+shared
    functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
    wanted=set();todo=[fn]
    while todo:
        key=todo.pop()
        if key in wanted:continue
        wanted.add(key);todo.extend(n.id for n in ast.walk(functions[key]) if isinstance(n,ast.Name) and n.id in functions)
    content='\n'.join(ast.unparse(n) for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom)) or isinstance(n,ast.FunctionDef) and n.name in wanted)
    args=', '.join(inputs)
    content+='\n\ndef main('+args+'):\n    return '+fn+'('+args+')\n'
    return g.node('code',title,code_language='python3',code=content,variables=[{'variable':key,'value_selector':val} for key,val in inputs.items()],
                  outputs={key:{'type':typ,'children':None} for key,typ in outputs.items()})


def http(title,path,method='get',body=None):
    return g.node('http-request',title,method=method,url='{{#'+routing+'.base_url#}}'+path,
                  authorization={'type':'api-key','config':{'type':'custom','header':'X-API-Key','api_key':'{{#env.DRAWING_READER_API_KEY#}}'}},
                  headers='' if body and body['type']=='form-data' else 'Content-Type:application/json',params='',body=body or {'type':'none','data':[]},
                  timeout={'connect':10,'read':180,'write':60},ssl_verify=True,retry_config={'retry_enabled':False,'max_retries':0,'retry_interval':1000})


def raw_body(text):return {'type':'json','data':[{'key':'','type':'text','value':text}]}


def persist(previous):
    n=g.node('assigner','保留识图项目编号，后续继续同一项目',version='2',items=[{'variable_selector':['conversation','drawing_project_id'],'input_type':'variable','operation':'over-write','value':[previous,'project_id']}])
    g.edge(previous,n);return n


start=g.node('start','在聊天框上传PDF；先独立识图',variables=[])
routing=code('检查上传格式与当前识图任务','route',{'files':['sys','files'],'project_id':['conversation','drawing_project_id'],'query':['sys','query'],
              'base_url':['env','DRAWING_READER_BASE_URL']},{'route':'string','base_url':'string','message':'string'})
g.edge(start,routing)
gate=g.branch('新项目、追加资料、继续识图或查看结果',[routing,'route'],['NEW','ADD','PROCESS','REPORT']);g.edge(routing,gate)
help=g.answer('上传位置和配置说明','{{#'+routing+'.message#}}');g.edge(gate,help,'false')
new=http('新建独立识图项目','/v1/projects','post',raw_body('{"name":"电气图纸识别项目"}'));g.edge(gate,new,'NEW')
created=code('读取项目编号','created',{'status_code':[new,'status_code'],'body':[new,'body']},{'project_id':'string'});g.edge(new,created);p=persist(created)
upload=g.node('iteration','逐文件上传；图纸原文和分区保留在解析工具',iterator_selector=['sys','files'],output_selector=[],output_type='array[string]',start_node_id='',is_parallel=False,parallel_nums=1,error_handle_mode='terminated',width=1100,height=270)
us=g.node('iteration-start','');g.data(upload)['start_node_id']=us
request=http('上传原始PDF并解析','/v1/projects/{{#conversation.drawing_project_id#}}/sources','post',{'type':'form-data','data':[{'key':'file','type':'file','value':'','file':[upload,'item']}]})
upcheck=code('上传解析失败时停止，不漏过文件','created_upload',{}, {}) if False else None
# Return HTTP response as a scalar; the root checks every upload response.
ucode='import json\ndef main(status_code,body):\n    if status_code!=200:raise ValueError("图纸解析失败，HTTP "+str(status_code)+"；请查看上传节点的响应。")\n    value=json.loads(body)\n    if not value.get("source_id"):raise ValueError("未返回图纸来源编号")\n    return {"result":body}\n'
upcheck=g.node('code','确认该文件完成解析',code_language='python3',code=ucode,variables=[{'variable':'status_code','value_selector':[request,'status_code']},{'variable':'body','value_selector':[request,'body']}],outputs={'result':{'type':'string','children':None}})
g.data(upload)['output_selector']=[upcheck,'result']
for n,x in ((us,50),(request,150),(upcheck,520)):g.inside(n,upload,x)
g.edge(us,request);g.edge(request,upcheck);g.edge(p,upload);g.edge(gate,upload,'ADD')
getbatch=http('获取下一批未处理分区（每轮6区，已保存的不会重做）','/v1/projects/{{#conversation.drawing_project_id#}}/batch?limit=6')
g.edge(upload,getbatch);g.edge(gate,getbatch,'PROCESS')
unpack=code('读取本轮分区索引','batch',{'status_code':[getbatch,'status_code'],'body':[getbatch,'body']},{'units':'array[string]'});g.edge(getbatch,unpack)
iteration=g.node('iteration','逐区看图识别；原文、符号、含义和归属一起提取',iterator_selector=[unpack,'units'],output_selector=[],output_type='array[string]',start_node_id='',is_parallel=False,parallel_nums=1,error_handle_mode='terminated',width=3300,height=460)
is_=g.node('iteration-start','');g.data(iteration)['start_node_id']=is_
unit=code('绑定可信来源和原文定位','unit',{'item':[iteration,'item']},{'unit_id':'string','context_json':'string'})
image=http('取得300dpi局部图片，千问在Dify里调用','/v1/projects/{{#conversation.drawing_project_id#}}/units/{{#'+unit+'.unit_id#}}/image')
from vision import SYSTEM
prompt=SYSTEM
model_template=deepcopy(next(n['data'] for n in base['workflow']['graph']['nodes'] if n['data']['type']=='llm'))
for key in ('isInIteration','iteration_id'):model_template.pop(key,None)
def llm(title,extra=''):
    d=deepcopy(model_template);d.pop('type',None);d.pop('title',None)
    d['prompt_template']=[{'id':uuid.uuid4().hex,'role':'system','text':prompt+extra},{'id':uuid.uuid4().hex,'role':'user','text':'{{#'+unit+'.context_json#}}'}]
    d['vision']={'enabled':True,'configs':{'detail':'high','variable_selector':[image,'files']}}
    d['model']['name']='qwen3-vl-plus'
    d['model']['completion_params']['max_tokens']=8192
    d['error_strategy']='default-value';d['default_value']=[{'key':'text','type':'string','value':'{}'}]
    return g.node('llm',title,**d)
vision=llm('千问：看原图识别元件、标注与归属')
check=code('校验原文引用、分区归属和JSON；未通过则重看同一图','checked_model',{'raw':[vision,'text'],'context_json':[unit,'context_json']},{'valid':'string','packet_json':'string','message':'string'})
valid=g.branch('当前分区结果是否可校验',[check,'valid'],['YES'])
retry=llm('只重看本区一次；不能修补截断JSON','\n上一轮未通过，请重看同一原图并完整输出。校验提示：{{#'+check+'.message#}}')
check2=code('第二次仍未通过则保留分区失败，不能当识别完成','checked_model',{'raw':[retry,'text'],'context_json':[unit,'context_json']},{'valid':'string','packet_json':'string','message':'string'})
combined=g.node('variable-aggregator','使用本区最终校验结果',output_type='string',variables=[[check2,'packet_json'],[check,'packet_json']])
save=http('立即保存本区结果，下一轮不重复已处理区','/v1/projects/{{#conversation.drawing_project_id#}}/results','post',raw_body('{{#'+combined+'.output#}}'))
saved=code('确认结果已保存','saved',{'status_code':[save,'status_code'],'body':[save,'body']},{'result':'string'})
g.data(iteration)['output_selector']=[saved,'result']
for index,n in enumerate((is_,unit,image,vision,check,valid,retry,check2,combined,save,saved)):g.inside(n,iteration,40+index*290)
g.edge(unpack,iteration);g.edge(is_,unit);g.edge(unit,image);g.edge(image,vision);g.edge(vision,check);g.edge(check,valid)
g.edge(valid,combined,'YES');g.edge(valid,retry,'false');g.edge(retry,check2);g.edge(check2,combined);g.edge(combined,save);g.edge(save,saved)
report=http('汇总所有来源和已保存分区，检查未绑定原文','/v1/projects/{{#conversation.drawing_project_id#}}/report')
g.edge(iteration,report);g.edge(gate,report,'REPORT')
summary=code('显示真实进度、失败和待核对标注','status',{'status_code':[report,'status_code'],'body':[report,'body']},{'message':'string'});g.edge(report,summary)
previous=summary;downloads=[]
for suffix,label in (('txt','文字说明'),('xlsx','Excel原文、含义与待核对表'),('json','后续整合用JSON')):
    n=http('取得'+label,'/v1/projects/{{#conversation.drawing_project_id#}}/files/extraction.'+suffix)
    g.edge(previous,n);previous=n;downloads.append(n)
answer=g.answer('识别结果与下一步','{{#'+summary+'.message#}}\n\n文字结果：{{#'+downloads[0]+'.files#}}\n\nExcel结果：{{#'+downloads[1]+'.files#}}\n\n结构化结果：{{#'+downloads[2]+'.files#}}')
g.edge(previous,answer)
g.layout();g.nodes.sort(key=lambda n:bool(n.get('parentId')))
definition=deepcopy(base)
definition['app'].update(name='电气图纸识别 V2（PDF原文、OCR与局部视觉）',description='独立识图：PDF原文定位、分区视觉识别、常用电气标注解释和待核对项。支持多文件多轮续处理；本阶段不做选型、报价或出图。')
definition['dependencies']=[d for d in definition.get('dependencies',[]) if 'tongyi' in json.dumps(d)]
wf=definition['workflow'];wf['graph']={'nodes':g.nodes,'edges':g.edges,'viewport':{'x':0,'y':0,'zoom':.5}}
wf['conversation_variables']=[{'id':str(uuid.uuid4()),'name':'drawing_project_id','description':'解析工具中的项目编号；不存放整套原文','value_type':'string','value':''}]
wf['environment_variables']=[{'id':str(uuid.uuid4()),'name':'DRAWING_READER_BASE_URL','description':'配套解析工具根地址，例如http://electrical-pdf-reader-v2:9912','value_type':'string','value':''},
                              {'id':str(uuid.uuid4()),'name':'DRAWING_READER_API_KEY','description':'配套解析工具密钥，与服务配置相同','value_type':'secret','value':''}]
wf['features']['opening_statement']='上传整套PDF，发送“开始识图”。本应用先提取器件、线缆及原文含义，保留来源和疑问。每轮处理6个分区，未完成时发送“继续识图”；查看和下载当前结果请发送“查看结果”。'
wf['features']['suggested_questions']=['开始识图','继续识图','查看结果','新建识图项目']
upload_settings=wf['features']['file_upload']
upload_settings.update(enabled=True,allowed_file_types=['custom'],allowed_file_extensions=['.pdf','.PDF'],allowed_file_upload_methods=['local_file'],number_limits=10)
upload_settings.pop('image',None)
for n in g.nodes:
    if n['data']['type']=='code':compile(n['data']['code'],n['id'],'exec')
(ROOT/'electrical_pdf_reader_native_v2.yml').write_text(yaml.safe_dump(definition,allow_unicode=True,sort_keys=False))
(ROOT/'workflow_node_ids.json').write_text(json.dumps({n['data']['title']:n['id'] for n in g.nodes},ensure_ascii=False,indent=2))
print('nodes',len(g.nodes),'edges',len(g.edges))
