"""Build the complete staged project app for Dify 1.8."""
import ast
from copy import deepcopy
import inspect
from pathlib import Path
import uuid
import yaml
import entry
from graph import Graph

ROOT=Path(__file__).resolve().parent
OUTPUT=ROOT/'cabinet_project_v28_dify18.yml'


def build():
    funcs={};imports=[];globals_=[]
    for path in [ROOT/'native/core.py',ROOT/'native/excel.py',ROOT/'native/delivery.py',ROOT/'entry.py']:
        for node in ast.parse(path.read_text()).body:
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                if not isinstance(node,ast.ImportFrom) or not node.level and not (node.module or '').startswith('native.'):
                    imports.append(ast.unparse(node))
            elif isinstance(node,(ast.Assign,ast.AnnAssign)):globals_.append(ast.unparse(node))
            elif isinstance(node,(ast.FunctionDef,ast.ClassDef)):funcs[node.name]=node
    graph=Graph();graph.counter=2800000000000
    def code(title,fn,inputs,outputs,fixed=None):
        needed=set();pending=[fn];legacy=False
        while pending:
            name=pending.pop()
            if name=='legacy_main':legacy=True;continue
            if name in needed:continue
            needed.add(name)
            pending.extend(n.id for n in ast.walk(funcs[name]) if isinstance(n,ast.Name) and (n.id in funcs or n.id=='legacy_main'))
        source='\n'.join(dict.fromkeys(imports))+'\n'+'\n'.join(globals_)+'\n'
        if legacy:source+=(ROOT/'native/legacy_selection.py').read_text()+'\nlegacy_main=main\n'
        source+='\n'.join(ast.unparse(node) for name,node in funcs.items() if name in needed)
        args=','.join(inputs)+('' if not fixed else (',' if inputs else '')+','.join(k+'='+repr(v) for k,v in fixed.items()))
        defaults={k:[] if t.startswith('array') else {} if t=='object' else 0 if t=='number' else '' for k,t in outputs.items()}
        defaults.update({k:'NO' if k=='valid' else 'STATUS' for k in outputs if k in ('valid','route')})
        source+='\n\ndef main('+','.join(inputs)+'):\n    fallback='+repr(defaults)+'\n'
        if 'state_json' in inputs and 'state_json' in outputs:source+='    fallback["state_json"]=state_json\n'
        source+='    try:\n        value='+fn+'('+args+')\n        value.setdefault("valid","YES")\n        return {k:value.get(k,v) for k,v in fallback.items()}\n    except Exception as exc:\n        fallback["message"]=str(exc)[:3000]\n        return {k:fallback[k] for k in '+repr(list(outputs))+'}\n'
        compile(source,title,'exec')
        return graph.node('code',title,code_language='python3',code=source,
            variables=[{'variable':k,'value_selector':v} for k,v in inputs.items()],outputs={k:{'type':v,'children':None} for k,v in outputs.items()})
    def persist(previous):
        node=graph.node('assigner','保存项目阶段与确认记录',version='2',items=[{'variable_selector':['conversation','project_state'],
            'input_type':'variable','operation':'over-write','value':[previous,'state_json']}]);graph.edge(previous,node);return node
    def guard(node,persist_state=True):
        previous=persist(node) if persist_state else node
        gate=graph.branch('当前处理是否通过',[node,'valid'],['YES']);graph.edge(previous,gate)
        error=graph.answer('保留项目并显示待处理原因','{{#'+node+'.message#}}');graph.edge(gate,error,'false');return gate
    def file_tool(title,encoded,filename):
        schema=yaml.safe_load((ROOT/'base64_file_decoder_schema.yml').read_text())['parameters']
        return graph.node('tool',title,provider_id='bowenliang123/base64_codec/base64_codec',provider_name='bowenliang123/base64_codec/base64_codec',
            provider_type='builtin',tool_name='base64_file_decoder',tool_label='Base64转文件',tool_description='生成本阶段真实文件',
            tool_node_version='2',tool_configurations={},tool_parameters={'encoded_text':{'type':'variable','value':encoded},'output_filename':{'type':'variable','value':filename}},
            paramSchemas=schema,params={p['name']:'' for p in schema},is_team_authorization=True,output_schema=None)
    def passthrough(title,selector):
        return graph.node('list-operator',title,variable=selector,var_type='array[file]',item_var_type='file',
            filter_by={'enabled':False,'conditions':[]},order_by={'enabled':False,'key':'','value':'asc'},
            limit={'enabled':False,'size':1},extract_by={'enabled':False,'serial':'1'})
    def encode_file(title,selector):
        selected=passthrough('读取唯一待处理附件：'+title,selector)
        schema=yaml.safe_load((ROOT/'base64_file_encoder_schema.yml').read_text())['parameters']
        encoder=graph.node('tool',title,provider_id='bowenliang123/base64_codec/base64_codec',provider_name='bowenliang123/base64_codec/base64_codec',
            provider_type='builtin',tool_name='base64_file_encoder',tool_label='文件转Base64',tool_description='原样读取文件字节，不进行文本推断',
            tool_node_version='2',tool_configurations={},tool_parameters={'input_file':{'type':'variable','value':[selected,'first_record']}},
            paramSchemas=schema,params={p['name']:'' for p in schema},is_team_authorization=True,output_schema=None)
        graph.edge(selected,encoder)
        return selected,encoder
    def http(title,url,params=''):
        return graph.node('http-request',title,method='get',url=url,authorization={'type':'api-key','config':{'type':'custom','header':'X-API-Key','api_key':'{{#env.MONGODB_API_KEY#}}'}},
             headers='Accept:application/json',params=params,body={'type':'none','data':[]},ssl_verify=True,
             timeout={'connect':5,'read':20,'write':5},retry_config={'retry_enabled':False,'max_retries':0,'retry_interval':1000},
             error_strategy='default-value',default_value=[{'key':'status_code','type':'number','value':0},{'key':'body','type':'string','value':''},
                {'key':'headers','type':'object','value':{}},{'key':'files','type':'array[file]','value':[]}])

    start=graph.node('start','首次填写项目；各阶段确认表在聊天框回传',variables=[
        {'variable':'project_name','label':'项目名称（新建时填写）','type':'text-input','required':False,'max_length':100,'options':[]},
        {'variable':'pdf_files','label':'客户图纸PDF（首次上传；也可在聊天框上传）','type':'file-list','required':False,'max_length':1,'options':[],
         'allowed_file_types':['document'],'allowed_file_extensions':['.pdf'],'allowed_file_upload_methods':['local_file']},
        {'variable':'technical_files','label':'技术协议PDF（选填，原件由Dify保存）','type':'file-list','required':False,'max_length':3,'options':[],
         'allowed_file_types':['document'],'allowed_file_extensions':['.pdf'],'allowed_file_upload_methods':['local_file']},
        {'variable':'notes','label':'项目补充说明（选填）','type':'paragraph','required':False,'max_length':3000,'options':[]}])
    route=code('识别明确操作，严格区分项目阶段','route_action',{'query':['sys','query'],'state_json':['conversation','project_state'],
        'chat_files':['sys','files'],'pdf_files':[start,'pdf_files'],'technical_files':[start,'technical_files'],
        'project_name':[start,'project_name'],'notes':[start,'notes']},
        {'route':'string','state_json':'string','message':'string','upload_source':'string','has_technical':'string'},fixed={'action':'开始选型'})
    graph.edge(start,route);saved=persist(route)
    switch=graph.branch('按当前业务阶段进入流程',[route,'route'],['NEW','FORM','SOURCE_RETURN','SELECT_RETURN','QUOTE_RETURN','MATCH','PRICE',
       'PRODUCTION','ENGINEERING_FORM','ENGINEERING_RETURN','CAD','ARCHIVE']);graph.edge(saved,switch)
    status=graph.answer('查看项目与操作提示','{{#'+route+'.message#}}');graph.edge(switch,status,'false')

    techgate=graph.branch('是否保留可选技术协议',[route,'has_technical'],['YES']);graph.edge(switch,techgate,'NEW')
    technical=graph.node('document-extractor','读取技术协议原文，保留原附件',is_array_file=True,variable_selector=[start,'technical_files'],
        error_strategy='default-value',default_value=[{'key':'text','type':'array[string]','value':[]}]);graph.edge(techgate,technical,'YES')
    techsave=code('保存协议原文与解析状态','save_technical',{'state_json':['conversation','project_state'],'texts':[technical,'text']},
        {'state_json':'string','valid':'string','message':'string'});graph.edge(technical,techsave);techvalid=guard(techsave)
    uploadgate=graph.branch('使用聊天图纸还是首次表单图纸',[route,'upload_source'],['CHAT']);graph.edge(techgate,uploadgate,'false');graph.edge(techvalid,uploadgate,'YES')
    chat=passthrough('使用本轮聊天框PDF',['sys','files']);form=passthrough('使用首次表单PDF',[start,'pdf_files'])
    graph.edge(uploadgate,chat,'CHAT');graph.edge(uploadgate,form,'false')
    files=graph.node('variable-aggregator','本次客户图纸文件',output_type='array[file]',variables=[[chat,'result'],[form,'result']]);graph.edge(chat,files);graph.edge(form,files)

    recognition=yaml.safe_load((ROOT/'recognition_v7.yml').read_text());rnodes=recognition['workflow']['graph']['nodes']
    old_start=next(n['id'] for n in rnodes if n['data']['type']=='start')
    old_answer=next(n['id'] for n in rnodes if n['data']['title']=='输出识图候选和具体待核对位置')
    check=next(n['id'] for n in rnodes if n['data']['title']=='检查PDF输入')
    collect=next(n['id'] for n in rnodes if n['data']['title']=='按原页位置合并实例，校验回路归属')
    def rewrite(value):
        if isinstance(value,list):
            if value==[old_start,'pdf_files']:return [files,'output']
            if value==[old_start,'notes']:return [start,'notes']
            return [rewrite(x) for x in value]
        if isinstance(value,dict):return {k:rewrite(v) for k,v in value.items()}
        return value
    graph.nodes.extend(rewrite(deepcopy(n)) for n in rnodes if n['id'] not in (old_start,old_answer))
    graph.edges.extend(deepcopy(e) for e in recognition['workflow']['graph']['edges'] if e['source']!=old_start and e['target']!=old_answer)
    graph.edge(files,check)
    imported=code('把识图结果转为待人工核对的项目清单','bridge_recognition',{'state_json':['conversation','project_state'],
        'result_json':[collect,'result_json'],'result_pages':[collect,'result_pages']},{'state_json':'string','message':'string','valid':'string'})
    graph.edge(collect,imported);importvalid=guard(imported)

    formnode=code('生成当前阶段确认表，保留项目版本与签名','make_stage_form',{'state_json':['conversation','project_state'],
        'markup':['env','MATERIAL_MARKUP_RATE'],'discount':['env','QUOTE_DISCOUNT_RATE']},
        {'state_json':'string','file_base64':'string','filename':'string','message':'string','valid':'string'})
    graph.edge(importvalid,formnode,'YES');graph.edge(switch,formnode,'FORM');graph.edge(switch,formnode,'ENGINEERING_FORM')
    formvalid=guard(formnode);formfile=file_tool('输出当前确认表XLSX',[formnode,'file_base64'],[formnode,'filename']);graph.edge(formvalid,formfile,'YES')
    forma=graph.answer('下载、填写后在本对话回传','{{#'+formnode+'.message#}}\n\n{{#'+formfile+'.files#}}');graph.edge(formfile,forma)

    return_nodes=[]
    for kind,handle,label in [('SOURCE','SOURCE_RETURN','问题清单'),('SELECTION','SELECT_RETURN','选型确认'),('QUOTE','QUOTE_RETURN','采购报价'),('ENGINEERING','ENGINEERING_RETURN','工程参数')]:
        selected,reader=encode_file('原样读取回传'+label+'XLSX，保留编码前导零',['sys','files']);graph.edge(switch,selected,handle)
        node=code('校验'+label+'的项目、版本、固定列与确认人','read_stage_return',{'state_json':['conversation','project_state'],'encoded':[reader,'text']},
                  {'state_json':'string','message':'string','route':'string','valid':'string'},fixed={'kind':kind});graph.edge(reader,node)
        gate=guard(node);branch=graph.branch('按确认结果继续处理',[node,'route'],['MATCH','PRICE','PRODUCTION']);graph.edge(gate,branch,'YES');graph.edge(branch,status,'false')
        return_nodes.append(branch)

    lookup_ends={}
    for mode,title in [('MATCH','产品选型'),('PRICE','报价参考价'),('VERIFY','排产前产品版本复核')]:
        prep=code('准备'+title+'，调用既有只读API','prepare_lookup',{'state_json':['conversation','project_state'],'api_base':['env','MONGODB_API_BASE_URL']},
            {'state_json':'string','queries':'array[string]','valid':'string','message':'string'},fixed={'mode':mode})
        if mode in ('MATCH','PRICE'):
            graph.edge(switch,prep,mode)
            for branch in return_nodes:graph.edge(branch,prep,mode)
        else:
            graph.edge(switch,prep,'PRODUCTION')
            for branch in return_nodes:graph.edge(branch,prep,'PRODUCTION')
        pg=guard(prep)
        it=graph.node('iteration',title+'：逐项处理',iterator_selector=[prep,'queries'],output_selector=[],output_type='array[string]',
              is_parallel=False,parallel_nums=1,error_handle_mode='terminated',start_node_id='',width=1500,height=300);graph.edge(pg,it,'YES')
        istart=graph.node('iteration-start','');graph.data(it)['start_node_id']=istart
        card=code('读取本项'+title+'请求','lookup_card',{'card':[it,'item']},
            {'url':'string','q':'string','model':'string','card_json':'string','message':'string'})
        request=http('只读HTTP：'+title,'{{#'+card+'.url#}}','q:{{#'+card+'.q#}}\nmodel:{{#'+card+'.model#}}\nlimit:50' if mode=='MATCH' else '')
        result=code('核对'+title+'响应，不用猜测数据替代','lookup_result',{'card_json':[card,'card_json'],'body':[request,'body'],'status_code':[request,'status_code'],'error_message':[request,'error_message']},
            {'result_json':'string','message':'string'})
        for i,n in enumerate((istart,card,request,result)):graph.inside(n,it,40+350*i)
        graph.edge(istart,card);graph.edge(card,request);graph.edge(request,result);graph.data(it)['output_selector']=[result,'result_json']
        finish=code('汇总'+title+'，失败保留项目','finish_lookup',{'state_json':[prep,'state_json'],'results':[it,'output']},
            {'state_json':'string','valid':'string','message':'string'},fixed={'mode':mode});graph.edge(it,finish);fg=guard(finish)
        lookup_ends[mode]=fg
        if mode!='VERIFY':graph.edge(fg,formnode,'YES')

    produced=code('仅在报价确认后计算采购报价与排产文件','production_files',{'state_json':['conversation','project_state']},
        {'state_json':'string','quote_base64':'string','quote_filename':'string','production_base64':'string','production_filename':'string','message':'string','valid':'string'})
    graph.edge(lookup_ends['VERIFY'],produced,'YES');pv=guard(produced)
    qfile=file_tool('生成已确认采购报价表',[produced,'quote_base64'],[produced,'quote_filename']);graph.edge(pv,qfile,'YES')
    pfile=file_tool('生成排产技术资料',[produced,'production_base64'],[produced,'production_filename']);graph.edge(qfile,pfile)
    done=graph.answer('已确认采购报价与排产技术资料','{{#'+produced+'.message#}}\n\n{{#'+qfile+'.files#}}\n\n{{#'+pfile+'.files#}}');graph.edge(pfile,done)
    cad=code('核对DWG接口与实际工程参数','prepare_cad',{'state_json':['conversation','project_state'],'cad_url':['env','CAD_CONVERT_URL']},
        {'valid':'string','payload_json':'string','message':'string'});graph.edge(switch,cad,'CAD')
    cg=guard(cad,False)
    convert=graph.node('http-request','CAD接口：转换已确认工程DXF',method='post',url='{{#env.CAD_CONVERT_URL#}}',
        authorization={'type':'api-key','config':{'type':'custom','header':'X-API-Key','api_key':'{{#env.CAD_API_KEY#}}'}},
        headers='Content-Type:application/json',params='',body={'type':'json','data':[{'key':'','type':'text','value':'{{#'+cad+'.payload_json#}}','file':[]}]},
        timeout={'connect':5,'read':90,'write':30},ssl_verify=True,retry_config={'retry_enabled':False,'max_retries':0,'retry_interval':1000},
        error_strategy='default-value',default_value=[{'key':'status_code','type':'number','value':0},{'key':'body','type':'string','value':''},
                {'key':'headers','type':'object','value':{}},{'key':'files','type':'array[file]','value':[]}])
    graph.edge(cg,convert,'YES')
    checked=code('不把JSON或改后缀文件当DWG完成','check_cad_response',{'files':[convert,'files'],'headers':[convert,'headers'],'status_code':[convert,'status_code']},
        {'valid':'string','message':'string'});graph.edge(convert,checked);cvg=guard(checked,False)
    selected,encoded=encode_file('读取转换附件用于DWG文件头检查',[convert,'files']);graph.edge(cvg,selected,'YES')
    binary=code('检查DWG文件头，拒绝改后缀伪文件','check_dwg_binary',{'encoded':[encoded,'text']},{'valid':'string','message':'string'});graph.edge(encoded,binary)
    bg=guard(binary,False)
    cada=graph.answer('DWG附件与审图状态','{{#'+binary+'.message#}}\n\n{{#'+convert+'.files#}}');graph.edge(bg,cada,'YES')
    archived=code('导出项目状态和确认记录','export_archive',{'state_json':['conversation','project_state']},
        {'file_base64':'string','filename':'string','message':'string','valid':'string'});graph.edge(switch,archived,'ARCHIVE')
    av=guard(archived,False);afile=file_tool('生成项目归档JSON',[archived,'file_base64'],[archived,'filename']);graph.edge(av,afile,'YES')
    aa=graph.answer('项目资料归档','{{#'+archived+'.message#}}\n\n{{#'+afile+'.files#}}');graph.edge(afile,aa)

    doc=deepcopy(recognition);doc['app'].update(name='成套柜项目助手 V28（Dify1.8完整业务流程）',
        description='V7局部识图→人工核对→只读产品匹配→采购报价确认回传→排产技术资料；技术协议可选保存，DWG使用已确认工程参数和实际CAD转换接口。')
    env=[]
    for name,kind,value,description in [('MONGODB_API_BASE_URL','string','','产品只读API根地址，不包含/v1或/live'),('MONGODB_API_KEY','secret','','产品API访问密钥'),
       ('MATERIAL_MARKUP_RATE','string','0.15','仅用于待确认报价表的参考预填，仍需确认'),('QUOTE_DISCOUNT_RATE','string','0','参考折扣率，仍需确认'),
       ('CAD_CONVERT_URL','string','','DXF转DWG接口；未配置时不宣称完成DWG'),('CAD_API_KEY','secret','','CAD转换服务访问密钥')]:
        env.append({'id':str(uuid.uuid5(uuid.NAMESPACE_DNS,'v28.'+name)),'name':name,'value_type':kind,'value':value,'description':description,'selector':['env',name]})
    wf=doc['workflow'];wf['environment_variables']=env;wf['conversation_variables']=[{'id':str(uuid.uuid5(uuid.NAMESPACE_DNS,'v28.project_state')),
       'name':'project_state','value_type':'string','value':'','selector':['conversation','project_state'],'description':'本会话项目状态与确认记录，不含API密钥'}]
    wf['features']['file_upload'].update(allowed_file_extensions=['.pdf','.xlsx'],allowed_file_types=['document'],number_limits=5)
    wf['features']['opening_statement']='首次填写项目名称并上传完整图纸PDF，技术协议可选，发送“开始选型”。流程会输出识图与问题清单：填写后在聊天框上传并发送“提交问题清单”；随后依次“提交选型确认”“提交报价表”。采购报价表确认回传后才生成排产技术资料。可随时“查看项目”。工程图使用“补充工程参数”“提交工程参数”“生成DWG”，未接CAD服务会明确显示待接入。'
    wf['features']['suggested_questions']=['开始选型','提交问题清单','提交选型确认','提交报价表','查看项目','继续处理','修订资料','生成排产资料','补充工程参数','提交工程参数','生成DWG','保存项目资料']
    graph.layout();graph.nodes.sort(key=lambda n:bool(n.get('parentId')))
    wf['graph']={'nodes':graph.nodes,'edges':graph.edges,'viewport':{'x':0,'y':0,'zoom':.3}}
    OUTPUT.write_text(yaml.safe_dump(doc,allow_unicode=True,sort_keys=False))
    print('Built',len(graph.nodes),'nodes',len(graph.edges),'edges',OUTPUT.name)
    return doc


if __name__=='__main__':build()
