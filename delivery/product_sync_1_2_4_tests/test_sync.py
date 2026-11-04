import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
import io
import urllib.error

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from product_sync.source import stage_snapshot, SyncError, request_page
from product_sync.sync import clean_product, decide, signature, stamp, run_lock
from product_sync.cleaning import parse_page


def raw(code='00123456', **fields):
    row={'ITEM_NUM':code,'ITEM_ID':123,'ITEM_DESC':'TGB1N-125H 4P C100','CATEGORY_NAME':'小型断路器类.小型断路器.TGB1N(祥云3.0)',
         'CATEGORY_CODE':'335.33501.3350165','SPEC_STRING':'额定电流&100A@极数&4P@额定电压&AC380V',
         'STATUS':'有效','LAST_UPDATE_DATE':'2026-10-20 00:00:00.0'}
    row.update(fields);return row


def active():
    product,version=clean_product(raw(),[])
    product.update(data_status='ACTIVE',publication_basis='AUTO_RULE_V1')
    return product


class SyncTest(unittest.TestCase):
    def test_cleaning_shared_semantics_and_numbers(self):
        self.assertEqual(parse_page(b'\xef\xbb\xbf{"status":true,"data":[]}')['data'],[])
        product,_=clean_product(raw(),[])
        self.assertEqual(product['material_code'],'00123456')
        self.assertEqual(product['category']['leaf'],'TGB1N(祥云3.0)')
        self.assertEqual(product['standard_specs']['rated_voltage']['value_num'],380)
        product,_=clean_product(raw(SPEC_STRING='额定电压&AC230/400V'),[])
        self.assertNotIn('value_num',product['standard_specs']['rated_voltage'])

    def test_old_records_are_noops(self):
        old=active();candidate,version=clean_product(raw(),[])
        action,patch=decide(old,candidate,version,'r1')
        self.assertEqual(action,'unchanged');self.assertIsNone(patch)

    def test_source_timestamp_formats_compare_equal(self):
        self.assertEqual(stamp('2026-10-20 00:00:00.0'),stamp('2026-10-20T00:00:00'))
        old=active();old['last_update_date']='2026-10-20T00:00:00'
        self.assertEqual(decide(old,*clean_product(raw(),[]),'r1')[0],'unchanged')

    def test_source_order_does_not_change_content(self):
        old=active();candidate,version=clean_product(raw(SPEC_STRING='极数&4P@额定电压&AC380V@额定电流&100A'),[])
        self.assertEqual(signature(old),signature(candidate))
        self.assertEqual(decide(old,candidate,version,'r1')[0],'unchanged')

    def test_new_and_changed_products_require_review(self):
        candidate,version=clean_product(raw(ITEM_DESC='new description'),[])
        for old in (None,active()):
            action,patch=decide(old,candidate,version,'r1')
            self.assertIn(action,('new','changed'))
            self.assertEqual(patch['data_status'],'PENDING_REVIEW')
        old=active();old['data_status']='INACTIVE'
        self.assertEqual(decide(old,candidate,version,'r1')[1]['data_status'],'INACTIVE')

    def test_newer_timestamp_alone_preserves_approval_and_is_idempotent(self):
        candidate,version=clean_product(raw(LAST_UPDATE_DATE='2026-10-21T00:00:00'),[])
        action,patch=decide(active(),candidate,version,'r1')
        self.assertEqual(action,'metadata_only');self.assertEqual(patch['data_status'],'ACTIVE')
        self.assertEqual(decide(patch,candidate,version,'r2')[0],'unchanged')

    def test_older_data_cannot_overwrite_newer_data(self):
        candidate,version=clean_product(raw(ITEM_DESC='older',LAST_UPDATE_DATE='2026-10-19T00:00:00'),[])
        self.assertEqual(decide(active(),candidate,version,'r1')[0],'older_or_unknown_source_version')

    def test_status_is_snapshot_and_can_recover_without_deleting(self):
        candidate,version=clean_product(raw(STATUS='无效'),[])
        action,patch=decide(active(),candidate,version,'r1')
        self.assertEqual(action,'changed');self.assertEqual(patch['source_status'],'无效')
        self.assertEqual(patch['data_status'],'PENDING_REVIEW')
        restored,_=clean_product(raw(),[])
        _,again=decide(patch,restored,version,'r2')
        self.assertEqual(again['source_status'],'有效');self.assertEqual(again['data_status'],'PENDING_REVIEW')

    def test_manual_fields_are_not_implicitly_reowned(self):
        old=active();old['manual_override']=True
        self.assertEqual(decide(old,*clean_product(raw(ITEM_DESC='changed'),[]),'r1')[0],'manual_override')
        old=active();old['source_attributes'].append({'name':'custom','value':'x','source':'manual'})
        self.assertEqual(decide(old,*clean_product(raw(),[]),'r1')[0],'manual_attributes_require_review')

    def test_type2_conflicts_and_updates_are_detected(self):
        attr={'ATTR_NAME':'额定电流','ATTR_VALUE':'63A','STATUS':'有效','LAST_UPDATE_DATE':'2026-10-21T00:00:00'}
        product,version=clean_product(raw(),[attr])
        self.assertIn('TYPE1_TYPE2_ATTRIBUTE_CONFLICT',product['quality_flags'])
        self.assertEqual(decide(active(),product,version,'r1')[0],'changed')
        old=active();old['sync_meta']={'type2_version':'2026-10-22T00:00:00+08:00'}
        self.assertEqual(decide(old,product,version,'r1')[0],'older_attribute_snapshot')

    def test_complete_snapshot_includes_short_then_empty_page(self):
        with tempfile.TemporaryDirectory() as directory:
            calls=[]
            def fetch(kind,page):
                calls.append((kind,page));return {'status':True,'data':[raw()] if (kind,page)==(1,1) else []}
            db,stats=stage_snapshot(Path(directory),fetch)
            self.assertEqual(stats['unique_products'],1)
            self.assertIn((1,2),calls);self.assertIn((2,1),calls)
            db.close()

    def test_bad_or_repeated_snapshot_fails_before_writes(self):
        cases=[lambda k,p:{'status':False,'data':[]},
               lambda k,p:{'status':True,'data':[raw()]},
               lambda k,p:{'status':True,'data':[raw(ITEM_DESC=str(p))]},
               lambda k,p:{'status':True,'data':[raw(code=123)]}]
        for fetch in cases:
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(SyncError):stage_snapshot(Path(directory),fetch,max_pages=3)

    def test_mixed_versions_of_same_code_select_newest_independent_of_order(self):
        old=raw(ITEM_DESC='older',LAST_UPDATE_DATE='2026-10-19T00:00:00')
        new=raw(ITEM_DESC='newer',LAST_UPDATE_DATE='2026-10-21T00:00:00')
        for rows in ([old,new],[new,old]):
            with tempfile.TemporaryDirectory() as directory:
                def fetch(k,p):return {'status':True,'data':[rows[p-1]] if k==1 and p<=2 else []}
                db,stats=stage_snapshot(Path(directory),fetch)
                record=json.loads(db.execute('SELECT body FROM type1').fetchone()[0])
                self.assertEqual(record['ITEM_DESC'],'newer')
                self.assertEqual(stats['type1']['duplicate_versions_resolved'],1)
                db.close()

    def test_type2_explicit_newer_invalid_version_replaces_older_valid_attribute(self):
        old={'ITEM_NUM':'00123456','ATTR_ID':'a','SA_ID':'s','ATTR_NAME':'额定电流','ATTR_VALUE':'100A',
             'STATUS':'有效','LAST_UPDATE_DATE':'2026-10-19T00:00:00'}
        new={**old,'STATUS':'无效','LAST_UPDATE_DATE':'2026-10-21T00:00:00'}
        with tempfile.TemporaryDirectory() as directory:
            def fetch(k,p):
                return {'status':True,'data':[raw()] if (k,p)==(1,1) else [[old],[new]][p-1] if k==2 and p<=2 else []}
            db,stats=stage_snapshot(Path(directory),fetch)
            attrs=[json.loads(r[0]) for r in db.execute('SELECT body FROM type2')]
            self.assertEqual(len(attrs),1);self.assertEqual(attrs[0]['STATUS'],'无效')
            product,_=clean_product(raw(),attrs)
            self.assertFalse(any(a['source']=='type2' for a in product['source_attributes']))
            db.close()

    def test_transient_source_failure_retries_then_succeeds(self):
        opener=Mock()
        opener.open.side_effect=[urllib.error.URLError('temporary'),
                                 urllib.error.HTTPError('http://source',429,'limited',{},None),
                                 io.BytesIO(b'{"status":true,"data":[]}')]
        with patch('product_sync.source.urllib.request.build_opener',return_value=opener),patch('product_sync.source.time.sleep'):
            self.assertTrue(request_page('http://source','fixture','2017-01-01',1,1,1000)['status'])
        self.assertEqual(opener.open.call_count,3)

    def test_auth_failure_stops_without_retrying(self):
        opener=Mock();opener.open.side_effect=urllib.error.HTTPError('http://source',403,'forbidden',{},None)
        with patch('product_sync.source.urllib.request.build_opener',return_value=opener),patch('product_sync.source.time.sleep'):
            with self.assertRaisesRegex(SyncError,'SOURCE_HTTP_403'):
                request_page('http://source','fixture','2017-01-01',1,1,1000)
        self.assertEqual(opener.open.call_count,1)

    @unittest.skipIf(sys.platform=='win32','nested process-local Windows locking has different semantics')
    def test_lock_excludes_overlapping_runs_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            with run_lock(Path(directory)):
                with self.assertRaises(SyncError):
                    with run_lock(Path(directory)):pass
            with run_lock(Path(directory)):pass


if __name__=='__main__':unittest.main()
