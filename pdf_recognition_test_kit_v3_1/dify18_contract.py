"""Load the official Dify 1.8 file types and code input boundary for regression tests.

This is a test harness, not a complete Dify server. The official File/Segment
classes, input conversion method, and segment JSON serializer are unchanged.
Only unrelated service wiring and the execution backend are supplied locally.
"""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parent/'vendor_dify18'


def load_contract():
    scope={'__name__':'dify18_type_contract'}
    def load(name,classes=None):
        tree=ast.parse((ROOT/name).read_text())
        body=[]
        for node in tree.body:
            if isinstance(node,ast.ImportFrom) and (node.level or (node.module or '').startswith('core.')):continue
            if classes is not None and not isinstance(node,(ast.Import,ast.ImportFrom)) and not (isinstance(node,ast.ClassDef) and node.name in classes):continue
            body.append(node)
        exec(compile(ast.Module(body=body,type_ignores=[]),name,'exec'),scope)
    load('file_enums.py');load('file_constants.py');load('file_models.py',{'File'})
    load('segment_types.py');load('segments.py')
    # SegmentGroup is never used in these tests; the official serializer's
    # isinstance branch still needs a nominal class to reference.
    scope['SegmentGroup']=type('UnusedSegmentGroup',(),{})
    load('segment_utils.py')
    tree=ast.parse((ROOT/'code_node.py').read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='CodeNode')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_run')
    scope.update(NodeRunResult=lambda **kwargs:SimpleNamespace(**kwargs),
                 WorkflowNodeExecutionStatus=SimpleNamespace(SUCCEEDED='succeeded',FAILED='failed'),
                 CodeExecutionError=type('TestExecutionError',(Exception,),{}),
                 CodeNodeError=type('TestCodeNodeError',(Exception,),{}))
    exec(compile(ast.Module(body=[method],type_ignores=[]),'official_CodeNode_run','exec'),scope)
    return scope


CONTRACT=load_contract()


def file_segment(value,array=False):
    values=value if array else [value]
    files=[CONTRACT['File'](id=v['id'],tenant_id='fixture-tenant',type=CONTRACT['FileType'](v.get('type','image')),
        transfer_method=CONTRACT['FileTransferMethod'].REMOTE_URL,remote_url='https://example.invalid/'+v['id'],
        filename=v['filename'],extension=Path(v['filename']).suffix,mime_type='image/png' if v.get('type')=='image' else 'text/plain') for v in values]
    return CONTRACT['ArrayFileSegment'](value=files) if array else CONTRACT['FileSegment'](value=files[0])


def primitive_segment(value):
    if value is None:return CONTRACT['NoneSegment']()
    if isinstance(value,bool):return CONTRACT['BooleanSegment'](value=value)
    if isinstance(value,int):return CONTRACT['IntegerSegment'](value=value)
    if isinstance(value,float):return CONTRACT['FloatSegment'](value=value)
    if isinstance(value,str):return CONTRACT['StringSegment'](value=value)
    if isinstance(value,list):return CONTRACT['ArrayStringSegment'](value=value)
    return CONTRACT['ObjectSegment'](value=value)


def execute_code_node(data,get_segment,executor=None):
    scope=CONTRACT
    def local(code,inputs):
        namespace={};exec(code,namespace)
        return namespace['main'](**inputs)
    def execute_workflow_code_template(language,code,inputs):
        # This official serializer raises exactly the reported File TypeError
        # when CodeNode has passed a bare File object instead of a FileSegment.
        wire=scope['dumps_with_segments'](inputs,ensure_ascii=False)
        return (executor or local)(code,json.loads(wire))
    scope['CodeExecutor']=SimpleNamespace(execute_workflow_code_template=execute_workflow_code_template)
    node=SimpleNamespace(_node_data=SimpleNamespace(code_language=data['code_language'],code=data['code'],
        variables=[SimpleNamespace(**v) for v in data['variables']],outputs=data['outputs']),
        graph_runtime_state=SimpleNamespace(variable_pool=SimpleNamespace(get=get_segment)),
        _transform_result=lambda result,output_schema:result)
    result=scope['_run'](node)
    if result.status!='succeeded':raise AssertionError(result.error)
    return result.outputs
