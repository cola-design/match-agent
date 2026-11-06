from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent
PLUGIN = ROOT / 'plugin'


def label(en, zh):
    return {'en_US': en, 'zh_Hans': zh}


def write(path, value):
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))


def main():
    write(PLUGIN / 'manifest.yaml', {
        'version': '0.1.0', 'type': 'plugin', 'author': 'cola-design', 'name': 'electrical-pdf',
        'label': label('Electrical PDF Reader', '电气PDF原文定位与自动放大'),
        'description': label('Native PDF evidence, automatic zoom and a unified selection workbook.',
                             '完整PDF输入，保留原文坐标，自动放大并统一导出选型信息；无独立后端。'),
        'icon': 'icon.svg', 'resource': {'memory': 1073741824, 'permission': {}},
        'plugins': {'tools': ['provider/electrical_pdf.yaml']},
        'meta': {'version': '0.0.1', 'arch': ['amd64', 'arm64'],
                 'runner': {'language': 'python', 'version': '3.12', 'entrypoint': 'main'},
                 'minimum_dify_version': '1.8.0'},
        'created_at': '2026-11-06T12:00:00+08:00', 'privacy': 'PRIVACY.md', 'verified': False,
    })
    write(PLUGIN / 'provider/electrical_pdf.yaml', {
        'identity': {'author': 'cola-design', 'name': 'electrical-pdf',
                     'label': label('Electrical PDF Reader', '电气PDF原文定位与自动放大'),
                     'description': label('Native PDF evidence and rendering inside Dify.', '在Dify插件内解析PDF、自动放大和生成统一核对表。'),
                     'icon': 'icon.svg'},
        'credentials_for_provider': {},
        'tools': ['tools/prepare_pdf.yaml', 'tools/export_selection.yaml'],
        'extra': {'python': {'source': 'provider/electrical_pdf.py'}},
    })
    pdf = {'name': 'pdf_files', 'type': 'files', 'required': True,
           'label': label('One complete PDF', '一份完整PDF'),
           'human_description': label('Upload exactly one PDF.', '只上传一份完整PDF，无需拆图。'),
           'llm_description': 'Exactly one uploaded PDF file.', 'form': 'llm'}
    for name, title, source, params in [
        ('prepare-pdf', '完整PDF原文定位与自动放大', 'prepare_pdf', [pdf]),
        ('export-selection', '整份选型信息统一导出', 'export_selection', [pdf,
            {'name': 'vision_results', 'type': 'string', 'required': True,
             'label': label('Validated visual results', '已校验视觉结果'),
             'human_description': label('Produced by the paired workflow.', '由配套流程自动生成。'),
             'llm_description': 'JSON array of validated or explicitly failed visual records.', 'form': 'llm'},
            {'name': 'selection_context', 'type': 'string', 'required': False,
             'label': label('Selection constraints', '已确认选型约束'),
             'human_description': label('Optional confirmed project constraints.', '可选的已确认项目约束。'),
             'llm_description': 'User-supplied constraints, not drawing evidence.', 'form': 'llm'}]),
    ]:
        write(PLUGIN / ('tools/' + source + '.yaml'), {
            'identity': {'name': name, 'author': 'cola-design', 'label': label(name, title)},
            'description': {'human': label(title, title), 'llm': title}, 'parameters': params,
            'extra': {'python': {'source': 'tools/' + source + '.py'}},
        })
    (PLUGIN / '_assets/icon.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#234F6B"/><path d="M20 12h18l8 8v32H20z" fill="white"/><path d="M34 22l-9 15h8l-3 12 12-18h-9z" fill="#EFAF24"/></svg>')


if __name__ == '__main__': main()
