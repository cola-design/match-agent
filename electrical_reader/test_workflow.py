"""Execute exported DSL with the real parser API; visual model replies are fixtures."""
from copy import deepcopy
import io,json,re,tempfile,unittest,uuid
from pathlib import Path
from urllib.parse import urlsplit
import fitz,yaml
from fastapi.testclient import TestClient
from app import create_app

ROOT=Path(__file__).resolve().parent


class Runner:
    def __init__(self,client,definition):
        self.client=client;self.definition=definition;self.nodes={n['id']:n['data'] for n in definition['workflow']['graph']['nodes']}
        self.edges={};self.state='';self.blobs={};self.fail_models=0;self.model_calls=[];self.downloads=[];self.scopes={}
        self.env={'DRAWING_READER_BASE_URL':'http://reader.fixture','DRAWING_READER_API_KEY':'fixture-key'}
        for e in definition['workflow']['graph']['edges']:self.edges.setdefault(e['source'],[]).append(e)
    def file(self,name,data):
        key=uuid.uuid4().hex;self.blobs[key]=data
        return {'id':key,'filename':name,'type':'image' if name.endswith('.png') else 'document'}
    def get(self,selector):
        if selector[0]=='conversation':return self.state
        if selector[0]=='env':return self.env.get(selector[1],'')
        value=self.values[selector[0]]
        for key in selector[1:]:value=value[key]
        return value
    def template(self,text):return re.sub(r'\{\{#([^#]+)#\}\}',lambda m:str(self.get(m.group(1).split('.'))),text)
    def run(self,query,files=None):
        self.values={'sys':{'query':query,'files':files or []}};self.messages=[];self.downloads=[]
        self.walk(next(k for k,v in self.nodes.items() if v['type']=='start'))
        return self.state
    def walk(self,key):
        for _ in range(300):
            handle=self.execute(key)
            edges=[e for e in self.edges.get(key,[]) if e['sourceHandle']==handle]
            if not edges:return
            assert len(edges)==1
            key=edges[0]['target']
        raise AssertionError('unexpected graph loop')
    def execute(self,key):
        d=self.nodes[key];typ=d['type'];out={};handle='source'
        if typ=='code':
            if key not in self.scopes:
                scope={};exec(d['code'],scope);self.scopes[key]=scope
            args={v['variable']:self.get(v['value_selector']) for v in d['variables']}
            out=self.scopes[key]['main'](**args)
            assert set(out)==set(d['outputs']),(d['title'],out.keys(),d['outputs'].keys())
        elif typ=='start':pass
        elif typ=='assigner':self.state=self.get(d['items'][0]['value'])
        elif typ=='iteration':
            result=[]
            for index,item in enumerate(self.get(d['iterator_selector'])):
                for cid,child in self.nodes.items():
                    if child.get('iteration_id')==key:self.values.pop(cid,None)
                self.values[key]={'index':index,'item':item};self.walk(d['start_node_id']);result.append(self.get(d['output_selector']))
            out={'output':result}
        elif typ=='iteration-start':pass
        elif typ=='if-else':
            handle='false'
            for case in d['cases']:
                if all(self.get(c['variable_selector'])==c['value'] for c in case['conditions']):handle=case['case_id'];break
        elif typ=='variable-aggregator':
            out={'output':next(self.get(v) for v in d['variables'] if v[0] in self.values)}
        elif typ=='http-request':
            url=self.template(d['url']);u=urlsplit(url);path=u.path+('?' + u.query if u.query else '')
            headers={'X-Api-Key':self.env['DRAWING_READER_API_KEY']}
            body=d['body'];kwargs={}
            if body['type']=='form-data':
                entry=body['data'][0];file=self.get(entry['file']);kwargs['files']={entry['key']:(file['filename'],self.blobs[file['id']])}
            elif body['type']=='json':kwargs['json']=json.loads(self.template(body['data'][0]['value']))
            response=self.client.request(d['method'].upper(),path,headers=headers,**kwargs)
            out={'status_code':response.status_code,'body':response.text if response.headers.get('content-type','').startswith('application/json') else '', 'headers':dict(response.headers),'files':[]}
            if response.status_code==200 and ('image/' in response.headers.get('content-type','') or 'attachment' in response.headers.get('content-disposition','')):
                name='drawing.png' if 'image/' in response.headers['content-type'] else re.search(r'filename="([^"]+)"',response.headers['content-disposition']).group(1)
                out['files']=[self.file(name,response.content)]
                if name!='drawing.png':self.downloads+=out['files']
        elif typ=='llm':
            context=json.loads(self.template(d['prompt_template'][1]['text']));self.model_calls.append(context['unit_id'])
            images=self.get(d['vision']['configs']['variable_selector']);assert images and self.blobs[images[0]['id']].startswith(b'\x89PNG')
            if self.fail_models>0:self.fail_models-=1;raw='{"items":["truncated'
            else:
                items=[]
                if context['kind']=='detail':
                    for a in context['anchors']:
                        items.append({'raw_label':a['raw'],'kind':'fixture','meaning':'test double, not model accuracy','anchor_ids':[a['anchor_id']],
                                      'bbox':a['bbox_unit'],'quantity':None,'uncertainties':[]})
                raw=json.dumps({'unit_id':context['unit_id'],'page_understood':True,'items':items,'issues':[]})
            out={'text':raw}
        elif typ=='answer':self.messages.append(self.template(d['answer']))
        else:raise AssertionError(typ)
        self.values[key]=out;return handle


class Workflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.definition=yaml.safe_load((ROOT/'electrical_drawing_reader_v1.yml').read_text())
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.client=TestClient(create_app(self.tmp.name,'fixture-key'));self.runner=Runner(self.client,self.definition)
        doc=fitz.open();p=doc.new_page();p.insert_text((50,50),'MCB-C16A/1P');p.insert_text((50,80),'WDZB-BYJ-3x2.5-JDG20-WC');self.pdf=doc.tobytes()
    def tearDown(self):self.tmp.cleanup()
    def summary(self):return self.client.get('/v1/projects/'+self.runner.state+'/report',headers={'X-Api-Key':'fixture-key'}).json()['summary']
    def test_native_file_upload_png_model_input_and_three_downloads(self):
        self.runner.run('开始识图',[self.runner.file('fixture.pdf',self.pdf)])
        self.assertTrue(self.summary()['processing_complete']);self.assertEqual(self.summary()['unassociated_native_labels'],0)
        self.assertEqual(sorted(f['filename'] for f in self.runner.downloads),['extraction.json','extraction.txt','extraction.xlsx'])
        self.assertFalse(self.summary()['engineering_verified'])
    def test_one_bad_model_response_retries_same_image_unit(self):
        self.runner.fail_models=1;self.runner.run('开始识图',[self.runner.file('fixture.pdf',self.pdf)])
        self.assertEqual(self.runner.model_calls[0],self.runner.model_calls[1]);self.assertEqual(self.summary()['failed_units'],0)
    def test_two_bad_responses_record_failure_not_success(self):
        self.runner.fail_models=2;self.runner.run('开始识图',[self.runner.file('fixture.pdf',self.pdf)])
        self.assertEqual(self.summary()['failed_units'],1);self.assertIn('识别失败',self.runner.messages[-1])
    def test_view_results_does_not_call_model_again(self):
        self.runner.run('开始识图',[self.runner.file('fixture.pdf',self.pdf)]);count=len(self.runner.model_calls)
        self.runner.run('查看结果');self.assertEqual(len(self.runner.model_calls),count)
    def test_add_sources_keeps_project_and_new_project_changes_id(self):
        self.runner.run('开始识图',[self.runner.file('one.pdf',self.pdf)]);before=self.runner.state
        self.runner.run('追加识图',[self.runner.file('same.pdf',self.pdf)]);self.assertEqual(self.runner.state,before);self.assertEqual(self.summary()['sources'],1)
        self.runner.run('新建识图项目',[self.runner.file('again.pdf',self.pdf)]);self.assertNotEqual(self.runner.state,before)
    def test_missing_configuration_has_actionable_message(self):
        self.runner.env['DRAWING_READER_BASE_URL']='';self.runner.run('开始识图',[self.runner.file('one.pdf',self.pdf)])
        self.assertEqual(self.runner.state,'');self.assertIn('解析工具',self.runner.messages[-1])
    def test_multiround_large_plan_does_not_reread_completed_units(self):
        doc=fitz.open()
        for _ in range(8):doc.new_page().insert_text((50,50),'MCB-C16A/1P')
        self.runner.run('开始识图',[self.runner.file('many.pdf',doc.tobytes())]);self.assertGreater(self.summary()['pending_units'],0)
        previous=len(self.runner.model_calls)
        for _ in range(4):
            if self.summary()['pending_units']==0:break
            self.runner.run('继续识图')
        self.assertTrue(self.summary()['processing_complete']);self.assertEqual(len(set(self.runner.model_calls)),len(self.runner.model_calls));self.assertGreater(len(self.runner.model_calls),previous)
    def test_all_declared_node_types_and_secrets_are_clean(self):
        for node in self.definition['workflow']['graph']['nodes']:
            if node['data']['type']=='code':compile(node['data']['code'],node['id'],'exec')
            if node['data']['type']=='iteration-start':self.assertEqual(node['type'],'custom-iteration-start')
        self.assertTrue(all(not v['value'] for v in self.definition['workflow']['environment_variables']))
        self.assertTrue(all(not v['value'] for v in self.definition['workflow']['conversation_variables']))


if __name__=='__main__':unittest.main(verbosity=2)
