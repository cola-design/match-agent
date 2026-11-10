"""Base/repair coverage accounting and negated uncertainty regressions."""
import unittest,json
from spatial import base_view_coverage
from evidence_contract import finalize_evidence
from test_v32 import item,data
import test_v31


def records(names,mode='detail'):
    return [{'job':{'page':1,'view_id':n,'mode':'overview' if n=='overview' else mode,'job_id':'P1-'+n},'status':'parsed'} for n in names]


class CoverageTests(unittest.TestCase):
    names=['overview','H1','H2','H3','V1','V2','V3']
    def test_seven_base_plus_twelve_repairs_is_not_missing_views(self):
        views=records(self.names)+records(['F'+str(i) for i in range(1,13)],'repair')
        self.assertEqual(len(views),19);self.assertEqual(base_view_coverage(views,{'page_count':1}),[])

    def test_extra_repairs_do_not_hide_a_missing_base_view(self):
        views=records([n for n in self.names if n!='H2'])+records(['F'+str(i) for i in range(1,14)],'repair')
        q=base_view_coverage(views,{'page_count':1});self.assertEqual(q[0]['missing_view_ids'],['H2'])

    def test_duplicate_base_identity_is_not_accepted_as_coverage(self):
        views=records(['overview','H1','H1','H3','V1','V2','V3'])
        q=base_view_coverage(views,{'page_count':1})[0]
        self.assertEqual(q['duplicate_view_ids'],['H1']);self.assertEqual(q['missing_view_ids'],['H2'])

    def test_missing_repair_remains_a_question_with_separate_counts(self):
        m=test_v31.encoded_manifest();packets=[]
        for name in self.names:
            ctx=json.loads(test_v31.context(m,job=name,mode='overview' if name=='overview' else 'detail'))
            packets.append(test_v31.packet([],json.dumps(ctx)))
        r=test_v31.result(test_v31.collect_native(packets,[],m))
        self.assertFalse(any(q['code']=='view_missing' for q in r['issues']))
        self.assertTrue(any(q['code']=='repair_view_missing' for q in r['issues']))
        self.assertEqual(r['summary']['base_views_received'],7)
        self.assertEqual(r['summary']['repair_views_expected'],1)
        self.assertEqual(r['summary']['repair_views_received'],0)


class NegationTests(unittest.TestCase):
    def check(self,message,verified=True,category='cable'):
        x=item('W','WDZB-YJY-5x6-SC32-WC',category,native_ref='P1-T1');x['symbol_observed']=verified
        q={'code':'visual_uncertainty','message':message,'focus_native_ids':['P1-T1'],'page':1}
        return finalize_evidence(data([x],issues=[q]),{})

    def test_explicit_no_uncertainty_is_not_a_question_for_a_verified_cable(self):
        r=self.check('P1-T1已核实为独立线缆标注，型号完整、位置明确；未见模糊或缺失。')
        self.assertFalse(any(q['code']=='visual_uncertainty' for q in r['issues']))
        self.assertTrue(any(q['code']=='visual_uncertainty' for q in r['resolved_checks']))

    def test_negation_does_not_erase_a_later_positive_concern(self):
        for text in ['未见模糊，但型号后半段缺失','无矛盾，但右侧仍看不清','没有冲突，另一处参数不一致']:
            r=self.check(text)
            self.assertTrue(any(q['code']=='visual_uncertainty' for q in r['issues']),text)

    def test_not_recognizable_is_not_a_negation_of_uncertainty(self):
        r=self.check('无法识别线缆完整型号')
        self.assertTrue(any(q['code']=='visual_uncertainty' for q in r['issues']))

    def test_no_verified_symbol_does_not_close_question(self):
        r=self.check('未见模糊或缺失',False)
        self.assertTrue(any(q['code']=='visual_uncertainty' for q in r['issues']))

    def test_other_device_categories_are_not_silently_released(self):
        r=self.check('未见模糊或缺失',True,'unknown')
        self.assertTrue(any(q['code']=='visual_uncertainty' for q in r['issues']))

if __name__=='__main__':unittest.main()
