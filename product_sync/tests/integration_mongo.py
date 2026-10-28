"""Actual HTTP -> stage -> actual MongoDB integration in an isolated container only."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import urllib.parse

sys.path.insert(0,str(Path(__file__).parents[2]))
from pymongo import MongoClient
from product_sync.sync import clean_product, execute, apply_plan, create_plan
from product_sync.source import stage_snapshot, SyncError


def raw(code,description='TGB1N-125H 4P C100',date='2026-10-20T00:00:00'):
    return {'ITEM_NUM':code,'ITEM_ID':123,'ITEM_DESC':description,'STATUS':'有效',
            'LAST_UPDATE_DATE':date,'CATEGORY_CODE':'335.33501.3350165',
            'CATEGORY_NAME':'小型断路器类.小型断路器.TGB1N(祥云3.0)',
            'SPEC_STRING':'额定电流&100A@极数&4P@额定电压&AC380V'}


def main():
    assert os.environ.get('MATCH_AGENT_DISPOSABLE_TEST')=='1'
    assert os.environ['SYNC_MONGODB_URI']=='mongodb://127.0.0.1:27017'
    assert os.environ['MONGODB_DB']=='match_agent_disposable_test'
    client=MongoClient(os.environ['SYNC_MONGODB_URI'],serverSelectionTimeoutMS=1000)
    for attempt in range(30):
        try:client.admin.command('ping');break
        except Exception:
            if attempt==29:raise
            time.sleep(.2)
    db=client[os.environ['MONGODB_DB']]
    assert db.products.count_documents({})==0
    originals=[]
    for code in ('001','002','003'):
        product,_=clean_product(raw(code),[])
        product.update(data_status='ACTIVE',publication_basis='AUTO_RULE_V1',manual_note='keep this')
        originals.append(product)
    db.products.insert_many(originals)
    db.prices.insert_one({'material_code':'001','unit_price_ex_tax':123,'status':'ACTIVE'})
    prices=list(db.prices.find({}));before=list(db.products.find({}).sort('material_code'))
    rows=[raw('001'),raw('002','CHANGED MODEL 63A'),raw('004','NEW MODEL')]
    bad_response=False
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers.get('apikey')=='fixture-source-key'
            query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            page=int(query['pageNum'][0]);size=int(query['pageSize'][0]);kind=int(query['type'][0])
            payload={'status':not (bad_response and kind==2),
                     'data':rows[(page-1)*size:page*size] if kind==1 else []}
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps(payload).encode())
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    os.environ.update(PRODUCT_API_URL='http://127.0.0.1:'+str(server.server_port),
                      PRODUCT_API_KEY='fixture-source-key',SOURCE_DATE_FROM='2017-01-01',
                      SOURCE_PAGE_SIZE='2',SOURCE_REQUEST_INTERVAL_SECONDS='0',MIN_SOURCE_PRODUCTS='1',MIN_COVERAGE_RATIO='0.7',MAX_CHANGE_RATIO='1')
    try:
        with tempfile.TemporaryDirectory() as folder:
            args=SimpleNamespace(state_dir=folder,apply=False)
            assert execute(args)==0
            report=json.loads((Path(folder)/'latest.json').read_text())
            assert report['counts']['unchanged']==1
            assert report['counts']['changed']==1
            assert report['counts']['new']==1
            assert report['counts']['not_returned_by_source']==1
            assert list(db.products.find({}).sort('material_code'))==before
            args.apply=True
            assert execute(args)==0
            report=json.loads((Path(folder)/'latest.json').read_text())
            assert report['applied_operations']==2 and report['verified_operations']==2
            assert db.products.find_one({'material_code':'001'})['data_status']=='ACTIVE'
            assert db.products.find_one({'material_code':'002'})['data_status']=='PENDING_REVIEW'
            assert db.products.find_one({'material_code':'002'})['manual_note']=='keep this'
            assert db.products.find_one({'material_code':'004'})['data_status']=='PENDING_REVIEW'
            assert db.products.find_one({'material_code':'003'})['data_status']=='ACTIVE'
            assert list(db.prices.find({}))==prices
            assert execute(args)==0
            report=json.loads((Path(folder)/'latest.json').read_text())
            assert report['applied_operations']==0 and report['counts']['unchanged']==3
            latest=list(db.products.find({}).sort('material_code'))
            bad_response=True
            assert execute(args)==1
            assert list(db.products.find({}).sort('material_code'))==latest
            assert list(db.prices.find({}))==prices
        # Real optimistic guards must refuse a manually edited field after planning.
        bad_response=False
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            changed=raw('001','CHANGED AGAIN')
            stage,_=stage_snapshot(path,lambda k,p:{'status':True,'data':[changed] if (k,p)==(1,1) else []})
            plan,counts=create_plan(db.products,stage,path,'concurrency-check')
            db.products.update_one({'material_code':'001'},{'$set':{'manual_override':True}})
            try:apply_plan(db.products,plan,lambda n:None)
            except SyncError:pass
            else:raise AssertionError('Concurrent manual override was overwritten')
            assert db.products.find_one({'material_code':'001'})['description']==raw('001')['ITEM_DESC']
            stage.close()
        print(json.dumps({'ok':True,'mongo_version':client.server_info()['version'],
                          'preview_no_writes':True,'mixed_snapshot_diff':True,'repeat_zero_writes':True,
                          'missing_products_preserved':True,'manual_fields_preserved':True,
                          'fetch_failure_no_writes':True,'concurrent_override_guard':True,'prices_unchanged':True}))
    finally:
        server.shutdown();server.server_close();client.close()


if __name__=='__main__':main()
