import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz
from fastapi.testclient import TestClient
from PIL import Image
from app import create_app
from recognize import recognize
from test_vision import reply


class APIContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.client=TestClient(create_app(self.root,'fixture-key',ocr_mode='off'))
        self.headers={'X-API-Key':'fixture-key'}
        self.pid=self.client.post('/v1/projects',json={},headers=self.headers).json()['project_id']
        self.path='/v1/projects/'+self.pid
    def tearDown(self):self.client.close();self.tmp.cleanup()

    def test_private_files_require_tool_authentication(self):
        self.assertEqual(self.client.get(self.path+'/batch').status_code,401)
        self.assertEqual(self.client.get('/v1/projects/../../etc/passwd/batch',headers=self.headers).status_code,404)

    def test_invalid_pdf_leaves_explicit_failed_source(self):
        r=self.client.post(self.path+'/sources',headers=self.headers,files={'file':('bad.pdf',b'not a pdf')})
        self.assertEqual(r.status_code,422)
        self.assertEqual(len(self.client.get(self.path+'/batch',headers=self.headers).json()['failed_sources']),1)

    def test_source_parse_and_result_download_do_not_invoke_cloud_model(self):
        doc=fitz.open();doc.new_page().insert_text((50,50),'ATS-63A-II')
        with patch('vision.urllib.request.urlopen') as cloud:
            self.assertEqual(self.client.post(self.path+'/sources',headers=self.headers,files={'file':('a.pdf',doc.tobytes())}).status_code,200)
            r=self.client.get(self.path+'/files/extraction.json',headers=self.headers)
        cloud.assert_not_called();self.assertFalse(r.json()['summary']['recognition_accuracy_verified'])
        self.assertGreater(r.json()['summary']['pending_units'],0)

    def test_failed_regions_can_be_retried_without_reprocessing_good_regions(self):
        doc=fitz.open();doc.new_page().insert_text((50,50),'MCB-C16A/1P')
        self.client.post(self.path+'/sources',headers=self.headers,files={'file':('a.pdf',doc.tobytes())})
        context=self.client.get(self.path+'/batch',headers=self.headers).json()['batch'][0];uid=context['unit_id']
        self.client.post(self.path+'/results',headers=self.headers,json={'unit_id':uid,'failed':True})
        self.assertEqual(self.client.get(self.path+'/batch',headers=self.headers).json()['batch'],[])
        self.assertEqual(self.client.post(self.path+'/retry-failed',headers=self.headers).json()['retry_units'],1)
        self.assertEqual(self.client.get(self.path+'/batch',headers=self.headers).json()['batch'][0]['unit_id'],uid)

    def test_image_pdf_with_native_title_is_still_flagged_for_ocr(self):
        data=io.BytesIO();Image.new('RGB',(300,200),'white').save(data,format='PNG')
        doc=fitz.open();pg=doc.new_page(width=300,height=300);pg.insert_text((20,20),'Drawing')
        pg.insert_image(fitz.Rect(10,50,290,290),stream=data.getvalue())
        file=self.root/'scan.pdf';doc.save(file)
        r=recognize(file,ocr_mode='off')
        self.assertIn('ocr_required',{x['type'] for x in r['issues']})


if __name__=='__main__':unittest.main()
