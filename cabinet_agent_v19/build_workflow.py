"""Build a Dify 1.8.0 advanced-chat DSL without marketplace tool dependencies."""
from pathlib import Path
import uuid
import yaml

ROOT=Path(__file__).resolve().parent
START='1900000000001';PREP='1900000000002';HTTP='1900000000003';PARSE='1900000000004';ASSIGN='1900000000005';ANSWER='1900000000006'

PREP_CODE='''def main(query: str, project_name: str, supplied_id: str, previous_id: str) -> dict:
    q = (query or '').strip()
    aliases = {'开始选型':'新建项目','开始项目':'新建项目','提交确认BOM':'提交BOM确认','生成报价':'生成交付文件','查看进度':'查看项目'}
    operations = ('新建项目','提交问题确认','提交BOM确认','生成交付文件','查看项目','重新识别')
    operation = next((x for x in operations if q == x or q.startswith(x+' ')), '')
    if not operation:
        operation = aliases.get(q, '查看项目')
    project_id = (supplied_id or previous_id or '').strip()
    return {'operation': operation, 'project_id': project_id, 'project_name': project_name or '电气成套项目'}
'''
PARSE_CODE='''import json
def main(body: str, status_code: int, previous_id: str) -> dict:
    try:
        data = json.loads(body)
    except Exception:
        data = {}
    project_id = data.get('project_id') or previous_id or ''
    if status_code != 200:
        message = data.get('message') or ('项目服务请求失败，HTTP '+str(status_code)+'。请管理员检查服务配置和上传限制。')
    else:
        message = data.get('message') or '项目服务未返回状态，请查看服务日志。'
    return {'project_id':project_id,'message':message}
'''


def node(identity,data,x):
    return {'id':identity,'type':'custom','data':dict(data,selected=False),'position':{'x':x,'y':200},
        'positionAbsolute':{'x':x,'y':200},'width':244,'height':120,'sourcePosition':'right','targetPosition':'left','selected':False}


def build():
    variables=[{'variable':'project_name','label':'项目名称（新建时使用）','type':'text-input','max_length':120,'required':False,'options':[]},
               {'variable':'project_id','label':'已有项目ID（留空自动使用当前对话项目）','type':'text-input','max_length':48,'required':False,'options':[]}]
    prepvars=[{'variable':name,'value_selector':selector} for name,selector in [('query',['sys','query']),('project_name',[START,'project_name']),
        ('supplied_id',[START,'project_id']),('previous_id',['conversation','project_id'])]]
    body=[{'key':key,'type':'text','value':value,'file':[]} for key,value in [('operation','{{#'+PREP+'.operation#}}'),
        ('project_id','{{#'+PREP+'.project_id#}}'),('project_name','{{#'+PREP+'.project_name#}}'),('actor','{{#sys.user_id#}}')]]
    body.append({'key':'message_files','type':'file','value':'','file':['sys','files']})
    nodes=[node(START,{'type':'start','title':'开始：每轮在聊天框上传对应文件','variables':variables},0),
        node(PREP,{'type':'code','title':'识别操作并读取当前项目','code_language':'python3','code':PREP_CODE,'variables':prepvars,
            'outputs':{name:{'type':'string','children':None} for name in ('operation','project_id','project_name')}},300),
        node(HTTP,{'type':'http-request','title':'项目服务：上传本轮文件并执行','method':'post','url':'{{#env.PROJECT_SERVICE_BASE_URL#}}/v1/agent/command',
            'authorization':{'type':'api-key','config':{'type':'custom','api_key':'{{#env.PROJECT_SERVICE_API_KEY#}}','header':'X-API-Key'}},
            'headers':'Accept:application/json','params':'','body':{'type':'form-data','data':body},
            'timeout':{'connect':5,'read':90,'write':60},'ssl_verify':True,
            'retry_config':{'max_retries':0,'retry_enabled':False,'retry_interval':1000}},600),
        node(PARSE,{'type':'code','title':'读取进度与下载链接','code_language':'python3','code':PARSE_CODE,
            'variables':[{'variable':'body','value_selector':[HTTP,'body']},{'variable':'status_code','value_selector':[HTTP,'status_code']},
                         {'variable':'previous_id','value_selector':[PREP,'project_id']}],
            'outputs':{name:{'type':'string','children':None} for name in ('project_id','message')}},900),
        node(ASSIGN,{'type':'assigner','version':'2','title':'记住当前项目ID','items':[{'variable_selector':['conversation','project_id'],
            'input_type':'variable','operation':'over-write','value':[PARSE,'project_id']}]},1200),
        node(ANSWER,{'type':'answer','title':'显示结果与下一步','answer':'{{#'+PARSE+'.message#}}','variables':[]},1500)]
    edges=[]
    for a,b in zip(nodes,nodes[1:]):
        edges.append({'id':a['id']+'-'+b['id'],'source':a['id'],'target':b['id'],'sourceHandle':'source','targetHandle':'target','type':'custom',
            'data':{'sourceType':a['data']['type'],'targetType':b['data']['type'],'isInIteration':False,'isInLoop':False},'zIndex':0})
    env=[]
    for name,kind,description in [('PROJECT_SERVICE_BASE_URL','string','项目服务地址，末尾不加斜杠。Dify容器必须可访问。'),
                                   ('PROJECT_SERVICE_API_KEY','secret','与项目服务.env中的PROJECT_API_KEY一致，不是MongoDB密码。')]:
        env.append({'id':str(uuid.uuid5(uuid.NAMESPACE_DNS,name)),'name':name,'value_type':kind,'value':'','description':description,'selector':['env',name]})
    data={'kind':'app','version':'0.4.0','app':{'name':'成套柜项目助手 V19（PDF视觉与工程确认）','description':'PDF识别→问题确认→BOM确认→报价/排产/DWG。由独立项目服务保存跨轮状态；DWG需配置真实转换器。',
        'mode':'advanced-chat','icon':'📐','icon_background':'#E4FBCC','use_icon_as_answer_icon':False},'dependencies':[],
        'workflow':{'conversation_variables':[{'id':str(uuid.uuid5(uuid.NAMESPACE_DNS,'cabinet.project_id')),'name':'project_id','value_type':'string','value':'',
            'description':'当前对话项目ID','selector':['conversation','project_id']}], 'environment_variables':env,
            'features':{'file_upload':{'enabled':True,'allowed_file_types':['document'],'allowed_file_extensions':['.pdf','.PDF','.xlsx','.XLSX'],
                'allowed_file_upload_methods':['local_file'],'number_limits':8,
                'fileUploadConfig':{'file_size_limit':32,'batch_count_limit':8,'workflow_file_upload_limit':8,'image_file_size_limit':10,'audio_file_size_limit':50,'video_file_size_limit':100},
                'image':{'enabled':False,'number_limits':3,'transfer_methods':['local_file']}},
                'opening_statement':'请在聊天输入框上传本轮附件并发送操作：新建项目、提交问题确认、提交BOM确认、生成交付文件、查看项目、重新识别。新建上传图纸PDF与可选技术协议PDF；确认阶段上传已填写Excel。',
                'suggested_questions':['新建项目','查看项目','提交问题确认','提交BOM确认','生成交付文件'],
                'suggested_questions_after_answer':{'enabled':False},'retriever_resource':{'enabled':False},'sensitive_word_avoidance':{'enabled':False},
                'speech_to_text':{'enabled':False},'text_to_speech':{'enabled':False,'language':'','voice':''}},
            'graph':{'nodes':nodes,'edges':edges,'viewport':{'x':0,'y':0,'zoom':0.75}}}}
    (ROOT/'成套柜项目助手_V19_Dify1.8.0.yml').write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf-8')
    return data


if __name__=='__main__':build()
