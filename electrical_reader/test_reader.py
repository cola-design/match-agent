import io,json,os,tempfile,unittest
from pathlib import Path
from copy import deepcopy

import fitz
import ezdxf
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from domain import decode
from reader import parse_source,render,model_context
from app import create_app,validate_result


class Terms(unittest.TestCase):
    def test_mcb_ratings(self):
        p=decode('MCB-C16A/1P')['parameters']
        self.assertEqual(p['rated_current_a'],16);self.assertEqual(p['trip_curve'],'C');self.assertEqual(p['pole_count'],'1P')
    def test_rcb_does_not_invent_rccb_rcbo(self):
        v=decode('RCB/C16A/1P+N 30mA');p=v['parameters']
        self.assertEqual(p['rated_current_a'],16);self.assertEqual(p['residual_operating_current_ma'],30);self.assertTrue(v['uncertainties'])
    def test_mccb_ma_is_not_leakage_or_curve(self):
        p=decode('MCCB100N-MA50A/3P')['parameters'];self.assertEqual(p['rated_current_a'],50)
        self.assertNotIn('trip_curve',p);self.assertNotIn('residual_operating_current_ma',p)
    def test_bundled_b_and_combustion_b1_distinct(self):
        b=decode('WDZB-YJY-5x10-SC40-WC')['parameters'];b1=decode('WDZB1-YJY-5x10')['parameters']
        self.assertEqual(b['bundled_flame_retardant_category'],'B');self.assertEqual(b1['combustion_performance_grade'],'B1')
        self.assertNotIn('bundled_flame_retardant_category',b1)
    def test_wire_is_not_automatically_multicore_cable(self):
        p=decode('WDZB-BYJ-3x2.5-JDG20-CC,WC')['parameters']
        self.assertEqual(p['conductor_groups'],[{'count':3,'cross_section_mm2':2.5}]);self.assertEqual(p['wire_family'],'BYJ')
        self.assertEqual(p['containment'][0]['size_marking_raw'],'20')
    def test_conductor_groups_preserved(self):
        p=decode('WDZB-YJY-4x10+1x6-CT-SC40-CE WE')['parameters']
        self.assertEqual(p['conductor_groups'],[{'count':4,'cross_section_mm2':10},{'count':1,'cross_section_mm2':6}])
    def test_phase_not_unique_tag(self):self.assertEqual(decode('L1~L3')['category'],'phase')
    def test_dc_voltage_is_not_spd_uc(self):
        p=decode('DC24V 2A')['parameters'];self.assertEqual(p['power_type'],'DC');self.assertEqual(p['current_a'],2);self.assertNotIn('continuous_operating_voltage_v',p)
    def test_unrecognized_original_retained(self):self.assertEqual(decode('不明型号ZX?')['raw'],'不明型号ZX?')


class Parsing(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def pdf(self,pages=1,rotation=0):
        doc=fitz.open()
        for _ in range(pages):
            p=doc.new_page(width=600,height=400);p.insert_text((100,100),'MCB-C16A/1P');p.insert_text((100,200),'WDZB-BYJ-3x2.5-JDG20-CC,WC');p.set_rotation(rotation)
        f=self.root/'fixture.pdf';doc.save(f);return f
    def test_multipage_source_identity_and_no_lost_text(self):
        s=parse_source(self.pdf(3),self.root/'parsed');self.assertEqual(len(s['pages']),3)
        ids=[a['anchor_id'] for p in s['pages'] for a in p['anchors']];self.assertEqual(len(ids),6);self.assertEqual(len(set(ids)),6)
    def test_rotated_coordinates_remain_on_page(self):
        s=parse_source(self.pdf(rotation=270),self.root/'parsed')
        for a in s['pages'][0]['anchors']:
            self.assertGreaterEqual(a['bbox_page'][0],0);self.assertLessEqual(a['bbox_page'][2],s['pages'][0]['size'][2])
        self.assertTrue(render(s,s['units'][0]['unit_id']).startswith(b'\x89PNG'))
    def test_native_dxf_text_and_chinese_survive(self):
        d=ezdxf.new('R2010');m=d.modelspace();m.add_text('MCB-C16A/1P',dxfattribs={'height':2.5}).dxf.insert=(0,10)
        m.add_text('机房插座',dxfattribs={'height':2.5}).dxf.insert=(0,0);f=self.root/'test.dxf';d.saveas(f)
        s=parse_source(f,self.root/'parsed');texts=[a['raw'] for a in s['pages'][0]['anchors']]
        self.assertIn('机房插座',texts);self.assertIn('MCB-C16A/1P',texts)
    def test_dense_drawing_subdivides_without_losing_native_anchors(self):
        d=fitz.open();p=d.new_page(width=600,height=400)
        for i in range(150):p.insert_text((50+(i%10)*45,50+(i//10)*12),'MCB-'+str(i),fontsize=5)
        f=self.root/'dense.pdf';d.save(f);s=parse_source(f,self.root/'parsed')
        native={a['anchor_id'] for a in s['pages'][0]['anchors']};covered=set()
        for u in s['units']:
            c=model_context(s,u)
            self.assertLess(len(json.dumps(c,ensure_ascii=False).encode()),44000)
            if u['kind']=='detail':covered.update(a['anchor_id'] for a in u['anchors'])
        self.assertEqual(native,covered)
    def test_unsupported_input_fails(self):
        f=self.root/'bad.bin';f.write_bytes(b'abc')
        with self.assertRaisesRegex(ValueError,'仅支持'):parse_source(f,self.root/'parsed')


class API(Parsing):
    def setUp(self):
        super().setUp();self.client=TestClient(create_app(self.root/'data','fixture-key'));self.headers={'X-Api-Key':'fixture-key'}
        self.pid=self.client.post('/v1/projects',headers=self.headers,json={'name':'fixture'}).json()['project_id']
    def upload(self):
        if not hasattr(self,'pdf_content'):self.pdf_content=self.pdf().read_bytes()
        r=self.client.post(f'/v1/projects/{self.pid}/sources',headers=self.headers,files={'file':('fixture.pdf',self.pdf_content,'application/pdf')})
        self.assertEqual(r.status_code,200);return r.json()
    def test_auth_required(self):self.assertEqual(self.client.get(f'/v1/projects/{self.pid}/batch').status_code,401)
    def test_upload_idempotent(self):
        self.upload();second=self.upload();self.assertTrue(second['duplicate_upload'])
    def test_failed_units_remain_explicit(self):
        self.upload();b=self.client.get(f'/v1/projects/{self.pid}/batch',headers=self.headers).json()
        u=b['batch'][0];r=self.client.post(f'/v1/projects/{self.pid}/results',headers=self.headers,json={'unit_id':u['unit_id'],'failed':True})
        self.assertEqual(r.status_code,200)
        report=self.client.get(f'/v1/projects/{self.pid}/report',headers=self.headers).json()['summary'];self.assertEqual(report['failed_units'],1);self.assertFalse(report['engineering_verified'])
    def test_original_and_meaning_export_even_before_visual_results(self):
        self.upload();r=self.client.get(f'/v1/projects/{self.pid}/files/extraction.txt',headers=self.headers)
        self.assertIn('MCB-C16A/1P',r.text);self.assertIn('小型断路器',r.text);self.assertIn('WDZB',r.text)
        r=self.client.get(f'/v1/projects/{self.pid}/files/extraction.xlsx',headers=self.headers)
        self.assertIn('attachment',r.headers['content-disposition']);w=load_workbook(io.BytesIO(r.content));self.assertIn('原文与含义',w.sheetnames)
    def test_false_raw_label_is_rejected(self):
        self.upload();b=self.client.get(f'/v1/projects/{self.pid}/batch',headers=self.headers).json()['batch'];u=next(x for x in b if x['kind']=='detail');a=u['anchors'][0]
        raw={'unit_id':u['unit_id'],'page_understood':True,'items':[{'raw_label':'MCCB-WRONG','anchor_ids':[a['anchor_id']],'bbox':[.1,.1,.2,.2]}],'issues':[]}
        r=self.client.post(f'/v1/projects/{self.pid}/results',headers=self.headers,json={'unit_id':u['unit_id'],'raw':json.dumps(raw)})
        self.assertEqual(r.status_code,422)
    def test_unknown_anchor_and_nan_rejected(self):
        context={'unit_id':'u','kind':'detail','anchors':[]}
        for raw in ('{"unit_id":"u","page_understood":true,"items":NaN,"issues":[]}', '{"unit_id":"u","unit_id":"u","items":[],"issues":[]}'):
            with self.assertRaises(ValueError):validate_result(context,raw)
    def test_empty_key_is_not_open_service(self):
        c=TestClient(create_app(self.root/'empty',''));self.assertEqual(c.post('/v1/projects',json={}).status_code,503)
    def test_path_traversal_not_project(self):self.assertNotEqual(self.client.get('/v1/projects/not-a-project/batch',headers=self.headers).status_code,200)


if __name__=='__main__':unittest.main(verbosity=2)
