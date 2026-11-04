"""Regressions for the actual SQL timestamp shape returned by the source API."""
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from product_sync.source import source_stamp, newer_version, SyncError
from product_sync.sync import clean_product, decide


def raw(date='2026-05-17 00:00:00.0', **fields):
    return {'ITEM_NUM':'fixture-001','ITEM_ID':1,'ITEM_DESC':'fixture',
        'CATEGORY_CODE':'335.1','CATEGORY_NAME':'fixture',
        'SPEC_STRING':'额定电流&100A','STATUS':'有效',
        'LAST_UPDATE_DATE':date,**fields}


class SourceDatesTest(unittest.TestCase):
    def test_actual_sql_shapes_have_independent_utc_expectations(self):
        for text,expected in (
            ('2026-05-17 00:00:00.0',datetime(2026,5,16,16,tzinfo=timezone.utc)),
            ('2026-01-29 00:00:00.0',datetime(2026,1,28,16,tzinfo=timezone.utc)),
            ('2026-07-02 17:48:01.0',datetime(2026,7,2,9,48,1,tzinfo=timezone.utc)),
        ):
            with self.subTest(text=text):self.assertEqual(source_stamp(text),expected)

    def test_fractional_precision_and_offsets_are_preserved(self):
        for fraction,microsecond in (('1',100000),('12',120000),('123',123000),
                ('1234',123400),('12345',123450),('123456',123456)):
            for separator in (' ','T'):
                for suffix,hour in (('',9),('+08:00',9),('Z',17),('+00:00',17)):
                    text='2026-07-02'+separator+'17:48:01.'+fraction+suffix
                    with self.subTest(text=text):
                        self.assertEqual(source_stamp(text),datetime(2026,7,2,hour,48,1,microsecond,tzinfo=timezone.utc))

    def test_datetime_and_existing_iso_formats_keep_their_interpretation(self):
        expected=datetime(2026,5,16,16,tzinfo=timezone.utc)
        for value in ('2026-05-17T00:00:00','2026-05-17',datetime(2026,5,17),
                datetime(2026,5,17,tzinfo=timezone(timedelta(hours=8)))):
            self.assertEqual(source_stamp(value),expected)

    def test_invalid_or_unsupported_precision_remains_unknown(self):
        for value in (None,'','bad-date','2026-02-30 00:00:00.0',
                '2026-05-17 25:00:00.0','2026-05-17 00:00:00.1234567'):
            with self.subTest(value=value):self.assertIsNone(source_stamp(value))

    def test_equal_source_and_bson_datetime_is_unchanged(self):
        candidate,version=clean_product(raw(),[])
        old=copy.deepcopy(candidate)
        old.update(last_update_date=datetime(2026,5,17),data_status='ACTIVE',publication_basis='AUTO_RULE_V1')
        self.assertEqual(decide(old,candidate,version,'test'),('unchanged',None))

    def test_older_or_invalid_date_cannot_overwrite_bson_date(self):
        old,_=clean_product(raw(),[])
        old.update(last_update_date=datetime(2026,5,17),data_status='ACTIVE')
        for value in ('2026-05-16 00:00:00.0','bad-date'):
            with self.subTest(value=value):
                self.assertEqual(decide(old,*clean_product(raw(value,ITEM_DESC='changed'),[]),'test'),
                    ('older_or_unknown_source_version',None))

    def test_newer_date_with_same_business_content_preserves_approval(self):
        old,_=clean_product(raw(),[])
        old.update(last_update_date=datetime(2026,5,17),data_status='ACTIVE',publication_basis='AUTO_RULE_V1')
        action,after=decide(old,*clean_product(raw('2026-05-18 00:00:00.0'),[]),'test')
        self.assertEqual(action,'metadata_only')
        self.assertEqual(after['data_status'],'ACTIVE')
        self.assertEqual(after['publication_basis'],'AUTO_RULE_V1')
        self.assertEqual(decide(after,*clean_product(raw('2026-05-18 00:00:00.0'),[]),'again'),('unchanged',None))

    def test_type2_date_is_parsed_and_its_older_version_still_protected(self):
        attribute={'ATTR_NAME':'额定电流','ATTR_VALUE':'100A','STATUS':'有效',
            'LAST_UPDATE_DATE':'2026-05-18 00:00:00.0'}
        candidate,version=clean_product(raw(),[attribute])
        self.assertEqual(version,'2026-05-17T16:00:00+00:00')
        old=copy.deepcopy(candidate)
        old.update(data_status='ACTIVE',sync_meta={'type2_version':'2026-05-18T16:00:00+00:00'})
        self.assertEqual(decide(old,candidate,version,'test'),('older_attribute_snapshot',None))

    def test_duplicate_version_ordering_with_sql_fraction(self):
        self.assertTrue(newer_version(raw(),raw('2026-05-18 00:00:00.0')))
        self.assertFalse(newer_version(raw('2026-05-18 00:00:00.0'),raw()))
        with self.assertRaises(SyncError):newer_version(raw(),raw('bad-date'))


if __name__=='__main__':unittest.main()
