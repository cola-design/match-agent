import ast
from copy import deepcopy
from pathlib import Path
import uuid
import yaml
from graph import Graph

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'PDF电气识图_V6_1_校验兼容与结果保留.yml'
from prompts import OUTLINE, EXTRACT


def build():
    base = yaml.safe_load((ROOT / 'workflow_seed.yml').read_text())
    llm_template = base['workflow'].pop('llm_template')
    tree = ast.parse((ROOT / 'nodes.py').read_text())
    tree.body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom) and n.module in ('literal_decode', 'transport', 'normalization'))]
    tree.body.extend(n for n in ast.parse((ROOT / 'transport.py').read_text()).body if not (isinstance(n, ast.ImportFrom) and n.module == 'literal_decode'))
    tree.body.extend(n for n in ast.parse((ROOT / 'literal_decode.py').read_text()).body if isinstance(n, ast.FunctionDef))
    tree.body.extend(n for n in ast.parse((ROOT / 'normalization.py').read_text()).body if not (isinstance(n, ast.ImportFrom) and n.module == 'literal_decode'))
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    g = Graph(); g.counter = 3100000000000

    def code(title, fn, inputs, outputs):
        selected = set(); pending = [fn]
        while pending:
            name = pending.pop()
            if name in selected: continue
            selected.add(name)
            pending += [x.id for x in ast.walk(functions[name]) if isinstance(x, ast.Name) and x.id in functions]
        nodes = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.Assign)) or isinstance(n, ast.FunctionDef) and n.name in selected]
        source = '\n\n'.join(ast.unparse(n) for n in nodes)
        source += '\n\ndef main(' + ','.join(inputs) + '):\n    return ' + fn + '(' + ','.join(inputs) + ')\n'
        compile(source, fn, 'exec')
        return g.node('code', title, code_language='python3', code=source,
                      variables=[{'variable': k, 'value_selector': v} for k, v in inputs.items()],
                      outputs={k: {'type': v, 'children': None} for k, v in outputs.items()})

    start = g.node('start', '上传完整PDF，使用已有Ollama视觉模型', variables=[
        {'variable': 'pdf_files', 'label': '① 客户电气图PDF（完整文件，无需拆图）', 'type': 'file-list',
         'required': True, 'max_length': 1, 'options': [], 'allowed_file_types': ['document'],
         'allowed_file_extensions': ['.pdf'], 'allowed_file_upload_methods': ['local_file']},
        {'variable': 'notes', 'label': '② 已确认选型要求（选填）', 'type': 'paragraph', 'required': False,
         'max_length': 3000, 'options': []}])
    check = code('核对PDF输入', 'check_input', {'files': [start, 'pdf_files']}, {'valid': 'string', 'message': 'string'})
    g.edge(start, check); gate = g.branch('是否收到一份PDF', [check, 'valid'], ['YES']); g.edge(check, gate)
    wrong = g.answer('说明输入问题', '{{#' + check + '.message#}}'); g.edge(gate, wrong, 'false')
    text = g.node('document-extractor', 'Dify内置：提取PDF文字辅助，不承担符号识别', is_array_file=True,
                  variable_selector=[start, 'pdf_files'], error_strategy='default-value',
                  default_value=[{'key': 'text', 'type': 'array[string]', 'value': []}])
    g.edge(gate, text, 'YES')
    convert_template = yaml.safe_load((ROOT / 'pdf_tool_template.yml').read_text())
    convert_template.update(title='现有PDF-to-Images：按整页转图，不用新增解析插件')
    convert_template['tool_parameters'] = {'pdf_files': {'type': 'variable', 'value': [start, 'pdf_files']},
                                           'dpi': {'type': 'constant', 'value': 300}}
    for key in ('type', 'title', 'isInIteration', 'iteration_id'): convert_template.pop(key, None)
    convert = g.node('tool', '现有PDF-to-Images：按整页转图，不用新增解析插件', **convert_template)
    g.edge(text, convert)
    bind = code('核对页图数量、页码和全文辅助', 'bind_pages',
                {'tool_json': [convert, 'json'], 'images': [convert, 'files'],
                 'document_text': [text, 'text'], 'notes': [start, 'notes']},
                {'valid': 'string', 'manifest_compact': 'string', 'message': 'string'})
    g.edge(convert, bind); gate2 = g.branch('全部PDF页图是否齐全', [bind, 'valid'], ['YES']); g.edge(bind, gate2)
    wrong2 = g.answer('说明转图返回的问题', '{{#' + bind + '.message#}}'); g.edge(gate2, wrong2, 'false')
    iteration = g.node('iteration', '逐页先定位回路，再提取器件并核对', iterator_selector=[convert, 'files'],
                       output_selector=[], output_type='array[string]', is_parallel=False, parallel_nums=1,
                       error_handle_mode='terminated', start_node_id='', width=4200, height=430)
    g.edge(gate2, iteration, 'YES')
    istart = g.node('iteration-start', ''); g.data(iteration)['start_node_id'] = istart
    context = code('按序号绑定当前页，图片不进入代码节点', 'context_with_inventory',
                   {'index': [iteration, 'index'], 'manifest_compact': [bind, 'manifest_compact']}, {'context_json': 'string'})

    def llm(title, input_node, system_prompt, extra=''):
        cfg = deepcopy(llm_template)
        for key in ('type', 'title', 'isInIteration', 'iteration_id'): cfg.pop(key, None)
        cfg['model'] = {'provider': 'langgenius/ollama/ollama', 'name': 'TenGen-ZhiHan:F16', 'mode': 'chat',
                        'completion_params': {'temperature': 0, 'num_predict': 4096, 'num_ctx': 32768,
                                              'format': 'json', 'think': False}}
        cfg['prompt_template'] = [{'id': uuid.uuid4().hex, 'role': 'system', 'text': system_prompt + extra},
                                  {'id': uuid.uuid4().hex, 'role': 'user', 'text': '{{#' + input_node[0] + '.' + input_node[1] + '#}}'}]
        cfg['vision'] = {'enabled': True, 'configs': {'detail': 'high', 'variable_selector': [iteration, 'item']}}
        cfg['error_strategy'] = 'default-value'; cfg['default_value'] = [{'key': 'text', 'type': 'string', 'value': '{}'}]
        return g.node('llm', title, **cfg)

    outline_model = llm('第一遍：定位回路号、负荷与同线器件', [context, 'context_json'], OUTLINE)
    outline = code('校验回路表，不按文字顺序补编号', 'parse_outline',
                   {'raw': [outline_model, 'text'], 'context_json': [context, 'context_json']}, {'outline_json': 'string'})
    extraction = code('绑定回路表与原文提示，供第二遍核对', 'extraction_context',
                      {'context_json': [context, 'context_json'], 'outline_json': [outline, 'outline_json']}, {'extraction_json': 'string'})
    model = llm('第二遍：器件、独立参数与回路归属', [extraction, 'extraction_json'], EXTRACT)
    validate = code('中文解码、回路一致性和漏项检查', 'checked',
                    {'raw': [model, 'text'], 'extraction_json': [extraction, 'extraction_json']},
                    {'action': 'string', 'packet_json': 'string', 'message': 'string'})
    retry_gate = g.branch('是否无需再看本页', [validate, 'action'], ['ACCEPT'])
    retry = llm('仅针对本页疑点重看一次', [extraction, 'extraction_json'], EXTRACT,
                '\n上次检查发现：{{#' + validate + '.message#}}。重新核对原图完整输出，不强行改号或猜厂家参数，无法确认时如实保留未知。')
    validate2 = code('复看后再检查，不掩盖剩余未知', 'checked',
                     {'raw': [retry, 'text'], 'extraction_json': [extraction, 'extraction_json']},
                     {'action': 'string', 'packet_json': 'string', 'message': 'string'})
    best = code('复看失败不丢弃首轮可读结果', 'choose_best',
                {'first_json': [validate, 'packet_json'], 'second_json': [validate2, 'packet_json']}, {'packet_json': 'string'})
    chosen = g.node('variable-aggregator', '保留本页结果与明确待核对项', output_type='string',
                    variables=[[best, 'packet_json'], [validate, 'packet_json']])
    g.data(iteration)['output_selector'] = [chosen, 'output']
    internal = (istart, context, outline_model, outline, extraction, model, validate, retry_gate, retry, validate2, best, chosen)
    for i, n in enumerate(internal): g.inside(n, iteration, 40 + 335 * i)
    for a, b in ((istart, context), (context, outline_model), (outline_model, outline), (outline, extraction),
                 (extraction, model), (model, validate), (validate, retry_gate), (retry, validate2), (validate2, best), (best, chosen)):
        g.edge(a, b)
    g.edge(retry_gate, chosen, 'ACCEPT'); g.edge(retry_gate, retry, 'false')
    collect = code('统一汇总中文解释与回路核对', 'collect',
                   {'packets': [iteration, 'output'], 'manifest_compact': [bind, 'manifest_compact']},
                   {'report': 'string', 'result_json': 'string', 'result_pages': 'array[string]', 'source_text': 'string'})
    g.edge(iteration, collect)
    answer = g.answer('统一识图候选表与待核对内容', '{{#' + collect + '.report#}}'); g.edge(collect, answer)
    g.layout(); g.nodes.sort(key=lambda n: bool(n.get('parentId')))
    out = deepcopy(base)
    out['app'].update(name='PDF电气识图 V6.1（校验兼容与结果保留）',
                      description='已有PDF转图和Ollama模型：先定位回路，再提取器件；逐段中文解释，检查原文漏项与归属差异。不增加解析插件，不进行设计审查或数据库选型。')
    out['dependencies'] = [
        {'current_identifier': None, 'type': 'marketplace', 'value': {'marketplace_plugin_unique_identifier':
            'langgenius/ollama:0.1.1@f3a8b649027d1bd13e67df96f3463a601c16628da4292705528d28d97d96b263'}},
        {'current_identifier': None, 'type': 'marketplace', 'value': {'marketplace_plugin_unique_identifier':
            'atoy0m0/pdf-to-images:1.0.1@bfd6805409081d0ce320c6ecfdd0cc74a3b5e07b9d3ee2450bd53226f9ce40ae'}}]
    wf = out['workflow']; wf['graph'] = {'nodes': g.nodes, 'edges': g.edges, 'viewport': {'x': 0, 'y': 0, 'zoom': .5}}
    wf['environment_variables'] = []; wf['conversation_variables'] = []
    wf['features']['opening_statement'] = '上传一份完整电气图PDF，发送“开始识图”。本版沿用已有PDF-to-Images与Ollama的TenGen-ZhiHan:F16。先定位回路号、负荷与线缆，再提取器件并给中文解释；漏项、unknown和归属差异会列待核对。无需新解析插件、TXT或手动拆图。本次只核对选型输入。'
    wf['features']['suggested_questions'] = ['开始识图']
    wf['features']['file_upload'].update(enabled=True, allowed_file_types=['document'], allowed_file_extensions=['.pdf'],
                                       allowed_file_upload_methods=['local_file'], number_limits=1)
    wf['features']['file_upload'].pop('image', None)
    OUTPUT.write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False))
    print('Built', len(g.nodes), 'nodes:', OUTPUT.name)
    return out


if __name__ == '__main__': build()
