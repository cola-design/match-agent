"""Load real plugin registrations and invoke both tools with SDK File objects."""
import io
import json
import os
from pathlib import Path
import sys
import unittest

from dify_plugin import DifyPluginEnv
from dify_plugin.core.plugin_registration import PluginRegistration
from dify_plugin.entities.tool import ToolRuntime
from dify_plugin.file.file import File
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'plugin'))
from tools.prepare_pdf import PreparePdfTool
from tools.export_selection import ExportSelectionTool
from library.reader import dumps
from test_v4 import fixture, controlled_records


class SDKTests(unittest.TestCase):
    def test_registration_loads_provider_both_tools_and_assets(self):
        old = os.getcwd()
        try:
            os.chdir(ROOT / 'plugin')
            registration = PluginRegistration(DifyPluginEnv())
            self.assertEqual(len(registration.tools_configuration), 1)
            self.assertEqual(len(registration.tools_configuration[0].tools), 2)
            self.assertEqual(registration.files[0].filename, 'icon.svg')
        finally: os.chdir(old)

    def test_prepare_and_export_use_real_sdk_file_and_message_types(self):
        data = fixture(2)
        f = File(url='https://example.invalid/offline-fixture.pdf', filename='fixture.pdf', type='document', size=len(data))
        f._blob = data
        runtime = ToolRuntime(credentials={}, user_id=None, session_id=None)
        prepared = list(PreparePdfTool(runtime=runtime, session=None).invoke({'pdf_files': [f]}))
        jsons = [m.message.json_object for m in prepared if m.type.value == 'json']
        self.assertEqual(len(jsons), 1); self.assertIs(jsons[0]['success'], True)
        manifest = jsons[0]['manifest']
        names = [m.meta['filename'] for m in prepared if m.type.value == 'blob']
        self.assertEqual(names, [u['image_name'] for u in manifest['units']])
        exported = list(ExportSelectionTool(runtime=runtime, session=None).invoke({
            'pdf_files': [f], 'vision_results': dumps(controlled_records(manifest)), 'selection_context': ''}))
        js = next(m.message.json_object for m in exported if m.type.value == 'json')
        self.assertIs(js['success'], True)
        files = [m for m in exported if m.type.value == 'blob']
        self.assertEqual(len(files), 2)
        wb = load_workbook(io.BytesIO(files[0].message.blob))
        self.assertEqual(wb['选型信息'].max_row, 7)
        r = json.loads(files[1].message.blob)
        self.assertEqual(len(r['source_evidence']), 6)
        self.assertFalse(r['summary']['selection_approved'])


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(SDKTests))
    (ROOT / 'validation_sdk.json').write_text(json.dumps({
        'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'sdk_version': '0.4.5', 'actual_model_calls': 0,
        'scope': 'Actual SDK registration, File objects and tool message generation; offline, not installed in user Dify.'
    }, indent=2))
    raise SystemExit(not result.wasSuccessful())
