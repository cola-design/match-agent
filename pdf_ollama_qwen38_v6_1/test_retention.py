from copy import deepcopy
import json
from pathlib import Path
import unittest
import nodes
from test_v6 import context, outline, extraction, checked

ROOT = Path(__file__).resolve().parent


class RetentionTests(unittest.TestCase):
    def test_rcb_alias_does_not_reject_page(self):
        d = extraction(); d['items'][1]['category'] = 'RCB'
        r = json.loads(checked(d)['packet_json'])
        self.assertEqual(r['status'], 'parsed'); self.assertEqual(len(r['items']), 3)
        self.assertEqual(r['items'][1]['category'], 'residual_current_device_unspecified')
        self.assertIn('RCB', r['diagnostics']['item_metadata'][1]['category_input']['value'])

    def test_chinese_and_case_category_variants(self):
        d = extraction(); d['items'][0]['category'] = '小型断路器'; d['items'][2]['category'] = 'CABLE'
        r = json.loads(checked(d)['packet_json'])
        self.assertEqual([o['category'] for o in r['items']], ['MCB', 'residual_current_device_unspecified', 'cable'])

    def test_literal_decoding_happens_before_category_allowlist(self):
        for value in ('circuit_breaker', 'custom-breaker', {'unsupported': 'object'}, None):
            d = extraction(); d['items'][0]['category'] = value
            with self.subTest(value=value):
                r = json.loads(checked(d)['packet_json']); self.assertEqual(r['status'], 'parsed')
                self.assertEqual(r['items'][0]['category'], 'MCB')

    def test_new_unknown_category_stays_unknown_and_retains_other_items(self):
        d = extraction(); d['items'].append({'id':'I4','drawing_id':'D1','raw':'ZZX-100/4P','category':'novel_device_kind','position':'bottom'})
        r = json.loads(checked(d)['packet_json'])
        self.assertEqual(len(r['items']), 4); self.assertEqual(r['items'][-1]['category'], 'unknown')
        self.assertTrue(r['items'][-1]['pending']); self.assertFalse(r['selection_approved'])

    def test_unrecognized_drawing_reference_not_assigned_to_first_drawing(self):
        d = extraction(); d['items'][0]['drawing_id'] = 'NOT-D1'
        r = json.loads(checked(d)['packet_json'])
        self.assertEqual(r['status'], 'parsed'); self.assertEqual(len(r['items']), 3)
        self.assertIsNone(r['items'][0]['drawing_id']); self.assertIsNone(r['items'][0]['circuit_id'])
        self.assertTrue(any(x['code']=='item_drawing_unresolved' for x in r['issues']))
        self.assertIn('NOT-D1', r['diagnostics']['item_metadata'][0]['drawing_input']['value'])

    def test_case_only_drawing_reference_matches_unique_definition(self):
        d = extraction(); d['items'][0]['drawing_id'] = ' d1 '
        r = json.loads(checked(d)['packet_json']); self.assertEqual(r['items'][0]['drawing_id'], 'D1')

    def test_numeric_id_matches_numeric_definition_without_guessing_d_prefix(self):
        d = extraction(); d['drawings'][0]['id'] = 1
        for o in d['items']: o['drawing_id'] = 1
        for c in d['circuits']: c['drawing_id'] = 1
        r = json.loads(checked(d)['packet_json']); self.assertEqual(r['status'], 'parsed')
        self.assertTrue(all(o['drawing_id']=='1' for o in r['items']))
        d = extraction(); d['items'][0]['drawing_id'] = 1
        r = json.loads(checked(d)['packet_json']); self.assertIsNone(r['items'][0]['drawing_id'])

    def test_missing_drawings_keeps_items_unbound(self):
        d = extraction(); d['drawings'] = []
        r = json.loads(checked(d)['packet_json']); self.assertEqual(r['status'], 'parsed')
        self.assertEqual(len(r['items']), 3); self.assertTrue(all(o['drawing_id'] is None for o in r['items']))
        self.assertTrue(r['unbound_circuits'])

    def test_one_nonobject_row_does_not_delete_valid_rows(self):
        d = extraction(); d['items'].append('invalid scalar row')
        r = json.loads(checked(d)['packet_json']); self.assertEqual(len(r['items']), 3)
        self.assertEqual(len(r['unparsed_records']), 1)

    def test_missing_raw_field_accepts_explicit_legacy_label_alias(self):
        d = extraction(); d['items'][0]['label'] = d['items'][0].pop('raw')
        r = json.loads(checked(d)['packet_json']); self.assertEqual(r['items'][0]['raw'], 'MCB-C16A/1P')

    def test_duplicate_item_ids_are_retained_but_ambiguous_members_not_bound(self):
        d = extraction(); extra = deepcopy(d['items'][0]); extra['position'] = 'another physical position'; d['items'].append(extra)
        r = json.loads(checked(d)['packet_json'])
        self.assertEqual(len(r['items']), 4); self.assertEqual(len({o['id'] for o in r['items']}), 4)
        self.assertIsNone(r['items'][0]['circuit_id']); self.assertIsNone(r['items'][-1]['circuit_id'])
        self.assertTrue(any(x['code']=='ambiguous_member_reference' for x in r['issues']))

    def test_cross_drawing_membership_keeps_all_rows(self):
        d = extraction(); d['drawings'].append({'id':'D2','title':None,'cabinet_tags':[]}); d['items'][0]['drawing_id']='D2'
        r = json.loads(checked(d)['packet_json']); self.assertEqual(len(r['items']),3)
        self.assertEqual(r['items'][0]['drawing_id'],'D2'); self.assertIsNone(r['items'][0]['circuit_id'])

    def test_bad_json_still_fails_with_diagnostic_excerpt(self):
        c = context(); p = nodes.parse_outline(nodes.j(outline()), nodes.j(c))['outline_json']; ec = nodes.extraction_context(nodes.j(c),p)['extraction_json']
        r = json.loads(nodes.checked('{"readable":true,"items":[',ec)['packet_json'])
        self.assertEqual(r['status'],'failed'); self.assertIn('raw_response_excerpt',r['diagnostics'])
        self.assertEqual(r['diagnostics']['error_type'],'JSONDecodeError')

    def test_failure_report_has_pending_checks_and_source_mentions(self):
        c = context(); meta={'success':True,'total_pages':1,'total_images':1,'images':[{'filename':c['image_name'],'file_index':1,'page_number':1}]}
        bound=nodes.bind_pages([meta],[{'filename':c['image_name']}],[c['whole_document_text']],'')
        failed={'status':'failed','page':1,'image_name':c['image_name'],'error':'候选类别或子图引用无效','issues':[]}
        result=nodes.collect([nodes.j(failed)],bound['manifest_compact']);r=json.loads(result['result_json'])
        self.assertGreater(r['summary']['pending_checks'],0)
        self.assertIn('辅助文字中仍有',result['report']);self.assertEqual(r['summary']['candidate_objects'],0)
        self.assertFalse(r['summary']['selection_approved'])

    def test_collect_unbound_item_does_not_crash(self):
        d=extraction();d['items'][0]['drawing_id']='OTHER'
        c=context();meta={'success':True,'total_pages':1,'total_images':1,'images':[{'filename':c['image_name'],'file_index':1,'page_number':1}]}
        bound=nodes.bind_pages([meta],[{'filename':c['image_name']}],[c['whole_document_text']],'')
        out=nodes.collect([checked(d)['packet_json']],bound['manifest_compact']);r=json.loads(out['result_json'])
        self.assertEqual(r['summary']['items_without_confirmed_drawing'],1)
        self.assertEqual(r['items'][0]['cabinet_tags'],[]);self.assertEqual(r['summary']['workflow_version'],'6.1')


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(RetentionTests))
    (ROOT/'validation_retention.json').write_text(json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
        'actual_model_calls':0,'scope':'Synthetic producer metadata variants and failure-summary replay. Exact deleted live model replies unavailable.'},indent=2))
    raise SystemExit(not result.wasSuccessful())
