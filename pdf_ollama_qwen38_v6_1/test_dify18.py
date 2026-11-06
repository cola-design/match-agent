"""Execute generated nodes through the official Dify 1.8 type boundary.

Set DIFY18_CONTRACT to the local test harness path. The execution backend
and service wiring are mocked; this does not run Dify or a vision model.
"""
import importlib.util
import json
import os
from pathlib import Path
import yaml
from test_v6 import context, outline, extraction
from nodes import j

ROOT = Path(__file__).resolve().parent


def main():
    harness = os.environ.get('DIFY18_CONTRACT')
    if not harness:
        raise SystemExit('Set DIFY18_CONTRACT to an existing local harness path')
    spec = importlib.util.spec_from_file_location('dify_contract', harness)
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    app = yaml.safe_load((ROOT/'PDF电气识图_V6_1_校验兼容与结果保留.yml').read_text())
    data = {n['data']['title']: n['data'] for n in app['workflow']['graph']['nodes']}
    visited = []

    def run(title, values):
        cfg = data[title]
        pool = {}
        for variable in cfg['variables']:
            name = variable['variable']
            value = values[name]
            if name in ('files', 'images'):
                segment = contract.file_segment([
                    dict(x, id=str(i), type='image' if name == 'images' else 'document')
                    for i, x in enumerate(value)], array=True)
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                segment = contract.CONTRACT['ArrayObjectSegment'](value=value)
            else:
                segment = contract.primitive_segment(value)
            pool[tuple(variable['value_selector'])] = segment
        result = contract.execute_code_node(cfg, lambda s: pool[tuple(s)])
        assert set(result) == set(cfg['outputs']), title
        visited.append(title)
        return result

    assert run('核对PDF输入', {'files': [{'filename': 'fixture.pdf'}]})['valid'] == 'YES'
    ctx = context()
    meta = {'success': True, 'total_pages': 1, 'total_images': 1, 'images': [
        {'filename': ctx['image_name'], 'file_index': 1, 'page_number': 1}]}
    bound = run('核对页图数量、页码和全文辅助', {
        'tool_json': [meta], 'images': [{'filename': ctx['image_name']}],
        'document_text': [ctx['whole_document_text']], 'notes': ''})
    assert bound['valid'] == 'YES', bound
    current = run('按序号绑定当前页，图片不进入代码节点', {
        'index': 0, 'manifest_compact': bound['manifest_compact']})
    plan = run('校验回路表，不按文字顺序补编号', {
        'raw': j(outline()), 'context_json': current['context_json']})
    request = run('绑定回路表与原文提示，供第二遍核对', {
        'context_json': current['context_json'], 'outline_json': plan['outline_json']})
    variant = extraction()
    variant['items'][1]['category'] = 'RCB'
    variant['items'][1]['drawing_id'] = 'UNDECLARED'
    first = run('中文解码、回路一致性和漏项检查', {
        'raw': j(variant), 'extraction_json': request['extraction_json']})
    page = json.loads(first['packet_json'])
    assert page['status'] == 'parsed' and len(page['items']) == 3
    assert page['items'][1]['drawing_id'] is None
    assert page['items'][1]['category'] == 'residual_current_device_unspecified'
    second = run('复看后再检查，不掩盖剩余未知', {
        'raw': '{"items":[', 'extraction_json': request['extraction_json']})
    best = run('复看失败不丢弃首轮可读结果', {
        'first_json': first['packet_json'], 'second_json': second['packet_json']})
    assert json.loads(best['packet_json'])['attempt_audit']['adopted'] == 'first'
    out = run('统一汇总中文解释与回路核对', {
        'packets': [best['packet_json']], 'manifest_compact': bound['manifest_compact']})
    summary = json.loads(out['result_json'])['summary']
    assert summary['candidate_objects'] == 3
    assert summary['items_without_confirmed_drawing'] == 1
    report = {'generated_code_nodes_executed': len(set(visited)), 'failures': 0,
              'actual_model_calls': 0, 'live_dify_server': False,
              'checks': ['ArrayFile inputs', 'generated normalization closure',
                         'RCB alias retention', 'unknown drawing remains unbound',
                         'broken retry preserves first result', 'result collection'],
              'scope': 'Official Dify 1.8 File/Segment input conversion and serialization; mocked backend and service wiring, not a complete deployed Dify test.'}
    (ROOT/'validation_dify18.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
