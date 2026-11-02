"""Persistent monthly / 15-day scheduling. Not-due checks never contact source or MongoDB."""
import argparse
from datetime import date,datetime,time,timedelta,timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from product_sync.source import SyncError
from product_sync.sync import atomic_json,execute,run_lock

BEIJING=timezone(timedelta(hours=8))


def local_now():return datetime.now(BEIJING)


def window_bounds(config,current):
    current=current.astimezone(BEIJING)
    start=config['start_hour'];end=config['end_hour'];day=current.date()
    if start<end:
        if not start<=current.hour<end:return None
        first=day;last=day
    else:
        if current.hour>=start:first=day;last=day+timedelta(days=1)
        elif current.hour<end:first=day-timedelta(days=1);last=day
        else:return None
    return (datetime.combine(first,time(start),BEIJING),datetime.combine(last,time(end),BEIJING))


def next_monthly(day,month_day):
    """First configured monthly date on or after day. Days 1..28 exist every month."""
    candidate=day.replace(day=month_day)
    if candidate>=day:return candidate
    year=day.year+(day.month==12);month=1 if day.month==12 else day.month+1
    return date(year,month,month_day)


def validate_config(config):
    if not isinstance(config,dict) or config.get('schema_version')!=1:raise SyncError('INVALID_SCHEDULE_CONFIG')
    if config.get('timezone')!='Asia/Shanghai':raise SyncError('INVALID_SCHEDULE_TIMEZONE')
    if config.get('period') not in ('monthly','15days'):raise SyncError('INVALID_SCHEDULE_PERIOD')
    for name in ('start_hour','end_hour'):
        if type(config.get(name)) is not int or not 0<=config[name]<=23:raise SyncError('INVALID_SCHEDULE_HOUR')
    if config['start_hour']==config['end_hour']:raise SyncError('EMPTY_SCHEDULE_WINDOW')
    if type(config.get('month_day')) is not int or not 1<=config['month_day']<=28:raise SyncError('INVALID_MONTH_DAY')
    try:date.fromisoformat(config['next_due'])
    except (ValueError,TypeError,KeyError):raise SyncError('INVALID_SCHEDULE_DATE') from None


def configure(state,period='monthly',month_day=1,start_hour=2,end_hour=6,current=None):
    state=Path(state);state.mkdir(parents=True,exist_ok=True)
    current=(current or local_now()).astimezone(BEIJING)
    config={'schema_version':1,'timezone':'Asia/Shanghai','period':period,'month_day':month_day,
            'start_hour':start_hour,'end_hour':end_hour,'next_due':current.date().isoformat()}
    validate_config(config)
    earliest=current.date()+(timedelta(days=1) if current.hour>=start_hour else timedelta())
    config['next_due']=(next_monthly(earliest,month_day) if period=='monthly' else earliest).isoformat()
    # Configuration is a one-time deployment operation, not a recurring manual update.
    with run_lock(state,'schedule.lock'),run_lock(state):
        existing=state/'schedule.json'
        if existing.exists():
            previous=json.loads(existing.read_text(encoding='utf-8-sig'))
            keys=('period','month_day','start_hour','end_hour')
            if all(previous.get(k)==config[k] for k in keys):
                validate_config(previous);return previous
        atomic_json(existing,config)
    return config


def read_config(state):
    try:config=json.loads((Path(state)/'schedule.json').read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):raise SyncError('SCHEDULE_NOT_CONFIGURED') from None
    validate_config(config);return config


def schedule_summary(state,current=None):
    config=read_config(state);current=(current or local_now()).astimezone(BEIJING)
    # Overdue is schedule-based; a monthly schedule is not stale after 36 hours.
    due=datetime.combine(date.fromisoformat(config['next_due']),time(config['start_hour']),BEIJING)
    return {k:config[k] for k in ('period','month_day','start_hour','end_hour','timezone','next_due')} | {
        'due_since':due.isoformat(),'due':current>=due,'last_result':config.get('last_result'),
        'last_attempt_window':config.get('last_attempt_window'),'last_success_run_id':config.get('last_success_run_id')}


def run_scheduled(state,current_provider=local_now,executor=execute):
    state=Path(state);state.mkdir(parents=True,exist_ok=True)
    with run_lock(state,'schedule.lock'):
        config=read_config(state);current=current_provider().astimezone(BEIJING)
        bounds=window_bounds(config,current)
        def status(result,**values):
            output={'checked_at':current_provider().isoformat(),'result':result,'next_due':config['next_due'],**values}
            atomic_json(state/'schedule_status.json',output)
            print(json.dumps(output,ensure_ascii=False),flush=True)
        if bounds is None:status('outside_night_window');return 0
        window_day=bounds[0].date()
        if window_day<date.fromisoformat(config['next_due']):status('not_due');return 0
        if config.get('last_attempt_window')==window_day.isoformat():status('already_attempted_this_window');return 0
        config.update(last_attempt_window=window_day.isoformat(),last_result='running')
        atomic_json(state/'schedule.json',config)
        deadline=bounds[1];capture={}
        def guard():
            now=current_provider().astimezone(BEIJING)
            if not bounds[0]<=now<deadline:raise SyncError('SCHEDULE_WINDOW_CLOSED')
        try:
            args=SimpleNamespace(state_dir=str(state),apply=True,check=False,reuse_snapshot=None,
                                 window_guard=guard,capture_report=capture)
            result=executor(args)
            complete=(result==0 and capture.get('mode')=='apply' and capture.get('result')=='success'
                      and capture.get('phase')=='complete' and 'verified_operations' in capture
                      and capture.get('applied_operations')==capture['verified_operations'])
            if complete:
                next_day=window_day+timedelta(days=1)
                config['next_due']=(next_monthly(next_day,config['month_day']) if config['period']=='monthly'
                                    else window_day+timedelta(days=15)).isoformat()
                config.update(last_result='success',last_success_run_id=capture.get('run_id'))
                atomic_json(state/'schedule.json',config);status('success',run_id=capture.get('run_id'));return 0
            config['last_result']='failed';atomic_json(state/'schedule.json',config)
            status('failed',error=capture.get('error','SCHEDULED_SYNC_NOT_VERIFIED'));return 1
        except Exception as exc:
            config['last_result']='failed';atomic_json(state/'schedule.json',config)
            status('failed',error=str(exc) if isinstance(exc,SyncError) else type(exc).__name__);return 1


def main():
    load_dotenv(Path(__file__).with_name('.env'),override=False,encoding='utf-8-sig')
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--configure',action='store_true')
    modes.add_argument('--status',action='store_true')
    parser.add_argument('--period',choices=['monthly','15days'],default='monthly')
    parser.add_argument('--month-day',type=int,default=1)
    parser.add_argument('--start-hour',type=int,default=2)
    parser.add_argument('--end-hour',type=int,default=6)
    parser.add_argument('--state-dir',default=os.environ.get('SYNC_STATE_DIR','sync_state'))
    args=parser.parse_args()
    try:
        if args.configure:
            print(json.dumps(configure(args.state_dir,args.period,args.month_day,args.start_hour,args.end_hour),ensure_ascii=False,indent=2));return 0
        if args.status:print(json.dumps(schedule_summary(args.state_dir),ensure_ascii=False,indent=2));return 0
        return run_scheduled(args.state_dir)
    except (SyncError,ValueError) as exc:
        print(str(exc) if isinstance(exc,SyncError) else 'INVALID_SCHEDULE_CONFIGURATION',flush=True);return 2


if __name__=='__main__':raise SystemExit(main())
