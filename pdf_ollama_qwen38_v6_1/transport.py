from collections import Counter
import base64
import html
import json
import math
import re
import unicodedata
import zlib
from literal_decode import norm

def j(v):
    return json.dumps(v, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

def canonical(s):
    return re.sub('\\s+', '', norm(s)).upper().replace('×', 'X')

def uncompress(value):
    obj = zlib.decompressobj()
    raw = obj.decompress(base64.b64decode(value, validate=True), 600001)
    if len(raw) > 600000 or not obj.eof or obj.unused_data:
        raise ValueError('整份说明超出预算或结构无效')
    return json.loads(raw)

def check_input(files):
    valid = isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict)
    name = str((files[0] if valid else {}).get('filename') or (files[0] if valid else {}).get('name') or '')
    valid = valid and name.lower().endswith('.pdf')
    return {'valid': 'YES' if valid else 'NO', 'message': '' if valid else '请在表单中上传一份完整PDF，不需要拆图或TXT。'}

def bind_pages(tool_json, images, document_text, notes):
    try:
        records = [x for x in tool_json or [] if isinstance(x, dict) and 'images' in x]
        if len(records) != 1 or records[0].get('success') is not True:
            raise ValueError('未收到PDF-to-Images成功记录，请查看现有转图节点')
        stats = records[0]
        pages = stats['images']
        if type(stats.get('total_pages')) is not int or not 1 <= stats['total_pages'] <= 12:
            raise ValueError('本次试用支持1至12页，未截取部分页冒充整份识别')
        if stats.get('total_images') != stats['total_pages'] or len(pages) != stats['total_pages']:
            raise ValueError('PDF页数与转图数量不一致，停止识别')
        names = [str(x.get('filename') or x.get('name') or '') for x in images or []]
        expected = [x['filename'] for x in pages]
        if len(set(names)) != len(names) or len(set(expected)) != len(expected) or set(names) != set(expected):
            raise ValueError('PDF页图文件缺失或重复，停止识别')
        numbers = [x.get('page_number') for x in pages]
        if any((type(n) is not int for n in numbers)) or set(numbers) != set(range(1, len(pages) + 1)):
            raise ValueError('PDF页码缺失或重复')
        if any((x.get('file_index') != 1 for x in pages)):
            raise ValueError('本次仅允许一份PDF，不混合不同文件的页图')
        if isinstance(document_text, list):
            if any((not isinstance(x, str) for x in document_text)):
                raise ValueError('文字辅助结构无效')
            text = '\n'.join(document_text)
        elif isinstance(document_text, str):
            text = document_text
        else:
            text = ''
        if len(text) > 60000:
            raise ValueError('PDF全文超过本次上下文预算，未删去后半部分继续识别')
        if len(text) + len(str(notes or '')[:3000]) + 1000 > 60000:
            raise ValueError('全文和补充说明超过单页上下文预算，尚未调用模型')
        packet = {'schema_version': 'pdf-ollama-page/5', 'images': pages, 'uploaded_names': names, 'native_text': text, 'notes': str(notes or '')[:3000], 'page_count': len(pages)}
        encoded = base64.b64encode(zlib.compress(j(packet).encode('utf8'))).decode('ascii')
        if len(encoded) > 60000:
            raise ValueError('整份说明超过节点传输预算')
        return {'valid': 'YES', 'manifest_compact': encoded, 'message': ''}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return {'valid': 'NO', 'manifest_compact': '', 'message': str(exc)}

def page_context(index, manifest_compact):
    p = uncompress(manifest_compact)
    if isinstance(index, bool) or not isinstance(index, (int, float)) or (not math.isfinite(index)) or (index != int(index)) or (not 0 <= index < len(p['uploaded_names'])):
        raise ValueError('页图序号无效')
    name = p['uploaded_names'][int(index)]
    image = next((x for x in p['images'] if x['filename'] == name))
    ctx = {'page': image['page_number'], 'image_name': name, 'page_count': p['page_count'], 'whole_document_text': p['native_text'], 'project_notes': p['notes'], 'native_text_scope': '全文件文字，仅用于辅助转写，不证明某标注属于当前页，也不提供文字坐标'}
    encoded = j(ctx)
    if len(encoded) > 60000:
        raise ValueError('单页辅助上下文超过预算，未截断')
    return {'context_json': encoded}
