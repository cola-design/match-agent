"""Regression for split labels, controls, coverage and signed Chinese confirmation."""
from copy import deepcopy
import base64,io,json,unittest
from evidence_contract import finalize_evidence
from refinements import *
from review_forms import *
from test_v32 import item,data,note
import test_v33


class EvidenceTests(unittest.TestCase):
    def test_unique_nearby_class_marker_is_associated(self):
        a=item('A','ATS-40A-II','ATS',(100,100,120,145));n=note('N','PC级',(100,80,120,90))
        r=finalize_evidence(data([a],[n]),{})
        self.assertEqual(r['items'][0]['selection_parameters']['ats_class'],'PC')
        self.assertFalse(any(q['code']=='parameter_unassigned' for q in r['issues']))

    def test_two_compatible_owners_are_not_resolved_by_nearest_guess(self):
        a=item('A','ATS-40A-II','ATS',(100,100,120,145));b=item('B','ATS-63A-II','ATS',(145,100,165,145))
        r=finalize_evidence(data([a,b],[note('N','PC级',(120,80,140,90))]),{})
        self.assertTrue(any(q['code']=='parameter_unassigned' for q in r['issues']))
        self.assertTrue(all('ats_class' not in x['selection_parameters'] for x in r['items']))

    def test_existing_conflicting_class_is_not_overwritten(self):
        a=item('A','ATS-40A-II','ATS',(100,100,120,145));a['associated_parameters']['ats_class']='CB'
        r=finalize_evidence(data([a],[note('N','PC级',(100,80,120,90))]),{})
        self.assertEqual(r['items'][0]['selection_parameters']['ats_class'],'CB')
        self.assertTrue(any(q['code']=='parameter_conflict' for q in r['issues']))

    def test_split_native_class_label_is_recombined_without_inventing_poles(self):
        p={'page':1,'spans':[{'id':'T1','text':'PC','bbox':[100,75,110,85]},{'id':'T2','text':'级','bbox':[110.1,75,120,85]}]}
        a=item('A','ATS-40A-II','ATS',(100,100,120,145));r=supplement_class_markers(data([a]),{'native_pages':{'1':p}})
        r=finalize_evidence(r,{})
        self.assertEqual(r['items'][0]['selection_parameters']['ats_class'],'PC')
        self.assertNotIn('pole_marking',r['items'][0]['selection_parameters'])

    def test_remote_grade_is_not_joined(self):
        p={'page':1,'spans':[{'id':'T1','text':'PC','bbox':[100,75,110,85]},{'id':'T2','text':'级','bbox':[250,75,260,85]}]}
        self.assertFalse(supplement_class_markers(data([]),{'native_pages':{'1':p}})['document_annotations'])

    def test_function_name_corrects_generic_controller_category(self):
        r=normalize_named_controls(data([item('T','时间控制器','controller')]))
        self.assertEqual(r['items'][0]['category'],'timer')
        x=item('U','时间控制器','unknown');x['symbol_observed']=False
        self.assertEqual(normalize_named_controls(data([x]))['items'][0]['category'],'unknown')

    def test_control_candidate_works_for_both_lane_directions_and_arbitrary_tag(self):
        for direction in ('horizontal','vertical'):
            t=item('T','时间控制器','controller',(100,90,120,100),native_ref='T1')
            c=item('K','KM2','contactor',(105,112,115,122),native_ref='K1',circuit_id='C')
            if direction=='vertical':
                t['bbox']=[90,100,100,120];c['bbox']=[112,105,122,115]
            a={'id':'C','tag':'12#','page':1,'drawing_id':'D1','direction':direction}
            r=refine_controls(data([t,c],circuits=[a]),{},lambda *args:None)
            self.assertEqual(r['items'][0]['circuit'],'12#')
            self.assertTrue(any(q['code']=='control_relation_candidate' for q in r['issues']))

    def test_two_contactors_keep_control_relationship_unresolved(self):
        t=item('T','时间控制器','controller',(100,90,120,100),native_ref='T1')
        c=item('K','KM2','contactor',(105,112,115,122),native_ref='K1',circuit_id='C')
        d=item('K2','KM3','contactor',(108,116,118,126),native_ref='K2',circuit_id='C')
        a={'id':'C','tag':'12#','page':1,'drawing_id':'D1','direction':'horizontal'}
        r=refine_controls(data([t,c,d],circuits=[a]),{},lambda *args:None)
        self.assertNotIn('circuit',r['items'][0])

    def test_verified_native_placeholder_moves_to_resolved_evidence(self):
        x=item('T','110/24V','transformer',native_ref='P1-T1')
        r=data([x]);r['quarantined_observations']=[{'raw':x['raw'],'native_ref':'P1-T1','page':1,'quarantine_reason':'native_label_symbol_not_verified'}]
        out=reconcile_observations(r,{})
        self.assertEqual(out['quarantined_observations'],[]);self.assertEqual(len(out['resolved_observations']),1)

    def test_unverified_placeholder_stays_visible(self):
        x=item('T','110/24V','unknown',native_ref='P1-T1');x['symbol_observed']=False
        r=data([x]);r['quarantined_observations']=[{'raw':x['raw'],'native_ref':'P1-T1','page':1,'quarantine_reason':'native_label_symbol_not_verified'}]
        self.assertEqual(len(reconcile_observations(r,{})['quarantined_observations']),1)

    def test_generic_incomplete_view_is_not_cleared_by_text_coverage(self):
        q={'code':'view_incomplete','message':'still incomplete','page':1,'view':'H1','view_rect':[0,0,500,500]}
        r=finalize_evidence(data([item('B','MCB-C10A/2P','MCB')],issues=[q]),{})
        self.assertTrue(any(x['code']=='view_incomplete' for x in r['issues']))

    def test_focused_repair_closes_only_when_all_its_targets_are_verified(self):
        x=item('T','110/24V','transformer',native_ref='P1-T1')
        q={'code':'view_incomplete','message':'target not read','page':1,'view':'F1','view_mode':'repair','focus_native_ids':['P1-T1']}
        r=finalize_evidence(data([x],issues=[q]),{})
        self.assertFalse(any(x['code']=='view_incomplete' for x in r['issues']))
        q['focus_native_ids'].append('P1-T2')
        r=finalize_evidence(data([x],issues=[q]),{})
        self.assertTrue(any(x['code']=='view_incomplete' for x in r['issues']))


class ChineseFormTests(unittest.TestCase):
    setUpClass=classmethod(test_v33.SignedWorkflowTests.setUpClass.__func__)
    fresh=test_v33.SignedWorkflowTests.fresh
    setrow=test_v33.SignedWorkflowTests.setrow
    def save_return(self,f,w):
        o=io.BytesIO();w.save(o)
        return self.reader['main'](f['state_json'],base64.b64encode(o.getvalue()).decode())

    def filled(self):
        f,w=self.fresh();self.setrow(w['填写说明'],2,{'确认人':'Test engineer'})
        self.setrow(w['识图与问题清单'],2,{'确认单柜数量':1,'指定厂商':'未指定','确认':'是'})
        self.setrow(w['柜组数量'],2,{'确认柜体数量':2,'确认':'是'})
        self.setrow(w['完整性确认'],2,{'核对结论':'已对照受控图纸核对型号、范围和数量','确认':'是'})
        return f,w

    def test_page_url_adapter_accepts_the_already_extracted_second_page(self):
        adapters=[n for n in self.nodes.values() if n.get('type')=='code' and '本页图片选择结果应恰好一份' in n.get('code','')]
        self.assertEqual(len(adapters),2)
        for node in adapters:
            env={};exec(node['code'],env)
            self.assertNotIn('serial',[v['variable'] for v in node['variables']])
            r=env['main']([{'url':'/files/page2.png'}],'http://dify.example')
            self.assertEqual(r['file_parameter']['url'],'http://dify.example/files/page2.png')
            with self.assertRaises(ValueError):env['main']([{'url':'/files/p1.png'},{'url':'/files/p2.png'}],'http://dify.example')

    def test_source_has_no_editable_json_and_chinese_columns_roundtrip(self):
        f,w=self.filled();self.assertNotIn('确认技术参数JSON',next(w['识图与问题清单'].values))
        self.setrow(w['技术参数确认'],2,{'额定电流A':20,'极数':'2P','分断能力kA':6})
        r=self.save_return(f,w);self.assertEqual(r['valid'],'YES',r['message'])
        req=self.reader['unpack'](r['state_json'])['facts']['items'][0]['requirements']
        self.assertEqual(req['rated_current_a'],20);self.assertEqual(req['pole_count'],'2P');self.assertEqual(req['breaking_capacity_ka'],6)

    def test_invalid_numeric_and_incompatible_parameter_are_rejected(self):
        f,w=self.filled();self.setrow(w['技术参数确认'],2,{'额定电流A':'16A'})
        self.assertEqual(self.save_return(f,w)['valid'],'NO')
        f,w=self.filled();self.setrow(w['技术参数确认'],2,{'一次电压V':110})
        self.assertEqual(self.save_return(f,w)['valid'],'NO')
        f,w=self.filled();self.setrow(w['技术参数确认'],2,{'剩余动作电流mA':30})
        self.assertEqual(self.save_return(f,w)['valid'],'NO')

    def test_parameter_identity_and_original_model_are_protected(self):
        f,w=self.filled();w['技术参数确认']['C2']='Changed source model'
        self.assertEqual(self.save_return(f,w)['valid'],'NO')

    def issue_form(self):
        f,w=self.filled();state=self.reader['unpack'](f['state_json']);state['revision']+=1;state.pop('form',None)
        state['recognition_issues']=[{'code':'view_incomplete','message':'此区域未完成读取','page':1,'view':'H2','view_rect':[100,200,500,600]}]
        f=self.form['main'](self.form['pack'](state),'0.15','0')
        from openpyxl import load_workbook
        w=load_workbook(io.BytesIO(base64.b64decode(f['file_base64'])))
        self.setrow(w['填写说明'],2,{'确认人':'Test engineer'})
        self.setrow(w['识图与问题清单'],2,{'确认单柜数量':1,'指定厂商':'未指定','确认':'是'})
        self.setrow(w['柜组数量'],2,{'确认柜体数量':2,'确认':'是'})
        self.setrow(w['完整性确认'],2,{'核对结论':'已核对本次全部图纸和范围','确认':'是'})
        return f,w

    def test_overall_confirmation_cannot_bypass_an_unresolved_question(self):
        f,w=self.issue_form();r=self.save_return(f,w)
        self.assertEqual(r['valid'],'NO');self.assertIn('待确认问题',r['message'])
        self.assertIn('左10.0%',w['待确认问题']['E2'].value)

    def test_named_issue_decision_is_retained_and_allows_next_stage(self):
        f,w=self.issue_form();self.setrow(w['待确认问题'],2,{'处理结论':'已核对','确认内容或依据':'工程师已对照本页原图重新核对，该区域器件已在主清单列出'})
        r=self.save_return(f,w);self.assertEqual(r['valid'],'YES',r['message'])
        s=self.reader['unpack'](r['state_json']);self.assertEqual(s['stage'],'MATCH_PENDING')
        self.assertEqual(len(s['recognition_issue_decisions']),1)
        self.assertFalse(s['recognition_summary']['recognition_accuracy_verified'])

if __name__=='__main__':unittest.main()
