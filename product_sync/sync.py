"""Full source snapshot, semantic diff, idempotent product writes. Default: preview."""
import argparse
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import gzip
import json
import os
from pathlib import Path
import re
import sys
import time
import unicodedata
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bson import json_util
from pymongo import MongoClient, InsertOne, UpdateOne
from mongodb_api.product_contract import normalize_product
from product_sync.cleaning import clean_text, parse_spec_string, add_standard_specs, category, ALIASES
from product_sync.source import SyncError, canonical, digest, request_page, stage_snapshot, source_stamp

CONTENT_FIELDS=('item_id','description','category','standard_specs','source_attributes','source_status','quality_flags')
OWNED_FIELDS=CONTENT_FIELDS+('specification_text','last_update_date','provenance','data_status','publication_basis','sync_meta')
GUARDED_FIELDS=OWNED_FIELDS+('manual_override',)
AUTO_FLAGS={'MISSING_DESCRIPTION','MISSING_CATEGORY','MISSING_SPECIFICATIONS','UNKNOWN_SOURCE_STATUS',
            'DUPLICATE_STANDARD_SPEC','TYPE1_TYPE2_ATTRIBUTE_CONFLICT','DUPLICATE_TYPE2_ATTRIBUTE'}

# Compare only these explicit code AND label combinations. Unknown combinations
# remain literal; a matching code alone never proves engineering equivalence.
ACCESSORY_LABELS={
    '00':('无附件','无'),
    '08':('报警触头',),
    '10':('分励线圈',),
    '18':('分励脱扣器+报警触头','分励+报警'),
    '20':('辅助触头',),
    '28':('辅助报警触头','辅助+报警'),
    '30':('欠压线圈',),
    '38':('欠压脱扣器+报警触头','欠压+报警'),
    '40':('分励脱扣器+辅助触头','分励+辅助'),
    '48':('分励脱扣器+辅助报警触头','分励+辅报'),
    '50':('分励脱扣器+欠压脱扣器','分励+欠压'),
    '60':('双组辅助触头','双组辅助'),
    '68':('辅助触头+辅助报警触头','辅助+辅报'),
    '70':('欠压脱扣器+辅助触头','欠压+辅助'),
    '78':('欠压脱扣器+辅助报警触头','欠压+辅报'),
}


def accessory_comparison_key(value):
    """Return a comparison key without editing any source or specification text."""
    if not isinstance(value,str):return ('raw',value)
    text=re.sub(r'\s+','',unicodedata.normalize('NFKC',value))
    if text in ACCESSORY_LABELS['00']:
        return ('accessory','00')
    prefix=re.fullmatch(r'([0-9]{2})([^0-9]+)',text)
    suffix=re.fullmatch(r'([^0-9]+)\(([0-9]{2})\)',text)
    if prefix:code,label=prefix.groups()
    elif suffix:label,code=suffix.groups()
    else:return ('raw',value)
    if label in ACCESSORY_LABELS.get(code,()):return ('accessory',code)
    return ('raw',value)


def attribute_comparison_values(attributes):
    by_name={}
    for attr in attributes:
        key=ALIASES.get(attr['name'],attr['name'])
        value=accessory_comparison_key(attr['value']) if key=='accessory_code' else ('raw',attr['value'])
        by_name.setdefault(key,set()).add(value)
    return by_name


def attribute_conflict_flags(attributes):
    groups={source:attribute_comparison_values([a for a in attributes if a.get('source')==source])
            for source in ('type1','type2')}
    first,second=groups['type1'],groups['type2']
    flags=[]
    if any(first[key]!=second[key] for key in first.keys() & second.keys()):
        flags.append('TYPE1_TYPE2_ATTRIBUTE_CONFLICT')
    if any(len(values)>1 for values in second.values()):flags.append('DUPLICATE_TYPE2_ATTRIBUTE')
    return flags


def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')


def atomic_json(path, value):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temp.replace(path)


@contextmanager
def run_lock(state):
    """OS releases this lock on process exit/crash. All workers must share this state directory."""
    with (state/'sync.lock').open('a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise SyncError('ANOTHER_SYNC_IS_RUNNING') from None
        else:
            import fcntl
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise SyncError('ANOTHER_SYNC_IS_RUNNING') from None
        try:yield
        finally:
            lock.seek(0)
            if os.name=='nt':msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(lock,fcntl.LOCK_UN)


def stamp(value):
    return source_stamp(value)


def clean_product(raw, attributes):
    row={key:clean_text(value) for key,value in raw.items()}
    code=row['ITEM_NUM'];issues=[]
    attrs1=parse_spec_string(row.get('SPEC_STRING',''),issues,code,'type1')
    attrs2=[];versions=[]
    for original in attributes:
        attr={key:clean_text(value) for key,value in original.items()}
        if attr.get('STATUS') not in ('有效','无效'):raise SyncError('UNKNOWN_TYPE2_SOURCE_STATUS')
        if stamp(attr.get('LAST_UPDATE_DATE')):versions.append(stamp(attr['LAST_UPDATE_DATE']))
        if attr.get('STATUS')!='有效':continue
        if not attr.get('ATTR_NAME'):
            issues.append({'issue':'MISSING_ATTRIBUTE_NAME'});continue
        attrs2.append({'name':attr['ATTR_NAME'],'value':attr.get('ATTR_VALUE',''),'source':'type2',
                       'attr_id':attr.get('ATTR_ID',''),'sa_id':attr.get('SA_ID','')})
    # Do not treat a change in page/attribute order as a product update.
    attrs2.sort(key=lambda a:(a['name'],a['value'],a['attr_id'],a['sa_id']))
    standards,flags=add_standard_specs(attrs1+attrs2,issues,code)
    flags.extend(attribute_conflict_flags(attrs1+attrs2))
    for source,key in (('ITEM_DESC','MISSING_DESCRIPTION'),('CATEGORY_NAME','MISSING_CATEGORY'),('SPEC_STRING','MISSING_SPECIFICATIONS')):
        if not row.get(source):flags.append(key)
    if row.get('STATUS') not in ('有效','无效'):flags.append('UNKNOWN_SOURCE_STATUS')
    flags.extend(issue['issue'] for issue in issues)
    product={'material_code':code,'item_id':row.get('ITEM_ID',''),'description':row.get('ITEM_DESC',''),
             'category':category(row.get('CATEGORY_NAME',''),row.get('CATEGORY_CODE','')),
             'standard_specs':standards,'source_attributes':attrs1+attrs2,
             'specification_text':'；'.join(a['name']+'='+a['value'] for a in attrs1),
             'source_status':row.get('STATUS',''),'quality_flags':sorted(set(flags)),
             'last_update_date':row.get('LAST_UPDATE_DATE',''),
             'provenance':{'source':'product-spec-api','type2_attribute_count_valid':len(attrs2)}}
    return product, max(versions).isoformat() if versions else None


def signature(product):
    value=deepcopy({key:product.get(key) for key in CONTENT_FIELDS})
    attributes=[]
    for attr in product['source_attributes']:
        item={key:attr.get(key,'') for key in ('name','value','source')}
        if ALIASES.get(item['name'])=='accessory_code':
            comparison=accessory_comparison_key(item['value'])
            if comparison[0]=='accessory':item.update(name='accessory_code',value=comparison)
        attributes.append(canonical(item))
    value['source_attributes']=sorted(set(attributes))
    for key,spec in value['standard_specs'].items():
        if key=='accessory_code':
            values=spec if isinstance(spec,list) else [spec]
            compared=[]
            for item in values:
                comparison=accessory_comparison_key(item.get('value_text',''))
                compared.append(canonical(comparison if comparison[0]=='accessory' else ('raw',item)))
            value['standard_specs'][key]=sorted(set(compared))
        elif isinstance(spec,list):value['standard_specs'][key]=sorted(spec,key=canonical)
    value['quality_flags']=sorted(value['quality_flags'])
    return digest(value)


def decide(old, candidate, type2_version, run_id):
    candidate=deepcopy(candidate)
    if old and old.get('manual_override'):
        return 'manual_override',None
    if old:
        current=normalize_product(old)
        # Preserve separately owned attributes; do not rewrite a manual supplement as upstream data.
        manual=[a for a in current['source_attributes'] if a.get('source') not in ('type1','type2')]
        if manual:return 'manual_attributes_require_review',None
        known=AUTO_FLAGS | {'EMPTY_SPEC_FRAGMENT','MALFORMED_SPEC_FRAGMENT','EMPTY_SPEC_NAME','MISSING_ATTRIBUTE_NAME'}
        candidate['quality_flags']=sorted(set(candidate['quality_flags']) | (set(current['quality_flags'])-known))
        changed=signature(current)!=signature(candidate)
        previous=stamp(current.get('last_update_date'));incoming=stamp(candidate.get('last_update_date'))
        if previous and (not incoming or incoming<previous):return 'older_or_unknown_source_version',None
        previous_attr=stamp(current.get('sync_meta',{}).get('type2_version'))
        incoming_attr=stamp(type2_version)
        if previous_attr and (not incoming_attr or incoming_attr<previous_attr):return 'older_attribute_snapshot',None
        if not changed and previous==incoming and previous_attr==incoming_attr:return 'unchanged',None
        # Legacy rows have no type2 checkpoint. Establish it without republishing unchanged content.
        if changed:
            candidate['data_status']='PENDING_REVIEW' if current.get('data_status')=='ACTIVE' else current.get('data_status','PENDING_REVIEW')
            candidate['publication_basis']='SYNC_CHANGED_REQUIRES_REVIEW_V1'
        else:
            candidate['data_status']=current.get('data_status','PENDING_REVIEW')
            if 'publication_basis'in current:candidate['publication_basis']=current['publication_basis']
        candidate['provenance']={**current.get('provenance',{}),**candidate['provenance']}
        meta=deepcopy(current.get('sync_meta',{}))
        action='changed' if changed else 'metadata_only'
    else:
        candidate.update(data_status='PENDING_REVIEW',publication_basis='SYNC_NEW_REQUIRES_REVIEW_V1')
        meta={};action='new'
    meta.update(run_id=run_id,synced_at=now(),content_hash=signature(candidate),type2_version=type2_version)
    candidate['sync_meta']=meta
    return action,candidate


def create_plan(collection, stage, folder, run_id):
    counts=Counter();path=folder/'changes.jsonl.gz'
    with gzip.open(path,'wt',encoding='utf-8') as plan, (folder/'issues.jsonl').open('w',encoding='utf-8') as issues:
        cursor=stage.execute('SELECT code,body FROM type1 ORDER BY code')
        while True:
            batch=cursor.fetchmany(500)
            if not batch:break
            codes=[row[0] for row in batch]
            existing={}
            for doc in collection.find({'material_code':{'$in':codes}}):
                if doc['material_code']in existing:raise SyncError('DUPLICATE_DATABASE_MATERIAL_CODE')
                existing[doc['material_code']]=doc
            for code,body in batch:
                attributes=[json.loads(row[0]) for row in stage.execute('SELECT body FROM type2 WHERE code=?',(code,))]
                candidate,version=clean_product(json.loads(body),attributes)
                old=existing.get(code)
                action,after=decide(old,candidate,version,run_id)
                counts[action]+=1
                if after is None:
                    if action!='unchanged':issues.write(canonical({'material_code':code,'issue':action})+'\n')
                    continue
                if candidate['quality_flags']:issues.write(canonical({'material_code':code,'quality_flags':candidate['quality_flags']})+'\n')
                before={key:old[key] for key in GUARDED_FIELDS if old and key in old}
                entry={'material_code':code,'action':action,'before':before,
                       'missing':[key for key in GUARDED_FIELDS if old and key not in old],
                       'after':after}
                if old:entry['_id']=old['_id']
                plan.write(json_util.dumps(entry,ensure_ascii=False)+'\n')
            counts['scanned']+=len(batch)
            if counts['scanned']%5000==0:print('plan scanned='+str(counts['scanned']),flush=True)
    with path.open('rb+') as stream:os.fsync(stream.fileno())
    return path,dict(counts)


def apply_plan(collection, path, progress):
    operations=[];written=0
    def flush():
        nonlocal operations,written
        if not operations:return
        result=collection.bulk_write(operations,ordered=True)
        if result.matched_count+result.inserted_count!=len(operations):
            raise SyncError('CONCURRENT_PRODUCT_CHANGE_PARTIAL_APPLY_POSSIBLE')
        written+=len(operations);operations=[]
        print('apply completed='+str(written),flush=True);progress(written)
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        for line in stream:
            entry=json_util.loads(line)
            if entry['action']=='new':operations.append(InsertOne(entry['after']))
            else:
                query={'_id':entry['_id'],'material_code':entry['material_code']}
                query.update({key:{'$eq':value,'$exists':True} for key,value in entry['before'].items()})
                query.update({key:{'$exists':False} for key in entry['missing']})
                operations.append(UpdateOne(query,{'$set':{key:value for key,value in entry['after'].items() if key!='material_code'}}))
            if len(operations)>=500:flush()
    flush()
    return written


def verify_plan(collection,path):
    verified=0;batch=[]
    def verify():
        nonlocal verified,batch
        rows={d['material_code']:d for d in collection.find({'material_code':{'$in':[e['material_code'] for e in batch]}})}
        for entry in batch:
            doc=rows.get(entry['material_code'])
            if doc is None or any(doc.get(key)!=value for key,value in entry['after'].items()):
                raise SyncError('POST_WRITE_VERIFICATION_FAILED')
        verified+=len(batch);batch=[]
        print('verify completed='+str(verified),flush=True)
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        for line in stream:
            batch.append(json_util.loads(line))
            if len(batch)>=500:verify()
    if batch:verify()
    return verified


def execute(args):
    state=Path(args.state_dir).resolve();state.mkdir(parents=True,exist_ok=True)
    with run_lock(state):
        run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
        folder=state/run_id;folder.mkdir(mode=0o700)
        report={'run_id':run_id,'mode':'apply' if args.apply else 'preview','phase':'starting','started_at':now(),
                'prices_touched':False,'source_missing_policy':'never_delete','checkpoint_advanced':False}
        def progress(**fields):
            report.update(fields);atomic_json(folder/'report.json',report)
            atomic_json(state/'latest.json',report)
        client=None;stage=None
        try:
            key=os.environ.get('PRODUCT_API_KEY','');uri=os.environ.get('SYNC_MONGODB_URI','')
            if not key or not uri:raise SyncError('SET_PRODUCT_API_KEY_AND_SYNC_MONGODB_URI')
            date_from=os.environ.get('SOURCE_DATE_FROM','')
            try:date.fromisoformat(date_from)
            except ValueError:raise SyncError('SET_CONFIRMED_FULL_SOURCE_DATE_FROM') from None
            client=MongoClient(uri,serverSelectionTimeoutMS=5000,connectTimeoutMS=5000,socketTimeoutMS=60000)
            db=client[os.environ.get('MONGODB_DB','match_agent')];products=db.products
            db.command('ping')
            required_types={'material_code':'string','category':'object','standard_specs':'object',
                            'source_attributes':'array','quality_flags':'array'}
            if products.count_documents({'$or':[{key:{'$not':{'$type':kind}}} for key,kind in required_types.items()]}):
                raise SyncError('FINISH_PRODUCT_FIELD_REPAIR_FIRST')
            baseline=products.count_documents({})
            attr_counts=list(products.aggregate([{'$match':{'source_attributes.source':'type2'}},
                {'$unwind':'$source_attributes'},{'$match':{'source_attributes.source':'type2'}},{'$count':'rows'}]))
            baseline_attrs=attr_counts[0]['rows'] if attr_counts else 0
            progress(phase='fetching',baseline_products=baseline,baseline_type2_attributes=baseline_attrs,date_from=date_from)
            url=os.environ.get('PRODUCT_API_URL','http://api2.tengen.com.cn/env-101/otherportal/otherproject/ai/getProductSpec')
            page_size=int(os.environ.get('SOURCE_PAGE_SIZE','1000'))
            if not 1<=page_size<=1000:raise SyncError('PAGE_SIZE_MUST_BE_1_TO_1000')
            interval=float(os.environ.get('SOURCE_REQUEST_INTERVAL_SECONDS','0.2'))
            if interval<0:raise SyncError('SOURCE_REQUEST_INTERVAL_MUST_BE_NONNEGATIVE')
            coverage=float(os.environ.get('MIN_COVERAGE_RATIO','0.9'))
            change_limit=float(os.environ.get('MAX_CHANGE_RATIO','0.2'))
            if not 0<=coverage<=1 or not 0<=change_limit<=1:raise SyncError('RATIOS_MUST_BE_0_TO_1')
            def fetch(kind,page):
                time.sleep(interval)
                return request_page(url,key,date_from,kind,page,page_size)
            stage,snapshot=stage_snapshot(folder,fetch,page_size)
            progress(phase='planning',snapshot=snapshot)
            minimum=int(os.environ.get('MIN_SOURCE_PRODUCTS','1'))
            if snapshot['unique_products']<max(minimum,baseline*coverage):
                raise SyncError('SOURCE_COUNT_BELOW_EXPECTED_BASELINE_NO_WRITES')
            if snapshot['type2']['rows']<baseline_attrs*coverage:
                raise SyncError('ATTRIBUTE_SNAPSHOT_BELOW_EXPECTED_BASELINE_NO_WRITES')
            plan,counts=create_plan(products,stage,folder,run_id)
            source_codes={row[0] for row in stage.execute('SELECT code FROM type1')}
            counts['not_returned_by_source']=0
            with (folder/'not_returned_by_source.jsonl').open('w',encoding='utf-8') as missing:
                for doc in products.find({}, {'material_code':1}):
                    if doc.get('material_code') not in source_codes:
                        counts['not_returned_by_source']+=1
                        missing.write(canonical({'material_code':doc.get('material_code'),'action':'preserved'})+'\n')
            progress(phase='preview_complete',counts=counts,plan_file=plan.name,
                     source_missing_policy='never_delete; compare unique source count with baseline')
            ratio=(counts.get('new',0)+counts.get('changed',0))/max(1,baseline)
            if not args.apply:
                progress(phase='preview_complete',result='preview_only',change_ratio=ratio,finished_at=now())
                return 0
            if ratio>change_limit:
                raise SyncError('CHANGE_RATIO_REQUIRES_REVIEW_NO_WRITES')
            # A unique index prevents two product identities from sharing one material code.
            indexes=list(products.list_indexes())
            if not any(list(i['key'].items())==[('material_code',1)] and i.get('unique') for i in indexes):
                products.create_index('material_code',unique=True,name='material_code_unique_sync')
            progress(phase='applying',applied_operations=0)
            written=apply_plan(products,plan,lambda count:progress(applied_operations=count))
            progress(phase='verifying',applied_operations=written)
            verified=verify_plan(products,plan)
            progress(phase='complete',result='success',verified_operations=verified,finished_at=now())
            db.product_sync_runs.insert_one(deepcopy(report))
            return 0
        except Exception as exc:
            progress(phase='failed',result='failed',error=str(exc) if isinstance(exc,SyncError) else type(exc).__name__,finished_at=now())
            print('Sync stopped:',report['error'],flush=True)
            print('If applying began, partial writes may exist; retain this run folder. Rerun is idempotent.',flush=True)
            return 1
        finally:
            if stage is not None:stage.close()
            if client is not None:client.close()
            print('Report:',folder/'report.json',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true',help='Write changes after the full snapshot and diff plan are complete')
    parser.add_argument('--state-dir',default=os.environ.get('SYNC_STATE_DIR','sync_state'))
    try:return execute(parser.parse_args())
    except SyncError as exc:
        print(str(exc),flush=True)
        return 2


if __name__=='__main__':raise SystemExit(main())
