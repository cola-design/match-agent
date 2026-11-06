"""Create the Tongyi qwen3-vl-plus variant from the V6.1 Ollama workflow."""
from copy import deepcopy
import argparse
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent
FILENAME = 'pdf_electrical_v6_1_qwen3_vl_plus.yml'
TONGYI = 'langgenius/tongyi:0.2.3@966063fe991c50e5ac82106e65fc1949f852b2989ab2433ac028f0d25c6f92b1'


def convert(source):
    app = deepcopy(source)
    app['app']['name'] = 'PDF电气识图 V6.1（qwen3-vl-plus）'
    app['app']['description'] = '使用qwen3-vl-plus及已有PDF-to-Images：先定位回路，再提取器件；保留V6.1逐项校验、中文解释和失败诊断。'
    old_deps = app['dependencies']
    replaced = 0
    for dep in old_deps:
        value = dep['value']
        if value.get('marketplace_plugin_unique_identifier', '').startswith('langgenius/ollama:'):
            value['marketplace_plugin_unique_identifier'] = TONGYI
            replaced += 1
    assert replaced == 1
    count = 0
    for node in app['workflow']['graph']['nodes']:
        data = node['data']
        if data['type'] == 'start':
            data['title'] = '上传完整PDF，使用qwen3-vl-plus视觉模型'
        if data['type'] == 'llm':
            data['model'] = {
                'provider': 'langgenius/tongyi/tongyi', 'name': 'qwen3-vl-plus', 'mode': 'chat',
                'completion_params': {'temperature': 0, 'max_tokens': 8192,
                                      'enable_thinking': False, 'response_format': 'json_object'}}
            count += 1
        if data['type'] == 'code':
            code = data['code']
            code = code.replace('TenGen-ZhiHan:F16', 'qwen3-vl-plus')
            code = code.replace("'provider': 'ollama'", "'provider': 'tongyi'")
            code = code.replace('Ollama / qwen3-vl-plus', '通义千问 / qwen3-vl-plus')
            code = code.replace('请确认think=false', '请关闭模型思考模式并输出完整JSON')
            compile(code, '<exported-node>', 'exec')
            data['code'] = code
    assert count == 3
    features = app['workflow']['features']
    features['opening_statement'] = '上传一份完整电气图PDF，发送“开始识图”。本版使用通义千问qwen3-vl-plus和已有PDF-to-Images。先定位回路号、负荷与线缆，再提取器件并给中文解释；漏项、未知型号和归属差异会列待核对。无需拆图或部署脚本。本次只核对选型输入。'
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path, help='Original V6.1 workflow YML')
    args = parser.parse_args()
    source = yaml.safe_load(args.source.read_text())
    output = ROOT/FILENAME
    output.write_text(yaml.safe_dump(convert(source), allow_unicode=True, sort_keys=False))
    print(output.name)


if __name__ == '__main__':
    main()
