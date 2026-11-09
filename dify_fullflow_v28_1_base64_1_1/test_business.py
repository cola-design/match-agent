"""Deterministic business regression tests. No live credentials or source data."""
import base64
from copy import deepcopy
import hashlib,io,json,unittest
from openpyxl import load_workbook
import entry as e
from native.core import new_state,pack,unpack,j,product_hash
from native.excel import parse_markdown
from fixture_data import PRODUCT,PRICE

def fresh():
 s=new_state('受控测试',[],[]);s['technical_text']=''
 result={'summary':{'pages':1,'views_expected':7,'views_received':7,'failed_views':[]},
  'drawings':[{'id':'D1','page':1,'cabinet_tags':['TEST-AP1'],'bbox':[0,0,1000,1000]}],
  'items':[{'id':'O1','page':1,'drawing_id':'D1','bbox':[100,100,300,130],'raw':'MCB-C16A/1P','category':'MCB',
            'literal_parameters':{'rated_current_a':16,'pole_marking':'1P','trip_curve':'C'},'meaning_cn':'小型断路器'}],
  'issues':[],'document_annotations':[]}
 return unpack(e.bridge_recognition(pack(s),j(result),[])['state_json'])

def setrow(ws,index,values):
 hs={c.value:c.column for c in ws[1]}
 for k,v in values.items():ws.cell(index,hs[k],v)

def encode(wb):
 out=io.BytesIO();wb.save(out);return base64.b64encode(out.getvalue()).decode()

SOURCE_VALUES={'确认柜组':'G1','确认回路':'QF1','确认元件类别':'小型断路器','确认型号':'TGB1N-63',
 '确认技术参数JSON':j({'rated_current_a':16,'pole_count':'1P','trip_curve':'C'}),'确认单柜数量':'1','计量单位':'只','指定厂商':'未指定','确认':'是'}

def form(s,kind,changes=None):
 generated=e.make_stage_form(pack(s),'0.15','0');s=unpack(generated['state_json'])
 wb=load_workbook(io.BytesIO(base64.b64decode(generated['file_base64'])))
 setrow(wb['填写说明'],2,{'确认人':'测试工程师'})
 if kind=='SOURCE':
  for i in range(2,wb['识图与问题清单'].max_row+1):setrow(wb['识图与问题清单'],i,SOURCE_VALUES)
  setrow(wb['柜组数量'],2,{'确认柜体数量':'2','确认':'是'})
  setrow(wb['完整性确认'],2,{'核对结论':'已对照全部受控原图核对器件回路和数量','确认':'是'})
 elif kind=='SELECTION':
  for i in range(2,wb['选型确认'].max_row+1):setrow(wb['选型确认'],i,{'确认':'是'})
 elif kind=='QUOTE':
  for i in range(2,wb['采购报价确认'].max_row+1):setrow(wb['采购报价确认'],i,{'确认未税采购单价':'5','确认未税销售单价':'6','确认税率':'0.13','价格依据':'供应商已确认报价单20261108','确认':'是'})
  setrow(wb['报价整体确认'],2,{'确认说明':'所有数量单价税率已逐项核对确认','确认':'是'})
 if changes:changes(wb)
 return s,wb

def returned(s,kind,changes=None):
 s,wb=form(s,kind,changes)
 return unpack(e.read_stage_return(pack(s),encode(wb),kind)['state_json'])

def match(s,product=None):
 cards=e.prepare_lookup(pack(s),'http://catalog.example:9000','MATCH')['queries']
 results=[e.lookup_result(c,j([product or PRODUCT]),200)['result_json'] for c in cards]
 return unpack(e.finish_lookup(pack(s),results,'MATCH')['state_json'])

def quote_state():
 s=match(returned(fresh(),'SOURCE'));s=returned(s,'SELECTION')
 cards=e.prepare_lookup(pack(s),'http://catalog.example:9000','PRICE')['queries']
 rs=[e.lookup_result(c,j([PRICE]),200)['result_json'] for c in cards]
 return unpack(e.finish_lookup(pack(s),rs,'PRICE')['state_json'])

class BusinessTests(unittest.TestCase):
 def test_no_project_commands_are_explained(self):
  for command in ('查看项目','生成排产资料','生成DWG','补充工程参数','提交工程参数','提交问题清单'):
   with self.subTest(command=command):
    x=e.route_action(command,'',[],[],[],'','','开始选型');self.assertEqual(x['route'],'STATUS');self.assertTrue(x['message'])
 def test_revision_invalidates_approved_quote_and_reopens_source_form(self):
  s=returned(quote_state(),'QUOTE');s['engineering']={'reviewed':True}
  out=e.route_action('修订资料',pack(s),[],[],[],'','','开始选型');state=unpack(out['state_json'])
  self.assertEqual(out['route'],'FORM');self.assertEqual(state['stage'],'SOURCE_REVIEW');self.assertNotIn('confirmed_quote_hash',state);self.assertNotIn('engineering',state);self.assertFalse(state['bom'])
 def test_recognition_never_guesses_purchase_or_cabinet_quantity(self):
  s=fresh();self.assertIsNone(s['facts']['items'][0]['quantity_per_cabinet']);self.assertIsNone(s['facts']['cabinets'][0]['quantity'])
 def test_empty_recognition_is_not_accepted(self):
  with self.assertRaisesRegex(ValueError,'没有可核对'):e.bridge_recognition(pack(new_state('x',[],[])),j({'items':[]}),[])
 def test_unknown_quantity_cannot_start_matching(self):
  with self.assertRaises(ValueError):returned(fresh(),'SOURCE',lambda w:setrow(w['识图与问题清单'],2,{'确认单柜数量':''}))
 def test_pending_completeness_is_not_approval(self):
  with self.assertRaisesRegex(ValueError,'完整性'):returned(fresh(),'SOURCE',lambda w:setrow(w['完整性确认'],2,{'核对结论':'尚未核对完成，请继续检查'}))
 def test_xlsx_leading_zero_code_is_preserved(self):
  s=returned(fresh(),'SOURCE',lambda w:setrow(w['识图与问题清单'],2,{'物料编码':'01234567'}))
  self.assertEqual(s['facts']['items'][0]['material_code'],'01234567')
  card=json.loads(e.prepare_lookup(pack(s),'http://catalog.example:9000','MATCH')['queries'][0]);self.assertTrue(card['url'].endswith('/01234567'))
 def test_formula_values_are_not_silently_approved(self):
  with self.assertRaisesRegex(ValueError,'公式'):returned(fresh(),'SOURCE',lambda w:setrow(w['识图与问题清单'],2,{'确认单柜数量':'=1+1'}))
 def test_fixed_model_column_tampering_is_rejected(self):
  with self.assertRaisesRegex(ValueError,'固定字段'):returned(fresh(),'SOURCE',lambda w:setrow(w['识图与问题清单'],2,{'原图型号':'forged'}))
 def test_old_revision_and_cross_project_forms_rejected(self):
  s,w=form(fresh(),'SOURCE');encoded=encode(w);stale=deepcopy(s);stale['revision']+=1
  with self.assertRaisesRegex(ValueError,'过期'):e.read_stage_return(pack(stale),encoded,'SOURCE')
  other=fresh()
  with self.assertRaisesRegex(ValueError,'签名'):e.read_stage_return(pack(other),encoded,'SOURCE')
 def test_no_silent_deletion_of_a_row(self):
  with self.assertRaisesRegex(ValueError,'行数'):returned(fresh(),'SOURCE',lambda w:w['识图与问题清单'].delete_rows(2))
 def test_explicit_exclusion_and_manual_addition_are_audited(self):
  def edits(w):
   setrow(w['识图与问题清单'],2,{'处理方式':'EXCLUDE','剔除或补录依据':'已核对这是图例文字，并非安装器件','确认':'是'})
   setrow(w['手工补录'],2,dict(SOURCE_VALUES,**{'剔除或补录依据':'第1页右侧QF1原图漏识别器件'}))
  s=returned(fresh(),'SOURCE',edits);self.assertEqual([x['row_id'] for x in s['facts']['items']],['ADD1']);self.assertEqual(len(s['excluded_items']),1)
  s['stage']='SOURCE_REVIEW';f=e.make_stage_form(pack(s),'0','0');records=parse_markdown(e.xlsx_markdown(f['file_base64']))
  self.assertIn(('SOURCE','ADD1'),records);self.assertNotIn(('SOURCEADD','ADD1'),records)
 def test_new_cabinet_group_needs_actual_count(self):
  def edits(w):
   setrow(w['柜组数量'],3,{'确认柜号':'TEST-NEW','确认柜体数量':'3','确认':'是'})
   setrow(w['识图与问题清单'],2,{'确认柜组':'NEWG1'})
  s=returned(fresh(),'SOURCE',edits);self.assertEqual(s['facts']['items'][0]['cabinet_id'],'NEWG1');self.assertEqual(s['facts']['cabinets'][1]['quantity'],3)
 def test_catalog_lookup_uses_existing_api_and_no_prices_before_selection(self):
  s=returned(fresh(),'SOURCE');q=e.prepare_lookup(pack(s),'http://catalog.example:9000','MATCH')['queries'];self.assertIn('/v1/products/search',q[0]);self.assertNotIn('/prices/',q[0])
  self.assertEqual(e.prepare_lookup(pack(s),'http://catalog.example:9000','PRICE')['valid'],'NO')
 def test_api_root_rejects_accidental_live_path(self):
  s=returned(fresh(),'SOURCE');self.assertEqual(e.prepare_lookup(pack(s),'http://catalog.example:9000/live','MATCH')['valid'],'NO')
 def test_http_failure_preserves_stage_and_state(self):
  s=returned(fresh(),'SOURCE');state=pack(s);c=e.prepare_lookup(state,'http://catalog.example:9000','MATCH')['queries'][0]
  r=e.lookup_result(c,'404 page not found',404)['result_json'];out=e.finish_lookup(state,[r],'MATCH');self.assertEqual(out['state_json'],state);self.assertEqual(out['valid'],'NO')
 def test_dify_http_default_value_retains_actual_error_category(self):
  s=returned(fresh(),'SOURCE');c=e.prepare_lookup(pack(s),'http://catalog.example:9000','MATCH')['queries'][0]
  self.assertIn('HTTP 404',json.loads(e.lookup_result(c,'',0,'Request failed with status code 404')['result_json'])['error'])
  self.assertIn('鉴权失败',json.loads(e.lookup_result(c,'',0,'Request failed with status code 401')['result_json'])['error'])
  self.assertIn('网络连接失败',json.loads(e.lookup_result(c,'',0,'Connection timeout')['result_json'])['error'])
 def test_bad_trip_curve_or_pole_or_status_not_auto_selected(self):
  s=returned(fresh(),'SOURCE')
  for product in (dict(PRODUCT,data_status='DRAFT'),dict(PRODUCT,source_attributes=[{'name':'额定电流','value':'16A'},{'name':'极数','value':'1P+N'},{'name':'脱扣曲线','value':'C'}]),dict(PRODUCT,description='TGB1N-63 D16 1P',source_attributes=[{'name':'额定电流','value':'16A'},{'name':'极数','value':'1P'},{'name':'脱扣曲线','value':'D'}])):
   with self.subTest(product=product):self.assertEqual(match(s,product)['stage'],'SOURCE_REVIEW')
 def test_required_manufacturer_is_not_silently_substituted(self):
  s=returned(fresh(),'SOURCE',lambda w:setrow(w['识图与问题清单'],2,{'指定厂商':'指定厂家甲'}));self.assertEqual(match(s)['stage'],'SOURCE_REVIEW')
 def test_unknown_critical_requirement_requires_manual_review(self):
  s=returned(fresh(),'SOURCE');s['facts']['items'][0]['requirements']['overload_action']='alarm_only';self.assertEqual(match(s)['stage'],'SOURCE_REVIEW')
 def test_unmatched_product_is_not_fake_selected(self):
  s=returned(fresh(),'SOURCE');s['facts']['items'][0]['shown_model']='NOT-A-CATALOG-MODEL';self.assertEqual(match(s)['stage'],'SOURCE_REVIEW')
 def test_selection_confirmation_precedes_price_lookup(self):
  s=match(returned(fresh(),'SOURCE'));self.assertEqual(s['stage'],'SELECTION_REVIEW');self.assertEqual(s['bom'][0]['quantity'],2)
  self.assertEqual(e.prepare_lookup(pack(s),'http://catalog.example:9000','PRICE')['valid'],'NO')
  self.assertEqual(returned(s,'SELECTION')['stage'],'PRICE_PENDING')
 def test_price_references_do_not_approve_quote(self):
  s=quote_state();self.assertEqual(s['stage'],'QUOTE_REVIEW');self.assertEqual(s['bom'][0]['price'],{});self.assertNotIn('confirmed_quote_hash',s)
 def test_reference_estimate_cannot_be_used_as_price_basis(self):
  with self.assertRaisesRegex(ValueError,'真实价格依据'):returned(quote_state(),'QUOTE',lambda w:setrow(w['采购报价确认'],2,{'价格依据':'REFERENCE_ESTIMATE'}))
 def test_invalid_tax_rate_is_rejected(self):
  with self.assertRaisesRegex(ValueError,'税率'):returned(quote_state(),'QUOTE',lambda w:setrow(w['采购报价确认'],2,{'确认税率':'13'}))
 def test_negative_overall_confirmation_is_not_approval(self):
  with self.assertRaisesRegex(ValueError,'整体确认'):returned(quote_state(),'QUOTE',lambda w:setrow(w['报价整体确认'],2,{'确认说明':'不同意此报价，请重新核对'}))
 def test_quote_is_required_before_production(self):
  with self.assertRaisesRegex(ValueError,'报价未确认'):e.production_files(pack(quote_state()))
 def test_approved_quote_math_and_production_are_consistent(self):
  s=returned(quote_state(),'QUOTE');out=e.production_files(pack(s));wb=load_workbook(io.BytesIO(base64.b64decode(out['quote_base64'])),data_only=True)
  values=list(wb['报价汇总'].values)[1];self.assertEqual(values[1:4],('12.00','1.56','13.56'));self.assertEqual(unpack(out['state_json'])['stage'],'PRODUCTION_READY')
 def test_changed_quote_requires_reapproval(self):
  s=returned(quote_state(),'QUOTE');s['bom'][0]['quantity']=9
  with self.assertRaisesRegex(ValueError,'发生变化'):e.production_files(pack(s))
 def test_missing_verify_response_cannot_pass(self):
  s=returned(quote_state(),'QUOTE');out=e.finish_lookup(pack(s),[],'VERIFY');self.assertEqual(out['valid'],'NO')
 def test_catalog_manufacturer_change_invalidates_quote(self):
  s=returned(quote_state(),'QUOTE');c=e.prepare_lookup(pack(s),'http://catalog.example:9000','VERIFY')['queries'][0]
  result=e.lookup_result(c,j([dict(PRODUCT,manufacturer='厂商发生变更')]),200)['result_json']
  out=e.finish_lookup(pack(s),[result],'VERIFY');self.assertEqual(out['valid'],'NO');state=unpack(out['state_json']);self.assertEqual(state['stage'],'MATCH_PENDING');self.assertNotIn('confirmed_quote_hash',state)
 def test_non_catalog_and_customer_supplied_items_do_not_crash_selection(self):
  for values in ({'采购方式':'NON_CATALOG','非标依据':'已确认专用非标组件规格与采购依据'},{'供货范围':'CUSTOMER_SUPPLIED'}):
   s=returned(fresh(),'SOURCE',lambda w:setrow(w['识图与问题清单'],2,values));s=match(s);self.assertEqual(returned(s,'SELECTION')['stage'],'PRICE_PENDING')
 def test_optional_agreement_is_preserved_without_false_readability_claim(self):
  s=fresh();out=e.save_technical(pack(s),[]);self.assertEqual(unpack(out['state_json'])['technical_read_status'],'manual_review_required')
  text='技术条款'*20000;out=e.save_technical(pack(s),[text]);self.assertEqual(out['valid'],'NO')
 def test_removed_drawing_commands_do_not_change_approved_project(self):
  s=returned(quote_state(),'QUOTE');original=pack(s)
  for command in ('生成DWG','生成CAD','补充工程参数','提交工程参数'):
   result=e.route_action(command,original,[],[],[],'','','开始选型')
   self.assertEqual(result['route'],'STATUS');self.assertEqual(result['state_json'],original)
 def test_archive_does_not_export_signing_secret(self):
  s=fresh();out=e.export_archive(pack(s));raw=base64.b64decode(out['file_base64']).decode();self.assertNotIn(s['sign_key'],raw);self.assertNotIn('sign_key',raw)

if __name__=='__main__':unittest.main(verbosity=2)
