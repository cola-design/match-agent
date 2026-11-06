from copy import deepcopy
import json
from pathlib import Path
import unittest
import yaml
import nodes

ROOT = Path(__file__).resolve().parent


def binding(pages=1):
    meta = {'success': True, 'total_images': pages, 'total_pages': pages,
            'images': [{'filename': 'fixture_page_%d.png' % i, 'file_index': 1, 'page_number': i,
                        'width': 1400, 'height': 1800, 'dpi': 300} for i in range(1, pages + 1)]}
    images = [{'filename': i['filename']} for i in meta['images']]
    return meta, images


def context(page=1):
    return nodes.j({'page': page, 'image_name': 'fixture_page_%d.png' % page,
                    'whole_document_text': 'MCB-C32A/4P 30mA 220/36V 1000VA KM L1 ATS-63A-II'})


def reply():
    return {'readable': True, 'page_complete': True,
            'items': [{'id': 'I001', 'label': 'MCB-C32A/4P', 'category': 'MCB', 'circuit': '8#', 'phase': 'L1~L3', 'position': 'bottom branch'},
                      {'id': 'I002', 'label': '220/36V', 'category': 'transformer', 'circuit': '2#', 'phase': 'L1', 'position': 'transformer'},
                      {'id': 'I003', 'label': 'KM', 'category': 'contactor', 'circuit': '6#', 'phase': 'L2', 'position': 'contact'}],
            'markings': [{'raw': '30mA', 'target_id': 'I001'}, {'raw': '1000VA', 'target_id': 'I002'}], 'unresolved': []}


class PageProtocolTests(unittest.TestCase):
    def test_page_order_is_bound_by_filename(self):
        m, files = binding(2)
        b = nodes.bind_pages([m], list(reversed(files)), ['document'], '')
        self.assertEqual(b['valid'], 'YES')
        self.assertEqual(json.loads(nodes.page_context(0.0, b['manifest_compact'])['context_json'])['page'], 2)

    def test_missing_page_stops_before_model(self):
        m, f = binding(2); m['images'].pop()
        self.assertEqual(nodes.bind_pages([m], f, [''], '')['valid'], 'NO')

    def test_wrong_image_or_duplicate_page_rejected(self):
        m, f = binding(2); f[0]['filename'] = 'wrong.png'
        self.assertEqual(nodes.bind_pages([m], f, [''], '')['valid'], 'NO')
        m, f = binding(2); m['images'][1]['page_number'] = 1
        self.assertEqual(nodes.bind_pages([m], f, [''], '')['valid'], 'NO')

    def test_independent_parameters_and_km_preserved(self):
        out = nodes.checked(nodes.j(reply()), context()); self.assertEqual(out['valid'], 'YES')
        items = json.loads(out['packet_json'])['items']
        self.assertEqual(items[0]['branch_requirements']['residual_current_ma'], 30)
        self.assertNotIn('residual_current_ma', items[0]['literal_parameters'])
        self.assertEqual(items[1]['literal_parameters']['voltage_pair_v'], [220, 36])
        self.assertEqual(items[1]['associated_parameters']['apparent_power_va'], 1000)
        self.assertEqual(items[2]['category'], 'contactor')

    def test_complete_think_wrapper_can_be_separated_from_json(self):
        raw = '<think>private reasoning</think>\n' + nodes.j(reply())
        out = nodes.checked(raw, context()); self.assertEqual(out['valid'], 'YES')
        self.assertNotIn('private reasoning', out['packet_json'])

    def test_unfinished_think_or_json_is_not_repaired(self):
        for raw in ('<think>unfinished', '<think>done</think>', '{"readable":true,"items":['):
            self.assertEqual(nodes.checked(raw, context())['valid'], 'NO')

    def test_duplicate_json_keys_and_nonfinite_numbers_rejected(self):
        self.assertEqual(nodes.checked('{"readable":true,"readable":false,"page_complete":true,"items":[]}', context())['valid'], 'NO')
        self.assertEqual(nodes.checked('{"readable":NaN,"page_complete":true,"items":[]}', context())['valid'], 'NO')

    def test_phase_annotation_does_not_become_device(self):
        d = reply(); d['items'].append({'id': 'I004', 'label': 'L1', 'category': 'unknown', 'circuit': None, 'phase': None, 'position': None})
        out = json.loads(nodes.checked(nodes.j(d), context())['packet_json'])
        self.assertEqual(len(out['items']), 3); self.assertEqual(out['annotations'][0]['raw'], 'L1')

    def test_ats_suffix_not_converted_to_poles(self):
        d = reply(); d['items'] = [{'id': 'I001', 'label': 'ATS-63A-II', 'category': 'ATS', 'circuit': None, 'phase': None, 'position': 'incoming'}]; d['markings'] = []
        item = json.loads(nodes.checked(nodes.j(d), context())['packet_json'])['items'][0]
        self.assertNotIn('pole_marking', item['literal_parameters']); self.assertTrue(item['warnings'])

    def test_model_cannot_rewrite_rcb_as_rcbo(self):
        d = reply(); d['items'][0]['label'] = 'RCB/C16A/1P+N 30mA'; d['items'][0]['category'] = 'RCBO'
        item = json.loads(nodes.checked(nodes.j(d), context())['packet_json'])['items'][0]
        self.assertEqual(item['category'], 'residual_current_device_unspecified')
        self.assertEqual(item['literal_parameters']['rated_current_a'], 16)

    def test_cable_cross_section_and_route_codes(self):
        d = reply(); d['items'][0].update(label='WDZB-YJY-4×10+1×10-CT-SC40-CE WE', category='cable'); d['markings'] = []
        item = json.loads(nodes.checked(nodes.j(d), context())['packet_json'])['items'][0]
        self.assertEqual(item['literal_parameters']['conductor_groups'], [{'count': 4, 'area_mm2': 10}, {'count': 1, 'area_mm2': 10}])
        self.assertEqual([r['code'] for r in item['literal_parameters']['installation_codes']], ['CE', 'WE'])

    def test_missing_native_text_does_not_claim_text_support(self):
        c = json.loads(context()); c['whole_document_text'] = ''
        o = json.loads(nodes.checked(nodes.j(reply()), nodes.j(c))['packet_json'])
        self.assertTrue(all(i['text_support'] == 'image_transcription_only' for i in o['items']))

    def test_duplicate_local_ids_are_invalid(self):
        d = reply(); d['items'][1]['id'] = 'I001'
        self.assertEqual(nodes.checked(nodes.j(d), context())['valid'], 'NO')

    def test_failed_page_is_kept_with_false_accuracy_flag(self):
        m, f = binding(); b = nodes.bind_pages([m], f, ['text'], '')
        fail = nodes.checked('broken', context())['packet_json']
        result = nodes.collect([fail], b['manifest_compact']); r = json.loads(result['result_json'])
        self.assertEqual(r['summary']['failed_pages'], 1)
        self.assertFalse(r['summary']['recognition_accuracy_verified'])
        self.assertFalse(r['summary']['automatic_zoom_enabled'])

    def test_many_pages_export_all_details_without_truncating_to_chat(self):
        m, f = binding(12); b = nodes.bind_pages([m], f, ['text'], '')
        packets = []
        for page in range(1, 13):
            d = reply(); d['markings'] = []; d['items'] = [dict(reply()['items'][0], id='I%03d' % i, position='branch %d' % i) for i in range(1, 31)]
            packets.append(nodes.checked(nodes.j(d), context(page))['packet_json'])
        out = nodes.collect(packets, b['manifest_compact'])
        self.assertEqual(len(out['result_pages']), 12)
        self.assertEqual(sum(len(json.loads(x)['normalized_items']) for x in out['result_pages']), 360)
        self.assertIn('前50项', out['report'])
        self.assertEqual(json.loads(out['result_json'])['details_location'], 'result_pages')

    def test_workflow_has_only_existing_plugins_and_ollama_image_nodes(self):
        d = yaml.safe_load((ROOT/'PDF电气识图_V5_Ollama_TenGen-ZhiHan_F16.yml').read_text())
        deps = [x['value']['marketplace_plugin_unique_identifier'] for x in d['dependencies']]
        self.assertEqual(len(deps), 2); self.assertTrue(any('ollama' in x for x in deps)); self.assertTrue(any('pdf-to-images' in x for x in deps))
        self.assertNotIn('cola-design/electrical-pdf', nodes.j(d))
        index = {n['id']: n['data'] for n in d['workflow']['graph']['nodes']}
        for a in index.values():
            if a['type'] == 'llm':
                self.assertEqual(a['model']['name'], 'TenGen-ZhiHan:F16')
                self.assertEqual(a['model']['provider'], 'langgenius/ollama/ollama')
                self.assertIs(a['model']['completion_params']['think'], False)
                self.assertEqual(a['model']['completion_params']['format'], 'json')
                self.assertEqual(a['vision']['configs']['variable_selector'][1], 'item')
            if a['type'] == 'code':
                compile(a['code'], '<node>', 'exec')
                self.assertFalse(any(index.get(v['value_selector'][0], {}).get('type') == 'iteration' and v['value_selector'][1] == 'item' for v in a['variables']))


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(PageProtocolTests))
    (ROOT/'validation.json').write_text(json.dumps({'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'actual_ollama_calls': 0, 'scope': 'Offline code/graph and controlled model reply tests; not a live model recognition test.'}, indent=2))
    raise SystemExit(not result.wasSuccessful())
