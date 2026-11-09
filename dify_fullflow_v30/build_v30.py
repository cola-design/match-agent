"""Build from the shipped V29 full project graph; installed plugins remain unchanged."""
import ast
from copy import deepcopy
from pathlib import Path
import json
import os
import re
import yaml
from graph import Graph
from prompts import VISION

ROOT=Path(__file__).resolve().parent
BASE=Path(os.environ.get('V30_BASE_YML',ROOT.parent/'downloads/cabinet_project_v29.yml'))
if not BASE.exists():BASE=ROOT.parent/'recognition-v29-20261109/cabinet_project_v29.yml'


def code_for(fn,args):
    body=[]
    modules={'transport','inventory','literal_decode','spatial','reconciliation'}
    for file in ('transport.py','literal_decode.py','inventory.py','png_crop.py','spatial.py','reconciliation.py'):
        body.extend(n for n in ast.parse((ROOT/file).read_text()).body if not
            (isinstance(n,ast.ImportFrom) and n.module in modules))
    funcs={n.name:n for n in body if isinstance(n,ast.FunctionDef)}
    needed=set();pending=[fn]
    while pending:
        name=pending.pop()
        if name in needed:continue
        needed.add(name)
        pending.extend(n.id for n in ast.walk(funcs[name]) if isinstance(n,ast.Name) and n.id in funcs)
    out=[n for n in body if isinstance(n,(ast.Import,ast.ImportFrom,ast.Assign)) or isinstance(n,ast.FunctionDef) and n.name in needed]
    src='\n'.join(ast.unparse(n) for n in out)+'\n\ndef main('+','.join(args)+'):\n    return '+fn+'('+','.join(args)+')\n'
    compile(src,fn,'exec');return src


RECONCILE=VISION+'''
本轮mode=reconcile：图片是真实去白边的完整有效图面，包含局部识别时可能被裁断的全部区域。你必须重新读图建立唯一实体清单，不拼接局部清单，也不依次累加其中器件。
unverified_local_readings只是待核对线索，含错号、截断、误识别与重复项，绝不是答案。以本轮完整图片为准。未能在完整图定位的局部读法在unresolved说明，不能猜坐标补进objects。
先按完整图确定子图范围、柜号和印刷回路编号。回路编号的bbox只围住编号，不能围住左侧相别、柜号、线缆或负荷。按导线追踪关系，不按从上往下第几个编号。不同子图的同号回路必须保留各自位置。
再逐符号建立器件：同一符号与型号跨裁图出现多次仍只有一个对象；同型号的多个独立符号各列一项。进线、转换开关、每个出线、辅助电源和控制监测器件均核对，不能只读带编号支路。
特别核对任何电压比和VA容量附近是否有独立变压器符号；变压器与旁边断路器必须分列。参数自身不算另一台器件。电压比不得拼进MCB型号。不要遗漏控制电源与未编号的辅助回路。
局部残缺型号若整图可读，输出完整型号；仍看不清时列unresolved，不把MCB-C、RCB/O、CAT等截断词建成采购实体。I/O、相别、负荷说明、备注文字不是独立采购实体。
回路编号与器件关系只用当前完整图上可以追踪的连线，不能用局部提示中的顺序。无法确认则不建member关系，绝不套用上一张图的回路编号或器件数量。
drawings必须覆盖当前图中真实的每个子图；objects、anchors、links同前述协议。readable/view_complete按实际填写。只输出一个紧凑JSON对象。
'''


def build():
    doc=yaml.safe_load(BASE.read_text());g=Graph()
    g.nodes=doc['workflow']['graph']['nodes'];g.edges=doc['workflow']['graph']['edges'];g.counter=4300000000000
    titles={n['data']['title']:n for n in g.nodes}
    first=titles['逐页整图与局部独立识别，不按读取顺序编回路']
    prep=titles['核对页图，生成整页和六个局部识别任务']
    collector=titles['按原页位置合并实例，校验回路归属']
    parent=first['id']
    children=[n for n in g.nodes if n.get('parentId')==parent]
    # Clone the tested File adapters, crop, decoder and vision branches intact.
    mapping={}
    for n in [first]+children:
        g.counter+=1;mapping[n['id']]=str(g.counter)
    def rewrite(x):
        if isinstance(x,list):return [rewrite(v) for v in x]
        if isinstance(x,dict):return {k:rewrite(v) for k,v in x.items()}
        if isinstance(x,str):
            for old,new in mapping.items():x=x.replace(old,new)
        return x
    cloned=[rewrite(deepcopy(n)) for n in [first]+children]
    cloned_edges=[rewrite(deepcopy(e)) for e in g.edges if e['source'] in mapping and e['target'] in mapping]
    jobs=g.node('code','整理局部读法，准备整图唯一实体复核',code_language='python3',
        code=code_for('prepare_reconciliation',['packets','manifest_compact']),
        variables=[{'variable':'packets','value_selector':[parent,'output']},
                   {'variable':'manifest_compact','value_selector':[prep['id'],'manifest_compact']}],
        outputs={'jobs':{'type':'array[string]','children':None}})
    master=cloned[0];master['data']['title']='回到完整图面，统一器件实体和印刷回路'
    master['data']['iterator_selector']=[jobs,'jobs']
    master['height']=first.get('height',700);master['width']=first.get('width',2400)
    for n in cloned[1:]:
        d=n['data'];original=d['title']
        d['title']='整图复核：'+original if original else ''
        if original=='仅传当前视图任务，隔离全页文字和全页坐标':
            d['code']=code_for('request_view',['context_json'])
        elif original=='qwen3-vl-plus：逐位置原文、符号和回路证据':
            d['title']='qwen3-vl-plus：整图去重、补漏和回路复核'
            d['prompt_template'][0]['text']=RECONCILE
        elif original=='映射回原页坐标，备注与器件分开':
            d['code']=code_for('parse_view',['raw','context_json'])
    g.nodes.extend(cloned);g.edges.extend(cloned_edges)
    g.edges=[e for e in g.edges if not(e['source']==parent and e['target']==collector['id'])]
    g.edge(parent,jobs);g.edge(jobs,master['id']);g.edge(master['id'],collector['id'])
    collector['data']['title']='仅用整图核实实体建清单，局部读法保留作证据'
    collector['data']['variables']=[{'variable':'local_packets','value_selector':[parent,'output']},
        {'variable':'master_packets','value_selector':[master['id'],'output']},
        {'variable':'manifest_compact','value_selector':[prep['id'],'manifest_compact']}]
    collector['data']['code']=code_for('collect_reconciled',['local_packets','master_packets','manifest_compact'])
    # The first pass uses the fixed literal decoder too; it cannot decide inventory counts.
    for n in g.nodes:
        if n['id']==titles['映射回原页坐标，备注与器件分开']['id']:
            n['data']['code']=code_for('parse_view',['raw','context_json'])
    bridge=titles['把识图结果转为待人工核对的项目清单']['data']
    # Avoid converting incomplete global recognition into a normal ready-to-confirm form.
    needle="value=previous_main(state_json,result_json,result_pages)"
    bridge['code']=bridge['code'].replace(needle,"raw_result=json.loads(result_json)\n    if raw_result.get('summary',{}).get('master_pages_incomplete'):\n        return {'valid':'NO','state_json':state_json,'message':'整图复核未完成，本次未生成可选型的确认表。请查看整图复核节点的失败或未覆盖区域。'}\n    "+needle)
    bridge['code']=bridge['code'].replace("if row.get('quarantine_reason')=='overview_not_inventory':continue", "if row.get('quarantine_reason') in ('overview_not_inventory','local_reading_corresponds_to_master','exact_duplicate_master_record','local_fragment_of_master'):continue")
    # Resolved clipped fragments remain in raw evidence but do not become a new business question.
    bridge['code']=bridge['code'].replace("key=row['raw'].strip();groups.setdefault(key,[]).append", "key=row['raw'].strip();groups.setdefault(key,[]).append")
    form=titles['生成当前阶段确认表，保留项目版本与签名']['data']
    form['code']=form['code'].replace("'识图待定位'", "'识图待复核'").replace('请对照识图待定位表核对漏项','请对照识图待复核表核对未核实读法与漏项')
    doc['app']['name']='成套柜项目助手 V30（整图统一复核）'
    doc['app']['description']='局部读细节后回到完整有效图面去重、补漏与回路核对；只以整图核实实体生成确认清单。选型→报价确认→排产资料。'
    doc['dependencies']=[]
    for n in g.nodes:
        if n['data']['type']=='code':compile(n['data']['code'],n['data']['title'],'exec')
    g.layout();g.nodes.sort(key=lambda n:bool(n.get('parentId')))
    doc['workflow']['graph'].update(nodes=g.nodes,edges=g.edges)
    out=ROOT/'cabinet_project_v30.yml';out.write_text(yaml.safe_dump(doc,allow_unicode=True,sort_keys=False))
    print(json.dumps({'nodes':len(g.nodes),'edges':len(g.edges),'file':str(out)}));return doc

if __name__=='__main__':build()
