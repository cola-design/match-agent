import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from recognize import recognize
from test_recognize import fixture
from vision import prepare,validate,run,collect


def unit():
    return {"unit_id":"p1-region1","image":"region.png","context":{"native_or_ocr_anchors":[
        {"id":"N1","raw":"MCB-C16A/1P","source":"native_pdf_text","literal_parameters":{"rated_current_a":16}}]}}


def reply():
    return {"readable":True,"coordinate_system":"normalized_1000","drawing_type":"single_line",
            "items":[{"id":"x1","kind":"MCB","observed_label":"MCB-C16A/1P","native_anchor_ids":["N1"],
                      "bbox":[100,200,300,400],"explicit_parameters":{"rated_current_a":16}}],
            "connections":[],"notes":[],"recognition_gaps":[]}


class VisionValidationTests(unittest.TestCase):
    def test_explicit_thousand_coordinates_normalize(self):
        r=validate(json.dumps(reply()),unit())
        self.assertEqual(r["data"]["items"][0]["bbox"],[.1,.2,.3,.4])
        self.assertEqual(r["data"]["items"][0]["bbox_raw"],[100,200,300,400])

    def test_explicit_unit_coordinates_supported(self):
        r=reply();r["coordinate_system"]="normalized";r["items"][0]["bbox"]=[.1,.2,.3,.4]
        self.assertEqual(validate(json.dumps(r),unit())["data"]["items"][0]["bbox"],[.1,.2,.3,.4])

    def test_undeclared_units_rejected(self):
        r=reply();r.pop("coordinate_system")
        with self.assertRaises(ValueError):validate(json.dumps(r),unit())

    def test_truncation_not_salvaged(self):
        with self.assertRaises(ValueError):validate(json.dumps(reply())[:-12],unit())

    def test_wrong_native_reference_rejected(self):
        r=reply();r["items"][0]["native_anchor_ids"]=["another-region"]
        with self.assertRaises(ValueError):validate(json.dumps(r),unit())

    def test_native_parameter_conflict_preserves_both_values(self):
        r=reply();r["items"][0]["explicit_parameters"]["rated_current_a"]=32
        u=unit();original=copy.deepcopy(u);v=validate(json.dumps(r),u)
        self.assertEqual(v["validation_conflicts"][0]["native_value"],16)
        self.assertEqual(v["validation_conflicts"][0]["model_value"],32)
        self.assertEqual(u,original)

    def test_bad_edge_reference_rejected(self):
        r=reply();r["connections"]=[{"from_item":"x1","to_item":"missing","bbox":[1,2,3,4]}]
        with self.assertRaises(ValueError):validate(json.dumps(r),unit())

    def test_model_cannot_silently_change_mcb_to_mccb(self):
        r=reply();r['items'][0]['observed_label']='MCCB-C16A/1P'
        v=validate(json.dumps(r),unit())
        self.assertIn('model_native_transcription_conflict',{x['type'] for x in v['validation_conflicts']})

    def test_pole_alias_does_not_bypass_literal_comparison(self):
        u=unit();u['context']['native_or_ocr_anchors'][0]['literal_parameters']['pole_marking']='3P'
        r=reply();r['items'][0]['explicit_parameters']['pole_count']=2
        v=validate(json.dumps(r),u)
        self.assertIn('pole_marking',{x.get('field') for x in v['validation_conflicts']})

    def test_ats_ii_is_not_native_support_for_two_poles(self):
        u=unit();a=u['context']['native_or_ocr_anchors'][0];a.update(raw='ATS-63A-II',literal_parameters={'rated_current_a':63})
        r=reply();r['items'][0].update(observed_label='ATS-63A-II',explicit_parameters={'rated_current_a':63,'pole_count':2})
        v=validate(json.dumps(r),u)
        self.assertIn('pole_marking_without_native_support',{x['type'] for x in v['validation_conflicts']})

    def test_nonfinite_position_rejected(self):
        r=reply();r["items"][0]["bbox"][0]=float("nan")
        with self.assertRaises(ValueError):validate(json.dumps(r),unit())

    def test_duplicate_instance_ids_rejected(self):
        r=reply();r["items"].append(copy.deepcopy(r["items"][0]))
        with self.assertRaises(ValueError):validate(json.dumps(r),unit())


class VisionRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        (self.folder/"region.png").write_bytes(b"fixture-image")
        self.manifest={"units":[unit()]}
    def tearDown(self):self.temp.cleanup()
    def response(self):return io.BytesIO(json.dumps({"choices":[{"message":{"content":json.dumps(reply())}}]}).encode())

    def test_bounded_retry_and_no_key_in_outputs(self):
        bad=io.BytesIO(json.dumps({"choices":[{"message":{"content":"{broken"}}]}).encode())
        with patch("vision.urllib.request.urlopen",side_effect=[bad,self.response()]) as call:
            r=run(self.manifest,self.folder,"secret-test-only","https://example.invalid/v1","fixture-model")
            self.assertEqual(call.call_count,2)
        self.assertEqual(r["actual_model_calls"],2)
        self.assertFalse(r["recognition_accuracy_verified"])
        for f in self.folder.rglob("*.json"):self.assertNotIn("secret-test-only",f.read_text())

    def test_unauthorized_stops_without_retrying_all_regions(self):
        self.manifest["units"]*=3
        error=urllib.error.HTTPError("https://example.invalid",401,"unauthorized",{},None)
        with patch("vision.urllib.request.urlopen",side_effect=error) as call:
            r=run(self.manifest,self.folder,"key","https://example.invalid/v1","fixture-model")
        self.assertEqual(call.call_count,1);self.assertEqual(r["unprocessed_units"],2)

    def test_same_model_resumes_and_other_model_keeps_separate_artifacts(self):
        with patch("vision.urllib.request.urlopen",side_effect=[self.response(),self.response()]) as call:
            run(self.manifest,self.folder,"key","https://example.invalid/v1","one")
            cached=run(self.manifest,self.folder,"key","https://example.invalid/v1","one")
            run(self.manifest,self.folder,"key","https://example.invalid/v1","two")
        self.assertEqual(call.call_count,2);self.assertEqual(cached["actual_model_calls"],0)
        self.assertEqual(len(list((self.folder/"responses").glob("*/p1-region1.json"))),2)

    def test_url_credentials_rejected_before_request(self):
        with patch("vision.urllib.request.urlopen") as call:
            with self.assertRaises(ValueError):run(self.manifest,self.folder,"key","https://u:p@example.invalid/v1","one")
        call.assert_not_called()

    def test_no_key_is_not_a_fake_model_test(self):
        with patch("vision.urllib.request.urlopen") as call:
            with self.assertRaises(ValueError):run(self.manifest,self.folder,"","https://example.invalid/v1","one")
        call.assert_not_called()


class PreparationTests(unittest.TestCase):
    def test_regions_cover_all_native_anchors_and_images_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);f=root/"drawing.pdf";fixture(f);r=recognize(f)
            p=prepare(f,r,root/"vision")
            self.assertEqual(p["model_calls"],0)
            covered={a["id"] for u in p["units"] for a in u["context"]["native_or_ocr_anchors"]}
            self.assertEqual(covered,{a["id"] for page in r["pages"] for a in page["annotations"]})
            self.assertTrue(all((root/"vision"/u["image"]).is_file() for u in p["units"]))


class CollectionTests(unittest.TestCase):
    def setUp(self):
        from openpyxl import Workbook
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.folder=self.root/"vision";self.folder.mkdir()
        self.base="https://example.invalid/v1";self.model="fixture-model"
        rid=hashlib.sha256((self.base+"\n"+self.model).encode()).hexdigest()[:12]
        self.responses=self.folder/"responses"/rid;self.responses.mkdir(parents=True)
        self.native={"pages":[{"annotations":[{"id":"N1","kind":"device_model","raw":"MCB-C16A/1P"}]}]}
        (self.root/"recognition.json").write_text(json.dumps(self.native))
        wb=Workbook();wb.active.title="原生证据";wb.save(self.root/"recognition.xlsx")
        self.u=unit();self.u.update(page=1,bbox_page=[10,20,110,220])
    def tearDown(self):self.temp.cleanup()
    def save(self,u,response):
        data={"status":"response_validated","model":self.model,**validate(json.dumps(response),u)}
        (self.responses/(u["unit_id"]+".json")).write_text(json.dumps(data))
    def gather(self,units):
        collect({"units":units},self.folder,self.base,self.model)
        return json.loads((self.root/"recognition_with_vision.json").read_text())

    def test_same_anchor_in_overlapping_regions_deduplicates_and_native_is_immutable(self):
        self.save(self.u,reply());other=copy.deepcopy(self.u);other["unit_id"]="p1-region2";self.save(other,reply())
        r=self.gather([self.u,other])
        self.assertEqual(len(r["vision"]["items"]),1)
        self.assertEqual(r["vision"]["items"][0]["bbox_page"],[20,60,40,100])
        self.assertIsNone(r["vision"]["items"][0]["physical_quantity"])
        self.assertEqual(r["native_extraction"],self.native)
        self.assertEqual(json.loads((self.root/"recognition.json").read_text()),self.native)
        from openpyxl import load_workbook
        wb=load_workbook(self.root/"recognition_with_vision.xlsx")
        self.assertIn("原生证据",wb.sheetnames);self.assertIn("视觉连接待核对",wb.sheetnames);wb.close()

    def test_same_model_label_without_same_evidence_is_not_merged(self):
        response=reply();other=copy.deepcopy(response["items"][0]);other.update(id="x2",native_anchor_ids=[],bbox=[600,200,800,400])
        response["items"].append(other);self.save(self.u,response)
        self.assertEqual(len(self.gather([self.u])["vision"]["items"]),2)

    def test_conflicting_overlapping_predictions_are_kept_for_review(self):
        self.save(self.u,reply());other=copy.deepcopy(self.u);other["unit_id"]="p1-region2"
        response=reply();response["items"][0]["explicit_parameters"]["rated_current_a"]=32;self.save(other,response)
        r=self.gather([self.u,other])["vision"]
        self.assertEqual(len(r["items"]),2);self.assertEqual(r["status"],"requires_further_processing")
        self.assertIn("overlap_disagreement",{x["type"] for x in r["conflicts"]})

    def test_unreadable_and_reported_gaps_survive_collection(self):
        response=reply();response["readable"]=False;response["recognition_gaps"]=["junction not visible"]
        response["items"][0]["recognition_gaps"]=["terminal marking unclear"];self.save(self.u,response)
        r=self.gather([self.u])["vision"]
        self.assertEqual(len(r["recognition_gaps"]),3);self.assertEqual(r["status"],"requires_further_processing")
        self.assertFalse(r["engineering_verified"])

    def test_missing_response_is_visible_without_model_calls(self):
        r=self.gather([self.u])["vision"]
        self.assertEqual(r["failed_or_unprocessed_units"],[{"unit_id":self.u["unit_id"],"status":"not_processed"}])
        self.assertEqual(r["unassociated_native_device_markings"],["N1"])


if __name__=="__main__":unittest.main()
