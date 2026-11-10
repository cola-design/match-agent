"""Final-result regressions deliberately contain the faults found in real output."""
from copy import deepcopy
import unittest
from evidence_contract import *


def item(i,raw,category,box=(100,100,150,120),**fields):
    return dict(id=i,raw=raw,category=category,page=1,bbox=list(box),drawing_id='D1',
                literal_parameters=decode(raw)['explicit_parameters'],associated_parameters={},
                observations=[],symbol_observed=True,**fields)


def note(i,raw,box=(100,110,150,125),**fields):
    return dict(id=i,raw=raw,kind='parameter',page=1,bbox=list(box),drawing_id='D1',**fields)


def data(items,notes=None,circuits=None,issues=None):
    return {'items':items,'document_annotations':notes or [],'circuits':circuits or [],'issues':issues or [],
            'drawings':[{'id':'D1','page':1,'bbox':[0,0,1000,1000],'cabinet_tags':['AP1']}],'summary':{},'quarantined_observations':[]}


class ContractTests(unittest.TestCase):
    def test_wrong_ratio_is_removed_from_breakers_but_preserved_on_transformer(self):
        b=item('B','MCB-C10A/2P','MCB');b['associated_parameters']['voltage_pair_v']=[110,24]
        t=item('T','110/24V','transformer')
        result=finalize_evidence(data([b,t]),{})
        self.assertNotIn('voltage_pair_v',result['items'][0]['selection_parameters'])
        self.assertNotIn('电压比',result['items'][0]['meaning_cn'])
        self.assertEqual(result['items'][1]['selection_parameters']['voltage_pair_v'],[110,24])
        self.assertEqual(len(result['parameter_rejections']),1)

    def test_wrong_parameter_without_actual_owner_remains_a_question(self):
        b=item('B','MCB-C10A/2P','MCB');b['associated_parameters']['voltage_pair_v']=[110,24]
        result=finalize_evidence(data([b]),{})
        self.assertIn('parameter_target_unresolved',{q['code'] for q in result['issues']})

    def test_parameter_schema_covers_multiple_incompatible_types(self):
        b=item('B','MCB-C10A/2P','MCB');b['associated_parameters'].update(apparent_power_va=630,ats_class='PC',residual_current_ma=30)
        result=finalize_evidence(data([b]),{})
        self.assertTrue(all(k not in result['items'][0]['selection_parameters'] for k in ('apparent_power_va','ats_class','residual_current_ma')))

    def test_legitimate_unresolved_category_and_parameter_conflict_are_not_erased(self):
        x=item('X','ZZ-101/4P','unknown')
        result=finalize_evidence(data([x],issues=[problem('parameter_conflict','Actual source conflict',page=1,item_id='X')]),{})
        self.assertIn('parameter_conflict',{q['code'] for q in result['issues']})
        self.assertIn('item_parameter_pending',{q['code'] for q in result['issues']})
        self.assertEqual(result['items'][0]['selection_parameters'],{})

    def test_function_glyph_next_to_timer_is_not_another_product(self):
        t=item('T','时间控制器','timer');mark=item('m','t','unknown',(110,115,113,125))
        result=finalize_evidence(data([t,mark]),{})
        self.assertEqual([x['id'] for x in result['items']],['T'])
        self.assertTrue(any(x['raw']=='t' and x['kind']=='symbol_mark' for x in result['document_annotations']))

    def test_isolated_glyph_is_not_deleted_without_context(self):
        x=item('X','t','unknown')
        result=finalize_evidence(data([x]),{})
        self.assertEqual([i['id'] for i in result['items']],['X'])

    def test_real_transformer_label_T_is_retained(self):
        x=item('X','T','transformer');y=item('Y','时间控制器','timer')
        result=finalize_evidence(data([x,y]),{})
        self.assertEqual(len(result['items']),2)

    def test_invalid_outgoing_labels_move_to_annotations_and_members_are_rebuilt(self):
        bad=['L1~L3','WDZB-BYJ-3','UTP/CAT','主用','备用','I/O']
        circuits=[{'id':str(i),'tag':raw,'page':1,'drawing_id':'D1','members':['ghost']} for i,raw in enumerate(bad+['9#','2#'])]
        a=item('A','MCB-C10A/2P','MCB',circuit_id='6');b=item('B','ATS-40A-II','ATS',circuit_id='0')
        result=finalize_evidence(data([a,b],circuits=circuits),{})
        self.assertEqual([a['tag'] for a in result['circuits']],['2#','9#'])
        self.assertEqual(result['circuits'][1]['members'],['A'])
        self.assertIsNone(result['items'][1]['circuit_id'])
        self.assertEqual(len(result['connection_annotations']),6)

    def test_custom_printed_identifiers_are_supported(self):
        manifest={'native_pages':{'1':{'spans':[{'id':'s1','text':'WP-21'}]}}}
        a={'id':'C','tag':'WP-21','native_ref':'s1','page':1,'drawing_id':'D1','members':[]}
        r=finalize_evidence(data([],circuits=[a]),manifest)
        self.assertEqual([c['tag'] for c in r['circuits']],['WP-21'])

    def test_resolved_VA_warning_clears_without_clearing_unresolved_markers(self):
        t=item('T','110/24V','transformer');t['associated_parameters']['apparent_power_va']=630
        issues=[problem('parameter_unassigned','“630VA”的目标未确认',page=1),problem('parameter_conflict','Keep this true conflict',page=1,item_id='T')]
        r=finalize_evidence(data([t],[note('N','630VA'),note('M','300mA',(700,700,740,720))],issues=issues),{})
        messages=' '.join(q['message'] for q in r['issues'])
        self.assertNotIn('630VA',messages);self.assertIn('300mA',messages)
        self.assertIn('parameter_conflict',{q['code'] for q in r['issues']})

    def test_breaker_with_residual_marker_gets_specific_question_not_silent_RCBO(self):
        a={'id':'C','tag':'12#','page':1,'drawing_id':'D1','bbox':[800,90,810,100],
           'wire_segment':[200,105,810,105],'direction':'horizontal','members':[]}
        b=item('B','MCB-C20A/4P','MCB',circuit_id='C')
        r=finalize_evidence(data([b],[note('N','30mA',(220,105,250,115))],[a]),{})
        self.assertEqual(r['items'][0]['category'],'MCB')
        self.assertNotIn('residual_current_ma',r['items'][0]['selection_parameters'])
        self.assertIn('protective_function_conflict',{q['code'] for q in r['issues']})

    def test_neighboring_RCBO_does_not_steal_another_circuit_marker(self):
        circuits=[{'id':'A','tag':'1#','page':1,'drawing_id':'D1','bbox':[800,60,810,70],'wire_segment':[200,75,810,75],'direction':'horizontal','members':[]},
                  {'id':'B','tag':'2#','page':1,'drawing_id':'D1','bbox':[800,90,810,100],'wire_segment':[200,105,810,105],'direction':'horizontal','members':[]}]
        one=item('A1','RCBO-C10A/1P+N 30mA','RCBO',(200,60,240,70),circuit_id='A')
        two=item('B1','MCB-C20A/4P','MCB',(200,90,240,100),circuit_id='B')
        r=finalize_evidence(data([one,two],[note('N','30mA',(220,105,250,115))],circuits),{})
        questions=[q for q in r['issues'] if q['code']=='protective_function_conflict']
        self.assertEqual([q['item_id'] for q in questions],['B1'])

    def test_finalization_is_idempotent_and_preserves_source(self):
        original=data([item('B','MCB-C10A/2P','MCB')]);before=deepcopy(original)
        once=finalize_evidence(original,{});twice=finalize_evidence(once,{})
        self.assertEqual(original,before);self.assertEqual(once,twice)

    def test_cable_does_not_require_a_separate_breaker_symbol_to_exist_as_a_candidate(self):
        cable=item('W','UTP/CAT5 ECS','cable',native_ref='P1-T8')
        q=problem('visual_uncertainty','focus_native_ids=[P1-T8]电缆已识别，但未见其连接的独立元件符号',page=1)
        r=finalize_evidence(data([cable],issues=[q]),{})
        self.assertNotIn('visual_uncertainty',{x['code'] for x in r['issues']})
        q['message']='focus_native_ids=[P1-T8]型号模糊且存在矛盾'
        r=finalize_evidence(data([cable],issues=[q]),{})
        self.assertIn('visual_uncertainty',{x['code'] for x in r['issues']})

    def test_communication_name_suffix_matches_without_confusing_CAT5_and_CAT5e(self):
        q=problem('source_label_unplaced','辅助原文“UTP/CAT5”尚未定位',page=1)
        r=finalize_evidence(data([item('W','UTP/CAT5 ECS','cable')],issues=[q]),{})
        self.assertNotIn('source_label_unplaced',{x['code'] for x in r['issues']})
        r=finalize_evidence(data([item('W','UTP/CAT5e ECS','cable')],issues=[q]),{})
        self.assertIn('source_label_unplaced',{x['code'] for x in r['issues']})

    def test_scanned_custom_circuit_is_retained_as_visual_candidate(self):
        a={'id':'C','tag':'WL-7','page':1,'drawing_id':'D1','members':[],'bbox':[800,100,810,110],
           'direction':'horizontal','observations':[{'tag':'WL-7'}]}
        r=finalize_evidence(data([],circuits=[a]),{})
        self.assertEqual([c['tag'] for c in r['circuits']],['WL-7'])
        self.assertIn('outgoing_number_visual_only',{q['code'] for q in r['issues']})

    def test_combined_literal_residual_marking_cannot_silently_enter_plain_breaker_specs(self):
        b=item('B','MCB-C20A/4P 30mA','MCB')
        r=finalize_evidence(data([b]),{})
        self.assertNotIn('residual_current_ma',r['items'][0]['selection_parameters'])
        self.assertIn('protective_function_conflict',{q['code'] for q in r['issues']})

    def test_accepted_marker_updates_explanation_and_selection_together(self):
        a={'id':'C','tag':'1#','page':1,'drawing_id':'D1','members':[],'wire_segment':[200,105,810,105],'direction':'horizontal'}
        b=item('B','RCBO-C10A/1P+N','RCBO',circuit_id='C')
        r=finalize_evidence(data([b],[note('N','30mA',(220,105,250,115))],[a]),{})
        self.assertEqual(r['items'][0]['selection_parameters']['residual_current_ma'],30)
        self.assertIn('30mA',r['items'][0]['meaning_cn'])

if __name__=='__main__':unittest.main(verbosity=2)
