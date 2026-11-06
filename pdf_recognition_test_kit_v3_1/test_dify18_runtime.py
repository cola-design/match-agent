"""Reproduce the original error and execute the flow with real Dify File types."""
import argparse
import base64
import json
from pathlib import Path
import unittest
import urllib.parse
import urllib.request

from dify18_contract import CONTRACT,execute_code_node,file_segment,primitive_segment
import test_kit

executions=0
sandbox_executions=0
executor=None
OriginalRunner=test_kit.Runner


class TypedRunner(OriginalRunner):
    def segment(self,selector):
        value=self.get(selector)
        source=self.nodes.get(selector[0],{})
        if isinstance(value,list) and value and isinstance(value[0],dict) and value[0].get('type') in ('image','document'):
            return file_segment(value,array=True)
        if source.get('type')=='iteration' and selector[1]=='item':return file_segment(value)
        return primitive_segment(value)
    def execute(self,key):
        global executions
        data=self.nodes[key]
        if data['type']=='llm':
            image=self.segment(data['vision']['configs']['variable_selector'])
            assert isinstance(image,CONTRACT['FileSegment'])
            assert isinstance(image.value,CONTRACT['File'])
        if data['type']!='code':return super().execute(key)
        self.values[key]=execute_code_node(data,self.segment,executor)
        assert set(self.values[key])==set(data['outputs'])
        executions+=1
        return 'source'


class FileBoundaryRegression(unittest.TestCase):
    def test_original_single_file_input_reproduces_exact_error(self):
        file=file_segment({'id':'fixture','filename':'region.png','type':'image'})
        data={'code_language':'python3','code':'def main(file):\n    return {"name":file["filename"]}',
              'variables':[{'variable':'file','value_selector':['iteration','item']}],'outputs':{'name':{'type':'string'}}}
        with self.assertRaisesRegex(TypeError,'Type is not JSON serializable: File'):
            execute_code_node(data,lambda _:file)
    def test_array_file_input_uses_official_to_dict_path(self):
        files=file_segment([{'id':'fixture','filename':'region.png','type':'image'}],array=True)
        data={'code_language':'python3','code':'def main(files):\n    return {"name":files[0]["filename"]}',
              'variables':[{'variable':'files','value_selector':['start','images']}],'outputs':{'name':{'type':'string'}}}
        self.assertEqual(execute_code_node(data,lambda _:files)['name'],'region.png')
    def test_fixed_iteration_code_receives_scalars_vision_keeps_file(self):
        import yaml
        d=yaml.safe_load((Path(__file__).parent/'pdf_recognition_test_v3_1.yml').read_text())
        iterations={n['id'] for n in d['workflow']['graph']['nodes'] if n['data']['type']=='iteration'}
        for n in d['workflow']['graph']['nodes']:
            if n['data']['type']=='code':
                self.assertFalse(any(v['value_selector'][0] in iterations and v['value_selector'][1]=='item' for v in n['data']['variables']))
            if n['data']['type']=='llm':
                selector=n['data']['vision']['configs']['variable_selector']
                self.assertIn(selector[0],iterations);self.assertEqual(selector[1],'item')
    def test_whole_number_segment_is_supported_for_iteration_index(self):
        import yaml
        d=yaml.safe_load((Path(__file__).parent/'pdf_recognition_test_v3_1.yml').read_text())
        node=next(n['data'] for n in d['workflow']['graph']['nodes'] if n['data']['type']=='code' and '按图片序号' in n['data']['title'])
        packet={'source_id':'fixture','uploaded_image_names':['demo.png'],'user_requirements':{},
                'units':[{'unit_id':'u1','image_name':'demo.png','context':{'unit_id':'u1'}}]}
        inputs={'index':primitive_segment(0.0),'manifest_json':primitive_segment(json.dumps(packet))}
        mapping={tuple(v['value_selector']):inputs[v['variable']] for v in node['variables']}
        result=execute_code_node(node,lambda selector:mapping[tuple(selector)],executor)
        self.assertEqual(json.loads(result['context_json'])['image_name'],'demo.png')


def main():
    global executor,sandbox_executions
    parser=argparse.ArgumentParser();parser.add_argument('--sandbox-url')
    args=parser.parse_args()
    if args.sandbox_url:
        url=args.sandbox_url.rstrip('/')
        if urllib.parse.urlparse(url).hostname not in ('127.0.0.1','localhost'):raise SystemExit('本开发检查仅访问本机测试沙箱')
        def sandbox(code,inputs):
            global sandbox_executions
            encoded=base64.b64encode(json.dumps(inputs,ensure_ascii=False).encode()).decode()
            runner=code+'\nimport json\nfrom base64 import b64decode\ninputs=json.loads(b64decode('+repr(encoded)+').decode())\nprint("<<RESULT>>"+json.dumps(main(**inputs),ensure_ascii=False)+"<<RESULT>>")\n'
            request=urllib.request.Request(url+'/v1/sandbox/run',data=json.dumps({'language':'python3','code':runner,'preload':'','enable_network':True}).encode(),
                headers={'X-Api-Key':'local-fixture-key','Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=30) as response:result=json.load(response)
            sandbox_executions+=1
            if result.get('code')!=0 or result.get('data',{}).get('error'):raise AssertionError(str(result))
            return json.loads(result['data']['stdout'].split('<<RESULT>>')[1])
        executor=sandbox
    test_kit.Runner=TypedRunner
    suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(FileBoundaryRegression),
                            unittest.defaultTestLoader.loadTestsFromTestCase(test_kit.PairedKitTests)])
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    report={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
        'typed_code_node_executions':executions,'sandbox_executions':sandbox_executions,
        'file_types':'official Dify 1.8.0 File/FileSegment/ArrayFileSegment classes',
        'input_boundary':'official CodeNode._run and dumps_with_segments',
        'original_single_File_error_reproduced':True,'visual_model':'controlled replies; actual Qwen calls 0',
        'scope':'Typed input-boundary and exported-node tests, not a complete Dify workspace'}
    Path('validation_dify18_'+('sandbox' if args.sandbox_url else 'local')+'.json').write_text(json.dumps(report,indent=2))
    raise SystemExit(not result.wasSuccessful())


if __name__=='__main__':main()
