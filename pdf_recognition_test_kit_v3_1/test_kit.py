"""Execute exported nodes with real prepared PNG/TXT and controlled VLM replies."""
from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import unittest
import uuid

import fitz
import yaml
from prepare_test import export_batches

ROOT=Path(__file__).resolve().parent


class Runner:
    def __init__(self,definition):
        self.nodes={n['id']:n['data'] for n in definition['workflow']['graph']['nodes']}
        self.edges={};self.blobs={};self.model_calls=[];self.bad_replies=0;self.replies={};self.scopes={}
        for e in definition['workflow']['graph']['edges']:self.edges.setdefault(e['source'],[]).append(e)
    def file(self,path):
        p=Path(path);identity=uuid.uuid4().hex;self.blobs[identity]=p.read_bytes()
        return {'id':identity,'filename':p.name,'type':'image' if p.suffix=='.png' else 'document'}
    def get(self,selector):
        value=self.values[selector[0]]
        for key in selector[1:]:value=value[key]
        return value
    def render(self,text):return re.sub(r'\{\{#([^#]+)#\}\}',lambda m:str(self.get(m.group(1).split('.'))),text)
    def run(self,evidence,images,scope='未指定',notes=''):
        self.values={};self.messages=[]
        self.start_values={'evidence_files':[self.file(evidence)],'region_images':[self.file(p) for p in images],
                           'overload_scope':scope,'notes':notes}
        self.walk(next(k for k,n in self.nodes.items() if n['type']=='start'))
        return self.messages[-1]
    def walk(self,key):
        for _ in range(100):
            handle=self.execute(key);edges=[e for e in self.edges.get(key,[]) if e['sourceHandle']==handle]
            if not edges:return
            assert len(edges)==1
            key=edges[0]['target']
        raise AssertionError('Unexpected loop')
    def execute(self,key):
        d=self.nodes[key];typ=d['type'];out={};handle='source'
        if typ=='start':out=deepcopy(self.start_values)
        elif typ=='document-extractor':out={'text':[self.blobs[f['id']].decode('utf-8') for f in self.get(d['variable_selector'])]}
        elif typ=='code':
            if key not in self.scopes:
                self.scopes[key]={};exec(d['code'],self.scopes[key])
            out=self.scopes[key]['main'](**{v['variable']:self.get(v['value_selector']) for v in d['variables']})
            assert set(out)==set(d['outputs'])
        elif typ=='if-else':
            handle='false'
            for case in d['cases']:
                if all(self.get(c['variable_selector'])==c['value'] for c in case['conditions']):handle=case['case_id'];break
        elif typ=='iteration':
            result=[]
            for index,item in enumerate(self.get(d['iterator_selector'])):
                for cid,child in self.nodes.items():
                    if child.get('iteration_id')==key:self.values.pop(cid,None)
                self.values[key]={'index':index,'item':item};self.walk(d['start_node_id'])
                result.append(self.get(d['output_selector']))
            out={'output':result}
        elif typ=='iteration-start':pass
        elif typ=='llm':
            context=json.loads(self.render(d['prompt_template'][1]['text']))
            image=self.get(d['vision']['configs']['variable_selector'])
            assert image['filename']==context['image_name']
            assert self.blobs[image['id']].startswith(b'\x89PNG')
            self.model_calls.append(context['unit_id'])
            if self.bad_replies:
                self.bad_replies-=1;raw='{"items":["truncated'
            elif context['unit_id'] in self.replies:raw=json.dumps(self.replies[context['unit_id']])
            else:raw=json.dumps(model_reply(context))
            out={'text':raw}
        elif typ=='variable-aggregator':out={'output':next(self.get(v) for v in d['variables'] if v[0] in self.values)}
        elif typ=='answer':self.messages.append(self.render(d['answer']))
        else:raise AssertionError(typ)
        self.values[key]=out
        return handle
    def result(self):
        key=next(k for k,n in self.nodes.items() if n['type']=='code' and '汇总识图结果' in n['title'])
        return json.loads(self.values[key]['result_json'])


def model_reply(context):
    return {'readable':True,'coordinate_system':'normalized_1000','drawing_type':'single_line',
            'items':[{'id':'i'+str(i),'object_type':'device','kind':'fixture','observed_label':a['raw'],
                      'meaning':'controlled test reply, not visual accuracy','native_anchor_ids':[a['id']],
                      'bbox':a['bbox'],'explicit_parameters':a['literal_parameters']} for i,a in enumerate(context['native_or_ocr_anchors'])],
            'connections':[],'notes':[],'recognition_gaps':[],'engineering_review':[]}


class PairedKitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.definition=yaml.safe_load((ROOT/'pdf_recognition_test_v3_1.yml').read_text())
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        doc=fitz.open()
        for label in ('MCB-C16A/1P','MCCB100N-MA50A/3P'):
            p=doc.new_page();p.insert_text((50,50),label)
        self.pdf=self.root/'input.pdf';doc.save(self.pdf)
        export_batches(self.pdf,self.root/'prepared')
        self.batch=self.root/'prepared/batch_001';self.txt=self.batch/'evidence.txt';self.images=sorted(self.batch.glob('*.png'))
        self.runner=Runner(self.definition)
    def tearDown(self):self.temp.cleanup()
    def test_matched_files_reach_visual_model_as_actual_png(self):
        self.runner.run(self.txt,self.images)
        r=self.runner.result();self.assertEqual(r['summary']['failed_units'],0)
        self.assertEqual(len(self.runner.model_calls),2);self.assertFalse(r['summary']['recognition_accuracy_verified'])
        self.assertTrue(r['items']);self.assertTrue(all(x['physical_quantity'] is None for x in r['items']))
    def test_reversed_upload_order_still_binds_correct_native_evidence(self):
        self.runner.run(self.txt,list(reversed(self.images)))
        self.assertEqual(self.runner.result()['summary']['unassociated_native_markings'],0)
    def test_missing_image_stops_before_model(self):
        msg=self.runner.run(self.txt,self.images[:-1]);self.assertIn('全部PNG',msg);self.assertEqual(self.runner.model_calls,[])
    def test_wrong_named_image_stops_before_model(self):
        other=self.root/'wrong.png';other.write_bytes(self.images[0].read_bytes())
        msg=self.runner.run(self.txt,[other,self.images[1]]);self.assertIn('文件名',msg);self.assertEqual(self.runner.model_calls,[])
    def test_truncated_reply_retries_same_image_once(self):
        self.runner.bad_replies=1;self.runner.run(self.txt,self.images)
        self.assertEqual(self.runner.model_calls[0],self.runner.model_calls[1]);self.assertEqual(self.runner.result()['summary']['failed_units'],0)
    def test_two_bad_outputs_keep_failed_region_visible(self):
        self.runner.bad_replies=2;self.runner.run(self.txt,self.images)
        r=self.runner.result();self.assertEqual(r['summary']['failed_units'],1)
        self.assertFalse(r['summary']['all_batch_responses_validated']);self.assertTrue(r['failed_units'])
    def test_native_parameter_conflict_is_retained(self):
        manifest=json.loads(self.txt.read_text());u=manifest['units'][0];reply=model_reply(u['context'])
        reply['items'][0]['explicit_parameters']['rated_current_a']=99
        self.runner.replies[u['unit_id']]=reply;self.runner.run(self.txt,self.images)
        self.assertIn('model_native_parameter_conflict',{x['type'] for x in self.runner.result()['conflicts']})
    def test_duplicate_json_fields_are_rejected_not_silently_overwritten(self):
        node=next(n for n in self.runner.nodes.values() if n['type']=='code' and n['title']=='校验完整JSON、坐标和原文引用')
        scope={};exec(node['code'],scope)
        u=json.loads(self.txt.read_text())['units'][0];context=dict(u['context'],image_name=u['image_name'])
        raw=json.dumps(model_reply(context)).replace('"readable": true','"readable": false, "readable": true')
        result=scope['main'](raw,json.dumps(context))
        self.assertEqual(result['valid'],'NO');self.assertIn('重复字段',result['message'])
    def test_all_cabinet_requirement_is_explicit_not_a_global_default(self):
        self.runner.run(self.txt,self.images,scope='整柜各回路')
        r=self.runner.result();self.assertTrue(any(x.get('type')=='overload_requirement_device_review' for x in r['engineering_review']))
        another=Runner(self.definition);another.run(self.txt,self.images)
        self.assertEqual(another.result()['project_requirements']['overload_alarm_only_scope'],'未指定')
        self.assertFalse(any(x.get('type')=='overload_requirement_device_review' for x in another.result()['engineering_review']))
    def test_fabricated_engineering_evidence_is_not_a_success(self):
        manifest=json.loads(self.txt.read_text());u=manifest['units'][0];reply=model_reply(u['context'])
        reply['engineering_review']=[{'question':'fake','basis':'fake','native_anchor_ids':['outside-region']}]
        self.runner.replies[u['unit_id']]=reply;self.runner.run(self.txt,self.images)
        self.assertEqual(self.runner.result()['summary']['failed_units'],1)
    def test_large_document_is_split_without_claiming_first_batch_is_all(self):
        doc=fitz.open()
        for _ in range(6):doc.new_page().insert_text((50,50),'MCB-C20A/1P')
        f=self.root/'many.pdf';doc.save(f);export_batches(f,self.root/'many')
        m=json.loads((self.root/'many/test_index.json').read_text());self.assertEqual(m['batch_count'],2);self.assertEqual(m['total_units'],6)
        b=self.root/'many/batch_001';msg=self.runner.run(b/'evidence.txt',sorted(b.glob('*.png')))
        self.assertIn('其他批次',msg);self.assertEqual(self.runner.result()['summary']['total_source_units'],6)
    def test_outputs_are_not_overwritten(self):
        with self.assertRaisesRegex(ValueError,'已有文件'):export_batches(self.pdf,self.root/'prepared')
    def test_graph_uses_only_existing_model_and_built_in_nodes(self):
        d=self.definition;self.assertEqual(d['workflow']['environment_variables'],[])
        self.assertFalse(any(n['data']['type'] in ('http-request','tool') for n in d['workflow']['graph']['nodes']))
        for n in d['workflow']['graph']['nodes']:
            if n['data']['type']=='llm':self.assertEqual(n['data']['model']['name'],'qwen3-vl-plus')
            if n['data']['type']=='iteration-start':self.assertEqual(n['type'],'custom-iteration-start')


if __name__=='__main__':unittest.main()
