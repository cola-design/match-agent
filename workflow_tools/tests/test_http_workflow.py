"""Offline contracts: no calls to the user's MongoDB or Dify instance."""
import copy
import json
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DOC = yaml.safe_load((ROOT/'formal_selection_quote_v18_1_http_api.yml').read_text(encoding='utf8'))
BASE = yaml.safe_load((ROOT/'formal_selection_quote_v18_mongodb.yml').read_text(encoding='utf8'))
NODES = {node['id']: node for node in DOC['workflow']['graph']['nodes']}


def load_node(nid):
    namespace = {}
    exec(NODES[nid]['data']['code'], namespace)
    return namespace


PRODUCT = {
    'material_code': '00123456', 'description': 'CJX2-3210 32A AC220V',
    'category': {'middle': '接触器', 'leaf': 'CJX2'},
    'specification_text': '额定电流=32A；控制电压=AC220V', 'standard_specs': {},
    'source_status': '有效', 'data_status': 'ACTIVE', 'quality_flags': []}
PRICE = {
    'material_code': '00123456', 'unit_price_ex_tax': 100, 'pricing_unit': '件',
    'tax_rate': 0.13, 'price_version': 'UNIT-TEST', 'valid_from': '2020-01-01',
    'valid_to': '2099-12-31', 'status': 'ACTIVE', 'price_basis': 'UNIT_TEST_FIXTURE', 'category': '接触器'}
ITEM = {'material_code': '00123456', 'material_name': '接触器',
        'model_spec': 'CJX2-3210', 'unit': '件', 'quantity': 2,
        'requirements_json': '{}', 'key_technical_parameters': ''}

class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.normalize = load_node('3400000000003')['main']
        self.quote = load_node('3400000000005')['main']

    def price(self, products, prices):
        result = self.quote(json.dumps(ITEM), ITEM['material_code'], products, prices,
                            products, prices, 'HIT', 'HIT')
        return json.loads(result['quote_item_json'])

    def test_http_response_normalization_and_failure(self):
        self.assertEqual(self.normalize(json.dumps([PRODUCT]),200)['result'][0]['material_code'],'00123456')
        self.assertEqual(self.normalize('[]',200), {'result': []})
        for body,code in [('[]',401), ('[]',503), ('not-json',200), ('{}',200), ('[{}]',200), ('[{"material_code":123456}]',200)]:
            with self.subTest(body=body, code=code):
                with self.assertRaises(ValueError): self.normalize(body,code)

    def test_price_changes_are_consumed_on_next_request(self):
        first = self.price([PRODUCT], [PRICE])
        second = self.price([PRODUCT], [{**PRICE, 'unit_price_ex_tax': 135}])
        self.assertEqual(first['item_status'],'PRICED')
        self.assertEqual(second['item_status'],'PRICED')
        self.assertEqual(first['unit_price_ex_tax'],100)
        self.assertEqual(second['unit_price_ex_tax'],135)
        self.assertEqual(second['price_source'],'PRICE_API')

    def test_ambiguous_and_missing_prices_block(self):
        overlap = self.price([PRODUCT], [PRICE, {**PRICE, 'price_version':'UNIT-TEST-2','unit_price_ex_tax':135}])
        self.assertEqual(overlap['item_status'],'PRICE_CONFLICT')
        missing = self.price([PRODUCT], [])
        self.assertEqual(missing['item_status'],'PRICE_NOT_RETRIEVED')
        invalid = self.price([PRODUCT], [{**PRICE,'unit_price_ex_tax':-1}])
        self.assertEqual(invalid['item_status'],'PRICE_INVALID')

    def test_inactive_product_rejected(self):
        result = self.price([{**PRODUCT,'data_status':'PENDING_REVIEW'}], [PRICE])
        self.assertEqual(result['item_status'],'PRODUCT_NOT_RETRIEVED')

    def test_templates_and_human_confirmation_preserved(self):
        base_nodes={n['id']:n for n in BASE['workflow']['graph']['nodes']}
        for nid,node in base_nodes.items():
            if node['data']['type']=='tool' or nid in ('3300000000014','3500000000001'):
                self.assertEqual(node,NODES[nid])
        http=[n for n in NODES.values() if n['data']['type']=='http-request']
        self.assertEqual(len(http),5)
        self.assertFalse(any(n['data']['type']=='knowledge-retrieval' for n in NODES.values()))
        for n in http:
            self.assertIn('{{#env.MONGODB_API_KEY#}}',n['data']['headers'])
        self.assertTrue(all(v['value']=='' for v in DOC['workflow']['environment_variables']))

    def test_graph_selectors_exist_and_edges_match_node_types(self):
        for edge in DOC['workflow']['graph']['edges']:
            self.assertIn(edge['source'], NODES)
            self.assertIn(edge['target'], NODES)
            self.assertEqual(edge['data']['sourceType'], NODES[edge['source']]['data']['type'])
            self.assertEqual(edge['data']['targetType'], NODES[edge['target']]['data']['type'])
        for node in NODES.values():
            if node['data']['type']=='code':
                for var in node['data'].get('variables',[]):
                    selector=var.get('value_selector') or []
                    if selector and selector[0] not in ('env','sys'):
                        self.assertIn(selector[0],NODES)
                        src=NODES[selector[0]]
                        if src['data']['type']=='code':
                            self.assertIn(selector[1],src['data']['outputs'])

if __name__=='__main__': unittest.main()
