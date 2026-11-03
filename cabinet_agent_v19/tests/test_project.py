from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ezdxf
import fitz
from fastapi.testclient import TestClient
import httpx
from openpyxl import load_workbook

from project_service.app import create_app
from project_service.core import Settings, Problem, Store
from project_service.engine import Engine
from project_service.engineering import assess, choose_product, merge_pages
from project_service.forms import read_form
from project_service.deliverables import quote_rows, write_dxf, CadClient
from project_service.pdf_vision import prepare_pdf, validate_evidence

PRODUCT={'material_code':'12345678','description':'TGB1N-63 C16 1P 小型断路器',
    'category':'小型断路器','data_status':'ACTIVE','source_status':'有效','standard_specs':{},
    'source_attributes':[{'name':'额定电流','value':'16A'},{'name':'极数','value':'1P'},{'name':'脱扣曲线','value':'C'}]}


class FixtureCatalog:
    def __init__(self):self.product=deepcopy(PRODUCT)
    def candidates(self,item):return [deepcopy(self.product)]
    def exact(self,code):
        if code!=self.product['material_code']:raise Problem('MATERIAL_CODE_UNAVAILABLE','fixture unavailable')
        return deepcopy(self.product)
    def prices(self,code):return [{'unit_price_ex_tax':5,'price_basis':'REFERENCE_ESTIMATE'}]


class FixtureVision:
    """A controlled model response, NOT a test of Qwen recognition accuracy."""
    def __init__(self,quantity=None):self.quantity=quantity
    def read(self,prepared):
        page=prepared[0]['pages'][0]
        evidence=[{'document_id':page['document_id'],'page':1,'quote':'FIXTURE',
                   'anchor_ids':[page['anchors'][0]['id']]}]
        return [{'_page':{'document_id':page['document_id'],'page':1},'page_understood':True,
            'cabinets':[{'cabinet_id':'CAB-A','quantity':2,'width_mm':400,'height_mm':600,'depth_mm':200,'evidence':evidence}],
            'items':[{'row_id':'CB','cabinet_id':'CAB-A','device_tag':'QF1','role':'小型断路器','shown_model':'TGB1N-63','model_family':'TGB1N-63',
                'requirements':{'rated_current_a':16,'pole_count':'1P','trip_curve':'C'},'quantity_per_cabinet':self.quantity,
                'unit':'只','supply_scope':'OUR_PURCHASE','evidence':evidence},
                {'row_id':'PM','cabinet_id':'CAB-A','device_tag':'PM1','role':'电能表','shown_model':'UTILITY-METER','requirements':{},
                'quantity_per_cabinet':1,'unit':'只','supply_scope':'UTILITY_SUPPLIED','evidence':evidence}],
            'connections':[],'issues':[],'critical_notes':[]}]


class SimulatedCad:
    """Tests orchestration only. Bytes are not an actual DWG and are never delivered."""
    def convert(self,path,target):
        ezdxf.readfile(path)
        Path(target).write_bytes(b'AC1032'+b'TEST_ONLY_NOT_VALID_DWG'*10)
        return target


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.cfg=Settings(data_dir=root/'data',api_key='fixture-project-api-key-123456',
            signing_key='fixture-form-signing-key-1234567890',public_base='http://testserver',wait_seconds=20)
        self.vision=FixtureVision();self.catalog=FixtureCatalog()
        self.engine=Engine(self.cfg,vision=self.vision,products=self.catalog)
        self.client=TestClient(create_app(self.cfg,self.engine))
        self.client.__enter__()
        doc=fitz.open();page=doc.new_page();page.insert_text((40,60),'FIXTURE electrical cabinet test')
        self.pdf=doc.tobytes();doc.close()
        self.headers={'X-API-Key':self.cfg.api_key}

    def tearDown(self):
        self.client.__exit__(None,None,None);self.temp.cleanup()

    def command(self,op,pid='',file=None):
        files={'message_files':('drawing.pdf' if op=='新建项目' else 'confirmation.xlsx',self.pdf if file is None else Path(file).read_bytes())} if op=='新建项目' or file else None
        r=self.client.post('/v1/agent/command',headers=self.headers,
            data={'operation':op,'project_id':pid,'project_name':'Generic fixture project','actor':'test-user'},files=files)
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def path(self,result,kind):
        item=next(a for a in result['artifacts'] if a['kind']==kind)
        with self.engine.store.connection() as db:
            return Path(db.execute('SELECT path FROM artifacts WHERE id=?',(item['id'],)).fetchone()['path'])

    def issue_to_bom(self):
        r=self.command('新建项目');self.assertEqual(r['state'],'NEEDS_CLARIFICATION')
        path=self.path(r,'issues');wb=load_workbook(path)
        wb['填写说明']['C2']='Fixture engineer'
        for row in wb['问题确认'].iter_rows(min_row=2):
            self.assertEqual(row[3].value,'quantity_per_cabinet')
            row[7].value=3;row[8].value='是'
        path=path.with_name('answered.xlsx');wb.save(path)
        r=self.command('提交问题确认',r['project_id'],path)
        self.assertEqual(r['state'],'BOM_REVIEW',r)
        return r

    def confirmed_form(self,r,*,prices=False,cad=False):
        path=self.path(r,'bom');wb=load_workbook(path)
        for row in wb['BOM确认'].iter_rows(min_row=2):row[15].value='是'
        ws=wb['报价设置'];ws['A2']='Fixture engineer';ws['D2']='是';ws['B2']=0.15;ws['C2']=0
        if prices:
            ws=wb['价格确认'];ws['E2']=10;ws['F2']=0.13;ws['G2']='Confirmed fixture purchase document';ws['H2']='是'
        if cad:
            payload=json.loads(wb['CAD工程参数']['B2'].value)
            payload.update(reviewed=True,reviewer='Fixture engineer')
            c=payload['cabinets'][0];c.update(width_mm=400,height_mm=600,depth_mm=200,wiring_reviewed=True)
            c['layout']=[{'row_id':'I0001','instance':i,'x_mm':20+60*(i-1),'y_mm':20,'width_mm':50,'height_mm':80} for i in range(1,4)]
            c['layout'].append({'row_id':'I0002','instance':1,'x_mm':220,'y_mm':20,'width_mm':50,'height_mm':80})
            c['schematic']=[{'row_id':'I0001','instance':i,'x_mm':0,'y_mm':400-100*i} for i in range(1,4)]
            c['schematic'].append({'row_id':'I0002','instance':1,'x_mm':0,'y_mm':0})
            c['external_nodes']=[{'id':'EXT_IN','label':'Source','x_mm':0,'y_mm':400}]
            endpoints=['EXT_IN','I0001@1','I0001@2','I0001@3','I0002']
            c['wires']=[{'from':a,'to':b,'from_terminal':'2','to_terminal':'1','wire_label':'W'+str(i),'conductor_spec':'fixture 2.5mm2'} for i,(a,b) in enumerate(zip(endpoints,endpoints[1:]),1)]
            wb['CAD工程参数']['B2']=json.dumps(payload)
        path=path.with_name('confirmed.xlsx');wb.save(path);return path

    def test_issue_gate_and_signed_return(self):
        r=self.issue_to_bom();p=self.engine.store.get(r['project_id'])
        self.assertEqual(p['bom'][0]['quantity'],6)
        self.assertEqual(p['bom'][1]['supply_scope'],'UTILITY_SUPPLIED')
        self.assertEqual(len(p['bom'][0]['price']['references']),1)

    def test_no_issue_directly_produces_review_not_approval(self):
        self.vision.quantity=1
        r=self.command('新建项目');self.assertEqual(r['state'],'BOM_REVIEW')
        self.assertFalse(any(x['kind']=='quote' for x in r['artifacts']))

    def test_bom_cannot_be_skipped(self):
        r=self.command('新建项目')
        r=self.command('生成交付文件',r['project_id'])
        self.assertEqual(r['error']['code'],'BOM_APPROVAL_REQUIRED')
        self.assertFalse(any(x['kind']=='procurement' for x in r['artifacts']))

    def test_readonly_tamper_rejected(self):
        r=self.issue_to_bom();path=self.path(r,'bom');wb=load_workbook(path)
        wb['BOM确认']['M2']='CUSTOMER_SUPPLIED';wb.save(path)
        with self.assertRaises(Problem) as caught:read_form(path,self.engine.store,self.engine.store.get(r['project_id']),'bom')
        self.assertEqual(caught.exception.code,'FORM_READONLY_CHANGED')

    def test_stale_form_rejected(self):
        r=self.issue_to_bom();path=self.confirmed_form(r)
        old=path.read_bytes();r=self.command('提交BOM确认',r['project_id'],path)
        self.assertEqual(r['state'],'DELIVERY_PARTIAL')
        path.write_bytes(old);r=self.command('提交BOM确认',r['project_id'],path)
        self.assertEqual(r['error']['code'],'FORM_STALE')

    def test_formula_and_missing_approval_rejected(self):
        r=self.issue_to_bom();path=self.confirmed_form(r)
        wb=load_workbook(path);wb['价格确认']['E2']='=1+1';wb.save(path)
        result=self.command('提交BOM确认',r['project_id'],path)
        self.assertEqual(result['error']['code'],'FORM_FORMULA_NOT_ALLOWED')
        self.assertEqual(result['state'],'BOM_REVIEW')

    def test_partial_delivery_blocks_reference_prices_and_fake_dwg(self):
        r=self.issue_to_bom();r=self.command('提交BOM确认',r['project_id'],self.confirmed_form(r))
        self.assertEqual(r['state'],'DELIVERY_PARTIAL',r)
        self.assertEqual({b['output'] for b in r['blockers']},{'报价','DWG'})
        kinds={a['kind'] for a in r['artifacts']}
        self.assertTrue({'procurement','production'}<=kinds);self.assertFalse({'quote','dwg'}&kinds)

    def test_actual_dxf_generation_with_simulated_converter(self):
        self.engine.cad=SimulatedCad()
        r=self.issue_to_bom();path=self.confirmed_form(r,prices=True,cad=True)
        r=self.command('提交BOM确认',r['project_id'],path)
        self.assertEqual(r['state'],'DELIVERED',r)
        project=self.engine.store.get(r['project_id'])
        rows=quote_rows(project)
        self.assertEqual(len(rows),1) # Utility meter is not purchased or quoted.
        self.assertEqual(rows[0][9],69.0);self.assertEqual(rows[0][11],77.97)
        target=Path(self.temp.name)/'actual.dxf';write_dxf(project,target)
        self.assertFalse(ezdxf.readfile(target).audit().has_errors)

    def test_product_change_requires_new_review(self):
        r=self.issue_to_bom();path=self.confirmed_form(r)
        self.catalog.product['description']+=' updated'
        r=self.command('提交BOM确认',r['project_id'],path)
        self.assertEqual(r['state'],'BOM_REVIEW',r)
        self.assertIn('变化',r['message'])

    def test_auth_and_artifact_tokens(self):
        self.assertEqual(self.client.get('/live').status_code,200)
        self.assertEqual(self.client.get('/ready').status_code,401)
        r=self.issue_to_bom();a=r['artifacts'][0]
        self.assertEqual(self.client.get(a['url']).status_code,200)
        self.assertEqual(self.client.get('/v1/files/'+a['id']+'?token=wrong').status_code,403)
        denied=self.client.get('/v1/projects/'+r['project_id'],headers=self.headers,params={'actor':'different-user'})
        self.assertEqual(denied.status_code,403)

    def test_evidence_and_empty_extraction_fail_closed(self):
        r=self.issue_to_bom();p=self.engine.store.get(r['project_id'])
        self.assertTrue(validate_evidence(p['facts']['items'][0]['evidence'],p['prepared']))
        evidence=deepcopy(p['facts']['items'][0]['evidence']);evidence[0]['quote']='invented'
        self.assertFalse(validate_evidence(evidence,p['prepared']))
        facts={'items':[],'cabinets':[]}
        self.assertTrue(assess(facts,p['prepared'],self.catalog))

    def test_duplicate_view_and_protocol_conflict(self):
        r=self.issue_to_bom();p=self.engine.store.get(r['project_id']);prepared=p['prepared']
        page=self.vision.read(prepared)[0]
        page['items'][0]['quantity_per_cabinet']=3
        second=deepcopy(page)
        merged=merge_pages([page,second],prepared)
        self.assertEqual(len(merged['items']),2)
        page['rules']=[{'text':'fixture protocol current differs','cabinet_ids':['CAB-A'],'device_tags':['QF1'],
            'requirements':{'rated_current_a':20},'evidence':page['items'][0]['evidence']}]
        merged=merge_pages([page],prepared)
        self.assertTrue(any('冲突' in x['message'] for x in merged['recognition_issues']))

    def test_poles_and_exact_current_are_not_substituted(self):
        item={'row_id':'I1','role':'小型断路器','shown_model':'TGB1N-63','requirements':{'rated_current_a':16,'pole_count':'4P','trip_curve':'C'}}
        self.assertIsNone(choose_product(item,[PRODUCT])[0])
        item['requirements']['pole_count']='1P';item['requirements']['rated_current_a']=20
        self.assertIsNone(choose_product(item,[PRODUCT])[0])

    def test_persistence_across_engine_restart(self):
        r=self.issue_to_bom();store=Store(self.cfg)
        self.assertEqual(store.get(r['project_id'])['state'],'BOM_REVIEW')

    def test_cad_client_rejects_dxf_named_dwg(self):
        cfg=Settings(cad_url='http://cad.test/convert')
        path=Path(self.temp.name)/'fixture.dxf';path.write_text('fixture')
        with patch('project_service.deliverables.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value=httpx.Response(200,content=b'DXF'+b' '*200)
            with self.assertRaises(Problem) as caught:CadClient(cfg).convert(path,path.with_suffix('.dwg'))
        self.assertEqual(caught.exception.code,'CAD_OUTPUT_INVALID')

    def test_scope_and_cabinet_corrections_require_new_bom_review(self):
        r=self.issue_to_bom();path=self.confirmed_form(r,prices=True)
        wb=load_workbook(path);wb['其他元件修订']['G2']='CUSTOMER_SUPPLIED';wb['柜体修订']['F2']=4;wb.save(path)
        r=self.command('提交BOM确认',r['project_id'],path)
        self.assertEqual(r['state'],'BOM_REVIEW')
        p=self.engine.store.get(r['project_id'])
        self.assertEqual(p['bom'][0]['supply_scope'],'CUSTOMER_SUPPLIED')
        self.assertEqual(p['bom'][0]['quantity'],12)
        self.assertFalse(p['bom'][0]['approval'])

    def test_all_real_component_instances_must_be_drawn(self):
        r=self.issue_to_bom();path=self.confirmed_form(r,cad=True)
        wb=load_workbook(path);engineering=json.loads(wb['CAD工程参数']['B2'].value)
        engineering['cabinets'][0]['layout'].pop(0)
        p=self.engine.store.get(r['project_id']);p['engineering']=engineering
        with self.assertRaises(Problem) as caught:write_dxf(p,Path(self.temp.name)/'bad.dxf')
        self.assertEqual(caught.exception.code,'CAD_ITEMS_INCOMPLETE')

    def test_other_brand_requires_explicit_substitution_confirmation(self):
        r=self.issue_to_bom();p=self.engine.store.get(r['project_id'])
        p['facts']['items'][0]['manufacturer_requested']='OTHER BRAND'
        issues=assess(p['facts'],p['prepared'],self.catalog)
        self.assertTrue(any(x['field']=='tengen_substitution_approved' for x in issues))

    def test_dify_graph_files_and_conversation_contract(self):
        import yaml
        from build_workflow import build,PREP,PARSE,HTTP
        workflow=build()['workflow'];nodes={n['id']:n['data'] for n in workflow['graph']['nodes']}
        self.assertEqual(nodes[HTTP]['body']['data'][-1]['file'],['sys','files'])
        self.assertFalse(nodes[HTTP]['retry_config']['retry_enabled'])
        namespace={};exec(nodes[PREP]['code'],namespace)
        self.assertEqual(namespace['main']('提交问题确认','','','abc')['operation'],'提交问题确认')
        self.assertEqual(namespace['main']('查看项目','','','abc')['project_id'],'abc')
        namespace={};exec(nodes[PARSE]['code'],namespace)
        out=namespace['main']('{"message":"bad form"}',422,'abc')
        self.assertEqual(out['project_id'],'abc');self.assertEqual(out['message'],'bad form')

    def test_issue_return_can_correct_related_parameters(self):
        r=self.command('新建项目');path=self.path(r,'issues');wb=load_workbook(path)
        wb['填写说明']['C2']='Fixture engineer';ws=wb['问题确认']
        ws['H2']=3;ws['I2']='是';ws['J2']=json.dumps({'unit':'台','shown_model':'TGB1N-63','model_family':'TGB1N-63'})
        path=path.with_name('corrected.xlsx');wb.save(path)
        r=self.command('提交问题确认',r['project_id'],path)
        self.assertEqual(r['state'],'BOM_REVIEW',r)
        self.assertEqual(self.engine.store.get(r['project_id'])['bom'][0]['unit'],'台')


if __name__=='__main__':unittest.main()
