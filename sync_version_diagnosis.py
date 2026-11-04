"""Read yesterday's saved snapshot and a sample of MongoDB version dates. No writes."""
from collections import Counter
from datetime import datetime,timedelta,timezone
import argparse
import json
import importlib.util
import os
from pathlib import Path
import random
import re
import sqlite3
import sys


def stamp(value):
    """Parse source SQL/ISO timestamps consistently on Python 3.10 and newer.

    Python 3.10 fromisoformat rejects one-digit fractions such as ``.0``.
    Normalize fractional seconds to microseconds before parsing. Keep the
    existing Beijing interpretation for naive dates and reject invalid dates.
    """
    if isinstance(value,datetime):parsed=value
    else:
        text=str(value).strip().replace('Z','+00:00')
        fractional=re.fullmatch(r'(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\.(\d+)([+-]\d{2}:\d{2})?',text)
        if fractional:
            prefix,seconds,offset=fractional.groups()
            if len(seconds)>6:return None
            text=prefix+'.'+seconds.ljust(6,'0')+(offset or '')
        try:parsed=datetime.fromisoformat(text)
        except ValueError:return None
    if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed.astimezone(timezone.utc)


def date_kind(value):
    if value is None or not str(value).strip():return 'missing'
    return 'parseable_iso' if stamp(value) else 'not_parseable'


def display(value):
    if value is None:return None
    if isinstance(value,datetime):return value.isoformat()
    if isinstance(value,(dict,list)):return '<'+type(value).__name__+'>'
    return str(value)[:100]


def read_snapshot(path,sample_size=120):
    path=Path(path).resolve()
    if not path.is_file():raise ValueError('STAGE_SQLITE_NOT_FOUND')
    counts=Counter();types=Counter();date_fields=Counter();sample=[];seen=0;generator=random.Random(20261104)
    source_min=source_max=None
    # mode=ro prevents the diagnostic from creating/changing a SQLite database.
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as connection:
        for code,body in connection.execute('SELECT code,body FROM type1 ORDER BY code'):
            row=json.loads(body);value=row.get('LAST_UPDATE_DATE');seen+=1
            counts[date_kind(value)]+=1;types[type(value).__name__]+=1
            date_fields.update(k for k in row if any(t in k.upper() for t in ('DATE','UPDATE_TIME','UPDATED_AT','LAST_MODIFIED')))
            parsed=stamp(value)
            if parsed is not None:
                source_min=parsed if source_min is None or parsed<source_min else source_min
                source_max=parsed if source_max is None or parsed>source_max else source_max
            item={'material_code':code,'source_date':value,'source_date_type':type(value).__name__,'source_row':row}
            if len(sample)<sample_size:sample.append(item)
            else:
                index=generator.randrange(seen)
                if index<sample_size:sample[index]=item
    return {'source_products_checked':seen,'source_date_counts':dict(counts),'source_date_types':dict(types),
        'source_date_field_names':dict(date_fields),'source_min_utc':display(source_min),'source_max_utc':display(source_max)},sample


def compare_sample(sample,documents):
    existing={d['material_code']:d for d in documents};counts=Counter();examples={}
    for item in sample:
        old=existing.get(item['material_code']);incoming=stamp(item['source_date'])
        previous=stamp(old.get('last_update_date')) if old else None
        if old is None:reason='database_product_not_found'
        elif previous is None:reason='database_date_not_parseable'
        elif incoming is None:reason='source_date_'+date_kind(item['source_date'])
        elif incoming<previous:reason='source_older_than_database'
        elif incoming==previous:reason='dates_equal'
        else:reason='source_newer_than_database'
        counts[reason]+=1
        values=examples.setdefault(reason,[])
        if len(values)<3:
            values.append({'material_code':item['material_code'],'source_date':display(item['source_date']),
                'source_date_type':item['source_date_type'],'source_date_utc':display(incoming),
                'database_date':display(old.get('last_update_date')) if old else None,
                'database_date_type':type(old.get('last_update_date')).__name__ if old else None,'database_date_utc':display(previous)})
    return {'database_sample_size':len(sample),'sample_comparison_counts':dict(counts),'sample_examples':examples}


def semantic_sample(snapshot,sample,documents,service_file):
    """Use installed service's exact pure cleaners/decider; never call execute/apply."""
    service_file=Path(service_file).resolve()
    if not service_file.is_file():return {'sample_content_audit':'service_file_not_found; date checks completed'}
    spec=importlib.util.spec_from_file_location('_sync_readonly_audit',service_file)
    if spec is None or spec.loader is None:raise ValueError('SERVICE_IMPORT_UNAVAILABLE')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    logic=sys.modules[module._PACKAGE+'.sync']
    existing={d['material_code']:d for d in documents};actions=Counter();content=Counter();quality=Counter();failures=Counter()
    old_attributes=new_attributes=0
    with sqlite3.connect(Path(snapshot).resolve().as_uri()+'?mode=ro',uri=True) as connection:
        for item in sample:
            try:
                attrs=[json.loads(r[0]) for r in connection.execute('SELECT body FROM type2 WHERE code=?',(item['material_code'],))]
                candidate,version=logic.clean_product(item['source_row'],attrs)
                old=existing.get(item['material_code'])
                quality.update(candidate.get('quality_flags',[]));new_attributes+=sum(a.get('source')=='type2' for a in candidate['source_attributes'])
                if old is not None:
                    current=logic.normalize_product(old)
                    old_attributes+=sum(a.get('source')=='type2' for a in current['source_attributes'])
                    known=logic.AUTO_FLAGS|{'EMPTY_SPEC_FRAGMENT','MALFORMED_SPEC_FRAGMENT','EMPTY_SPEC_NAME','MISSING_ATTRIBUTE_NAME'}
                    candidate['quality_flags']=sorted(set(candidate['quality_flags'])|(set(current['quality_flags'])-known))
                    different=logic.signature(current)!=logic.signature(candidate)
                    content['different' if different else 'same']+=1
                else:content['new_product']+=1
                action,_=logic.decide(old,candidate,version,'READ_ONLY_DIAGNOSIS')
                actions[action]+=1
            except Exception as exc:
                failures[type(exc).__name__]+=1
    return {'sample_content_audit':'pure cleaning and planning functions only; no apply function called',
        'sample_business_content_comparison':dict(content),'sample_planner_decisions':dict(actions),
        'sample_cleaning_failures':dict(failures),'sample_source_quality_flags':dict(quality),
        'sample_database_type2_attributes':old_attributes,'sample_source_type2_attributes_valid':new_attributes}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',required=True,help='Existing completed sync run directory')
    parser.add_argument('--env-file',default='.env',help='Existing sync .env; never printed')
    parser.add_argument('--service-file',default='product_sync_service.py',help='Existing service; imports pure cleaner functions without executing main')
    parser.add_argument('--sample-size',type=int,default=120)
    args=parser.parse_args();client=None
    try:
        if not 1<=args.sample_size<=500:raise ValueError('SAMPLE_SIZE_MUST_BE_1_TO_500')
        folder=Path(args.run_dir).resolve();report=json.loads((folder/'report.json').read_text(encoding='utf-8-sig'))
        print('正在读取昨天已保存的本地快照...',flush=True)
        statistics,sample=read_snapshot(folder/'stage.sqlite',args.sample_size)
        from dotenv import load_dotenv
        from pymongo import MongoClient
        load_dotenv(Path(args.env_file).resolve(),override=False,encoding='utf-8-sig')
        uri=os.environ.get('SYNC_MONGODB_URI','').strip()
        if not uri:raise ValueError('SYNC_MONGODB_URI_NOT_CONFIGURED')
        print('正在只读抽查MongoDB中的版本日期...',flush=True)
        client=MongoClient(uri,serverSelectionTimeoutMS=5000,connectTimeoutMS=5000,socketTimeoutMS=12000)
        collection=client[os.environ.get('MONGODB_DB','match_agent')].products
        fields={k:1 for k in ('material_code','item_id','description','category','standard_specs','source_attributes','source_status','quality_flags',
            'last_update_date','data_status','provenance','sync_meta','manual_override','publication_basis')};fields['_id']=0
        documents=list(collection.find({'material_code':{'$in':[s['material_code'] for s in sample]}},fields).max_time_ms(5000))
        print('正在用原程序的清洗规则只读比较抽样内容...',flush=True)
        semantics=semantic_sample(folder/'stage.sqlite',sample,documents,args.service_file)
        output={'diagnostic_version':'1.0.1','snapshot_run_id':report.get('run_id'),
            'database_observed_at_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'database_read_only':True,'source_api_requested':False,'product_writes':0,
            **statistics,**compare_sample(sample,documents),**semantics,
            'scope':'Full saved type1 date scan; database date comparison is a sample of the current database, not a reconstruction of yesterday.'}
        print(json.dumps(output,ensure_ascii=False,indent=2));return 0
    except Exception as exc:
        code=str(exc) if isinstance(exc,ValueError) and str(exc).isupper() else type(exc).__name__
        print(json.dumps({'result':'diagnostic_failed','error':code,'hint':'在原product_sync目录激活已有py310环境；保留昨天运行目录及.env。此诊断不输出连接字符串或密钥。'},ensure_ascii=False))
        return 1
    finally:
        if client is not None:client.close()


if __name__=='__main__':raise SystemExit(main())
