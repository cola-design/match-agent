import json

def main(mongo_uri: str, db_name: str) -> dict:
    stage = 'parse_uri'
    conn = None
    try:
        import socket
        import struct
        import time
        import ipaddress
        from urllib.parse import urlsplit, parse_qs
        from bson import BSON
        uri = urlsplit(mongo_uri.strip())
        if uri.scheme != 'mongodb':
            raise ValueError('Only mongodb scheme supported by this probe')
        host = str(ipaddress.IPv4Address(uri.hostname))
        options = parse_qs(uri.query.lower())
        if options.get('tls', options.get('ssl', ['false']))[-1] == 'true':
            return {'result': json.dumps({'ok': False, 'stage': 'tls_required',
                'reason': 'Raw hello probe skipped: use a TLS-capable probe. TLS was not disabled.'})}
        stage = 'socket_connect'
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.settimeout(2)
        conn.connect((host, uri.port or 27017))
        # OP_MSG hello is a standard unauthenticated handshake command (MongoDB >= 3.6).
        # This probe does not implement login and never sends the URI credentials.
        body = struct.pack('<I', 0) + b'\x00' + BSON.encode({'hello': 1, '$db': 'admin'})
        request_id = 17027
        packet = struct.pack('<iiii', 16 + len(body), request_id, 0, 2013) + body
        stage = 'send_hello'
        print('MONGO_DIAG send_hello', flush=True)
        conn.sendall(packet)
        deadline = time.monotonic() + 3
        def recv_exact(length):
            data = b''
            while len(data) < length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                conn.settimeout(min(2, remaining))
                chunk = conn.recv(length - len(data))
                if not chunk:
                    raise ConnectionError('EOF')
                data += chunk
            return data
        stage = 'receive_hello_header'
        length, response_id, response_to, opcode = struct.unpack('<iiii', recv_exact(16))
        if length < 16 or length > 1048576 or response_to != request_id:
            raise ValueError('Unexpected response framing')
        stage = 'receive_hello_body'
        payload = recv_exact(length - 16)
        stage = 'decode_hello'
        if opcode != 2013 or len(payload) < 10 or payload[4] != 0:
            raise ValueError('Unexpected protocol response')
        doc_length = struct.unpack_from('<i', payload, 5)[0]
        if doc_length < 5 or 5 + doc_length > len(payload):
            raise ValueError('Invalid BSON length')
        doc = BSON(payload[5:5 + doc_length]).decode()
        data = {'ok': doc.get('ok') == 1, 'stage': 'hello_response',
                'server_code': doc.get('code'),
                'replica_set': bool(doc.get('setName')),
                'is_primary': doc.get('isWritablePrimary', doc.get('ismaster')),
                'is_mongos': doc.get('msg') == 'isdbgrid',
                'member_count': len(doc.get('hosts') or []),
                'max_wire_version': doc.get('maxWireVersion')}
        print('MONGO_DIAG hello_response_received', flush=True)
    except Exception as exc:
        data = {'ok': False, 'stage': stage, 'error_type': type(exc).__name__,
                'errno': getattr(exc, 'errno', None)}
    finally:
        if conn is not None:
            conn.close()
    return {'result': json.dumps(data, ensure_ascii=False)}
