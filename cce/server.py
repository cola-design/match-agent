"""Huawei Cloud gateway for the workbench's Dify application."""
import hmac
import json
import re
import threading
from email import policy
from email.parser import BytesParser
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError

from relay_http import (
    JSON_LIMIT, UPLOAD_LIMIT, USER_ID, JSONHandler, RequestError,
    contact, endpoint, opener, read_settings, relay_token,
)


def valid_upload(content_type, body):
    if not content_type.isascii():
        raise RequestError(400, '文件上传格式无效。')
    message = BytesParser(policy=policy.default).parsebytes(
        b'Content-Type: ' + content_type.encode('ascii') + b'\r\n\r\n' + body
    )
    parts = list(message.iter_parts())
    fields = {part.get_param('name', header='content-disposition'): part for part in parts}
    if len(parts) != 2 or set(fields) != {'file', 'user'} or any(
        part.is_multipart() or part.get_content_disposition() != 'form-data' for part in parts
    ):
        raise RequestError(400, '请上传一份图纸。')
    try:
        user = fields['user'].get_payload(decode=True).decode('ascii')
    except UnicodeError:
        user = ''
    if not USER_ID.fullmatch(user):
        raise RequestError(400, '上传请求的用户标识无效。')
    file_part = fields['file']
    filename = file_part.get_filename() or ''
    extension = Path(filename).suffix.lower()
    content = file_part.get_payload(decode=True)
    limits = {'.pdf': (15, b'%PDF-'), '.png': (10, b'\x89PNG\r\n\x1a\n'),
              '.jpg': (10, b'\xff\xd8\xff'), '.jpeg': (10, b'\xff\xd8\xff')}
    if extension not in limits or not content:
        raise RequestError(415, '请上传 PDF、PNG 或 JPG 图纸。')
    max_mb, signature = limits[extension]
    if len(content) > max_mb * 1024 * 1024:
        raise RequestError(413, f'此图纸的大小上限为 {max_mb} MB。')
    # Some PDF writers put padding ahead of the header.
    signature_ok = signature in content[:1024] if extension == '.pdf' else content.startswith(signature)
    if not signature_ok:
        raise RequestError(415, '图纸内容与文件格式不匹配。')


def chat_payload(body):
    try:
        payload = json.loads(body)
        inputs = payload['inputs']
        drawing = inputs['drawing']
        kind, upload_id = drawing['type'], drawing['upload_file_id']
        project, count, user = inputs['project'], inputs['cabinet_count'], payload['user']
        if (
            kind not in ('document', 'image') or drawing['transfer_method'] != 'local_file'
            or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', upload_id)
            or not isinstance(project, str) or len(project) > 80
            or not re.fullmatch(r'(?:[1-9][0-9]{0,5})?', count)
            or not USER_ID.fullmatch(user)
            or payload['response_mode'] != 'streaming'
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise RequestError(400, '识图参数无效，请检查图纸、项目名称和柜台数。') from None
    return json.dumps({
        'inputs': {
            'drawing': {'type': kind, 'transfer_method': 'local_file', 'upload_file_id': upload_id},
            'project': project,
            'cabinet_count': count,
        },
        'query': '请分析',
        'response_mode': 'streaming',
        'user': user,
        'conversation_id': '',
    }, ensure_ascii=False).encode()


class Gateway(JSONHandler):
    client = opener(direct=True)
    slots = threading.BoundedSemaphore(8)

    def authenticate(self):
        supplied = self.headers.get('Authorization', '')
        return hmac.compare_digest(supplied.encode(), f'Bearer {self.server.token}'.encode())

    def do_GET(self):
        if self.path != '/api/check':
            return self.json_response({'error': '接口不存在。'}, 404)
        if not self.authenticate():
            return self.json_response({'error': '华为云接入令牌无效。'}, 401)
        self.forward('/info', 'check')

    def do_POST(self):
        if self.path not in ('/api/upload', '/api/run'):
            return self.json_response({'error': '接口不存在。'}, 404)
        if not self.authenticate():
            return self.json_response({'error': '华为云接入令牌无效。'}, 401)
        if not self.slots.acquire(blocking=False):
            return self.json_response({'error': '当前识图服务繁忙，请稍后再试。'}, 429)
        try:
            if self.path == '/api/upload':
                body = self.read_body(UPLOAD_LIMIT, 'multipart/form-data')
                content_type = self.headers['Content-Type']
                valid_upload(content_type, body)
                self.forward('/files/upload', 'upload', body, content_type)
            else:
                body = chat_payload(self.read_body(JSON_LIMIT, 'application/json'))
                self.forward('/chat-messages', 'run', body, 'application/json')
        except RequestError as exc:
            self.json_response({'error': str(exc)}, exc.status)
        finally:
            self.slots.release()

    def forward(self, path, operation, body=None, content_type=None):
        try:
            with contact(
                self.client, self.server.dify_base + path, self.server.dify_key,
                '公司内网 Dify', body, content_type, timeout=10 if operation == 'check' else 180,
            ) as upstream:
                if operation == 'run':
                    return self.stream(upstream, 'Dify')
                result = self.json_upstream(upstream, 'Dify')
                if not isinstance(result, dict):
                    raise RequestError(502, 'Dify 返回的数据格式异常。')
                if operation == 'check':
                    return self.json_response({'ok': True})
                file_id = result.get('id')
                if not isinstance(file_id, str) or not USER_ID.fullmatch(file_id):
                    raise RequestError(502, 'Dify 未返回有效的文件标识。')
                return self.json_response({'id': file_id})
        except HTTPError as exc:
            with exc:
                code = exc.code
            labels = {'check': '检查连接', 'upload': '上传图纸', 'run': '启动识图'}
            advice = {
                401: '请核对云端配置的 Dify 应用密钥。',
                403: '请核对内网 API 入口和访问权限。',
                404: '请核对内网 API 地址及 /v1 路径。',
                413: '请核对 Dify 和网关的上传限制。',
            }
            message = f'Dify {labels[operation]}返回 HTTP {code}。' + advice.get(code, '')
            status = 502 if code in (401, 403, 404) or not 400 <= code < 600 else code
            self.json_response({'error': message}, status)
        except RequestError as exc:
            self.json_response({'error': str(exc)}, exc.status)


def main():
    config = read_settings(Path(__file__).resolve().parent, {
        'DIFY_API_BASE', 'DIFY_APP_API_KEY', 'GATEWAY_TOKEN', 'BIND_HOST', 'PORT',
    })
    try:
        base = endpoint(config['DIFY_API_BASE'], 'DIFY_API_BASE', '/v1', allow_http=True)
        token = relay_token(config['GATEWAY_TOKEN'])
        key = config['DIFY_APP_API_KEY']
        if not key or not key.isascii() or any(character.isspace() for character in key):
            raise ValueError('请填写有效的 DIFY_APP_API_KEY。')
        port = int(config['PORT'] or '8791')
        if not 1 <= port <= 65535:
            raise ValueError('PORT 必须在 1 至 65535 之间。')
        host = config['BIND_HOST'] or '127.0.0.1'
        server = ThreadingHTTPServer((host, port), Gateway)
    except (ValueError, OSError) as exc:
        raise SystemExit(f'调用服务启动失败。{exc}') from None
    server.dify_base, server.dify_key, server.token = base, key, token
    print(f'Dify 调用服务已启动，监听 {server.server_address[0]}:{port}。', flush=True)
    with server:
        server.serve_forever()


if __name__ == '__main__':
    main()
