from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
import yaml
import nodes
from literal_decode import decode, explain

ROOT = Path(__file__).resolve().parent


def context(text='MCB-C16A/1P RCB/C16A/1P+N 30mA WDZB-BYJ-2x4-SC20-CC,WC 1# 2#', page=1):
    return {'page': page, 'page_count': 1, 'image_name': 'fixture_page_%d.png' % page,
            'whole_document_text': text, 'source_inventory': nodes.source_inventory(text), 'project_notes': ''}


def outline():
    return {'readable': True, 'view': 'single_line', 'drawings': [{'id': 'D1', 'title': '测试图', 'cabinet_tags': ['BOX-A']}],
            'circuits': [{'id': 'C1', 'drawing_id': 'D1', 'tag': '1#', 'phase': 'L1', 'load': '照明',
                          'device_labels': ['MCB-C16A/1P'], 'cable_labels': ['WDZB-BYJ-2x4-SC20-CC,WC']},
                         {'id': 'C2', 'drawing_id': 'D1', 'tag': '2#', 'phase': 'L2', 'load': '插座',
                          'device_labels': ['RCB/C16A/1P+N 30mA'], 'cable_labels': []}], 'unresolved': []}


def extraction():
    return {'readable': True, 'page_complete': True, 'drawings': outline()['drawings'],
            'items': [{'id': 'I1', 'drawing_id': 'D1', 'raw': 'MCB-C16A/1P', 'category': 'MCB', 'position': '上部'},
                      {'id': 'I2', 'drawing_id': 'D1', 'raw': 'RCB/C16A/1P+N 30mA', 'category': 'residual_current_device_unspecified', 'position': '下部'},
                      {'id': 'I3', 'drawing_id': 'D1', 'raw': 'WDZB-BYJ-2x4-SC20-CC,WC', 'category': 'cable', 'position': '上部出线'}],
            'circuits': [{'id': 'C1', 'drawing_id': 'D1', 'tag': '1#', 'phase': 'L1', 'load': '照明', 'members': ['I1', 'I3']},
                         {'id': 'C2', 'drawing_id': 'D1', 'tag': '2#', 'phase': 'L2', 'load': '插座', 'members': ['I2']}],
            'markings': [], 'unresolved': []}


def checked(value=None, ctx=None, plan=None):
    ctx = ctx or context()
    p = nodes.parse_outline(nodes.j(plan or outline()), nodes.j(ctx))['outline_json']
    ec = nodes.extraction_context(nodes.j(ctx), p)['extraction_json']
    return nodes.checked(nodes.j(value or extraction()), ec)


class MeaningTests(unittest.TestCase):
    def test_mcb_fields_are_chinese_and_explicit(self):
        d = decode('MCB-C16A/1P'); e = explain('MCB-C16A/1P', 'MCB', d['explicit_parameters'])
        self.assertIn('额定电流16A', e['meaning_cn']); self.assertIn('1极', e['meaning_cn'])
        self.assertEqual(e['selection_fields_cn']['额定电流_A'], 16)

    def test_byj_conductors_not_multicore_cable(self):
        raw = 'WDZB-BYJ-2×4-SC20-CC,WC'; p = decode(raw)['explicit_parameters']; e = explain(raw, 'cable', p)
        self.assertIn('2根、每根4', e['meaning_cn']); self.assertIn('布电线', e['meaning_cn'])
        self.assertNotIn('2芯', e['meaning_cn']); self.assertIn('顶板内暗敷', e['meaning_cn'])

    def test_yjy_conductor_groups_and_route_codes(self):
        raw = 'WDZB-YJY-4x10+1x10-CT-SC40-CE WE'; e = explain(raw, 'cable', decode(raw)['explicit_parameters'])
        self.assertIn('4芯', e['meaning_cn']); self.assertIn('1芯', e['meaning_cn'])
        self.assertIn('沿顶板面敷设', e['meaning_cn']); self.assertIn('沿墙面敷设', e['meaning_cn'])

    def test_outer_multiplier_not_purchase_quantity(self):
        raw = '2*(WDZ-RYJS-8*1.0-JDG25-WC)'; p = decode(raw)['explicit_parameters']; e = explain(raw, 'cable', p)
        self.assertEqual(p['explicit_multiplier'], 2); self.assertEqual(p['conductor_groups'][0]['count'], 8)
        self.assertEqual(e['selection_fields_cn']['图面倍数'], 2); self.assertIn('不代表已确认采购数量', e['meaning_cn'])

    def test_cat5_and_cat5e_not_equal(self):
        self.assertFalse(nodes.same_label('UTP/CAT5', 'UTP/CAT5e'))
        self.assertTrue(nodes.same_label('UTP/CAT5', 'UTP/CAT5 ECS通讯总线'))
        self.assertIn('非屏蔽双绞线', explain('UTP/CAT5', 'cable', decode('UTP/CAT5')['explicit_parameters'])['meaning_cn'])

    def test_ats_class_not_poles(self):
        p = decode('ATS-63A-II')['explicit_parameters']; p.update(decode('PC级')['explicit_parameters'])
        e = explain('ATS-63A-II', 'ATS', p)
        self.assertEqual(e['selection_fields_cn']['转换开关类别'], 'PC级'); self.assertNotIn('pole_marking', p)
        self.assertIn('极数未明确', '；'.join(e['pending']))

    def test_manufacturer_unknown_is_visible(self):
        raw = 'ABX-R100/4P'; e = explain(raw, 'unknown', decode(raw)['explicit_parameters'])
        self.assertIn('尚未核实', e['meaning_cn']); self.assertTrue(e['pending'])
        self.assertEqual(e['selection_fields_cn']['型号P标记'], '4P')
        self.assertNotIn('极数标记', e['selection_fields_cn'])

    def test_nhbv_and_zn_prefix_distinguished(self):
        self.assertTrue(decode('NHBV-2x1.5-JDG20')['explicit_parameters']['fire_resistant_marking'])
        e = explain('ZNBV-2x1.5-JDG20', 'cable', decode('ZNBV-2x1.5-JDG20')['explicit_parameters'])
        self.assertTrue(any('ZN' in x for x in e['pending']))


class ProtocolTests(unittest.TestCase):
    def test_consistent_fixture_has_no_repairable_issues(self):
        r = checked(); self.assertEqual(r['action'], 'ACCEPT')
        data = json.loads(r['packet_json']); self.assertTrue(any(o['meaning_cn'] for o in data['items']))
        self.assertFalse(data['selection_approved'])

    def test_binding_not_renumbered_by_global_offset(self):
        d = extraction(); d['circuits'][0]['members'], d['circuits'][1]['members'] = ['I2', 'I3'], ['I1']
        out = checked(d); self.assertEqual(out['action'], 'RETRY')
        r = json.loads(out['packet_json']); self.assertTrue(any(x['code'] == 'circuit_members_disagree' for x in r['issues']))
        self.assertEqual(r['items'][0]['circuit'], '2#')  # Flag error; never silently add/subtract a circuit number.

    def test_member_and_cable_pair_cross_checked(self):
        d = extraction(); d['circuits'][0]['members'] = ['I1']; d['circuits'][1]['members'].append('I3')
        r = json.loads(checked(d)['packet_json'])
        self.assertTrue(any(x['code'] in ('circuit_members_disagree', 'circuit_extra_members') for x in r['issues']))

    def test_missing_cat5_tag_and_pc_marker_are_not_complete(self):
        c = context(context()['whole_document_text'] + ' UTP/CAT5 PC级 7#')
        r = json.loads(checked(ctx=c)['packet_json']); codes = {x['code'] for x in r['issues']}
        self.assertTrue({'source_label_unplaced', 'source_marker_unassigned', 'source_tag_unplaced'} <= codes)

    def test_marker_target_and_parameter_conflicts(self):
        d = extraction(); d['markings'] = [{'raw': 'PC级', 'target_id': 'I1'}, {'raw': '220V', 'target_id': 'I1'}, {'raw': '380V', 'target_id': 'I1'}]
        r = json.loads(checked(d)['packet_json']); codes = {x['code'] for x in r['issues']}
        self.assertIn('ats_class_target_invalid', codes); self.assertIn('parameter_conflict', codes)
        self.assertEqual(r['items'][0]['associated_parameters']['voltage_v'], 220)

    def test_independent_30ma_stays_branch_requirement(self):
        d = extraction(); d['markings'] = [{'raw': '30mA', 'target_id': 'I1'}]
        r = json.loads(checked(d)['packet_json']); self.assertEqual(r['items'][0]['branch_requirements']['residual_current_ma'], 30)
        self.assertNotIn('residual_current_ma', r['items'][0]['literal_parameters'])
        self.assertIn('支路剩余电流要求30', r['items'][0]['meaning_cn'])

    def test_mcb_caption_leakage_stays_requirement_not_catalog_property(self):
        d = extraction(); d['items'][0]['raw'] = 'MCB-C16A/1P 30mA'
        r = json.loads(checked(d)['packet_json']); item = r['items'][0]
        self.assertEqual(item['branch_requirements']['residual_current_ma'], 30)
        self.assertNotIn('residual_current_ma', item['literal_parameters'])

    def test_unknown_has_pending_even_if_model_reports_complete(self):
        d = extraction(); d['items'].append({'id': 'I4', 'drawing_id': 'D1', 'raw': 'ABX-R100/4P', 'category': 'unknown', 'position': 'bottom'})
        r = json.loads(checked(d)['packet_json']); self.assertTrue(next(o for o in r['items'] if o['id'] == 'I4')['pending'])
        self.assertTrue(any(x['item_id'] == 'I4' for x in r['issues']))

    def test_one_item_in_two_circuits_is_unassigned(self):
        d = extraction(); d['circuits'][1]['members'].append('I1')
        r = json.loads(checked(d)['packet_json']); self.assertIsNone(r['items'][0]['circuit'])
        self.assertTrue(any(x['code'] == 'multiple_circuit_owners' for x in r['issues']))

    def test_same_tag_in_different_drawings_not_merged(self):
        d = extraction(); d['drawings'].append({'id': 'D2', 'title': '其他子图', 'cabinet_tags': ['BOX-B']})
        d['items'][1]['drawing_id'] = 'D2'; d['circuits'][1].update(drawing_id='D2', tag='1#')
        r = json.loads(checked(d)['packet_json']); self.assertEqual(len(r['circuits']), 2)
        self.assertEqual({x['drawing_id'] for x in r['circuits']}, {'D1', 'D2'})

    def test_missing_load_or_phase_is_pending(self):
        d = extraction(); d['circuits'][0].update(load=None, phase=None)
        r = json.loads(checked(d)['packet_json']); self.assertTrue(any(x['code'] == 'circuit_context_missing' for x in r['issues']))

    def test_does_not_repair_truncated_json_or_accept_duplicate_keys(self):
        c = context(); plan = nodes.parse_outline(nodes.j(outline()), nodes.j(c))['outline_json']
        ec = nodes.extraction_context(nodes.j(c), plan)['extraction_json']
        for raw in ('{"readable":true,"items":[', '{"readable":true,"readable":false}', '<think>unfinished'):
            self.assertEqual(json.loads(nodes.checked(raw, ec)['packet_json'])['status'], 'failed')

    def test_complete_think_prefix_separated(self):
        self.assertEqual(nodes.read_json('<think>reasoning</think>{"ok":true}'), {'ok': True})

    def test_retry_failure_keeps_readable_first_pass(self):
        first = checked()['packet_json']; a = json.loads(first)
        second = nodes.j({'status': 'failed', 'page': a['page'], 'image_name': a['image_name'], 'error': 'bad JSON', 'issues': []})
        r = json.loads(nodes.choose_best(first, second)['packet_json'])
        self.assertEqual(r['status'], 'parsed'); self.assertEqual(len(r['items']), 3)

    def test_retry_cannot_win_by_omitting_items(self):
        a = checked()['packet_json']; b = json.loads(a); b['items'].pop(); b['issues'] = []
        r = json.loads(nodes.choose_best(a, nodes.j(b))['packet_json'])
        self.assertEqual(len(r['items']), 3); self.assertEqual(r['attempt_audit']['adopted'], 'first')

    def test_retry_can_correct_unsupported_typo_but_keeps_audit(self):
        good = json.loads(checked()['packet_json']); bad = deepcopy(good)
        bad['items'][0].update(raw='MCB-C61A/1P', source_text_supported=False)
        bad['issues'].append(nodes.issue('typo', 'unsupported transcription', repairable=True))
        out = json.loads(nodes.choose_best(nodes.j(bad), nodes.j(good))['packet_json'])
        self.assertEqual(out['attempt_audit']['adopted'], 'second')
        self.assertEqual(out['items'][0]['raw'], 'MCB-C16A/1P')
        self.assertTrue(any(x['code'] == 'unverified_transcription_changed' for x in out['issues']))

    def test_global_links_and_cabinet_scope_preserved(self):
        meta = {'success': True, 'total_images': 1, 'total_pages': 1, 'images': [{'filename': 'fixture_page_1.png', 'file_index': 1, 'page_number': 1}]}
        p = nodes.bind_pages([meta], [{'filename': 'fixture_page_1.png'}], [context()['whole_document_text']], '')
        out = nodes.collect([checked()['packet_json']], p['manifest_compact']); r = json.loads(out['result_json'])
        ids = {o['id'] for o in r['items']}
        self.assertTrue(all(i in ids for c in r['circuits'] for i in c['members']))
        self.assertEqual(r['items'][0]['cabinet_tags'], ['BOX-A'])
        self.assertTrue(all(o['candidate_quantity'] is None for o in r['items']))
        self.assertIn('2根、每根4', out['report']); self.assertIn('额定电流_A', out['report'])

    def test_scrambled_source_numbers_not_sorted_into_bindings(self):
        inv = nodes.source_inventory('1#3#2# L2 8# 7#楼 附图2#N')
        self.assertEqual(inv['circuit_tag_mentions'], ['1#', '2#', '3#', '8#'])

    def test_mention_counts_are_not_purchase_quantities(self):
        inv = nodes.source_inventory('MCB-C16A/1P MCB-C16A/1P')
        items = [{'raw': 'MCB-C16A/1P', 'literal_parameters': decode('MCB-C16A/1P')['explicit_parameters']}]
        problems = nodes.inventory_checks(inv, items, [], [])
        self.assertTrue(any(x['code'] == 'source_mention_count_pending' for x in problems))

    def test_graph_model_and_file_boundaries(self):
        d = yaml.safe_load((ROOT/'PDF电气识图_V6_中文解释与回路核对.yml').read_text())
        index = {n['id']: n['data'] for n in d['workflow']['graph']['nodes']}
        self.assertEqual(len(d['dependencies']), 2)
        for a in index.values():
            if a['type'] == 'code':
                compile(a['code'], '<node>', 'exec')
                self.assertFalse(any(index.get(v['value_selector'][0], {}).get('type') == 'iteration' and v['value_selector'][1] == 'item' for v in a['variables']))
            if a['type'] == 'llm':
                self.assertEqual(a['model']['name'], 'TenGen-ZhiHan:F16')
                self.assertEqual(a['model']['provider'], 'langgenius/ollama/ollama')
                self.assertIs(a['model']['completion_params']['think'], False)


def private_replay(correct=False):
    legacy = json.loads(Path(os.environ['PRIVATE_RESULT_JSON']).read_text())
    native = json.loads(Path(os.environ['PRIVATE_NATIVE_JSON']).read_text())['pages'][0]
    lookup = {a['id']: a for a in native['annotations']}
    ds = [{'id': 'D1', 'title': 'private drawing', 'cabinet_tags': []}]
    plan = {'readable': True, 'view': 'single_line', 'drawings': ds, 'circuits': [], 'unresolved': []}
    for i, c in enumerate(native['branches'], 1):
        plan['circuits'].append({'id': 'C%d' % i, 'drawing_id': 'D1', 'tag': c['circuit_tag'],
            'phase': '、'.join(lookup[x]['raw'] for x in c['phase_anchor_ids']) or None,
            'load': ''.join(lookup[x]['raw'] for x in c['load_annotation_ids']) or None,
            'device_labels': [lookup[x]['raw'] for x in c['device_annotation_ids'] + c['function_annotation_ids']],
            'cable_labels': [lookup[x]['raw'] for x in c['wire_annotation_ids']]})
    d = {'readable': True, 'page_complete': True, 'drawings': ds, 'items': [], 'circuits': [], 'markings': [], 'unresolved': []}
    for o in legacy['items']:
        iid = o['id'].split('-')[-1]
        d['items'].append({'id': iid, 'drawing_id': 'D1', 'raw': o['label'], 'category': o['category'], 'position': o['position']})
        for m in o.get('associated_markings', []): d['markings'].append({'raw': m['raw'], 'target_id': iid})
    if correct:
        # Private, manually checked reference associations. Production code has no sample branch map.
        members = {1: ['I004','I020'], 2: ['I005','I006','I007','I021'], 3: ['I008','I022'],
                   4: ['I009','I023'], 5: ['I010','I024'], 6: ['I011','I012','I013','I025'], 7: ['I014'], 8: ['I015']}
        d['items'].append({'id': 'I031', 'drawing_id': 'D1', 'raw': 'UTP/CAT5 ECS', 'category': 'cable', 'position': 'communications'})
        d['markings'].append({'raw': 'PC级', 'target_id': 'I003'})
        for i, c in enumerate(plan['circuits'], 1): d['circuits'].append({k: c[k] for k in ('id','drawing_id','tag','phase','load')} | {'members': members[i]})
    else:
        for tag in sorted({o.get('circuit') for o in legacy['items'] if o.get('circuit')}, key=lambda x: int(x.rstrip('#'))):
            d['circuits'].append({'id': 'C'+tag.rstrip('#'), 'drawing_id': 'D1', 'tag': tag, 'phase': None, 'load': None,
                                  'members': [o['id'].split('-')[-1] for o in legacy['items'] if o.get('circuit') == tag]})
    c = context(legacy['source_text'])
    return checked(d, c, plan)


class PrivateReplayTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('PRIVATE_RESULT_JSON') and os.environ.get('PRIVATE_NATIVE_JSON'), 'private files are not bundled')
    def test_actual_previous_result_now_exposes_failures(self):
        r = json.loads(private_replay()['packet_json']); codes = {x['code'] for x in r['issues']}
        self.assertTrue({'circuit_members_disagree', 'source_tag_unplaced', 'source_label_unplaced', 'source_marker_unassigned', 'item_pending'} <= codes)
        self.assertTrue(any('CAT5' in x['message'] for x in r['issues']))
        self.assertTrue(any('PC级' in x['message'] for x in r['issues']))
        self.assertEqual(next(o for o in r['items'] if o['raw'].startswith('2*('))['selection_fields_cn']['图面倍数'], 2)

    @unittest.skipUnless(os.environ.get('PRIVATE_RESULT_JSON') and os.environ.get('PRIVATE_NATIVE_JSON'), 'private files are not bundled')
    def test_manually_corrected_reference_passes_binding_checks_not_vendor_certification(self):
        r = json.loads(private_replay(True)['packet_json']); codes = {x['code'] for x in r['issues']}
        self.assertNotIn('circuit_members_disagree', codes); self.assertNotIn('source_tag_unplaced', codes)
        self.assertNotIn('source_label_unplaced', codes); self.assertNotIn('source_marker_unassigned', codes)
        self.assertEqual(next(o for o in r['items'] if o['category']=='ATS')['selection_fields_cn']['转换开关类别'], 'PC级')
        self.assertEqual(len(r['items']), 31); self.assertFalse(r['selection_approved'])


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    (ROOT/'validation.json').write_text(json.dumps({'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'skipped': len(result.skipped), 'actual_model_calls': 0,
        'scope': 'Offline protocol and rule tests; private actual-result replay and manually checked development reference, not live recognition accuracy or a blind benchmark.'}, indent=2))
    raise SystemExit(not result.wasSuccessful())
