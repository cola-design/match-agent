import ast
from copy import deepcopy
from pathlib import Path
import uuid
import yaml
from graph import Graph
from prompts import VISION

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT/'pdf_electrical_v7_spatial_crop.yml'
BASE64_ID = 'bowenliang123/base64_codec:0.3.0@c8f203a60a313fda5d6286033b96969f9a7219f849b7ff5f3fb3d79d10068bc2'


def build():
    base = yaml.safe_load((ROOT/'workflow_seed.yml').read_text())
    template = base['workflow'].pop('llm_template')
    body = []
    for name in ('transport.py', 'literal_decode.py', 'inventory.py', 'png_crop.py', 'spatial.py'):
        tree = ast.parse((ROOT/name).read_text())
        body.extend(n for n in tree.body if not (isinstance(n, ast.ImportFrom) and n.module in ('literal_decode','transport','inventory')))
    functions = {n.name: n for n in body if isinstance(n, ast.FunctionDef)}
    graph = Graph(); graph.counter = 3700000000000
    def code(title, fn, inputs, outputs):
        selected = set(); pending = [fn]
        while pending:
            name = pending.pop()
            if name in selected:
                continue
            selected.add(name)
            pending.extend(n.id for n in ast.walk(functions[name]) if isinstance(n, ast.Name) and n.id in functions)
        exported = [n for n in body if isinstance(n, (ast.Import, ast.ImportFrom, ast.Assign)) or isinstance(n, ast.FunctionDef) and n.name in selected]
        source = '\n\n'.join(ast.unparse(n) for n in exported)
        source += '\n\ndef main(' + ','.join(inputs) + '):\n    return ' + fn + '(' + ','.join(inputs) + ')\n'
        compile(source, title, 'exec')
        return graph.node('code', title, code_language='python3', code=source,
                          variables=[{'variable': k, 'value_selector': v} for k,v in inputs.items()],
                          outputs={k: {'type': v, 'children': None} for k,v in outputs.items()})
    def codec(title, name, inputs):
        schema = yaml.safe_load((ROOT/(name+'_schema.yml')).read_text())
        schemas = schema['parameters']
        return graph.node('tool', title, provider_id='bowenliang123/base64_codec/base64_codec',
                          provider_name='bowenliang123/base64_codec/base64_codec', provider_type='builtin',
                          tool_name=name, tool_label=title, tool_description=title, tool_node_version='2',
                          is_team_authorization=True, tool_configurations={},
                          tool_parameters=inputs, paramSchemas=schemas,
                          params={p['name']:'' for p in schemas}, output_schema=None)

    start = graph.node('start', '上传完整PDF，自动裁切并识别', variables=[
        {'variable':'pdf_files','label':'客户电气图PDF（完整文件，无需拆图）','type':'file-list','required':True,
         'max_length':1,'options':[],'allowed_file_types':['document'],'allowed_file_extensions':['.pdf'],
         'allowed_file_upload_methods':['local_file']},
        {'variable':'notes','label':'已确认选型要求（选填）','type':'paragraph','required':False,'max_length':3000,'options':[]}])
    check = code('检查PDF输入', 'check_input', {'files':[start,'pdf_files']}, {'valid':'string','message':'string'})
    graph.edge(start,check); gate = graph.branch('是否为一份PDF',[check,'valid'],['YES']);graph.edge(check,gate)
    wrong = graph.answer('提示PDF输入问题','{{#'+check+'.message#}}');graph.edge(gate,wrong,'false')
    extractor = graph.node('document-extractor','读取文字辅助，文字顺序不代表图面位置',is_array_file=True,
                           variable_selector=[start,'pdf_files'],error_strategy='default-value',
                           default_value=[{'key':'text','type':'array[string]','value':[]}])
    graph.edge(gate,extractor,'YES')
    cfg = yaml.safe_load((ROOT/'pdf_tool_template.yml').read_text())
    for key in ('type','title','isInIteration','iteration_id'):
        cfg.pop(key,None)
    cfg['tool_parameters']={'pdf_files':{'type':'variable','value':[start,'pdf_files']},'dpi':{'type':'constant','value':300}}
    convert = graph.node('tool','PDF整页转PNG，保留原始页图',**cfg);graph.edge(extractor,convert)
    jobs = code('核对页图，生成整页和六个局部识别任务','prepare_jobs',
                {'tool_json':[convert,'json'],'images':[convert,'files'],'document_text':[extractor,'text'],'notes':[start,'notes']},
                {'valid':'string','manifest_compact':'string','message':'string','jobs':'array[string]'})
    graph.edge(convert,jobs); gate2=graph.branch('页图是否齐全',[jobs,'valid'],['YES']);graph.edge(jobs,gate2)
    wrong2=graph.answer('提示转图问题','{{#'+jobs+'.message#}}');graph.edge(gate2,wrong2,'false')
    iteration=graph.node('iteration','逐页整图与局部独立识别，不按读取顺序编回路',iterator_selector=[jobs,'jobs'],
                         output_selector=[],output_type='array[string]',is_parallel=False,parallel_nums=1,
                         error_handle_mode='terminated',start_node_id='',width=5000,height=650)
    graph.edge(gate2,iteration,'YES'); istart=graph.node('iteration-start','');graph.data(iteration)['start_node_id']=istart
    context=code('绑定页码和裁切范围，代码不接收单个File','job_context',
                 {'job_json':[iteration,'item'],'manifest_compact':[jobs,'manifest_compact']},
                 {'file_serial':'string','mode':'string','context_json':'string','job_json':'string'})
    select=graph.node('list-operator','按序号选择原始页图（Dify原生文件节点）',variable=[convert,'files'],
                      var_type='array[file]',item_var_type='file',filter_by={'enabled':False,'conditions':[]},
                      order_by={'enabled':False,'key':'','value':'asc'},limit={'enabled':False,'size':1},
                      extract_by={'enabled':True,'serial':'{{#'+context+'.file_serial#}}'})
    mode=graph.branch('整页概览还是实际裁切',[context,'mode'],['overview'])
    overview=graph.node('list-operator','仅整页分支提供原始图片',variable=[select,'result'],
                        var_type='array[file]',item_var_type='file',filter_by={'enabled':False,'conditions':[]},
                        order_by={'enabled':False,'key':'','value':'asc'},limit={'enabled':False,'size':1},
                        extract_by={'enabled':False,'serial':'1'})
    encode=codec('现成插件：原页图片转Base64','base64_image_encoder',{'input_image':{'type':'variable','value':[select,'first_record']}})
    outputs={'valid':'string','message':'string','context_json':'string','filename':'string'}
    outputs.update({'part%d'%i:'string' for i in range(16)})
    crop=code('按真实像素裁切局部PNG，保留颜色','crop_node',
              {'encoded':[encode,'text'],'job_json':[context,'context_json']},outputs)
    crop_gate=graph.branch('裁切是否成功',[crop,'valid'],['YES'])
    decode=codec('现成插件：局部PNG恢复为Dify图片文件','base64_image_decoder',
                 {'encoded_text':{'type':'mixed','value':''.join('{{#'+crop+'.part%d#}}'%i for i in range(16))},
                  'output_filename':{'type':'variable','value':[crop,'filename']}})
    files=graph.node('variable-aggregator','选择当前整页或局部图文件',output_type='array[file]',
                     variables=[[decode,'files'],[overview,'result']])
    ctx=graph.node('variable-aggregator','选择当前图像的位置说明',output_type='string',
                   variables=[[crop,'context_json'],[context,'context_json']])
    file_check=code('核对图片身份，不将原图冒充局部图','check_view_files',
                    {'files':[files,'output'],'context_json':[ctx,'output']},{'valid':'string','message':'string'})
    file_gate=graph.branch('当前视图图片是否正确',[file_check,'valid'],['YES'])
    cfg=deepcopy(template)
    for key in ('type','title','isInIteration','iteration_id'):
        cfg.pop(key,None)
    cfg['model']={'provider':'langgenius/tongyi/tongyi','name':'qwen3-vl-plus','mode':'chat',
                  'completion_params':{'temperature':0,'max_tokens':8192,'enable_thinking':False,'response_format':'json_object'}}
    cfg['prompt_template']=[{'id':uuid.uuid4().hex,'role':'system','text':VISION},
                            {'id':uuid.uuid4().hex,'role':'user','text':'{{#'+ctx+'.output#}}'}]
    cfg['vision']={'enabled':True,'configs':{'detail':'high','variable_selector':[files,'output']}}
    cfg['error_strategy']='default-value';cfg['default_value']=[{'key':'text','type':'string','value':'{}'}]
    model=graph.node('llm','qwen3-vl-plus：逐位置原文、符号和回路证据',**cfg)
    parsed=code('映射回原页坐标，备注与器件分开','parse_view',
                {'raw':[model,'text'],'context_json':[ctx,'output']},{'packet_json':'string'})
    failed=code('保留失败区域，不让局部失败清空其他区域','crop_failure',
                {'context_json':[crop,'context_json'],'message':[crop,'message']},{'packet_json':'string'})
    bad_file=code('保留当前视图图片缺失记录','crop_failure',
                  {'context_json':[ctx,'output'],'message':[file_check,'message']},{'packet_json':'string'})
    packet=graph.node('variable-aggregator','本视图结果或明确失败记录',output_type='string',
                      variables=[[parsed,'packet_json'],[failed,'packet_json'],[bad_file,'packet_json']])
    graph.data(iteration)['output_selector']=[packet,'output']
    internal=[istart,context,select,mode,overview,encode,crop,crop_gate,decode,files,ctx,file_check,file_gate,model,parsed,failed,bad_file,packet]
    for i,n in enumerate(internal):
        graph.inside(n,iteration,40+335*i)
    for a,b in [(istart,context),(context,select),(select,mode),(overview,files),(encode,crop),(crop,crop_gate),(decode,files),
                (files,ctx),(ctx,file_check),(file_check,file_gate),(model,parsed),(parsed,packet),(failed,packet),(bad_file,packet)]:
        graph.edge(a,b)
    graph.edge(mode,overview,'overview');graph.edge(mode,encode,'false')
    graph.edge(crop_gate,decode,'YES');graph.edge(crop_gate,failed,'false')
    graph.edge(file_gate,model,'YES');graph.edge(file_gate,bad_file,'false')
    collect=code('按原页位置合并实例，校验回路归属','collect_spatial',
                 {'packets':[iteration,'output'],'manifest_compact':[jobs,'manifest_compact']},
                 {'report':'string','result_json':'string','result_pages':'array[string]','source_text':'string'})
    graph.edge(iteration,collect);answer=graph.answer('输出识图候选和具体待核对位置','{{#'+collect+'.report#}}');graph.edge(collect,answer)
    graph.layout();graph.nodes.sort(key=lambda n:bool(n.get('parentId')))
    app=deepcopy(base);app['app'].update(name='PDF电气识图 V7（自动局部图与位置校验）',
       description='完整PDF自动裁切为局部图，使用qwen3-vl-plus；按实际位置保留同型号实例，分离备注和参数，检查回路绑定。需Base64插件。')
    app['dependencies']=[{'current_identifier':None,'type':'marketplace','value':{'marketplace_plugin_unique_identifier':v}} for v in [
       'langgenius/tongyi:0.2.3@966063fe991c50e5ac82106e65fc1949f852b2989ab2433ac028f0d25c6f92b1',
       'atoy0m0/pdf-to-images:1.0.1@bfd6805409081d0ce320c6ecfdd0cc74a3b5e07b9d3ee2450bd53226f9ce40ae',BASE64_ID]]
    workflow=app['workflow'];workflow['graph']={'nodes':graph.nodes,'edges':graph.edges,'viewport':{'x':0,'y':0,'zoom':.4}}
    workflow['environment_variables']=[];workflow['conversation_variables']=[]
    workflow['features']['file_upload'].update(enabled=True,allowed_file_types=['document'],allowed_file_extensions=['.pdf'],
        allowed_file_upload_methods=['local_file'],number_limits=1)
    workflow['features']['opening_statement']='上传一份完整电气图PDF，发送“开始识图”。流程自动生成重叠局部图，不需要手动拆图。模型使用qwen3-vl-plus；保留每个位置实例，柜号/备注单列，归属不清留待核对。首次需安装Base64插件（bowenliang123），无需新增服务器。'
    workflow['features']['suggested_questions']=['开始识图']
    OUTPUT.write_text(yaml.safe_dump(app,allow_unicode=True,sort_keys=False))
    print('Built',len(graph.nodes),'nodes:',OUTPUT.name)
    return app


if __name__=='__main__':
    build()
