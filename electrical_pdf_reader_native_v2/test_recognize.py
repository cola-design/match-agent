import json
from pathlib import Path
import tempfile
import unittest

import fitz

from recognize import decode,recognize,write_reports


def fixture(path,numbers=(21,33,47),models=None,with_bus=True,with_symbols=True,separate_leak=True):
    models=models or ["MCB-C10A/1P","RCB/C16A/1P+N 30mA","MCB-C32A/4P"]
    d=fitz.open();p=d.new_page(width=800,height=500)
    if with_bus:p.draw_line((100,95),(100,245),width=.6)
    for i,(number,model) in enumerate(zip(numbers,models)):
        y=110+i*55
        p.insert_text((103,y-8),"L"+str(i%3+1),fontsize=10)
        p.insert_text((145,y-8),model,fontsize=10)
        p.insert_text((500,y-5),str(number)+"#",fontsize=10)
        p.insert_text((530,y+4),"LOAD"+str(i+1),fontsize=10)
        p.draw_line((100,y),(170,y),width=.6)
        p.draw_line((190,y),(515,y),width=.6)
        if with_symbols:p.draw_line((170,y+7),(190,y),width=.6)
    if separate_leak:p.insert_text((205,110+(len(numbers)-1)*55+12),"30mA",fontsize=10)
    d.save(path)


class LiteralMeaningTests(unittest.TestCase):
    def test_current_and_residual_are_distinct(self):
        p=decode("RCB/C16A/1P+N 30mA")["explicit_parameters"]
        self.assertEqual((p["rated_current_a"],p["residual_current_ma"],p["pole_marking"]),(16,30,"1P+N"))

    def test_ats_suffix_does_not_infer_poles(self):
        p=decode("ATS-63A-II")["explicit_parameters"]
        self.assertEqual(p["rated_current_a"],63)
        self.assertNotIn("pole_marking",p)

    def test_ma_trip_marking_is_not_leakage(self):
        p=decode("MCCB100N-MA50A/3P")["explicit_parameters"]
        self.assertEqual(p["rated_current_a"],50)
        self.assertNotIn("residual_current_ma",p)
        self.assertNotIn("trip_curve",p)

    def test_cable_conduit_and_sheath(self):
        p=decode("WDZB-YJY-5x10-JDG40-WC")["explicit_parameters"]
        self.assertIn("聚烯烃护套",p["family_meaning"])
        self.assertEqual(p["containment"][0]["meaning"],"套接紧定式钢导管")

    def test_cat5_not_upgraded_to_cat5e(self):
        self.assertEqual(decode("UTP/CAT5")["explicit_parameters"]["category_raw"],"CAT5")

    def test_ocr_joined_catalogue_numbers_are_not_huge_current(self):
        self.assertNotIn("rated_current_a",decode("RDM5-250L/4300160A")["explicit_parameters"])
        self.assertEqual(decode("RDM5-250L/4300 160A")["explicit_parameters"]["rated_current_a"],160)


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def source(self,**kwargs):
        p=self.root/"input.pdf";fixture(p,**kwargs);return p,recognize(p)

    def test_nonsequential_circuit_numbers_not_array_indices(self):
        _,r=self.source();p=r["pages"][0];lookup={a["id"]:a for a in p["annotations"]}
        self.assertEqual([b["circuit_tag"] for b in p["branches"]],["21#","33#","47#"])
        self.assertEqual([[lookup[x]["raw"] for x in b["device_annotation_ids"]] for b in p["branches"]],
                         [["MCB-C10A/1P"],["RCB/C16A/1P+N 30mA"],["MCB-C32A/4P"]])

    def test_independent_residual_parameter_has_owner(self):
        _,r=self.source();p=r["pages"][0];a={a["id"]:a for a in p["annotations"]}
        links=p["parameter_links"]
        self.assertEqual(len(links),1)
        self.assertEqual(a[links[0]["target_anchor"]]["raw"],"MCB-C32A/4P")
        self.assertEqual(links[0]["parameters"]["residual_current_ma"],30)

    def test_repeated_models_keep_distinct_instances(self):
        _,r=self.source(models=["MCB-C16A/1P"]*3)
        found=[x for x in r["pages"][0]["diagram_instances"] if x["raw_label"]=="MCB-C16A/1P"]
        self.assertEqual(len(found),3)
        self.assertEqual(len({x["id"] for x in found}),3)
        self.assertTrue(all(x["physical_quantity"] is None for x in found))

    def test_without_bus_text_rows_are_not_topology(self):
        _,r=self.source(with_bus=False)
        self.assertFalse(r["pages"][0]["branches"])
        self.assertIn("unresolved_bus",[x["type"] for x in r["issues"]])

    def test_table_lines_not_misread_as_switching_circuits(self):
        _,r=self.source(with_symbols=False)
        self.assertFalse(r["pages"][0]["branches"])
        self.assertIn("circuit_symbol_support_missing",[x["type"] for x in r["issues"]])

    def test_transform_coordinates_and_overlay_all_rotations(self):
        base=self.root/"base.pdf";fixture(base)
        for angle in (0,90,180,270):
            with self.subTest(angle=angle):
                d=fitz.open(base);d[0].set_rotation(angle);f=self.root/f"r{angle}.pdf";d.save(f)
                r=recognize(f);p=r["pages"][0];w,h=p["size"][2:]
                for a in p["annotations"]:
                    self.assertTrue(-.1<=a["bbox"][0]<=a["bbox"][2]<=w+.1)
                    self.assertTrue(-.1<=a["bbox"][1]<=a["bbox"][3]<=h+.1)
                write_reports(r,f,self.root/f"out{angle}")
                saved=fitz.open(self.root/f"out{angle}"/"source_with_evidence.pdf")
                self.assertGreater(sum(1 for _ in saved[0].annots()),0)

    def test_every_page_processed(self):
        base=self.root/"base.pdf";fixture(base)
        d=fitz.open(base);multi=fitz.open();multi.insert_pdf(d);multi.insert_pdf(d);f=self.root/"multi.pdf";multi.save(f)
        r=recognize(f)
        self.assertEqual(r["summary"]["associated_circuits"],6)
        self.assertEqual(len({a["id"] for p in r["pages"] for a in p["annotations"]}),sum(len(p["annotations"]) for p in r["pages"]))

    def test_report_is_not_engineering_acceptance(self):
        _,r=self.source()
        self.assertFalse(r["engineering_verified"])
        self.assertFalse(r["summary"]["recognition_complete"])
        self.assertFalse(r["summary"]["purchase_quantities_confirmed"])

    def test_scanned_pdf_is_flagged_for_ocr(self):
        d=fitz.open();p=d.new_page(width=300,height=200);p.insert_image(p.rect,stream=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,20,20),False).tobytes("png"))
        f=self.root/"scan.pdf";d.save(f);r=recognize(f)
        self.assertIn("ocr_required",[i["type"] for i in r["issues"]])
        self.assertEqual(r["summary"]["native_annotations"],0)

    def test_output_is_json_serializable(self):
        _,r=self.source();json.dumps(r,allow_nan=False)


if __name__=="__main__":unittest.main()
