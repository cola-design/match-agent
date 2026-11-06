"""Exercise inspected official request construction with a controlled backend.

Optional developer check; no network calls and no model inference. Supply the
official provider source file via OLLAMA_PROVIDER_SOURCE_PATH.
"""
import ast
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from typing import cast
from urllib.parse import urljoin

from dify_plugin.entities.model.llm import LLMMode
from dify_plugin.entities.model.message import (
    UserPromptMessage, SystemPromptMessage, AssistantPromptMessage, ToolPromptMessage,
    TextPromptMessageContent, ImagePromptMessageContent, PromptMessageContentType,
)

ROOT = Path(__file__).resolve().parent


def main():
    path = Path(os.environ['OLLAMA_PROVIDER_SOURCE_PATH'])
    tree = ast.parse(path.read_text())
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == 'OllamaLargeLanguageModel')
    functions = [f for f in cls.body if isinstance(f, ast.FunctionDef) and f.name in ('_generate', '_convert_prompt_message_to_dict')]
    seen = {}
    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return SimpleNamespace(status_code=200, encoding='utf-8')
    scope = dict(globals(), requests=SimpleNamespace(post=post))
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)] + functions, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), 'official_ollama_011_methods', 'exec'), scope)
    Harness = type('Harness', (), {'_generate': scope['_generate'], '_convert_prompt_message_to_dict': scope['_convert_prompt_message_to_dict'],
                                   '_handle_generate_response': lambda *a, **k: 'controlled response'})
    image = ImagePromptMessageContent(base64_data='aGVsbG8=', format='png', mime_type='image/png')
    message = UserPromptMessage(content=[TextPromptMessageContent(data='recognize this image'), image])
    Harness()._generate('TenGen-ZhiHan:F16', {'base_url': 'http://offline.invalid:11434', 'mode': 'chat'}, [message],
                        {'temperature': 0, 'num_predict': 4096, 'num_ctx': 32768, 'format': 'json', 'think': False}, stream=False)
    payload = seen['json']
    assert seen['url'] == 'http://offline.invalid:11434/api/chat'
    assert payload['model'] == 'TenGen-ZhiHan:F16'
    assert payload['think'] is False
    assert payload['format'] == 'json'
    assert payload['options']['num_ctx'] == 32768
    assert payload['messages'][0]['images'] == ['aGVsbG8=']
    assert 'think' not in payload['options']
    assert 'format' not in payload['options']
    report = {'provider_version': '0.1.1', 'source_ref': '29ddda7d12f3a75c61bba7b82e3cb13c8e084e80',
              'assertions': 8, 'failures': 0, 'actual_ollama_calls': 0,
              'endpoint_path': '/api/chat', 'image_in_body': True, 'think_false_at_top_level': True,
              'format_json_at_top_level': True, 'backend': 'controlled requests.post, no network inference'}
    (ROOT / 'validation_ollama_protocol.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == '__main__': main()
