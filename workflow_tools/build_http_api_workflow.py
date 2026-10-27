"""Build HTTP-backed variants without changing the existing knowledge-base workflow."""
import copy
import json
from pathlib import Path
import uuid
import zipfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
BASE_ENV = [
    {'id': str(uuid.UUID('d9d99477-ae3d-416d-aa87-1d729b238601')), 'name': 'MONGODB_API_BASE_URL',
     'value_type': 'string', 'value': '', 'description': '电脑临时测试填http://电脑IPv4:8000；服务器部署后只改此地址。末尾不加斜杠。', 'selector': ['env', 'MONGODB_API_BASE_URL']},
    {'id': str(uuid.UUID('df00d21d-e594-48d6-9280-a393813cab55')), 'name': 'MONGODB_API_KEY',
     'value_type': 'secret', 'value': '', 'description': 'API服务启动时的MONGODB_API_KEY，不是MongoDB密码。', 'selector': ['env', 'MONGODB_API_KEY']},
]
NORMALIZE = '''import json

def main(body: str, status_code: int) -> dict:
    if int(status_code) != 200:
        raise ValueError("API_HTTP_%s: 检查API地址、密钥、网络或API数据库连接；本次停止，不使用旧知识库代替。" % status_code)
    try:
        rows = json.loads(body)
    except (ValueError, TypeError):
        raise ValueError("API_RESPONSE_INVALID_JSON")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("API_RESPONSE_NOT_DOCUMENT_ARRAY")
    # An empty array means no match; existing exact-code and missing-price gates handle it.
    if any(not isinstance(row.get("material_code"), str) or not row["material_code"].strip() for row in rows):
        raise ValueError("API_MATERIAL_CODE_MUST_BE_NONEMPTY_STRING")
    allowed = {"material_code", "description", "category", "standard_specs", "specification_text",
               "source_attributes", "source_status", "data_status", "quality_flags",
               "unit_price_ex_tax", "pricing_unit", "tax_rate", "price_version",
               "valid_from", "valid_to", "status", "price_basis", "confidence"}
    result = [{key: value for key, value in row.items() if key in allowed} for row in rows]
    return {"result": result}
'''

def http_data(title, url, params='', parent=None):
    data = {
        'type': 'http-request', 'title': title, 'desc': '通过独立API读取MongoDB，不在Dify代码沙箱中导入PyMongo。',
        'selected': False, 'method': 'get', 'url': url,
        'authorization': {'type': 'no-auth', 'config': None},
        'headers': 'X-API-Key:{{#env.MONGODB_API_KEY#}}\nAccept:application/json',
        'params': params, 'body': {'type': 'none', 'data': []},
        'timeout': {'connect': 5, 'read': 15, 'write': 5}, 'ssl_verify': True,
        'isInIteration': bool(parent), 'isInLoop': False,
    }
    if parent:
        data['iteration_id'] = parent
    return data


def edge(source, target, source_type, target_type, parent=None):
    data = {'sourceType': source_type, 'targetType': target_type, 'isInIteration': bool(parent), 'isInLoop': False}
    if parent: data['iteration_id'] = parent
    return {'id': source+'-source-'+target+'-target', 'source': source, 'sourceHandle': 'source',
            'target': target, 'targetHandle': 'target', 'type': 'custom', 'data': data,
            'zIndex': 1002 if parent else 0}


def write_yaml(path, value):
    text = yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=1000)
    path.write_text(text, encoding='utf-8')
    assert yaml.safe_load(text) == value


def build():
    source = yaml.safe_load((ROOT/'formal_selection_quote_v18_mongodb.yml').read_text(encoding='utf-8'))
    d = copy.deepcopy(source)
    d['app']['name'] = '成套柜智能选型与报价 V18.1（HTTP API接入版）'
    d['app']['description'] = '基于V18.0，将五处知识库检索替换为HTTP API查询和返回值校验。保留原有资料提取、人工确认、模板和整批报价校验。目标Dify运行环境仍需联调。'
    d['workflow']['environment_variables'] = copy.deepcopy(BASE_ENV)
    graph = d['workflow']['graph']
    by_id = {n['id']: n for n in graph['nodes']}
    replacements = [
        ('2000000000022', '1830000000001', '/v1/products/search', 'q:{{#2000000000021.retrieval_query#}}\nlimit:30', 'API产品候选查询'),
        ('3400000000003', '1830000000002', '/v1/products/{{#3400000000002.material_code#}}', '', 'API产品精确编码查询'),
        ('3400000000004', '1830000000003', '/v1/prices/{{#3400000000002.material_code#}}', '', 'API有效价格精确编码查询'),
        ('3400000000009', '1830000000004', '/v1/products/{{#3400000000002.material_code#}}', '', 'API产品精确编码复查'),
        ('3400000000010', '1830000000005', '/v1/prices/{{#3400000000002.material_code#}}', '', 'API有效价格精确编码复查'),
    ]
    for nid, http_id, path, params, title in replacements:
        node = by_id[nid]
        parent = node.get('parentId')
        http = copy.deepcopy(node)
        http['id'] = http_id
        http['data'] = http_data(title, '{{#env.MONGODB_API_BASE_URL#}}'+path, params, parent)
        http['height'] = 160
        http['position']['y'] -= 190
        http['positionAbsolute']['y'] -= 190
        # Keep the original node ID and its 'result' output to preserve downstream references.
        node['data'] = {
            'type': 'code', 'title': title+'：校验响应', 'selected': False,
            'desc': '仅接受HTTP 200和JSON文档数组；空数组走原有缺物料/缺价门禁，接口错误直接停止。',
            'code_language': 'python3', 'code': NORMALIZE,
            'outputs': {'result': {'children': None, 'type': 'array[object]'}},
            'variables': [
                {'variable': 'body', 'value_type': 'string', 'value_selector': [http_id, 'body']},
                {'variable': 'status_code', 'value_type': 'number', 'value_selector': [http_id, 'status_code']},
            ],
            'isInIteration': bool(parent), 'isInLoop': False,
        }
        if parent: node['data']['iteration_id'] = parent
        for e in graph['edges']:
            if e['target'] == nid:
                e['target'] = http_id
                e['id'] = e['id'].replace(nid+'-target', http_id+'-target')
                e['data']['targetType'] = 'http-request'
            if e['source'] == nid:
                e['data']['sourceType'] = 'code'
        graph['nodes'].append(http)
        graph['edges'].append(edge(http_id, nid, 'http-request', 'code', parent))
    verifier = by_id['3400000000005']['data']
    verifier['code'] = verifier['code'].replace("'PRODUCT_KB'", "'PRODUCT_API'").replace("'PRICE_KB'", "'PRICE_API'")
    # Add price provenance to Excel remarks; changes in MongoDB are read on the next pricing request.
    quote = by_id['3400000000006']['data']
    fragment = "(item.get('approval_status') or '') + '；' + (item.get('price_validity') or '')"
    addition = fragment + " + ('；价格依据：' + item['price_basis'] if item.get('price_basis') else '')"
    assert fragment in quote['code']
    quote['code'] = quote['code'].replace(fragment, addition)
    full_path = ROOT/'formal_selection_quote_v18_1_http_api.yml'
    write_yaml(full_path, d)

    # Small independent HTTP reachability probe with no database credentials or model requirements.
    probe = yaml.safe_load((ROOT/'dify_mongodb_direct_test.yml').read_text(encoding='utf-8'))
    probe['app']['name'] = 'API连接检查（电脑或服务器）'
    probe['app']['description'] = '仅通过HTTP节点检查独立API的健康接口，无PyMongo依赖，不修改数据库。'
    probe['dependencies'] = []
    probe['workflow']['environment_variables'] = copy.deepcopy(BASE_ENV)
    probe['workflow']['features']['opening_statement'] = '先设置环境变量MONGODB_API_BASE_URL和MONGODB_API_KEY，再发送任意消息运行。'
    start, old_code, answer = probe['workflow']['graph']['nodes']
    start['data']['variables'] = []
    start['data']['title'] = '开始'
    start['data']['desc'] = '配置引用环境变量，无需填写数据库账号。'
    http_id = '1830000000100'
    request = copy.deepcopy(old_code)
    request['id'] = http_id
    request['data'] = http_data('HTTP检查API与MongoDB连接', '{{#env.MONGODB_API_BASE_URL#}}/health')
    request['position'] = request['positionAbsolute'] = {'x': 360, 'y': 200}
    old_code['position'] = old_code['positionAbsolute'] = {'x': 700, 'y': 200}
    probe_code = '''import json

def main(body: str, status_code: int) -> dict:
    code = int(status_code)
    # Echo only API health state, not request headers or unexpected response content.
    if code != 200:
        return {"result": json.dumps({"ok": False, "http_status": code}, ensure_ascii=False)}
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return {"result": json.dumps({"ok": False, "http_status": code, "reason": "invalid_json"})}
    if not isinstance(data, dict):
        return {"result": json.dumps({"ok": False, "http_status": code, "reason": "unexpected_body"})}
    return {"result": json.dumps({"ok": data.get("status") == "ok", "http_status": code,
                                  "database": data.get("database", "")}, ensure_ascii=False)}
'''
    old_code['data'] = {'title': '解释健康检查结果', 'type': 'code', 'selected': False,
                        'code_language': 'python3', 'code': probe_code,
                        'outputs': {'result': {'children': None, 'type': 'string'}},
                        'variables': [
                            {'variable': 'body', 'value_type': 'string', 'value_selector': [http_id, 'body']},
                            {'variable': 'status_code', 'value_type': 'number', 'value_selector': [http_id, 'status_code']},
                        ]}
    answer['position'] = answer['positionAbsolute'] = {'x': 1040, 'y': 200}
    answer['data']['title'] = '输出API检查结果'
    answer['data']['answer'] = 'API检查结果：{{#'+old_code['id']+'.result#}}'
    probe['workflow']['graph']['nodes'] = [start, request, old_code, answer]
    probe['workflow']['graph']['edges'] = [edge(a['id'], b['id'], a['data']['type'], b['data']['type'])
                                          for a,b in zip([start,request,old_code],[request,old_code,answer])]
    probe_path = ROOT/'dify_http_api_connection_check.yml'
    write_yaml(probe_path, probe)
    for document in (d, probe):
        ids = {node['id'] for node in document['workflow']['graph']['nodes']}
        assert len(ids) == len(document['workflow']['graph']['nodes'])
        for e in document['workflow']['graph']['edges']:
            assert e['source'] in ids and e['target'] in ids
        for node in document['workflow']['graph']['nodes']:
            if node['data'].get('type') == 'code':
                compile(node['data']['code'], node['id'], 'exec')
    assert sum(n['data']['type'] == 'http-request' for n in graph['nodes']) == 5
    assert not any(n['data']['type'] == 'knowledge-retrieval' for n in graph['nodes'])
    print('Built full HTTP API workflow and minimal reachability check.')

if __name__ == '__main__':
    build()
