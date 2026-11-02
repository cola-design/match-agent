"""Least-privilege check; only run in the disposable authenticated test container."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from pymongo import MongoClient
from pymongo.errors import OperationFailure
from product_sync.sync import clean_product,create_plan,apply_plan,verify_plan
from product_sync.source import stage_snapshot


def main():
    assert os.environ.get('MATCH_AGENT_DISPOSABLE_TEST')=='1'
    bootstrap=MongoClient('mongodb://127.0.0.1:27017',serverSelectionTimeoutMS=1000)
    for attempt in range(60):
        try:bootstrap.admin.command('ping');break
        except Exception:
            if attempt==59:raise
            time.sleep(.2)
    # Localhost exception is available only in this fresh --auth container.
    bootstrap.admin.command('createUser','fixture_admin',pwd='disposable_admin_password',roles=['root'])
    bootstrap.close()
    admin=MongoClient('mongodb://fixture_admin:disposable_admin_password@127.0.0.1:27017/?authSource=admin')
    name='match_agent_disposable_test';db=admin[name]
    assert db.products.count_documents({})==0
    db.command('createUser','fixture_reader',pwd='disposable_reader_password',roles=[{'role':'read','db':name}])
    db.command('createRole','fixture_writer',privileges=[
        {'resource':{'db':name,'collection':'products'},'actions':['find','insert','update','createIndex','listIndexes']},
        {'resource':{'db':name,'collection':'product_sync_runs'},'actions':['insert']},
    ],roles=[])
    db.command('createUser','fixture_sync',pwd='disposable_sync_password',roles=[{'role':'fixture_writer','db':name}])
    raw={'ITEM_NUM':'001','ITEM_ID':'1','ITEM_DESC':'before','STATUS':'有效','LAST_UPDATE_DATE':'2026-10-20',
         'CATEGORY_NAME':'测试','SPEC_STRING':'额定电流&100A'}
    product,_=clean_product(raw,[]);product.update(data_status='ACTIVE',manufacturer='天正电气',manual_note='keep')
    db.products.insert_one(product);db.prices.insert_one({'material_code':'001','unit_price_ex_tax':123})
    prices=list(db.prices.find({}))
    reader=MongoClient('mongodb://fixture_reader:disposable_reader_password@127.0.0.1:27017/?authSource='+name)
    writer=MongoClient('mongodb://fixture_sync:disposable_sync_password@127.0.0.1:27017/?authSource='+name)
    try:
        assert reader[name].products.find_one({'material_code':'001'})
        assert reader[name].prices.find_one({'material_code':'001'})
        for collection in (reader[name].products,writer[name].prices):
            try:collection.update_one({'material_code':'001'},{'$set':{'unexpected':True}})
            except OperationFailure as exc:assert exc.code==13
            else:raise AssertionError('Unexpected write permission')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            stage,_=stage_snapshot(path,lambda k,p:{'status':True,'data':[{**raw,'ITEM_DESC':'after'}] if (k,p)==(1,1) else []})
            plan,counts=create_plan(writer[name].products,stage,path,'isolated-permissions')
            assert counts['changed']==1
            writer[name].products.create_index('material_code',unique=True)
            assert list(writer[name].products.list_indexes())
            assert apply_plan(writer[name].products,plan,lambda n:None)==1
            assert verify_plan(writer[name].products,plan)==1
            writer[name].product_sync_runs.insert_one({'run_id':'isolated-permissions','result':'success'})
            stage.close()
        after=db.products.find_one({'material_code':'001'})
        assert after['description']=='after' and after['data_status']=='PENDING_REVIEW'
        assert after['manufacturer']=='天正电气' and after['manual_note']=='keep'
        assert list(db.prices.find({}))==prices
        print(json.dumps({'ok':True,'mongo_version':admin.server_info()['version'],'authentication_enabled':True,
                          'reader_cannot_write':True,'sync_cannot_write_prices':True,'sync_guarded_apply_verified':True,
                          'manufacturer_and_manual_note_preserved':True}))
    finally:reader.close();writer.close();admin.close()


if __name__=='__main__':main()
