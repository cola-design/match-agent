"""Fresh project entry from visible issue requirements; prior approvals never restored."""
from pathlib import Path
from copy import deepcopy
import base64,io,json,unittest
import yaml
from openpyxl import Workbook,load_workbook
ROOT=Path(__file__).parent


def encode(w):
 b=io.BytesIO();w.save(b);return base64.b64encode(b.getvalue()).decode()


def fixture(complete=True):
 w=Workbook();s=w.active;s.title='问题清单'
 s.append(['记录类型','行ID','原图型号','原图解释','待核对内容','确认柜组','确认回路','确认元件类别','确认型号','确认单柜数量','计量单位','供货范围','采购方式','物料编码','非标依据','指定厂商','同意替代','确认','处理方式','剔除或补录依据'])
 s.append(['SOURCE','A1','MCB-C10A/2P','','','G1','QF1','小型断路器','MCB-C10A/2P',1 if complete else '', '只','OUR_PURCHASE','CATALOG','','','未指定','否','是','KEEP',''])
 s=w.create_sheet('参数');s.append(['记录类型','行ID','额定电流A','极数','脱扣曲线']);s.append(['PARAM','A1',10,'2P','C'])
 s=w.create_sheet('柜体');s.append(['记录类型','行ID','原图柜号','确认柜号','确认柜体数量','宽mm','高mm','深mm','确认']);s.append(['CAB','G1','AP1','AP1',2,600,800,250,'是'])
 s=w.create_sheet('确认');s.append(['记录类型','行ID','确认人']);s.append(['SETTINGS','APPROVER','Source engineer']);s=w.create_sheet('完整性确认');s.append(['记录类型','行ID','核对结论','确认']);s.append(['SETTINGS','SOURCE_COMPLETE','已核对本次清单所有需求、数量和范围','是'])
 s=w.create_sheet('旧上下文');s.append(['记录类型','行ID','值']);s.append(['META','PART_0001','OLD UNVERIFIABLE SIGNED DATA']);s.append(['META','SIGNATURE','OLD_SIGNATURE_NOT_RESTORED']);s.sheet_state='veryHidden'
 return w


class ImportTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.doc=yaml.safe_load((ROOT/'cabinet_project_v35.yml').read_text());cls.nodes={n['data']['title']:n['data'] for n in cls.doc['workflow']['graph']['nodes']}
  cls.r={};exec(cls.nodes['识别明确操作，严格区分项目阶段']['code'],cls.r)
  cls.reader={};exec(cls.nodes['校验问题清单的项目、版本、固定列与确认人']['code'],cls.reader)
  cls.form={};exec(cls.nodes['生成当前阶段确认表，保留项目版本与签名']['code'],cls.form)
  cls.lookup={};exec(cls.nodes['准备产品选型，调用既有只读API']['code'],cls.lookup)
 def start(self,command='提交问题清单',form=False,state=''):
  files=[{'filename':'existing.xlsx','upload_file_id':'NEW_UPLOAD'}]
  return self.r['route_action'](command,state,[] if form else files,files if form else [],[],'Independent project','','')
 def ingest(self,w=None):
  start=self.start();return self.reader['main'](start['state_json'],encode(w or fixture()))
 def test_partial_import_branch_reaches_form_generator(self):
  reader=next(n for n in self.doc['workflow']['graph']['nodes'] if n['data']['title']=='校验问题清单的项目、版本、固定列与确认人')
  branch=next(n for n in self.doc['workflow']['graph']['nodes'] if n['data']['type']=='if-else' and any(c.get('variable_selector')==[reader['id'],'route'] for case in n['data'].get('cases',[]) for c in case.get('conditions',[])))
  form=next(n for n in self.doc['workflow']['graph']['nodes'] if n['data']['title']=='生成当前阶段确认表，保留项目版本与签名')
  self.assertTrue(any(e['source']==branch['id'] and e['sourceHandle']=='FORM' and e['target']==form['id'] for e in self.doc['workflow']['graph']['edges']))
 def test_new_chat_with_issue_list_can_start_selection(self):
  for command in ('提交问题清单','开始选型','导入问题清单','提交'):
   with self.subTest(command=command):
    r=self.start(command);self.assertEqual(r['route'],'SOURCE_RETURN');self.assertEqual(self.r['unpack'](r['state_json'])['stage'],'SOURCE_IMPORT_PENDING')
 def test_start_form_xlsx_is_supported(self):
  r=self.start('开始选型',True);self.assertEqual(r['route'],'SOURCE_RETURN');self.assertEqual(r['upload_source'],'FORM')
 def test_pdf_entry_still_routes_to_recognition(self):
  r=self.r['route_action']('开始选型','',[{'filename':'drawing.pdf','upload_file_id':'P'}],[],[],'PDF','','')
  self.assertEqual(r['route'],'NEW')
 def test_no_attachment_does_not_require_pdf(self):
  r=self.r['route_action']('提交问题清单','',[],[],[],'','','')
  self.assertEqual(r['route'],'STATUS');self.assertIn('直接从已有',r['message'])
 def test_current_project_is_not_overwritten_by_new_list(self):
  s=self.r['new_state']('Existing',[],[]);s['stage']='QUOTE_REVIEW';old=self.r['pack'](s)
  r=self.start('导入问题清单',state=old);self.assertEqual(r['route'],'STATUS');self.assertEqual(r['state_json'],old)
 def test_complete_list_enters_match_without_old_context_key(self):
  r=self.ingest();self.assertEqual(r['valid'],'YES',r['message']);self.assertEqual(r['route'],'MATCH')
  s=self.reader['unpack'](r['state_json']);self.assertEqual(s['stage'],'MATCH_PENDING');self.assertEqual(s['facts']['cabinets'][0]['quantity'],2)
  self.assertEqual(s['facts']['items'][0]['requirements']['rated_current_a'],10)
  self.assertEqual(s['bom'],[]);self.assertNotIn('confirmed_quote_hash',s);self.assertNotIn('OLD_SIGNATURE',json.dumps(s))
  q=self.lookup['main'](r['state_json'],'https://catalog.example');self.assertEqual(q['valid'],'YES');self.assertEqual(len(q['queries']),1)
 def test_incomplete_list_returns_prefilled_form_without_pdf(self):
  r=self.ingest(fixture(False));self.assertEqual(r['valid'],'YES',r['message']);self.assertEqual(r['route'],'FORM')
  f=self.form['main'](r['state_json'],'0.15','0');self.assertEqual(f['valid'],'YES',f['message']);self.assertIn('无需重新上传PDF',f['message'])
  w=load_workbook(io.BytesIO(base64.b64decode(f['file_base64'])))
  self.assertNotIn('识图待复核',w.sheetnames);self.assertIn('待确认问题',w.sheetnames)
  h={c.value:c.column for c in w['识图与问题清单'][1]};self.assertEqual(w['识图与问题清单'].cell(2,h['指定厂商']).value,'未指定')
 def test_filled_values_preserved_when_confirmation_is_missing(self):
  w=fixture();w['问题清单']['P2']='待核对'
  r=self.ingest(w);self.assertEqual(r['route'],'FORM');s=self.reader['unpack'](r['state_json'])
  self.assertEqual(s['facts']['items'][0]['quantity_per_cabinet'],1)
  self.assertEqual(s['facts']['items'][0]['requirements']['pole_count'],'2P')
 def test_normal_same_project_return_keeps_signature_check(self):
  s=self.r['new_state']('Other project',[],[]);s['stage']='SOURCE_REVIEW'
  r=self.reader['main'](self.r['pack'](s),encode(fixture()))
  self.assertEqual(r['valid'],'NO');self.assertNotEqual(r['route'],'MATCH')
 def test_hidden_old_rows_are_not_imported(self):
  w=fixture();s=w.create_sheet('Hidden source');s.append(list(w['问题清单'].values)[0]);row=list(w['问题清单'].values)[1];row=list(row);row[1]='HIDDEN';s.append(row);s.sheet_state='veryHidden'
  r=self.ingest(w);s=self.reader['unpack'](r['state_json']);self.assertEqual(len(s['facts']['items']),1)
 def test_formula_is_not_accepted_as_requirement_value(self):
  w=fixture();w['问题清单']['J2']='=1+1';r=self.ingest(w)
  self.assertEqual(r['valid'],'NO');self.assertIn('公式',r['message'])
 def test_duplicate_row_ids_are_rejected(self):
  w=fixture();w['问题清单'].append(list(w['问题清单'].values)[1]);r=self.ingest(w)
  self.assertEqual(r['valid'],'NO');self.assertIn('重复',r['message'])
 def test_generic_requirement_table_can_start(self):
  w=Workbook();s=w.active;s.append(['型号','元件类别','单柜数量','单位','柜组','柜体数量','额定电流A','极数','脱扣曲线']);s.append(['MCB-C10A/2P','小型断路器',1,'只','AP1',2,10,'2P','C'])
  r=self.ingest(w);self.assertEqual(r['route'],'MATCH',r['message'])
 def test_unspecified_quantity_basis_requests_confirmation(self):
  w=Workbook();s=w.active;s.append(['型号','元件类别','数量','单位','柜组','柜体数量']);s.append(['MCB-C10A/2P','小型断路器',2,'只','AP1',2])
  r=self.ingest(w);self.assertEqual(r['route'],'FORM')
 def test_unresolved_external_questions_do_not_enter_matching(self):
  w=fixture();s=w.create_sheet('待确认问题');s.append(['记录类型','行ID','关联清单行ID','问题说明','原图位置','处理结论','确认内容或依据']);s.append(['CHECK','QEXTERNAL','A1','该项规格需补充确认','源清单','',''])
  r=self.ingest(w);self.assertEqual(r['route'],'FORM')
 def test_missing_ats_poles_cannot_be_approved_by_old_context(self):
  w=fixture();w['问题清单']['C2']='ATS-40A-II';w['问题清单']['I2']='ATS-40A-II';w['问题清单']['H2']='自动转换开关';w['参数']['C2']=40;w['参数']['D2']='';w['参数']['E2']=''
  r=self.ingest(w);self.assertEqual(r['route'],'FORM')
 def test_visible_old_quote_fields_are_not_new_price_approval(self):
  w=fixture();s=w.create_sheet('Old quote');s.append(['记录类型','行ID','物料编码','确认未税销售单价','确认']);s.append(['QUOTE','OLDPRICE','12345678',900,'是'])
  r=self.ingest(w);self.assertEqual(r['route'],'MATCH');state=self.reader['unpack'](r['state_json']);self.assertEqual(state['bom'],[]);self.assertNotIn('quote_approver',state)
 def test_old_quote_cannot_be_restored_as_approved_project(self):
  w=Workbook();s=w.active;s.append(['记录类型','行ID','物料编码','确认未税销售单价','确认']);s.append(['QUOTE','P','12345678',100,'是'])
  r=self.ingest(w);self.assertEqual(r['valid'],'NO');self.assertIn('旧BOM',r['message'])
 def test_new_partial_form_has_a_working_new_signature(self):
  r=self.ingest(fixture(False));f=self.form['main'](r['state_json'],'0.15','0');w=load_workbook(io.BytesIO(base64.b64decode(f['file_base64'])))
  ws=w['识图与问题清单'];h={c.value:c.column for c in ws[1]};ws.cell(2,h['确认单柜数量'],1)
  for name in ['填写说明','完整性确认','待确认问题']:
   ws=w[name];c={x.value:x.column for x in ws[1]}
   for i in range(2,ws.max_row+1):
    if name=='填写说明':ws.cell(i,c['确认人'],'Current engineer')
    if name=='完整性确认':ws.cell(i,c['核对结论'],'已核对本次清单全部需求与数量');ws.cell(i,c['确认'],'是')
    if name=='待确认问题':ws.cell(i,c['处理结论'],'已核对');ws.cell(i,c['确认内容或依据'],'已对照原需求清单核实单柜用量为一件')
  done=self.reader['main'](f['state_json'],encode(w));self.assertEqual(done['valid'],'YES',done['message']);self.assertEqual(done['route'],'MATCH')

if __name__=='__main__':unittest.main()
