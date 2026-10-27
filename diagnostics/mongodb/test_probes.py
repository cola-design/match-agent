"""Run with pymongo installed: python -m unittest discover -s diagnostics/mongodb."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import struct
import unittest
from unittest.mock import patch

from bson import BSON
import pymongo

ROOT = Path(__file__).parent
URI = 'mongodb://readonly:unit-test-secret@127.0.0.1:27017/?authSource=admin'

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class FakeSocket:
    def __init__(self, reply, fail=False):
        self.reply = reply
        self.sent = b''
        self.closed = False
        self.fail = fail
    def settimeout(self, value):
        assert value > 0
    def connect(self, address):
        assert address == ('127.0.0.1', 27017)
    def sendall(self, packet):
        self.sent = packet
    def recv(self, length):
        if self.fail:
            raise TimeoutError()
        # Also exercise fragmented reads.
        size = min(length, 7)
        chunk, self.reply = self.reply[:size], self.reply[size:]
        return chunk
    def close(self):
        self.closed = True

class Client:
    def __init__(self, fail=False, close_fail=False):
        self.fail = fail
        self.close_fail = close_fail
        self.closed = False
    @property
    def admin(self):
        return self
    def command(self, name):
        if self.fail:
            raise RuntimeError('unit-test-secret must not appear in logs')
        return {'ok': 1}
    def close(self):
        self.closed = True
        if self.close_fail:
            raise RuntimeError('unit-test-secret must not appear in logs')

class ProbesTest(unittest.TestCase):
    def test_thread_probe(self):
        data = json.loads(load('thread_probe').main(URI, 'match_agent')['result'])
        self.assertTrue(data['ok'])
        self.assertFalse(data['worker_alive'])

    def test_raw_hello_fragmentation_no_credentials(self):
        doc = BSON.encode({'ok': 1.0, 'isWritablePrimary': True, 'maxWireVersion': 17})
        body = struct.pack('<I', 0) + b'\x00' + doc
        response = struct.pack('<iiii', 16 + len(body), 99, 17027, 2013) + body
        sock = FakeSocket(response)
        with patch.object(socket, 'socket', return_value=sock):
            data = json.loads(load('hello_probe').main(URI, 'match_agent')['result'])
        self.assertTrue(data['ok'])
        self.assertEqual(data['stage'], 'hello_response')
        self.assertNotIn(b'unit-test-secret', sock.sent)
        self.assertNotIn(b'readonly', sock.sent)
        self.assertTrue(sock.closed)
        self.assertEqual(BSON(sock.sent[21:]).decode(), {'hello': 1, '$db': 'admin'})

    def test_raw_hello_timeout_and_tls_skip(self):
        sock = FakeSocket(b'', fail=True)
        with patch.object(socket, 'socket', return_value=sock):
            result = json.loads(load('hello_probe').main(URI, 'match_agent')['result'])
        self.assertEqual(result['stage'], 'receive_hello_header')
        self.assertEqual(result['error_type'], 'TimeoutError')
        self.assertTrue(sock.closed)
        with patch.object(socket, 'socket') as factory:
            result = json.loads(load('hello_probe').main(URI + '&tls=true', 'match_agent')['result'])
        self.assertEqual(result['stage'], 'tls_required')
        factory.assert_not_called()

    def driver_call(self, client):
        log = io.StringIO()
        with patch.object(pymongo, 'MongoClient', return_value=client) as factory, contextlib.redirect_stdout(log):
            result = json.loads(load('driver_probe').main(URI, 'match_agent', '{"ok":true}', '{"ok":true}')['result'])
        actual_uri = factory.call_args.args[0]
        self.assertNotIn('unit-test-secret', actual_uri)
        self.assertNotIn('readonly', actual_uri)
        self.assertNotIn('authSource', actual_uri)
        self.assertNotIn('unit-test-secret', log.getvalue())
        return result, log.getvalue()

    def test_driver_no_auth_and_cleanup_order(self):
        result, log = self.driver_call(Client())
        self.assertTrue(result['ok'])
        self.assertLess(log.index('result_before_cleanup'), log.index('client_close_begin'))
        result, log = self.driver_call(Client(fail=True))
        self.assertEqual(result['stage'], 'driver_hello_without_credentials')
        self.assertEqual(result['error_type'], 'RuntimeError')
        self.assertIn('error_before_cleanup', log)
        result, log = self.driver_call(Client(close_fail=True))
        self.assertEqual(result['stage'], 'client_close')
        self.assertTrue(result['operation']['ok'])

    def test_auth_probe_skip_and_error_no_secret(self):
        module = load('auth_probe')
        with patch.object(pymongo, 'MongoClient') as factory:
            result = json.loads(module.main(URI, 'match_agent', '{"ok":false}')['result'])
        factory.assert_not_called()
        self.assertEqual(result['stage'], 'skipped')
        log = io.StringIO()
        with patch.object(pymongo, 'MongoClient', return_value=Client(fail=True)), contextlib.redirect_stdout(log):
            result = json.loads(module.main(URI, 'match_agent', '{"ok":true}')['result'])
        self.assertEqual(result['stage'], 'authenticate_and_ping')
        self.assertNotIn('unit-test-secret', log.getvalue())
        self.assertNotIn('unit-test-secret', json.dumps(result))

if __name__ == '__main__':
    unittest.main()
