from pathlib import Path
from copy import deepcopy
import json,unittest,yaml
ROOT=Path(__file__).parent

class SubmissionTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.doc=yaml.safe_load((ROOT/'cabinet_project_v35.yml').read_text())
  cls.nodes={n['data']['title']:n['data'] for n in cls.doc['workflow']['graph']['nodes']}
  cls.router={};exec(cls.nodes['识别明确操作，严格区分项目阶段']['code'],cls.router)
  cls.form={};exec(cls.nodes['生成当前阶段确认表，保留项目版本与签名']['code'],cls.form)
  cls.bridge={};exec(cls.nodes['把识图结果转为待人工核对的项目清单']['code'],cls.bridge)
 def route(self,text,stage,files=None):
  s=self.router['new_state']('Submission fixture',[],[]);s['stage']=stage
  return self.router['route_action'](text,self.router['pack'](s),[{'filename':'return.xlsx'}] if files is None else files,[],[],'','','')
 def test_problem_confirmation_names(self):
  for command in ['提交问题清单','提交问题确认清单','提交问题确认单','提交']:
   with self.subTest(command=command):self.assertEqual(self.route(command,'SOURCE_REVIEW')['route'],'SOURCE_RETURN')
 def test_bom_aliases_case_and_spacing(self):
  for command in ['提交BOM表','提交bom表','提交Bom表','提交 BOM 表','提交BOM确认表','提交选型确认','提交选型确认表','提交']:
   with self.subTest(command=command):self.assertEqual(self.route(command,'SELECTION_REVIEW')['route'],'SELECT_RETURN')
 def test_quote_aliases(self):
  for command in ['提交报价表','提交报价确认表','提交采购报价表','提交']:
   with self.subTest(command=command):self.assertEqual(self.route(command,'QUOTE_REVIEW')['route'],'QUOTE_RETURN')
 def test_missing_wrong_or_multiple_attachments_remain_blocked(self):
  for files in [[],[{'filename':'x.pdf'}],[{'filename':'a.xlsx'},{'filename':'b.xlsx'}]]:
   r=self.route('提交BOM表','SELECTION_REVIEW',files)
   self.assertEqual(r['route'],'STATUS');self.assertIn('附件按钮',r['message'])
 def test_wrong_stage_remains_blocked(self):
  r=self.route('提交BOM表','SOURCE_REVIEW')
  self.assertEqual(r['route'],'STATUS');self.assertIn('不匹配',r['message'])
 def test_stage_help_is_explicit(self):
  for stage,command in [('SOURCE_REVIEW','提交问题清单'),('SELECTION_REVIEW','提交BOM表'),('QUOTE_REVIEW','提交报价表')]:
   r=self.route('查看项目',stage,[]);self.assertIn(command,r['message']);self.assertIn('聊天附件',r['message'])
 def test_bom_filename_and_reply_include_submission_location(self):
  s=self.router['new_state']('Submission fixture',[],[])
  raw={'summary':{},'drawings':[{'id':'D1','page':1,'bbox':[0,0,1000,1000],'cabinet_tags':['AP1']}],'items':[{'id':'O1','raw':'MCB-C10A/2P','category':'MCB','page':1,'bbox':[100,100,150,120],'drawing_id':'D1','literal_parameters':{'rated_current_a':10,'pole_marking':'2P'}}],'circuits':[],'issues':[],'document_annotations':[]}
  b=self.bridge['main'](self.router['pack'](s),json.dumps(raw),[]);self.assertEqual(b['valid'],'YES')
  source=self.form['main'](b['state_json'],'0.15','0');self.assertEqual(source['valid'],'YES',source['message']);self.assertIn('提交位置',source['message']);self.assertIn('同一条消息',source['message'])
  state=self.form['unpack'](source['state_json']);state['stage']='SELECTION_REVIEW';state['facts']['items'][0].update(quantity_per_cabinet=1,selected={'material_code':'12345678','description':'Controlled fixture'})
  bom=self.form['main'](self.form['pack'](state),'0.15','0');self.assertEqual(bom['valid'],'YES',bom['message'])
  self.assertTrue(bom['filename'].startswith('BOM选型确认表'));self.assertIn('提交BOM表',bom['message']);self.assertIn('回形针',bom['message'])
 def test_graph_model_and_upload_contract_unchanged(self):
  base=ROOT.parent/'recognition-v34-1-20261110/cabinet_project_v34_1.yml'
  if not base.exists():base=ROOT.parent/'downloads/cabinet_project_v34_1.yml'
  d=yaml.safe_load(base.read_text())
  self.assertGreaterEqual(len(self.doc['workflow']['graph']['edges']),len(d['workflow']['graph']['edges']))
  self.assertTrue({n['id'] for n in d['workflow']['graph']['nodes']}<={n['id'] for n in self.doc['workflow']['graph']['nodes']})
  self.assertEqual([n['data'] for n in self.doc['workflow']['graph']['nodes'] if n['data']['type']=='llm'],[n['data'] for n in d['workflow']['graph']['nodes'] if n['data']['type']=='llm'])
  self.assertEqual(self.doc['workflow']['features']['file_upload'],d['workflow']['features']['file_upload'])
  self.assertEqual(len(self.doc['workflow']['features']['suggested_questions']),6)
if __name__=='__main__':unittest.main()
