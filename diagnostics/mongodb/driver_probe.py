import json

def main(mongo_uri: str, db_name: str, thread_result: str, hello_result: str) -> dict:
    if not json.loads(thread_result).get('ok') or not json.loads(hello_result).get('ok'):
        return {'result': json.dumps({'ok': False, 'stage': 'skipped', 'reason': 'Check thread and hello results first.'})}
    stage = 'import_driver'
    client = None
    try:
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        import pymongo
        parsed = urlsplit(mongo_uri.strip())
        # Remove credentials and auth-specific options; preserve topology/TLS settings.
        options = [(key, value) for key, value in parse_qsl(parsed.query)
                   if key.lower() not in ('authsource', 'authmechanism', 'authmechanismproperties')]
        plain_uri = urlunsplit((parsed.scheme, parsed.netloc.rsplit('@', 1)[-1], parsed.path,
                              urlencode(options), ''))
        stage = 'driver_client_setup'
        print('MONGO_DIAG driver_client_setup', flush=True)
        client = pymongo.MongoClient(plain_uri, connect=False, retryReads=False, retryWrites=False,
                                    serverSelectionTimeoutMS=1500, connectTimeoutMS=1500,
                                    socketTimeoutMS=1500, timeoutMS=2500)
        stage = 'driver_hello_without_credentials'
        print('MONGO_DIAG driver_hello_without_credentials_begin', flush=True)
        with pymongo.timeout(2.5):
            doc = client.admin.command('hello')
        print('MONGO_DIAG driver_hello_without_credentials_done', flush=True)
        data = {'ok': doc.get('ok') == 1, 'stage': 'driver_hello_without_credentials'}
    except Exception as exc:
        data = {'ok': False, 'stage': stage, 'error_type': type(exc).__name__}
        if isinstance(getattr(exc, 'code', None), int):
            data['error_code'] = exc.code
        print('MONGO_DIAG error_before_cleanup ' + json.dumps(data), flush=True)
    # Print the safe result BEFORE close so cleanup cannot hide the preceding error in stdout.
    print('MONGO_DIAG result_before_cleanup ' + json.dumps(data), flush=True)
    if client is not None:
        print('MONGO_DIAG client_close_begin', flush=True)
        try:
            client.close()
        except Exception as exc:
            data = {'ok': False, 'stage': 'client_close', 'error_type': type(exc).__name__, 'operation': data}
        print('MONGO_DIAG client_close_done', flush=True)
    return {'result': json.dumps(data, ensure_ascii=False)}
