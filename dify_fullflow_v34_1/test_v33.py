"""Scope and native-symbol regressions, including signed confirmation round trips."""
import base64,io,json,sys,unittest
from copy import deepcopy
from pathlib import Path
import yaml
from vector_symbols import *
from line_scopes import *


def circle(cx,cy,r=10):
    k=.5522847498307936*r
    return {'paint':'S','curves':[
        [[cx+r,cy],[cx+r,cy+k],[cx+k,cy+r],[cx,cy+r]],
        [[cx,cy+r],[cx-k,cy+r],[cx-r,cy+k],[cx-r,cy]],
        [[cx-r,cy],[cx-r,cy-k],[cx-k,cy-r],[cx,cy-r]],
        [[cx,cy-r],[cx+k,cy-r],[cx+r,cy-k],[cx+r,cy]]]}


def symbol_page(ratio='110/24V',va='630VA'):
    return {'page':1,'display_size_points':[1000,1000],'closed_cubic_paths':[circle(200,120),circle(215,120)],
        'spans':[{'id':'P1-T1','text':ratio,'bbox':[190,80,225,95]},{'id':'P1-T2','text':va,'bbox':[190,140,230,151]}],
        'stroke_segments':[[160,120,190,120]],'straight_segments':[[160,120,190,120]]}


class VectorTests(unittest.TestCase):
    def test_combined_geometry_and_labels_identifies_a_transformer(self):
        self.assertEqual(len(transformer_vector_proofs(symbol_page())),1)
        self.assertEqual(len(transformer_vector_proofs(symbol_page('48/12V','160VA'))),1)

    def test_ratio_without_two_coils_is_not_enough(self):
        p=symbol_page();p['closed_cubic_paths']=p['closed_cubic_paths'][:1]
        self.assertEqual(transformer_vector_proofs(p),[])

    def test_no_capacity_or_no_wire_keeps_candidate_unverified(self):
        p=symbol_page();p['spans']=p['spans'][:1];self.assertEqual(transformer_vector_proofs(p),[])
        p=symbol_page();p['stroke_segments']=[];self.assertEqual(transformer_vector_proofs(p),[])

    def test_unpainted_path_is_not_wire_evidence(self):
        p=symbol_page();p['stroke_segments']=[]
        self.assertTrue(p['straight_segments'])
        self.assertEqual(transformer_vector_proofs(p),[])
        from test_v31 import make_pdf,parsed
        _,pages=parsed(make_pdf(content=b'10 10 m 200 10 l n 20 20 m 200 20 l S'))
        self.assertEqual(len(pages[0]['straight_segments']),2)
        self.assertEqual(len(pages[0]['stroke_segments']),1)

    def test_font_circles_are_not_winding_symbols(self):
        p=symbol_page();p['spans'].append({'id':'TEXT','text':'00','bbox':[180,105,230,135]})
        self.assertEqual(transformer_vector_proofs(p),[])

    def test_ambiguous_capacity_does_not_auto_select_one(self):
        p=symbol_page();p['spans'].append({'id':'SECOND','text':'1000VA','bbox':[190,145,230,156]})
        self.assertEqual(transformer_vector_proofs(p),[])

    def test_promoted_symbol_still_has_no_purchase_quantity(self):
        p=symbol_page();s=p['spans'][0]
        r={'items':[{'id':'U','raw':s['text'],'native_ref':s['id'],'bbox':s['bbox'],'page':1,'category':'unknown','symbol_observed':False}],
           'drawings':[{'id':'D1','page':1,'bbox':[0,0,1000,1000]}],'circuits':[],'summary':{}}
        out=apply_vector_transformers(r,{'native_pages':{'1':p}},lambda *args:None)
        self.assertEqual(out['items'][0]['category'],'transformer')
        self.assertEqual(out['items'][0]['associated_parameters']['apparent_power_va'],630)
        self.assertNotIn('quantity',out['items'][0])
        self.assertFalse(r['items'][0]['symbol_observed'])


class ScopeTests(unittest.TestCase):
    def test_external_route_is_reference_and_explicit_internal_wire_is_kept(self):
        state={'facts':{'items':[{'row_id':'E','role':'线缆','shown_model':'WDZB-BYJ-3x2.5-JDG20-WC'},
                                {'row_id':'I','role':'线缆','shown_model':'柜内BVR-2.5'},
                                {'row_id':'B','role':'小型断路器','shown_model':'MCB-C10A/2P'}]}}
        out=partition_line_references(state)
        self.assertEqual([x['row_id'] for x in out['facts']['items']],['I','B'])
        self.assertEqual([x['row_id'] for x in out['line_references']],['E'])
        self.assertEqual(len(state['facts']['items']),3)

    def test_unconfirmed_communication_scope_is_not_silently_priced(self):
        x={'role':'线缆','shown_model':'UTP/CAT5 ECS'}
        self.assertIn('尚未明确',line_reference_reason(x))

    def test_default_reference_needs_no_length_or_manufacturer(self):
        ref={'row_id':'R','shown_model':'WDZB-YJY-5x6-SC32-WC'}
        state={'facts':{'items':[],'cabinets':[]}}
        row={'是否由我方采购并计价':'否','确认单柜采购用量':'','物料编码':'','纳入依据':''}
        out=apply_line_reference_choices(state,[ref],{('REF','R'):row},'Engineer',lambda *args:None)
        self.assertEqual(len(out['line_references']),1)

    def test_no_with_purchase_values_requires_an_explicit_choice(self):
        row={'是否由我方采购并计价':'否','确认单柜采购用量':'12','物料编码':'','纳入依据':''}
        with self.assertRaisesRegex(ValueError,'明确选择'):
            apply_line_reference_choices({'facts':{'items':[],'cabinets':[]}},[{'row_id':'R'}],{('REF','R'):row},'E',lambda *args:None)


class SignedWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        doc=yaml.safe_load((Path(__file__).parent/'cabinet_project_v34_1.yml').read_text())
        cls.nodes={n['data']['title']:n['data'] for n in doc['workflow']['graph']['nodes']}
        cls.start={};exec(next(d['code'] for d in cls.nodes.values() if d.get('type')=='code' and 'def new_state(' in d.get('code','')),cls.start)
        cls.bridge={};exec(cls.nodes['把识图结果转为待人工核对的项目清单']['code'],cls.bridge)
        cls.form={};exec(cls.nodes['生成当前阶段确认表，保留项目版本与签名']['code'],cls.form)
        cls.reader={};exec(cls.nodes['校验问题清单的项目、版本、固定列与确认人']['code'],cls.reader)
        cls.lookup={};exec(cls.nodes['准备产品选型，调用既有只读API']['code'],cls.lookup)

    def fresh(self):
        s=self.start['new_state']('Controlled scope fixture',[],[])
        result={'summary':{},'drawings':[{'id':'D1','page':1,'bbox':[0,0,1000,1000],'cabinet_tags':['AP1']}],
            'items':[{'id':'B','raw':'MCB-C16A/1P','page':1,'bbox':[100,100,150,120],'drawing_id':'D1','category':'MCB',
                      'literal_parameters':{'rated_current_a':16,'pole_marking':'1P','trip_curve':'C'}},
                     {'id':'W','raw':'WDZB-BYJ-3x2.5-JDG20-WC','page':1,'bbox':[400,100,550,120],'drawing_id':'D1','category':'cable','literal_parameters':{}}],
            'document_annotations':[],'issues':[],'circuits':[]}
        r=self.bridge['main'](self.bridge['pack'](s),json.dumps(result),[]);self.assertEqual(r['valid'],'YES',r.get('message'))
        f=self.form['main'](r['state_json'],'0.15','0');self.assertEqual(f['valid'],'YES',f.get('message'))
        from openpyxl import load_workbook
        return f,load_workbook(io.BytesIO(base64.b64decode(f['file_base64'])))

    def setrow(self,ws,index,values):
        cols={c.value:c.column for c in ws[1]}
        for k,v in values.items():ws.cell(index,cols[k],v)

    def submit(self,include=False,omit_length=False,exclude_main=False):
        f,w=self.fresh()
        self.setrow(w['填写说明'],2,{'确认人':'Test engineer'})
        self.setrow(w['识图与问题清单'],2,{'确认单柜数量':1,'计量单位':'只','指定厂商':'未指定','确认':'是'})
        if exclude_main:self.setrow(w['识图与问题清单'],2,{'处理方式':'EXCLUDE','剔除或补录依据':'本次受控核对明确排除此器件'})
        self.setrow(w['柜组数量'],2,{'确认柜体数量':2,'确认':'是'})
        self.setrow(w['完整性确认'],2,{'核对结论':'已核对本次纳入报价的器件和参考线路范围','确认':'是'})
        if include:
            self.setrow(w['线路参考_默认不报价'],2,{'是否由我方采购并计价':'是','确认单柜采购用量':'' if omit_length else 12.5,
                    '指定厂商':'未指定','采购方式':'NON_CATALOG','外购或非标依据':'按确认规格向供应商外购','纳入依据':'本项目合同明确包含此段线缆','确认':'是'})
        out=io.BytesIO();w.save(out)
        return self.reader['main'](f['state_json'],base64.b64encode(out.getvalue()).decode())

    def test_default_reference_is_outside_source_and_product_queries(self):
        f,w=self.fresh()
        self.assertEqual(w['识图与问题清单'].max_row,2)
        self.assertEqual(w['线路参考_默认不报价'].max_row,2)
        r=self.submit();self.assertEqual(r['valid'],'YES',r['message'])
        state=self.reader['unpack'](r['state_json']);self.assertEqual(len(state['facts']['items']),1)
        q=self.lookup['main'](r['state_json'],'http://catalog.example:9000')
        self.assertEqual(len(q['queries']),1)

    def test_explicit_inclusion_requires_length_and_preserves_quantity_basis(self):
        r=self.submit(True);self.assertEqual(r['valid'],'YES',r['message'])
        s=self.reader['unpack'](r['state_json']);self.assertEqual(len(s['facts']['items']),2)
        self.assertEqual(s['facts']['items'][1]['quantity_per_cabinet'],12.5)
        self.assertEqual(s['facts']['cabinets'][0]['quantity'],2)
        self.assertEqual(s['line_references'],[])
        r=self.submit(True,True);self.assertEqual(r['valid'],'NO')

    def test_reference_only_requires_explicit_inclusion_before_selection(self):
        rejected=self.submit(exclude_main=True)
        self.assertEqual(rejected['valid'],'NO')
        accepted=self.submit(include=True,exclude_main=True)
        self.assertEqual(accepted['valid'],'YES',accepted['message'])
        state=self.reader['unpack'](accepted['state_json'])
        self.assertEqual(len(state['facts']['items']),1)
        self.assertTrue(state['facts']['items'][0]['scope_explicitly_included'])

    def test_reference_original_text_is_signature_protected(self):
        f,w=self.fresh();w['线路参考_默认不报价']['C2']='tampered model'
        out=io.BytesIO();w.save(out)
        r=self.reader['main'](f['state_json'],base64.b64encode(out.getvalue()).decode())
        self.assertEqual(r['valid'],'NO');self.assertIn('固定',r['message'])

if __name__=='__main__':unittest.main(verbosity=2)
