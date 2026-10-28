import gzip
import json
from pathlib import Path
import tempfile
import unittest

from product_sync.diagnose_snapshot import code_issue, diagnose


class SavedSnapshotDiagnosticTest(unittest.TestCase):
    def test_code_validation_matches_delivered_fetcher(self):
        cases = [({'ITEM_NUM': '00123'}, ''), ({'ITEM_NUM': ' 00123 '}, ''),
                 ({}, 'MATERIAL_CODE_FIELD_MISSING'),
                 ({'ITEM_NUM': None}, 'MATERIAL_CODE_NOT_STRING'),
                 ({'ITEM_NUM': 123}, 'MATERIAL_CODE_NOT_STRING'),
                 ({'ITEM_NUM': True}, 'MATERIAL_CODE_NOT_STRING'),
                 ({'ITEM_NUM': '  '}, 'MATERIAL_CODE_EMPTY_OR_WHITESPACE'),
                 ({'ITEM_NUM': '00\t123'}, 'MATERIAL_CODE_CONTAINS_INTERNAL_WHITESPACE'),
                 (None, 'ROW_NOT_OBJECT')]
        for row, issue in cases:
            with self.subTest(row=row):
                self.assertEqual(code_issue(row), issue)

    def test_existing_failed_page_can_be_diagnosed_without_repull_or_database(self):
        run_id = '20261028T060837Z_78e887f2'
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / run_id
            (folder / 'raw/type1').mkdir(parents=True)
            (folder / 'raw/type2').mkdir()
            original = {'run_id': run_id, 'phase': 'failed', 'error': 'SOURCE_MATERIAL_CODE_NOT_NONEMPTY_STRING'}
            (state / 'latest.json').write_text(json.dumps(original))
            (folder / 'report.json').write_text(json.dumps(original))
            fixtures = [(1, 117, []), (2, 93, [{'ITEM_NUM': '00123'}]),
                        (2, 94, [{'ITEM_NUM': '00123'}, {'ITEM_NUM': None, 'ATTR_ID': 'a', 'SA_ID': 's', 'private_extra': 'not copied'}])]
            for kind, page, rows in fixtures:
                with gzip.open(folder / ('raw/type%s/page-%06d.json.gz' % (kind, page)), 'wt', encoding='utf-8') as stream:
                    json.dump({'status': True, 'data': rows}, stream)
            report, output = diagnose(state)
            self.assertEqual(report['invalid_code_rows'], 1)
            page = report['pages_checked'][1]
            self.assertEqual((page['source_type'], page['page']), (2, 94))
            example = page['examples'][0]
            self.assertEqual(example['row_on_page'], 2)
            self.assertEqual(example['record_identifiers'], {'ATTR_ID': 'a', 'SA_ID': 's'})
            self.assertNotIn('not copied', output.read_text())
            self.assertFalse(report['network_used'])
            self.assertFalse(report['database_accessed'])
            self.assertEqual(json.loads((state / 'latest.json').read_text()), original)
            self.assertEqual(json.loads((folder / 'report.json').read_text()), original)

    def test_run_id_cannot_escape_state_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'INVALID_OR_MISSING_RUN_ID'):
                diagnose(directory, '../another-directory')

    def test_diagnostic_examples_are_bounded_without_losing_error_count(self):
        from product_sync.diagnose_snapshot import page_diagnostic
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'page-000001.json.gz'
            with gzip.open(path, 'wt', encoding='utf-8') as stream:
                json.dump({'status': True, 'data': [{'ITEM_NUM': None}] * 25}, stream)
            result = page_diagnostic(path, 1)
            self.assertEqual(result['invalid_code_rows'], 25)
            self.assertEqual(len(result['examples']), 20)
            self.assertTrue(result['examples_truncated'])


if __name__ == '__main__':
    unittest.main()
