"""Source pagination regressions; standard library only, no MongoDB or network."""
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from product_sync.source import stage_snapshot, SyncError


def raw(code='00123456', **fields):
    row = {'ITEM_NUM': code, 'ITEM_DESC': 'fixture',
           'LAST_UPDATE_DATE': '2026-10-20 00:00:00.0'}
    row.update(fields)
    return row


class SourcePaginationTest(unittest.TestCase):
    def test_empty_object_end_marker_is_confirmed_for_both_source_types(self):
        for ending in ([], [{}]):
            with self.subTest(ending=ending), tempfile.TemporaryDirectory() as directory:
                calls = []
                def fetch(kind, page):
                    calls.append((kind, page))
                    rows = {1: [raw('001'), raw('002')], 2: [raw('003')],
                            3: [{}], 4: ending}[page]
                    return {'status': True, 'data': rows}
                folder = Path(directory)
                db, stats = stage_snapshot(folder, fetch, page_size=2)
                try:
                    for kind in (1, 2):
                        self.assertEqual(stats['type' + str(kind)]['rows'], 3)
                        self.assertEqual(stats['type' + str(kind)]['pages'], 2)
                        self.assertEqual(stats['type' + str(kind)]['termination']['confirmation_page'], 4)
                        self.assertIn((kind, 4), calls)
                        with gzip.open(folder / 'raw' / ('type' + str(kind)) / 'page-000003.json.gz', 'rt') as stream:
                            self.assertEqual(json.load(stream)['data'], [{}])
                    self.assertEqual(stats['unique_products'], 3)
                    self.assertEqual(db.execute('SELECT count(*) FROM type2').fetchone()[0], 3)
                finally:
                    db.close()

    def test_empty_object_at_start_or_after_full_page_stops(self):
        for first in ([{}], [raw('001'), raw('002')]):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as directory:
                def fetch(kind, page):
                    return {'status': True, 'data': first if page == 1 else [{}]}
                with self.assertRaisesRegex(SyncError, 'SOURCE_EMPTY_OBJECT_END_MARKER_WITHOUT_SHORT_PAGE'):
                    stage_snapshot(Path(directory), fetch, page_size=2)

    def test_data_after_empty_object_marker_stops_and_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            def fetch(kind, page):
                return {'status': True, 'data': {1: [raw('001')], 2: [{}], 3: [raw('002')]}[page]}
            folder = Path(directory)
            with self.assertRaisesRegex(SyncError, 'SOURCE_DATA_AFTER_EMPTY_OBJECT_END_MARKER'):
                stage_snapshot(folder, fetch, page_size=2)
            with gzip.open(folder / 'raw/type1/page-000003.json.gz', 'rt') as stream:
                self.assertEqual(json.load(stream)['data'][0]['ITEM_NUM'], '002')

    def test_real_invalid_products_and_mixed_empty_objects_still_stop(self):
        for rows in ([{'ITEM_DESC': 'missing code'}], [raw('002'), {}], [{}, {}],
                     [raw(code=None)], [raw(code='')], [raw(code=123)], [raw(code='00 123')]):
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as directory:
                def fetch(kind, page):
                    return {'status': True, 'data': [raw('001')] if page == 1 else rows}
                with self.assertRaisesRegex(SyncError, 'SOURCE_MATERIAL_CODE_NOT_NONEMPTY_STRING'):
                    stage_snapshot(Path(directory), fetch, page_size=2)

    def test_end_marker_probe_failure_still_stops(self):
        for response in ({'status': False, 'data': []}, {'status': True, 'data': None}):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory:
                def fetch(kind, page):
                    return {'status': True, 'data': [raw('001')] if page == 1 else [{}]} if page < 3 else response
                with self.assertRaisesRegex(SyncError, 'SOURCE_BUSINESS_OR_RESPONSE_ERROR'):
                    stage_snapshot(Path(directory), fetch, page_size=2)

    def test_end_marker_probe_respects_page_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def fetch(kind, page):
                calls.append((kind, page))
                return {'status': True, 'data': [raw('001')] if page == 1 else [{}]}
            with self.assertRaisesRegex(SyncError, 'SOURCE_MAX_PAGES_REACHED'):
                stage_snapshot(Path(directory), fetch, page_size=2, max_pages=2)
            self.assertNotIn((1, 3), calls)

    def test_short_page_does_not_end_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def fetch(kind, page):
                calls.append((kind, page))
                return {'status': True, 'data': [raw(str(page))] if kind == 1 and page <= 2 else []}
            db, stats = stage_snapshot(Path(directory), fetch, page_size=2)
            try:
                self.assertEqual(stats['unique_products'], 2)
                self.assertIn((1, 3), calls)
                self.assertIn((2, 1), calls)
                self.assertEqual(stats['type1']['termination']['format'], 'empty_array')
            finally:
                db.close()

    def test_repeated_page_still_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SyncError, 'REPEATED_PAGE_REFETCH_SNAPSHOT'):
                stage_snapshot(Path(directory), lambda k, p: {'status': True, 'data': [raw()]})

    def test_duplicate_versions_keep_newest_in_both_orders(self):
        old = raw(ITEM_DESC='older', LAST_UPDATE_DATE='2026-10-19T00:00:00')
        new = raw(ITEM_DESC='newer', LAST_UPDATE_DATE='2026-10-21T00:00:00')
        for versions in ([old, new], [new, old]):
            with self.subTest(versions=versions), tempfile.TemporaryDirectory() as directory:
                def fetch(kind, page):
                    return {'status': True, 'data': [versions[page - 1]] if kind == 1 and page <= 2 else []}
                db, stats = stage_snapshot(Path(directory), fetch)
                try:
                    self.assertEqual(json.loads(db.execute('SELECT body FROM type1').fetchone()[0])['ITEM_DESC'], 'newer')
                    self.assertEqual(stats['type1']['duplicate_versions_resolved'], 1)
                finally:
                    db.close()

    def test_probe_transport_failure_does_not_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            def fetch(kind, page):
                if page == 3:
                    raise SyncError('SOURCE_NETWORK_UNAVAILABLE')
                return {'status': True, 'data': [raw()] if page == 1 else [{}]}
            with self.assertRaisesRegex(SyncError, 'SOURCE_NETWORK_UNAVAILABLE'):
                stage_snapshot(Path(directory), fetch, page_size=2)

    def test_oversized_confirmation_page_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            def fetch(kind, page):
                return {'status': True, 'data': {1: [raw()], 2: [{}], 3: [{}, {}, {}]}[page]}
            with self.assertRaisesRegex(SyncError, 'SOURCE_IGNORED_PAGE_SIZE'):
                stage_snapshot(Path(directory), fetch, page_size=2)

    def test_business_failure_with_empty_object_is_not_end_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            def fetch(kind, page):
                return {'status': True, 'data': [raw()]} if page == 1 else {'status': False, 'data': [{}]}
            with self.assertRaisesRegex(SyncError, 'SOURCE_BUSINESS_OR_RESPONSE_ERROR'):
                stage_snapshot(Path(directory), fetch, page_size=2)


if __name__ == '__main__':
    unittest.main()
