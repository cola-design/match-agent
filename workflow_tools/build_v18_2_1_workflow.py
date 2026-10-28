"""Patch the reviewed V18.2 artifact without rebuilding or changing its baseline."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(__file__).resolve().parent / 'runtime'


def build():
    document = yaml.safe_load((ROOT / 'formal_selection_quote_v18_2_http_api.yml').read_text())
    nodes = {node['id']: node for node in document['workflow']['graph']['nodes']}
    common = (RUNTIME / 'pole_checks.py').read_text()
    for nid, snippet in [('2000000000024', 'selection_v18_2_1.py'), ('3400000000005', 'price_v18_2_1.py')]:
        nodes[nid]['data']['code'] += '\n' + common + '\n' + (RUNTIME / snippet).read_text()
    nodes['2000000000025']['data']['code'] += '''
_v1821_requirement_display = v182_requirement_display
def v182_requirement_display(text):
    return _v1821_requirement_display(text).replace('ip_rating：', '防护等级：')
'''
    document['app']['name'] = '成套柜智能选型与报价 V18.2.1（实测问题修正）'
    document['app']['description'] += '；依据实际生成的确认单修正后备保护器类别别名，严格区分4P与3P+N，并在计价时复核。'
    for node in nodes.values():
        data = node['data']
        if data['type'] == 'code':
            data['code'] = data['code'].replace('V18.2', 'V18.2.1')
            compile(data['code'], node['id'], 'exec')
        if data['type'] == 'tool':
            filename = data['tool_configurations'].get('output_filename', {})
            if filename.get('type') == 'constant':
                filename['value'] = filename['value'].replace('V18.2', 'V18.2.1')
    output = ROOT / 'formal_selection_quote_v18_2_1_http_api.yml'
    output.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False, width=1000))
    assert yaml.safe_load(output.read_text()) == document
    return output


if __name__ == '__main__':
    print(build())
