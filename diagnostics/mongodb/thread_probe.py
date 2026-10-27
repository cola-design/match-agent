import json

def main(mongo_uri: str, db_name: str) -> dict:
    stage = 'import_threading'
    try:
        import threading
        flag = threading.Event()
        stage = 'thread_start'
        print('MONGO_DIAG thread_start', flush=True)
        worker = threading.Thread(target=flag.set, daemon=True)
        worker.start()
        stage = 'thread_wait'
        done = flag.wait(0.5)
        worker.join(timeout=0.5)
        data = {'ok': bool(done), 'stage': 'thread_completed' if done else stage,
                'worker_alive': worker.is_alive()}
    except Exception as exc:
        data = {'ok': False, 'stage': stage, 'error_type': type(exc).__name__}
    return {'result': json.dumps(data, ensure_ascii=False)}
