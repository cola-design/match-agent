"""API contract tests. No live MongoDB or user credentials are used."""
import importlib.util
import os
from datetime import datetime
from pathlib import Path
import unittest
from unittest.mock import patch
import sys
import json
sys.path.insert(0, str(Path(__file__).parents[1]))

from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

API_KEY = 'unit-test-api-key'

class Cursor:
    def __init__(self, rows):
        self.rows = rows
    def limit(self, count):
        self.rows = self.rows[:count]
        return self
    def max_time_ms(self, value):
        return self
    def __iter__(self):
        return iter(self.rows)

class Collection:
    def __init__(self):
        self.rows = []
        self.query = None
    def find(self, query, projection):
        self.query = query
        return Cursor(self.rows)
    def aggregate(self, pipeline, **kwargs):
        self.pipeline = pipeline
        self.query = pipeline[0]['$match']
        return Cursor(self.rows)

class Database:
    def __init__(self):
        self.collections = {'products': Collection(), 'prices': Collection()}
        self.fail = False
    def __getitem__(self, name):
        return self.collections[name]
    def command(self, name):
        if self.fail:
            raise ServerSelectionTimeoutError('do not expose connection details')
        return {'ok': 1}

class Mongo:
    def __init__(self, database):
        self.database = database
    def __getitem__(self, name):
        return self.database


def load_api(key=API_KEY):
    database = Database()
    spec = importlib.util.spec_from_file_location('mongo_api_under_test', Path(__file__).parents[1] / 'app.py')
    module = importlib.util.module_from_spec(spec)
    env = {'MONGODB_URI': 'mongodb://unused-unit-test-host:27017', 'MONGODB_DB': 'match_agent', 'MONGODB_API_KEY': key}
    with patch.dict(os.environ, env), patch('pymongo.MongoClient', return_value=Mongo(database)):
        spec.loader.exec_module(module)
    return module, database

class APITest(unittest.TestCase):
    def setUp(self):
        self.module, self.db = load_api()
        self.client = TestClient(self.module.app)
        self.headers = {'X-API-Key': API_KEY}

    def test_missing_key_fails_startup(self):
        with self.assertRaises(RuntimeError):
            load_api('')

    def test_auth_and_live(self):
        self.assertEqual(self.client.get('/live').json(), {'status': 'up'})
        self.assertEqual(self.client.get('/health').status_code, 401)
        self.assertEqual(self.client.get('/health', headers={'X-API-Key': 'wrong'}).status_code, 401)
        self.assertEqual(self.client.get('/health', headers=self.headers).status_code, 200)

    def test_static_search_is_not_a_material_code(self):
        self.db.collections['products'].rows = [{'material_code':'001','description':'TGM1 160A','category':{'middle':'塑壳断路器'},'standard_specs':{},'source_attributes':[],'quality_flags':[],'_id':'hidden'}]
        response = self.client.get('/v1/products/search', params={'q': 'TGM1 160A'}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]['material_code'], '001')
        self.assertNotIn('_id',response.json()[0])
        query = self.db.collections['products'].query
        self.assertNotIn('material_code', query)
        self.assertEqual(query['data_status'], 'ACTIVE')
        self.assertEqual(query['source_status'], '有效')
        model_filter = query['$and'][0]['description']['$regex']
        self.assertIn('TGM1', model_filter)
        pipeline = self.db.collections['products'].pipeline
        self.assertIn('$sort', pipeline[2])
        self.assertIn('$limit', pipeline[3])

    def test_exact_code_preserves_leading_zero(self):
        self.client.get('/v1/products/00123456', headers=self.headers)
        self.assertEqual(self.db.collections['products'].query['material_code'], '00123456')

    def test_price_dates_and_overlaps(self):
        self.db.collections['prices'].rows = [
            {'status': 'ACTIVE', 'valid_from': '2026-01-01', 'valid_to': '2026-12-31', 'price_version': 'A'},
            {'status': 'ACTIVE', 'valid_from': datetime(2026, 1, 1), 'valid_to': datetime(2026, 12, 31), 'price_version': 'B'},
            {'status': 'ACTIVE', 'valid_from': '2025-01-01', 'valid_to': '2025-12-31'},
            {'status': 'PENDING', 'valid_from': '2026-01-01', 'valid_to': '2026-12-31'},
        ]
        result = self.client.get('/v1/prices/001?as_of=2026-10-27', headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual([r['price_version'] for r in result.json()], ['A', 'B'])
        self.assertEqual(self.client.get('/v1/prices/001?as_of=not-a-date', headers=self.headers).status_code, 422)

    def test_navicat_serialized_fields_normalized_without_writing(self):
        record = {'material_code':'00123456','description':'TGB1N-125H 4P C100',
                  'category':json.dumps({'middle':'小型断路器'}),
                  'standard_specs':json.dumps({'voltage':{'value_text':'AC380V','value_num':80,'unit':'V'}}),
                  'source_attributes':'[]','quality_flags':'[]', 'provenance':'{}', 'item_id':123,
                  'data_status':'ACTIVE','source_status':'有效'}
        self.db.collections['products'].rows = [record]
        response=self.client.get('/v1/products/00123456',headers=self.headers)
        self.assertEqual(response.status_code,200)
        output=response.json()[0]
        self.assertIsInstance(output['category'],dict)
        self.assertIsInstance(output['source_attributes'],list)
        self.assertEqual(output['standard_specs']['voltage']['value_num'],380)
        self.assertEqual(output['item_id'],'123')
        self.assertIsInstance(record['category'],str)

    def test_invalid_nested_json_stops_instead_of_inventing_empty_specs(self):
        record={'material_code':'00123456','category':'not-json','standard_specs':'{}','source_attributes':'[]','quality_flags':'[]'}
        self.db.collections['products'].rows=[record]
        response=self.client.get('/v1/products/00123456',headers=self.headers)
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()['field'],'category')

    def test_default_date_works_without_os_timezone_database(self):
        self.db.collections['prices'].rows=[]
        self.assertEqual(self.client.get('/v1/prices/001',headers=self.headers).status_code,200)

    def test_excessive_price_records_block(self):
        self.db.collections['prices'].rows = [{} for _ in range(101)]
        self.assertEqual(self.client.get('/v1/prices/001', headers=self.headers).status_code, 409)

    def test_db_failure_is_sanitized(self):
        self.db.fail = True
        response = self.client.get('/health', headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('connection details', response.text)

if __name__ == '__main__':
    unittest.main()
