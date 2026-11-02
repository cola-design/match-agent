import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from product_sync.source import stage_snapshot,SyncError
from product_sync.runtime import file_hash,reuse_snapshot,report_summary
from product_sync.sync import execute,atomic_json


RUN='20261102T000000Z_12345678'


def row():
    return {'ITEM_NUM':'001','ITEM_ID':'1','ITEM_DESC':'TEST','STATUS':'有效',
            'LAST_UPDATE_DATE':'2026-10-20','CATEGORY_NAME':'测试','SPEC_STRING':'额定电流&100A'}


def snapshot(state,legacy=False):
    folder=state/RUN;folder.mkdir()
    db,stats=stage_snapshot(folder,lambda kind,page:{'status':True,'data':[row()] if (kind,page)==(1,1) else []})
    db.close()
    report={'result':'preview_only','phase':'preview_complete','date_from':'2017-01-01','snapshot':stats,
            'finished_at':'2026-11-02T01:00:00+00:00'}
    if not legacy:report.update(snapshot_sha256=file_hash(folder/'stage.sqlite'),staged_type2_rows=0)
    atomic_json(folder/'report.json',report)
    return folder,report


class OperationsTest(unittest.TestCase):
    def test_fetch_progress_includes_short_and_terminal_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            progress=[]
            db,_=stage_snapshot(Path(folder),lambda k,p:{'status':True,'data':[row()] if (k,p)==(1,1) else []},on_progress=progress.append)
            db.close()
            self.assertEqual([(p['type'],p['page']) for p in progress],[(1,1),(1,2),(2,1)])
            self.assertEqual(progress[1]['rows_received'],1)

    def test_completed_snapshot_can_be_reused_read_only_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder);source,old=snapshot(state);target=state/'new';target.mkdir()
            before=file_hash(source/'stage.sqlite')
            db,stats,details=reuse_snapshot(state,RUN,target,'2017-01-01')
            self.assertEqual(stats['unique_products'],1)
            self.assertEqual(details['snapshot_integrity'],'sha256_verified')
            with self.assertRaises(Exception):db.execute('DELETE FROM type1')
            db.close();self.assertEqual(file_hash(source/'stage.sqlite'),before)
            self.assertEqual(json.loads((source/'report.json').read_text()),old)

    def test_legacy_snapshot_is_explicitly_labelled(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder);snapshot(state,legacy=True);target=state/'new';target.mkdir()
            db,_,details=reuse_snapshot(state,RUN,target,'2017-01-01')
            self.assertEqual(details['snapshot_integrity'],'legacy_snapshot_counts_checked');db.close()

    def test_reuse_rejects_failed_run_changed_hash_wrong_date_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder);source,report=snapshot(state);target=state/'new';target.mkdir()
            for run,changed,date,expected in [
                ('../escape',report,'2017-01-01','INVALID_SNAPSHOT_RUN_ID'),
                (RUN,{**report,'result':'failed'},'2017-01-01','SNAPSHOT_RUN_NOT_COMPLETE'),
                (RUN,{**report,'snapshot_sha256':'bad'},'2017-01-01','SNAPSHOT_HASH_MISMATCH'),
                (RUN,report,'2020-01-01','SNAPSHOT_DATE_FROM_MISMATCH'),
                (RUN,{**report,'staged_type2_rows':10},'2017-01-01','SNAPSHOT_COUNTS_MISMATCH'),
            ]:
                with self.subTest(expected=expected):
                    atomic_json(source/'report.json',changed)
                    with self.assertRaisesRegex(SyncError,expected):reuse_snapshot(state,run,target,date)

    def test_reused_snapshot_cannot_be_applied_even_if_called_without_argparse(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('product_sync.sync.MongoClient') as mongo:
                result=execute(SimpleNamespace(state_dir=folder,apply=True,reuse_snapshot=RUN))
            self.assertEqual(result,1);mongo.assert_not_called()
            report=json.loads((Path(folder)/'latest.json').read_text())
            self.assertEqual(report['error'],'REUSE_SNAPSHOT_PREVIEW_ONLY')
            self.assertFalse(report['products_write_started'])

    def check_run(self,folder,response,extra_env=None):
        products=Mock();products.count_documents.side_effect=[0,1];products.aggregate.return_value=[]
        db=SimpleNamespace(products=products,command=Mock())
        client=Mock();client.__getitem__=Mock(return_value=db)
        env={'PRODUCT_API_KEY':'fixture-key','SYNC_MONGODB_URI':'mongodb://fixture',
             'PRODUCT_API_URL':'http://127.0.0.1:1/fixture-source',
             'SOURCE_DATE_FROM':'2017-01-01','SOURCE_REQUEST_INTERVAL_SECONDS':'0'}
        env.update(extra_env or {})
        with patch.dict(os.environ,env,clear=True),patch('product_sync.sync.MongoClient',return_value=client),patch('product_sync.sync.request_page',side_effect=response):
            result=execute(SimpleNamespace(state_dir=folder,apply=False,check=True))
        return result,products

    def test_preflight_does_not_overwrite_previous_preview_or_write_products(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder);atomic_json(state/'latest.json',{'run_id':'previous-preview'})
            result,products=self.check_run(folder,[{'status':True,'data':[row()]},{'status':True,'data':[]}])
            self.assertEqual(result,0)
            self.assertEqual(json.loads((state/'latest.json').read_text()),{'run_id':'previous-preview'})
            check=json.loads((state/'latest_check.json').read_text())
            self.assertEqual(check['result'],'check_passed');self.assertFalse(check['products_write_started'])
            products.bulk_write.assert_not_called();products.create_index.assert_not_called()

    def test_403_report_explains_source_failure_without_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            result,_=self.check_run(folder,[SyncError('SOURCE_HTTP_403')])
            report=(Path(folder)/'latest_check.json').read_text();out=json.loads(report)
            self.assertEqual(result,1);self.assertEqual(out['source_failure']['page'],1)
            self.assertIn('IP白名单',out['error_hint']);self.assertNotIn('fixture-key',report)
            self.assertNotIn('mongodb://fixture',report);self.assertFalse(out['products_write_started'])

    def test_local_summary_does_not_echo_unrecognized_private_fields(self):
        self.assertEqual(report_summary({'run_id':RUN,'PRODUCT_API_KEY':'private','uri':'private'}),{'run_id':RUN})

    def test_low_disk_space_stops_before_source_requests_and_product_writes(self):
        with tempfile.TemporaryDirectory() as folder,patch('product_sync.sync.shutil.disk_usage',return_value=SimpleNamespace(free=0)):
            result,products=self.check_run(folder,[],{'MIN_FREE_DISK_GB':'5'})
            self.assertEqual(result,1)
            out=json.loads((Path(folder)/'latest_check.json').read_text())
            self.assertEqual(out['error'],'INSUFFICIENT_FREE_DISK_NO_PRODUCT_WRITES')
            self.assertFalse(out['products_write_started']);products.bulk_write.assert_not_called()

    def test_public_endpoint_placeholder_and_missing_endpoint_are_rejected(self):
        for endpoint in ('','https://source.example.invalid/products'):
            with self.subTest(endpoint=endpoint),tempfile.TemporaryDirectory() as folder:
                result,products=self.check_run(folder,[],{'PRODUCT_API_URL':endpoint})
                self.assertEqual(result,1)
                report=json.loads((Path(folder)/'latest_check.json').read_text())
                self.assertEqual(report['error'],'SET_PRODUCT_API_URL')
                self.assertFalse(report['products_write_started']);products.bulk_write.assert_not_called()


if __name__=='__main__':unittest.main()
