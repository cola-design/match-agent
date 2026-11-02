import gzip
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from datetime import datetime

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from product_sync.scheduled import BEIJING,configure,run_scheduled,read_config,schedule_summary,window_bounds
from product_sync.source import SyncError
from product_sync.sync import apply_plan


def at(year=2026,month=11,day=2,hour=12,minute=0):return datetime(year,month,day,hour,minute,tzinfo=BEIJING)


def successful(args):
    args.window_guard()
    args.capture_report.update(mode='apply',phase='complete',result='success',run_id='fixture-run',applied_operations=3,verified_operations=3)
    return 0


class ScheduleTest(unittest.TestCase):
    def test_monthly_defaults_to_next_first_at_beijing_two(self):
        with tempfile.TemporaryDirectory() as directory:
            config=configure(directory,current=at())
            self.assertEqual(config['period'],'monthly');self.assertEqual(config['next_due'],'2026-12-01')
            self.assertEqual((config['start_hour'],config['end_hour']),(2,6))

    def test_monthly_first_can_be_today_before_start_hour(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(configure(directory,current=at(day=1,hour=1))['next_due'],'2026-11-01')

    def test_not_due_night_does_not_call_sync_or_replace_latest_report(self):
        with tempfile.TemporaryDirectory() as directory:
            configure(directory,current=at());p=Path(directory)/'latest.json';p.write_text('{"run_id":"previous"}')
            executor=Mock()
            self.assertEqual(run_scheduled(directory,lambda:at(day=3,hour=2),executor),0)
            executor.assert_not_called();self.assertEqual(json.loads(p.read_text()),{'run_id':'previous'})

    def test_daytime_after_missed_monthly_tick_never_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            configure(directory,current=at());executor=Mock()
            run_scheduled(directory,lambda:at(month=12,day=1,hour=10),executor)
            executor.assert_not_called()
            self.assertEqual(read_config(directory)['next_due'],'2026-12-01')

    def test_missed_monthly_run_catches_up_next_night_and_advances_once(self):
        with tempfile.TemporaryDirectory() as directory:
            configure(directory,current=at());executor=Mock(side_effect=successful)
            self.assertEqual(run_scheduled(directory,lambda:at(month=12,day=2,hour=2),executor),0)
            self.assertEqual(executor.call_count,1)
            self.assertEqual(read_config(directory)['next_due'],'2027-01-01')
            self.assertEqual(run_scheduled(directory,lambda:at(month=12,day=3,hour=2),executor),0)
            self.assertEqual(executor.call_count,1)

    def test_failure_is_not_retried_same_window_but_retries_next_night(self):
        with tempfile.TemporaryDirectory() as directory:
            configure(directory,current=at());failed=Mock(return_value=1)
            self.assertEqual(run_scheduled(directory,lambda:at(month=12,day=1,hour=2),failed),1)
            run_scheduled(directory,lambda:at(month=12,day=1,hour=3),failed)
            self.assertEqual(failed.call_count,1)
            self.assertEqual(read_config(directory)['next_due'],'2026-12-01')
            self.assertEqual(run_scheduled(directory,lambda:at(month=12,day=2,hour=2),successful),0)
            self.assertEqual(read_config(directory)['last_result'],'success')

    def test_unverified_zero_exit_does_not_advance_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            configure(directory,current=at())
            self.assertEqual(run_scheduled(directory,lambda:at(month=12,day=1,hour=2),lambda args:0),1)
            self.assertEqual(read_config(directory)['next_due'],'2026-12-01')

    def test_fifteen_days_is_exact_calendar_interval_across_month_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            configure(directory,period='15days',current=at(day=29))
            self.assertEqual(read_config(directory)['next_due'],'2026-11-30')
            run_scheduled(directory,lambda:at(day=30,hour=2),successful)
            self.assertEqual(read_config(directory)['next_due'],'2026-12-15')
            run_scheduled(directory,lambda:at(month=12,day=15,hour=2),successful)
            self.assertEqual(read_config(directory)['next_due'],'2026-12-30')

    def test_schedule_survives_restart_and_identical_reconfiguration(self):
        with tempfile.TemporaryDirectory() as directory:
            configure(directory,current=at())
            run_scheduled(directory,lambda:at(month=12,day=1,hour=2),successful)
            config=read_config(directory)
            self.assertEqual(configure(directory,current=at(month=12,day=2)),config)

    def test_window_deadline_guard_stops_worker_and_preserves_due_date(self):
        with tempfile.TemporaryDirectory() as directory:
            configure(directory,current=at());clock=[at(month=12,day=1,hour=2)]
            def worker(args):
                clock[0]=at(month=12,day=1,hour=6)
                args.window_guard()
            self.assertEqual(run_scheduled(directory,lambda:clock[0],worker),1)
            self.assertEqual(read_config(directory)['next_due'],'2026-12-01')
            self.assertEqual(json.loads((Path(directory)/'schedule_status.json').read_text())['error'],'SCHEDULE_WINDOW_CLOSED')

    def test_no_new_bulk_write_starts_after_window_guard_rejects(self):
        with tempfile.TemporaryDirectory() as directory:
            plan=Path(directory)/'plan.jsonl.gz'
            with gzip.open(plan,'wt') as f:f.write(json.dumps({'action':'new','after':{'material_code':'001'}})+'\n')
            collection=Mock()
            def closed():raise SyncError('SCHEDULE_WINDOW_CLOSED')
            with self.assertRaisesRegex(SyncError,'SCHEDULE_WINDOW_CLOSED'):apply_plan(collection,plan,lambda n:None,closed)
            collection.bulk_write.assert_not_called()

    def test_cross_midnight_window_is_anchored_to_its_start_day(self):
        config={'start_hour':22,'end_hour':6}
        bounds=window_bounds(config,at(day=3,hour=1))
        self.assertEqual(bounds[0],at(day=2,hour=22));self.assertEqual(bounds[1],at(day=3,hour=6))
        self.assertIsNone(window_bounds(config,at(day=3,hour=10)))

    def test_monthly_status_is_not_marked_stale_after_36_hours(self):
        with tempfile.TemporaryDirectory() as directory:
            configure(directory,current=at())
            self.assertFalse(schedule_summary(directory,at(day=20))['due'])
            self.assertTrue(schedule_summary(directory,at(month=12,day=1,hour=3))['due'])

    def test_invalid_window_and_month_day_do_not_create_schedule(self):
        for kwargs in ({'start_hour':2,'end_hour':2},{'month_day':31},{'period':'daily'}):
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(SyncError):configure(directory,current=at(),**kwargs)
                self.assertFalse((Path(directory)/'schedule.json').exists())


if __name__=='__main__':unittest.main()
