from copy import deepcopy
import gzip
import json
import unittest

from case_helpers import FIXTURES, load_node


def uploaded_requirements():
    return json.loads(gzip.decompress((FIXTURES / 'uploaded_v18_2_requirements.json.gz').read_bytes()))


def snapshot_products():
    return json.loads((FIXTURES / 'scb_spd_snapshot.json').read_text())


class UploadedWorkbookRegressionTest(unittest.TestCase):
    def test_scb_alias_in_uploaded_rejection_does_not_hide_existing_product(self):
        req = next(row for row in uploaded_requirements() if row['symbol'] == 'SCB')
        candidates = [p for p in snapshot_products() if p['material_code'] == '338020700012']
        selector = load_node('2000000000024')
        for category in ('后备保护器', 'SPD后备保护器', '防雷后备保护器', 'scb'):
            req['category_path'] = category
            row = json.loads(selector['main']('', json.dumps(req), candidates)['item_json'])
            self.assertEqual(row['match_status'], 'MATCHED', row)
            self.assertEqual(row['recommended']['material_code'], '338020700012')
        req['category_path'] = '电涌保护器'
        row = json.loads(selector['main']('', json.dumps(req), candidates)['item_json'])
        self.assertEqual(row['match_status'], 'NOT_RETRIEVED')

    def test_spd_actual_3p_plus_n_candidates_are_removed_from_4p_requirement(self):
        req = next(row for row in uploaded_requirements() if row['symbol'] == 'SPD')
        selector = load_node('2000000000024')
        candidates = [p for p in snapshot_products() if p['material_code'] != '338020700012']
        row = json.loads(selector['main']('', json.dumps(req), candidates)['item_json'])
        self.assertEqual(row['match_status'], 'NEED_REVIEW')
        self.assertFalse(row['recommended'])
        self.assertEqual({r['material_code'] for r in row['alternatives']},
                         {'338020100237', '338020100238', '338020600106'})
        req['requirements']['pole_count'] = '3P+N'
        row = json.loads(selector['main']('', json.dumps(req), candidates)['item_json'])
        self.assertEqual({r['material_code'] for r in row['alternatives']},
                         {'338020100234', '338020100235'})

    def test_strict_poles_missing_conflicting_and_normalized(self):
        check = load_node('2000000000024')['v1821_pole_error']
        record = {'物料描述': 'MODEL 4P', '规格参数': '极数=4P'}
        self.assertEqual(check('４ Ｐ', record), '')
        self.assertEqual(check('4极', record), '')
        self.assertTrue(check('3P+N', record))
        self.assertTrue(check('4P', {'物料描述': 'MODEL', '规格参数': ''}))
        self.assertTrue(check('4P', {'物料描述': 'MODEL 3P+N', '规格参数': '极数=4P'}))

    def test_price_revalidation_blocks_typed_pole_mismatch_with_sparse_display(self):
        product = next(p for p in snapshot_products() if p['material_code'] == '338020100237')
        price = {'material_code': product['material_code'], 'unit_price_ex_tax': 10,
                 'tax_rate': 0.13, 'pricing_unit': '只', 'price_version': 'REGRESSION-ONLY',
                 'valid_from': '2020-01-01T00:00:00', 'valid_to': '2099-12-31T00:00:00',
                 'status': 'ACTIVE', 'category': '电涌保护器', 'price_basis': 'MARKET_REFERENCE_ESTIMATE'}
        item = {'material_code': product['material_code'], 'material_name': '电涌保护器',
                'model_spec': 'TGDY55II-40', 'unit': '只', 'quantity': 1,
                'key_technical_parameters': '', 'requirements_json': json.dumps({'pole_count': '3P+N'})}
        ns = load_node('3400000000005')
        for selected, primary in [([product], [product]), ([product], [])]:
            out = ns['main'](json.dumps(item), product['material_code'], selected, [price], primary, [price], 'HIT', 'HIT')
            row = json.loads(out['quote_item_json'])
            self.assertEqual(row['item_status'], 'TECH_SPEC_MISMATCH', row)
            self.assertIn('极数不一致', row['issue'])
        item['requirements_json'] = json.dumps({'pole_count': '4P'})
        row = json.loads(ns['main'](json.dumps(item), product['material_code'], [product], [price], [], [], 'HIT', 'HIT')['quote_item_json'])
        self.assertEqual(row['item_status'], 'PRICED', row)


if __name__ == '__main__':
    unittest.main()
