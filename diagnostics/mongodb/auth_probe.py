import json

def main(mongo_uri: str, db_name: str, driver_result: str) -> dict:
    if not json.loads(driver_result).get('ok'):
        return {'result': json.dumps({'ok': False, 'stage': 'skipped', 'reason': 'Unauthenticated driver probe did not pass.'})}
    client = None
    stage = 'client_setup'
    try:
        import pymongo
        client = pymongo.MongoClient(mongo_uri.strip(), connect=False, retryReads=False, retryWrites=False,
                                    serverSelectionTimeoutMS=1500, connectTimeoutMS=1500,
                                    socketTimeoutMS=1500, timeoutMS=2500)
        stage = 'authenticate_and_ping'
        print('MONGO_DIAG authenticate_and_ping_begin', flush=True)
        with pymongo.timeout(2.5):
            client[db_name.strip()].command('ping')
        print('MONGO_DIAG authenticate_and_ping_done', flush=True)
        stage = 'read_product'
        print('MONGO_DIAG read_product_begin', flush=True)
        with pymongo.timeout(2.5):
            product = client[db_name.strip()].products.find_one(
                {'data_status': 'ACTIVE', 'source_status': '有效'},
                {'_id': 0, 'material_code': 1}, max_time_ms=1000)
        stage = 'read_price'
        print('MONGO_DIAG read_price_begin', flush=True)
        with pymongo.timeout(2.5):
            price = client[db_name.strip()].prices.find_one(
                {'status': 'ACTIVE'}, {'_id': 0, 'material_code': 1}, max_time_ms=1000)
        data = {'ok': True, 'stage': 'completed', 'product': product or {}, 'price': price or {}}
    except Exception as exc:
        data = {'ok': False, 'stage': stage, 'error_type': type(exc).__name__}
        if isinstance(getattr(exc, 'code', None), int):
            data['error_code'] = exc.code
    print('MONGO_DIAG result_before_cleanup ' + json.dumps(data), flush=True)
    if client is not None:
        print('MONGO_DIAG client_close_begin', flush=True)
        try:
            client.close()
        except Exception as exc:
            data = {'ok': False, 'stage': 'client_close', 'error_type': type(exc).__name__, 'operation': data}
        print('MONGO_DIAG client_close_done', flush=True)
    return {'result': json.dumps(data, ensure_ascii=False)}
