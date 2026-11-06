"""Developer check against a separately started local Dify sandbox container."""
import argparse
import base64
import json
from pathlib import Path
import unittest
import urllib.parse
import urllib.request

import test_kit


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--sandbox-url',required=True)
    args=parser.parse_args();url=args.sandbox_url.rstrip('/')
    if urllib.parse.urlparse(url).hostname not in ('127.0.0.1','localhost'):raise SystemExit('本开发检查只访问本机测试沙箱')
    calls=0
    def sandbox_call(code,inputs):
        nonlocal calls
        encoded=base64.b64encode(json.dumps(inputs,ensure_ascii=False).encode()).decode()
        runner=code+'\nimport json\nfrom base64 import b64decode\ninputs=json.loads(b64decode('+repr(encoded)+').decode())\nprint("<<RESULT>>"+json.dumps(main(**inputs),ensure_ascii=False)+"<<RESULT>>")\n'
        # Match Dify 1.8 CodeExecutor's request flag. The tested node code uses
        # standard-library parsing only and performs no external requests.
        request=urllib.request.Request(url+'/v1/sandbox/run',data=json.dumps({'language':'python3','code':runner,'preload':'','enable_network':True}).encode(),
            headers={'X-Api-Key':'local-fixture-key','Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=30) as response:result=json.load(response)
        calls+=1
        if result.get('code')!=0 or result.get('data',{}).get('error'):raise AssertionError('Sandbox execution failed: '+str(result))
        return json.loads(result['data']['stdout'].split('<<RESULT>>')[1])
    OriginalRunner=test_kit.Runner
    class SandboxRunner(OriginalRunner):
        def execute(self,key):
            d=self.nodes[key]
            if d['type']!='code':return super().execute(key)
            inputs={v['variable']:self.get(v['value_selector']) for v in d['variables']}
            self.values[key]=sandbox_call(d['code'],inputs)
            assert set(self.values[key])==set(d['outputs'])
            return 'source'
    test_kit.Runner=SandboxRunner
    result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(test_kit.PairedKitTests))
    report={'tests':result.testsRun,'errors':len(result.errors),'failures':len(result.failures),'actual_code_node_executions':calls,
            'sandbox_image':'langgenius/dify-sandbox:0.2.12','visual_model':'controlled fixtures; actual Qwen calls 0'}
    Path('validation_sandbox.json').write_text(json.dumps(report,indent=2))
    raise SystemExit(not result.wasSuccessful())


if __name__=='__main__':main()
