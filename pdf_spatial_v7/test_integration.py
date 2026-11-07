"""Dify 1.8 node boundary and unchanged Base64 codec tool-body checks.

Requires local DIFY18_CONTRACT, BASE64_PACKAGE, PNG_FIXTURE paths.
Service wiring and tool transport are mocked; no live Dify or model call.
"""
import ast
from collections.abc import Mapping
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import zipfile
import yaml
from test_v7 import response, obj, anchor, link

ROOT = Path(__file__).resolve().parent


def main():
    spec=importlib.util.spec_from_file_location('dify_contract',os.environ['DIFY18_CONTRACT'])
    contract=importlib.util.module_from_spec(spec);spec.loader.exec_module(contract)
    app=yaml.safe_load((ROOT/'pdf_electrical_v7_spatial_crop.yml').read_text())
    nd={n['data']['title']:n['data'] for n in app['workflow']['graph']['nodes']}
    visited=[]
    def run(title,inputs):
        cfg=nd[title];pool={}
        for variable in cfg['variables']:
            name=variable['variable'];value=inputs[name]
            if name in ('files','images'):
                segment=contract.file_segment([dict(x,id=str(i),type='document' if title=='检查PDF输入' else 'image')
                                               for i,x in enumerate(value)],array=True)
            elif isinstance(value,list) and value and isinstance(value[0],dict):
                segment=contract.CONTRACT['ArrayObjectSegment'](value=value)
            else:
                segment=contract.primitive_segment(value)
            pool[tuple(variable['value_selector'])]=segment
        result=contract.execute_code_node(cfg,lambda s:pool[tuple(s)])
        assert set(result)==set(cfg['outputs'])
        for k,v in result.items():
            if isinstance(v,str):assert len(v)<=80000,(title,k)
            if isinstance(v,list):assert all(not isinstance(x,str) or len(x)<=80000 for x in v)
        visited.append(title)
        return result

    assert run('检查PDF输入',{'files':[{'filename':'fixture.pdf'}]})['valid']=='YES'
    meta={'success':True,'total_pages':1,'total_images':1,'images':[
        {'filename':'fixture_page_1.png','file_index':1,'page_number':1}]}
    prepared=run('核对页图，生成整页和六个局部识别任务',{'tool_json':[meta],
        'images':[{'filename':'fixture_page_1.png'}],'document_text':[''],'notes':''})
    context=run('绑定页码和裁切范围，代码不接收单个File',{'job_json':prepared['jobs'][2],
        'manifest_compact':prepared['manifest_compact']})

    # Execute the official 1.8 list selection methods with real File segments.
    scope=dict(contract.CONTRACT,Mapping=Mapping,Any=Any,_SUPPORTED_TYPES_ALIAS=object,
               _SUPPORTED_TYPES_TUPLE=(contract.CONTRACT['ArrayFileSegment'],),
               NodeRunResult=lambda **kw:SimpleNamespace(**kw),
               WorkflowNodeExecutionStatus=SimpleNamespace(SUCCEEDED='succeeded',FAILED='failed'),
               ListOperatorError=ValueError,InvalidKeyError=ValueError)
    list_source=Path(os.environ.get('DIFY18_LIST_OPERATOR',str(ROOT/'vendor_dify18/list_operator_node.py')))
    tree=ast.parse(list_source.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ListOperatorNode')
    methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('_run','_extract_slice')]
    exec(compile(ast.Module(body=methods,type_ignores=[]),'official_list_operator','exec'),scope)
    all_files=contract.file_segment([{'filename':'fixture_page_1.png','id':'page1','type':'image'},
                                     {'filename':'fixture_page_2.png','id':'page2','type':'image'}],array=True)
    node=SimpleNamespace(_node_data=SimpleNamespace(variable=['pages','files'],filter_by=SimpleNamespace(enabled=False),
        extract_by=SimpleNamespace(enabled=True,serial='{{#ctx.serial#}}'),order_by=SimpleNamespace(enabled=False),
        limit=SimpleNamespace(enabled=False)),graph_runtime_state=SimpleNamespace(variable_pool=SimpleNamespace(
            get=lambda s:all_files,convert_template=lambda s:SimpleNamespace(text='2'))))
    node._extract_slice=lambda v:scope['_extract_slice'](node,v)
    selection=scope['_run'](node)
    assert selection.status=='succeeded'
    assert selection.outputs['first_record'].filename=='fixture_page_2.png'

    # Unchanged upstream encoder/decoder method bodies, with mock SDK messages.
    class ToolStub:
        def create_text_message(self,text):return {'text':text}
        def create_blob_message(self,blob,meta):return {'blob':blob,'meta':meta}
    class FileStub:
        def __init__(self,blob):self.blob=blob;self.mime_type='image/png'
    with zipfile.ZipFile(os.environ['BASE64_PACKAGE']) as z:
        utility={};exec(z.read('tools/utils/image_prefix_utils.py'),utility)
        tool_scope={'Tool':ToolStub,'File':FileStub,'ToolInvokeMessage':object,'wrap_mime_text_with_prefix':utility['wrap_mime_text_with_prefix'],
                    'extract_mime_type':utility['extract_mime_type']}
        for name in ('base64_image_encoder','base64_image_decoder'):
            tree=ast.parse(z.read('tools/'+name+'/'+name+'.py'))
            tree.body=[n for n in tree.body if not (isinstance(n,ast.ImportFrom) and
                       (n.module.startswith('dify_plugin') or n.module.startswith('tools.')))]
            exec(compile(tree,'official_'+name,'exec'),tool_scope)
    png=Path(os.environ['PNG_FIXTURE']).read_bytes()
    encoded=list(tool_scope['Base64ImageEncoderTool']()._invoke({'input_image':FileStub(png)}))[0]['text']
    cropped=run('按真实像素裁切局部PNG，保留颜色',{'encoded':encoded,'job_json':context['context_json']})
    assert cropped['valid']=='YES'
    text=''.join(cropped['part%d'%i] for i in range(16))
    # Test the exact Dify mixed parameter template, including all chunks.
    template=nd['现成插件：局部PNG恢复为Dify图片文件']['tool_parameters']['encoded_text']['value']
    crop_id=next(n['id'] for n in app['workflow']['graph']['nodes'] if n['data']['title']=='按真实像素裁切局部PNG，保留颜色')
    for i in range(16):template=template.replace('{{#'+crop_id+'.part%d#}}'%i,cropped['part%d'%i])
    assert template==text
    decoded=list(tool_scope['Base64ImageDecoderTool']()._invoke({'encoded_text':template,'output_filename':cropped['filename']}))[0]
    assert decoded['blob'].startswith(b'\x89PNG') and decoded['meta']['mime_type']=='image/png'
    files=[{'filename':decoded['meta']['filename']}]
    good=run('核对图片身份，不将原图冒充局部图',{'files':files,'context_json':cropped['context_json']})
    assert good['valid']=='YES'
    assert run('核对图片身份，不将原图冒充局部图',{'files':[{'filename':'fixture_page_1.png'}],
        'context_json':cropped['context_json']})['valid']=='NO'
    model=response(objects=[obj('A','MCB-C16A/1P',[100,200,240,220])],anchors=[anchor('R','F7')],links=[link('A','R')])
    record=run('映射回原页坐标，备注与器件分开',{'raw':json.dumps(model),'context_json':cropped['context_json']})
    overview_context=run('绑定页码和裁切范围，代码不接收单个File',{'job_json':prepared['jobs'][0],
        'manifest_compact':prepared['manifest_compact']})
    overview=run('映射回原页坐标，备注与器件分开',{'raw':json.dumps(response(overview=True)),
        'context_json':overview_context['context_json']})
    fail=run('保留失败区域，不让局部失败清空其他区域',{'context_json':cropped['context_json'],'message':'fixture failure'})
    bad=run('保留当前视图图片缺失记录',{'context_json':cropped['context_json'],'message':'fixture image mismatch'})
    out=run('按原页位置合并实例，校验回路归属',{'packets':[overview['packet_json'],record['packet_json'],fail['packet_json']],
        'manifest_compact':prepared['manifest_compact']})
    result=json.loads(out['result_json']);assert result['summary']['candidate_objects']==1
    assert result['summary']['failed_views']
    # A crop branch must not fall back to the already-existing original file.
    file_agg=nd['选择当前整页或局部图文件']
    assert file_agg['output_type']=='array[file]'
    index={n['id']:n['data']['title'] for n in app['workflow']['graph']['nodes']}
    assert all(index[v[0]]!='按序号选择原始页图（Dify原生文件节点）' for v in file_agg['variables'])
    report={'generated_code_nodes_executed':len(set(visited)),'official_list_operator_selection':True,
            'upstream_base64_0_3_tool_bodies_executed':True,'real_png_byte_bridge':True,
            'mixed_parameter_chunk_join':True,'original_not_used_as_crop_fallback':True,
            'actual_model_calls':0,'live_dify_server':False,
            'scope':'Official Dify 1.8 File/Segment and list selection methods; unchanged codec tool bodies with mock SDK messages and local code backend. Not a complete deployed Dify or live recognition test.'}
    (ROOT/'validation_dify18.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
