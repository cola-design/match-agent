"""Offline protocol and evidence tests. All model replies are controlled fixtures."""
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest

import fitz
from openpyxl import load_workbook
import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'plugin'))
from library.reader import analyze, dumps, merge, prepare, report, workbook_bytes
import workflow_nodes as nodes


def fixture(pages=1, rotate=0):
    doc = fitz.open()
    for i in range(pages):
        p = doc.new_page(width=600, height=450)
        p.insert_text((40, 60), 'MCB-C16A/1P', fontsize=8)
        p.insert_text((40, 90), 'RCBO/C16A/1P+N 30mA', fontsize=8)
        p.insert_text((40, 130), 'CUSTOM-01', fontsize=8)
        p.draw_rect(fitz.Rect(40, 135, 120, 170))
        p.set_rotation(rotate)
    data = doc.tobytes(); doc.close(); return data


def controlled_records(manifest, valid=True):
    records = []
    for u in manifest['units']:
        ctx = dumps(u['context'])
        reply = dumps({'readable': True, 'checks': [], 'unmapped_symbols': []}) if valid else '{broken'
        records.append(json.loads(nodes.validate_reply(reply, ctx)['packet_json']))
    return records


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = fixture()
        cls.native = analyze(cls.data, 'fixture.pdf')
        cls.manifest, cls.images = prepare(cls.data, 'fixture.pdf', native_data=cls.native)
        cls.records = controlled_records(cls.manifest)

    def test_full_page_always_first_and_zoom_keeps_source_ids(self):
        self.assertEqual(self.manifest['units'][0]['view'], 'full')
        self.assertGreater(len(self.images), 1)
        all_ids = {a['id'] for a in self.native['pages'][0]['anchors']}
        for u in self.manifest['units']:
            self.assertTrue(set(u['context']['target_anchor_ids']) <= all_ids)

    def test_native_parameters_preserved_without_model(self):
        r = merge(self.native, self.manifest, self.records)
        o = next(o for o in r['objects'] if o['category'] == 'RCBO')
        self.assertEqual(o['explicit_parameters']['rated_current_a'], 16)
        self.assertEqual(o['explicit_parameters']['residual_current_ma'], 30)
        self.assertEqual(o['explicit_parameters']['pole_marking'], '1P+N')

    def test_overlapping_crops_do_not_duplicate_objects(self):
        r = merge(self.native, self.manifest, self.records)
        self.assertEqual(len(r['objects']), 3)
        self.assertEqual(len({o['id'] for o in r['objects']}), 3)
        self.assertTrue(all(o['candidate_quantity'] is None for o in r['objects']))

    def test_two_pages_same_model_keeps_distinct_evidence(self):
        data = fixture(2); n = analyze(data, 'two.pdf'); m, _ = prepare(data, 'two.pdf', False, n)
        r = merge(n, m, controlled_records(m))
        self.assertEqual(len(r['objects']), 6)
        self.assertEqual(len({o['id'] for o in r['objects']}), 6)

    def test_rotated_page_coordinates_stay_within_display_page(self):
        data = fixture(rotate=270); n = analyze(data, 'rotated.pdf'); m, imgs = prepare(data, 'rotated.pdf', native_data=n)
        bounds = fitz.Rect(n['pages'][0]['size'])
        self.assertEqual(bounds.width, 450)
        self.assertTrue(all(bounds.contains(fitz.Rect(a['bbox_page'])) for a in n['pages'][0]['anchors']))
        self.assertEqual(len(m['units']), len(imgs))

    def test_bad_model_json_retains_all_native_candidates(self):
        r = merge(self.native, self.manifest, controlled_records(self.manifest, False))
        self.assertEqual(len(r['objects']), 3)
        self.assertEqual(r['summary']['visual_response_failures'], len(self.manifest['units']))
        self.assertFalse(r['summary']['recognition_accuracy_verified'])

    def test_foreign_pdf_results_rejected(self):
        records = deepcopy(self.records); records[0]['source_id'] = 'foreign'
        with self.assertRaisesRegex(ValueError, '来源不一致'): merge(self.native, self.manifest, records)

    def test_missing_or_duplicate_image_results_rejected(self):
        with self.assertRaises(ValueError): merge(self.native, self.manifest, self.records[:-1])
        with self.assertRaises(ValueError): merge(self.native, self.manifest, self.records + self.records[:1])

    def test_conflicting_visual_categories_do_not_choose_first(self):
        records = deepcopy(self.records)
        target = next(a['id'] for a in self.native['pages'][0]['anchors'] if a['kind'] == 'unclassified_model')
        seen = 0
        for r in records:
            unit = next(u for u in self.manifest['units'] if u['unit_id'] == r['unit_id'])
            if target in unit['context']['target_anchor_ids']:
                r['data']['checks'] = [{'anchor_id': target, 'category': 'SPD' if seen == 0 else 'controller',
                                      'symbol_visible': True, 'confidence': 'high'}]
                seen += 1
        self.assertGreaterEqual(seen, 2)
        r = merge(self.native, self.manifest, records)
        self.assertEqual(next(o['category'] for o in r['objects'] if o['id'] == target), 'unknown')
        self.assertEqual(len(r['classification_conflicts']), 1)

    def test_no_native_text_is_explicitly_reported(self):
        doc = fitz.open(); p = doc.new_page(); p.draw_rect(fitz.Rect(10, 10, 30, 30))
        data = doc.tobytes(); doc.close(); n = analyze(data, 'outline.pdf'); m, _ = prepare(data, 'outline.pdf', False, n)
        r = merge(n, m, controlled_records(m))
        self.assertIn('未包含OCR', report(r))
        self.assertFalse(r['summary']['complete_symbol_coverage_verified'])

    def test_visual_suggestion_cannot_rewrite_explicit_timer_caption(self):
        n = deepcopy(self.native)
        anchor = next(a for a in n['pages'][0]['anchors'] if a['kind'] == 'unclassified_model')
        anchor.update(kind='function_label', raw='时间控制器')
        records = deepcopy(self.records)
        for r in records:
            u = next(u for u in self.manifest['units'] if u['unit_id'] == r['unit_id'])
            if anchor['id'] in u['context']['target_anchor_ids']:
                r['data']['checks'] = [{'anchor_id': anchor['id'], 'category': 'contactor',
                                      'confidence': 'high', 'symbol_visible': True}]
        result = merge(n, self.manifest, records)
        self.assertEqual(next(o['category'] for o in result['objects'] if o['id'] == anchor['id']), 'timer')
        self.assertEqual(len(result['classification_conflicts']), 1)

    def test_page_limit_rejects_instead_of_truncating(self):
        with self.assertRaisesRegex(ValueError, '1至12页'): analyze(fixture(13), 'large.pdf')

    def test_encrypted_and_invalid_pdfs_rejected(self):
        with self.assertRaises(ValueError): analyze(b'not PDF', 'bad.pdf')
        doc = fitz.open(stream=self.data, filetype='pdf')
        data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='reader'); doc.close()
        with self.assertRaisesRegex(ValueError, '密码'): analyze(data, 'locked.pdf')

    def test_workbook_contains_full_evidence_and_strings_not_formulas(self):
        r = merge(self.native, self.manifest, self.records)
        r['objects'][0]['observed_label'] = '=1+1'
        wb = load_workbook(io.BytesIO(workbook_bytes(r)))
        self.assertEqual(wb['全部原文'].max_row, 4)
        self.assertEqual(wb['选型信息']['E2'].data_type, 's')
        self.assertEqual(wb['选型信息']['E2'].value, '=1+1')


class NodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest, _ = prepare(fixture(), 'fixture.pdf', False)
        cls.files = [{'filename': u['image_name']} for u in cls.manifest['units']]

    def test_upload_file_order_can_change_without_binding_wrong_image(self):
        files = list(reversed(self.files))
        b = nodes.bind_manifest([{'success': True, 'manifest': deepcopy(self.manifest)}], files, '')
        self.assertEqual(b['valid'], 'YES')
        ctx = json.loads(nodes.bind_image(0.0, b['manifest_compact'])['context_json'])
        self.assertEqual(ctx['unit_id'], self.manifest['units'][-1]['unit_id'])

    def test_wrong_image_filename_stops_before_model(self):
        b = nodes.bind_manifest([{'success': True, 'manifest': deepcopy(self.manifest)}], [{'filename': 'wrong.png'}], '')
        self.assertEqual(b['valid'], 'NO')

    def test_bad_json_foreign_ids_duplicate_keys_and_nan_rejected(self):
        ctx = dumps(self.manifest['units'][0]['context'])
        bad = ['{"readable":true,"checks":[', '{"readable":true,"readable":false,"checks":[]}',
               '{"readable":NaN,"checks":[]}',
               dumps({'readable': True, 'checks': [{'anchor_id': 'foreign', 'category': 'SPD', 'confidence': 'high'}]})]
        for raw in bad:
            with self.subTest(raw=raw): self.assertEqual(nodes.validate_reply(raw, ctx)['valid'], 'NO')

    def test_aggregate_rejects_missing_results(self):
        b = nodes.bind_manifest([{'success': True, 'manifest': deepcopy(self.manifest)}], self.files, '')
        with self.assertRaises(ValueError): nodes.collect_records([], b['manifest_compact'])

    def test_graph_has_one_pdf_input_and_files_never_enter_iteration_code(self):
        d = yaml.safe_load(next(ROOT.glob('*Dify导入.yml')).read_text())
        graph = d['workflow']['graph']; index = {n['id']: n['data'] for n in graph['nodes']}
        start = next(n for n in index.values() if n['type'] == 'start')
        files = [v for v in start['variables'] if v['type'] == 'file-list']
        self.assertEqual([v['variable'] for v in files], ['pdf_files'])
        for e in graph['edges']:
            self.assertIn(e['source'], index); self.assertIn(e['target'], index)
        for nd in index.values():
            if nd['type'] == 'code':
                compile(nd['code'], '<dify-node>', 'exec')
                self.assertFalse(any(index.get(v['value_selector'][0], {}).get('type') == 'iteration'
                                     and v['value_selector'][1] == 'item' for v in nd['variables']))
            if nd['type'] == 'llm': self.assertEqual(nd['vision']['configs']['variable_selector'][1], 'item')


class PrivateDrawingTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('ELEVATOR_SAMPLE_PATH'), 'private drawing is never bundled')
    def test_elevator_parameters_branches_and_native_evidence(self):
        p = Path(os.environ['ELEVATOR_SAMPLE_PATH']); data = p.read_bytes()
        n = analyze(data, 'private.pdf'); m, _ = prepare(data, 'private.pdf', False, n)
        r = merge(n, m, controlled_records(m)); objects = r['objects']
        self.assertEqual(len(r['source_evidence']), 95)
        self.assertEqual(len(r['branches']), 8)
        self.assertEqual(len(objects), len({o['id'] for o in objects}))
        self.assertEqual(next(o for o in objects if o['observed_label'] == '220/36V')['explicit_parameters']['apparent_power_va'], 1000)
        b = next(o for o in objects if o['observed_label'] == 'MCB-C32A/4P')
        self.assertEqual(b['circuit_tags'], ['8#']); self.assertEqual(b['branch_selection_requirements']['residual_current_ma'], 30)
        self.assertNotIn('residual_current_ma', b['explicit_parameters'])
        for label in ('KM', '时间控制器'):
            self.assertEqual(next(o for o in objects if o['observed_label'] == label)['circuit_tags'], ['6#'])
        self.assertEqual(sum(o['category'] == 'MCCB' for o in objects), 3)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    (ROOT / 'validation.json').write_text(json.dumps({
        'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped),
        'actual_model_calls': 0, 'scope': 'Offline native PDF, file protocol, graph structure and controlled-reply tests; not a deployed Dify workspace or recognition accuracy certification.'
    }, ensure_ascii=False, indent=2))
    raise SystemExit(not result.wasSuccessful())
