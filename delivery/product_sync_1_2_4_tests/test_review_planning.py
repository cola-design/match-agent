import copy
import csv
import gzip
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from bson import json_util

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from product_sync.sync import clean_product,decide,planned_after,change_details,create_plan,conflict_details


def raw(**fields):
    return {'ITEM_NUM':'fixture-001','ITEM_ID':1,'ITEM_DESC':'fixture product',
        'CATEGORY_CODE':'335.1','CATEGORY_NAME':'fixture',
        'SPEC_STRING':'分断能力&L 25kA@额定电流&100A','STATUS':'有效',
        'LAST_UPDATE_DATE':'2026-05-17 00:00:00.0',**fields}


def attr(value='L 35kA'):
    return {'ATTR_NAME':'分断','ATTR_VALUE':value,'ATTR_ID':'1','SA_ID':'2',
        'STATUS':'有效','LAST_UPDATE_DATE':'2026-05-17 00:00:00.0'}


def active(attributes=None):
    product,version=clean_product(raw(),attributes or [])
    product.update(data_status='ACTIVE',publication_basis='AUTO_RULE_V1',sync_meta={'type2_version':version})
    return product


class ReviewPlanningTest(unittest.TestCase):
    def test_preexisting_disagreement_is_quality_only_and_still_requires_review(self):
        old=active([attr()]);old['quality_flags']=[]
        candidate,version=clean_product(raw(),[attr()])
        action,after=decide(old,candidate,version,'test')
        self.assertEqual(action,'quality_only')
        self.assertEqual(after['data_status'],'PENDING_REVIEW')
        self.assertEqual(after['publication_basis'],'SYNC_QUALITY_REQUIRES_REVIEW_V1')
        details=change_details(old,after)
        self.assertEqual(details['source_changed_fields'],[])
        self.assertTrue(details['preexisting_conflicts'])
        self.assertEqual(details['conflicts'][0]['type1_values'],['L 25kA'])
        self.assertEqual(details['conflicts'][0]['type2_values'],['L 35kA'])
        self.assertEqual(decide(after,candidate,version,'again'),('unchanged',None))

    def test_removed_automatic_flag_preserves_approval_without_auto_approving_pending(self):
        for status in ('ACTIVE','PENDING_REVIEW','INACTIVE'):
            old=active();old['data_status']=status;old['quality_flags']=['TYPE1_TYPE2_ATTRIBUTE_CONFLICT']
            action,after=decide(old,*clean_product(raw(),[]),'test')
            self.assertEqual(action,'quality_only')
            self.assertEqual(after['data_status'],status)
            self.assertEqual(after['publication_basis'],'AUTO_RULE_V1')

    def test_unknown_manual_quality_flags_are_preserved(self):
        old=active();old['quality_flags']=['MANUAL_ENGINEERING_HOLD']
        self.assertEqual(decide(old,*clean_product(raw(),[]),'test'),('unchanged',None))

    def test_business_change_with_equal_version_requires_review(self):
        action,after=decide(active(),*clean_product(raw(ITEM_DESC='changed product'),[]),'test')
        self.assertEqual(action,'changed')
        self.assertEqual(after['data_status'],'PENDING_REVIEW')
        self.assertEqual(change_details(active(),after)['source_changed_fields'],['description'])

    def test_metadata_plan_has_no_business_or_approval_fields(self):
        action,after=decide(active(),*clean_product(raw(LAST_UPDATE_DATE='2026-05-18 00:00:00.0'),[]),'test')
        write=planned_after(action,after)
        self.assertEqual(action,'metadata_only')
        self.assertEqual(set(write),{'material_code','last_update_date','provenance','sync_meta'})

    def test_quality_plan_has_no_business_parameter_fields(self):
        old=active([attr()]);old['quality_flags']=[]
        action,after=decide(old,*clean_product(raw(),[attr()]),'test')
        write=planned_after(action,after)
        self.assertNotIn('source_attributes',write)
        self.assertNotIn('standard_specs',write)
        self.assertNotIn('description',write)
        self.assertEqual(write['quality_flags'],['TYPE1_TYPE2_ATTRIBUTE_CONFLICT'])

    def test_ambiguous_label_is_reported_without_inventing_equivalence(self):
        product,_=clean_product(raw(SPEC_STRING='分断能力&L 80kA'),[attr('常规分断 80kA')])
        detail=conflict_details(product)[0]
        self.assertEqual(detail['reason'],'label_or_qualifier_difference')
        self.assertIn('TYPE1_TYPE2_ATTRIBUTE_CONFLICT',product['quality_flags'])

    def test_real_numeric_disagreement_remains_explicit(self):
        for before,after in (('L 25kA','L 35kA'),('M 35kA','M 50kA')):
            product,_=clean_product(raw(SPEC_STRING='分断能力&'+before),[attr(after)])
            detail=conflict_details(product)[0]
            self.assertEqual(detail['reason'],'numeric_value_difference')

    def plan(self,old,row,attributes=()):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);folder=Path(temp.name)
        stage=sqlite3.connect(':memory:');self.addCleanup(stage.close)
        stage.executescript('create table type1(code text,body text);create table type2(code text,body text);')
        stage.execute('insert into type1 values(?,?)',(row['ITEM_NUM'],json.dumps(row)))
        for a in attributes:stage.execute('insert into type2 values(?,?)',(row['ITEM_NUM'],json.dumps(a)))
        class Collection:
            def find(self,query):return [copy.deepcopy(old)]
        impact={};path,counts=create_plan(Collection(),stage,folder,'test',impact)
        with gzip.open(path,'rt') as f:entries=[json_util.loads(s) for s in f]
        return folder,entries,counts,impact

    def test_plan_counts_and_csv_explain_quality_only(self):
        old=active([attr()]);old.update(_id='fixture-id',quality_flags=[])
        folder,entries,counts,impact=self.plan(old,raw(),[attr()])
        self.assertEqual(counts,{'quality_only':1,'scanned':1})
        self.assertEqual(impact['active_to_pending_review'],1)
        self.assertEqual(impact['preexisting_conflict_products'],1)
        self.assertEqual(impact['numeric_conflict_products'],1)
        self.assertNotIn('source_attributes',entries[0]['after'])
        issue=json.loads((folder/'issues.jsonl').read_text())
        self.assertEqual(issue['change_details']['source_changed_fields'],[])
        with (folder/'review_items.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.reader(f))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1][4:7],['L 25kA','L 35kA','数值不一致'])

    def test_metadata_plan_versions_and_no_review_rows(self):
        old=active();old['_id']='fixture-id'
        folder,entries,counts,impact=self.plan(old,raw(LAST_UPDATE_DATE='2026-05-18 00:00:00.0'))
        self.assertEqual(counts,{'metadata_only':1,'scanned':1})
        self.assertEqual(impact['metadata_version_reasons'],{'product_newer;attributes_both_unknown':1})
        self.assertNotIn('data_status',entries[0]['after'])
        self.assertEqual(len((folder/'review_items.csv').read_text(encoding='utf-8-sig').splitlines()),1)

    def test_skipped_unknown_date_is_explained(self):
        old=active();old['_id']='fixture-id'
        folder,entries,counts,impact=self.plan(old,raw(LAST_UPDATE_DATE='unparseable'))
        self.assertEqual(entries,[])
        issue=json.loads((folder/'issues.jsonl').read_text())
        self.assertEqual(issue['version_relation'],'incoming_unknown')
        self.assertEqual(issue['version_field'],'last_update_date')

    def test_csv_neutralizes_source_formula_text(self):
        old=active();old['_id']='fixture-id'
        folder,entries,counts,impact=self.plan(old,raw(ITEM_DESC='=HYPERLINK("https://example.invalid")'))
        with (folder/'review_items.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.reader(f))
        self.assertTrue(rows[1][1].startswith("'="))

    def test_csv_keeps_numeric_material_codes_as_text_and_plan_code_unchanged(self):
        old=active();old.update(_id='fixture-id',material_code='00123456')
        folder,entries,counts,impact=self.plan(old,raw(ITEM_NUM='00123456',ITEM_DESC='new description'))
        with (folder/'review_items.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.reader(f))
        self.assertEqual(rows[1][0],"'00123456")
        self.assertEqual(entries[0]['material_code'],'00123456')


if __name__=='__main__':unittest.main()
