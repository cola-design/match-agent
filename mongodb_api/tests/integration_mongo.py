"""Run ONLY in an isolated disposable MongoDB test container (see run_docker_check.py)."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).parents[1]))
from pymongo import MongoClient
from repair_products import apply_plan, build_plan, delta


def main():
    assert os.environ.get('MATCH_AGENT_DISPOSABLE_TEST') == '1'
    assert os.environ['MONGODB_URI'] == 'mongodb://127.0.0.1:27017'
    assert os.environ['MONGODB_DB'] == 'match_agent_disposable_test'
    client = MongoClient(os.environ['MONGODB_URI'], serverSelectionTimeoutMS=1000)
    for attempt in range(30):
        try:
            client.admin.command('ping')
            break
        except Exception:
            if attempt == 29:
                raise
            time.sleep(.3)
    db = client[os.environ['MONGODB_DB']]
    assert db.products.count_documents({}) == 0
    fixtures = json.loads((Path(__file__).parent/'fixtures/core_products.json').read_text())
    fields = ('category', 'standard_specs', 'source_attributes', 'quality_flags', 'provenance')
    legacy = []
    for doc in fixtures:
        doc = copy.deepcopy(doc)
        for field in fields:
            doc[field] = json.dumps(doc[field], ensure_ascii=False)
        doc['item_id'] = int(doc['item_id'])
        legacy.append(doc)
    # Unrelated products intentionally precede all matching models in natural record order.
    distractors = []
    for i in range(45):
        doc = copy.deepcopy(legacy[0])
        doc.update(material_code=f'000{i:09d}', description='UNRELATED 4P C100 100A 10kA')
        distractors.append(doc)
    db.products.insert_many(distractors + legacy)
    db.prices.insert_one({'material_code': legacy[0]['material_code'], 'status':'ACTIVE',
                         'valid_from':'2026-01-01', 'valid_to':'2026-12-31',
                         'unit_price_ex_tax':123, 'price_basis':'MARKET_REFERENCE_ESTIMATE'})
    original = list(db.products.find({}).sort('material_code'))
    original_prices = list(db.prices.find({}))
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--app-dir',
                                str(Path(__file__).parents[1]), '--host','127.0.0.1', '--port','8000',
                                '--log-level','warning'])
    base = 'http://127.0.0.1:8000'
    def get(path, params=None):
        url = base + path + ('?' + urllib.parse.urlencode(params) if params else '')
        request = urllib.request.Request(url, headers={'X-API-Key':os.environ['MONGODB_API_KEY']})
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
            return json.load(response)
    try:
        for attempt in range(50):
            try:
                get('/health')
                break
            except OSError:
                if attempt == 49: raise
                time.sleep(.1)
        searches = [('TGB1N-125H','小型断路器','335012800535',3),
                    ('TGH1N-125','隔离开关','336010500031',1),
                    ('TGSCB1-40II','SPD后备保护器','338020700012',1),
                    ('TGDY55II-40','电涌保护器','338020100237',3)]
        for model, category, code, count in searches:
            for explicit in (False, True):
                params={'q':model+' 4P 100A', 'limit':30}
                if explicit:params.update(model=model,category=category)
                rows = get('/v1/products/search', params)
                assert code in [row['material_code'] for row in rows], (params, rows)
                assert len(rows) == count, (model,len(rows))
                assert all(model in row['description'] for row in rows)
                assert all(isinstance(row['category'],dict) for row in rows)
        # Specs rank ahead of deterministic code order, BEFORE limiting the result set.
        rows = get('/v1/products/search', {'q':'TGB1N-125H MX 4P', 'limit':1})
        assert rows[0]['material_code'] == '335012800537'
        exact = get('/v1/products/335012800535')[0]
        assert isinstance(exact['standard_specs'],dict)
        assert isinstance(exact['source_attributes'],list)
        assert isinstance(exact['quality_flags'],list)
        assert isinstance(exact['item_id'],str)
        assert get('/v1/products/search', {'q':'NO-SUCH-MODEL99 4P 100A'}) == []
        assert len(get('/v1/prices/335012800535',{'as_of':'2026-10-28'})) == 1
        with tempfile.TemporaryDirectory() as folder:
            plan, report = build_plan(db.products,Path(folder))
            assert report['stats']['invalid_documents'] == 0
            assert report['stats']['documents_to_change'] == len(original)
            assert list(db.products.find({}).sort('material_code')) == original
            assert apply_plan(db.products,plan) == len(original)
            assert all(not delta(doc) for doc in db.products.find({}))
            assert db.products.count_documents({'data_status':'ACTIVE','source_status':'有效'}) == len(original)
            assert list(db.prices.find({})) == original_prices
            assert isinstance(get('/v1/products/335012800535')[0]['category'],dict)
            assert apply_plan(db.products,plan,rollback=True) == len(original)
            assert list(db.products.find({}).sort('material_code')) == original
            assert list(db.prices.find({})) == original_prices
            assert apply_plan(db.products,plan,rollback=True) == 0
        print(json.dumps({'ok':True,'mongo_version':client.server_info()['version'],
                          'fixture_products':len(original),'core_search_cases':8,
                          'ranking_before_limit':True,'preview_no_writes':True,
                          'apply_and_rollback':True,'prices_unchanged':True}))
    finally:
        process.terminate()
        process.wait(timeout=10)
        client.close()


if __name__ == '__main__':
    main()
