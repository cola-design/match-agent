import unittest
from schedules import extract


def table_fixture(above=False,bad_quantity=False,footer=False):
    a=[];g=[]
    def text(raw,x,y):
        a.append({"id":str(len(a)+1),"raw":raw,"bbox":[x-5,y-4,x+5,y+4],"font_size":8,"source":"native_pdf_text"})
    for i,x in enumerate((10,110,310,370)):
        # Deliberately split each vertical border at row intersections.
        for start,end in ((20,40),(40,60),(60,80)):
            g.append({"axis":"v","value":x,"lo":start,"hi":end,"id":"v"+str(len(g))})
    for y in (20,40,60,80):
        for x1,x2 in ((10,110),(110,310),(310,370)):
            g.append({"axis":"h","value":y,"lo":x1,"hi":x2,"id":"h"+str(len(g))})
    hy=70 if above else 30
    for raw,x in (("名称",60),("型号规格",210),("数量",340)):text(raw,x,hy)
    for name,model,quantity,y in (("开关","S-10","?" if bad_quantity else "2",50),
                                 ("互感器","CT-20","3",30 if above else 70)):
        for raw,x in ((name,60),(model,210),(quantity,340)):text(raw,x,y)
    if footer:
        for y in (100,120):g.append({"axis":"h","value":y,"lo":10,"hi":370,"id":"footer"+str(y)})
        text("图号",60,110);text("AA",210,110);text("1",340,110)
    return a,g


class ScheduleTests(unittest.TestCase):
    def test_header_above_body_and_fragmented_grid(self):
        tables=extract(*table_fixture());self.assertEqual(len(tables),1)
        self.assertEqual([r["quantity_literal"] for r in tables[0]["rows"]],[2,3])
        self.assertEqual(tables[0]["body_direction"],"below")

    def test_engineering_table_header_can_be_below_body(self):
        t=extract(*table_fixture(above=True))[0]
        self.assertEqual(t["body_direction"],"above")
        self.assertEqual([(r["name_raw"],r["quantity_literal"]) for r in t["rows"]],[("开关",2),("互感器",3)])

    def test_bad_quantity_kept_as_gap_not_zero_or_dropped(self):
        t=extract(*table_fixture(bad_quantity=True))[0]
        self.assertEqual(len(t["rows"]),2)
        self.assertIsNone(t["rows"][0]["quantity_literal"])
        self.assertEqual(t["rows"][0]["cells"]["quantity"]["raw"],"?")

    def test_title_block_below_table_does_not_become_equipment(self):
        t=extract(*table_fixture(footer=True))[0]
        self.assertEqual(len(t["rows"]),2)
        self.assertNotIn("图号",[r["name_raw"] for r in t["rows"]])


if __name__=="__main__":unittest.main()
