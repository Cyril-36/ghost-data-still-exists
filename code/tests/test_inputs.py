import csv
import tempfile
import unittest
from pathlib import Path

import helpers
from evaluation.fixtures import FIXTURES, write_fixture
from ghostaudit.contracts import ContractError
from ghostaudit.inputs import load_context, load_requests


class RealDatasetTests(unittest.TestCase):
    @unittest.skipUnless((helpers.DATASET / 'evidence_files.csv').exists(), 'supplied dataset not present')
    def test_supplied_dataset_loads_with_documented_counts(self):
        context = load_context(helpers.DATASET)
        self.assertEqual(len(load_requests(helpers.DATASET / 'deletion_requests.csv')), 518)
        self.assertEqual(len(load_requests(helpers.DATASET / 'sample_deletion_requests.csv')), 32)
        self.assertEqual((len(context.identities), len(context.audit_log), len(context.activities),
                          len(context.rules), len(context.manifest)), (850, 306, 4011, 15, 94))
        self.assertEqual(len(context.evidence), 94 + 4011 + 306)


class InputValidationTests(unittest.TestCase):
    """Start from a valid fixture input directory, corrupt one thing, expect rejection."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = write_fixture(FIXTURES['fixture_a'], Path(self.tmp.name)) / 'input'

    def tearDown(self):
        self.tmp.cleanup()

    def rewrite(self, name, mutate):
        path = self.root / name
        with open(path, encoding='utf-8', newline='') as f:
            rows = list(csv.reader(f))
        mutate(rows)
        with open(path, 'w', encoding='utf-8', newline='') as f:
            csv.writer(f, lineterminator='\n').writerows(rows)

    def assert_rejected(self, loader=None):
        with self.assertRaises(ContractError):
            (loader or (lambda: load_context(self.root)))()

    def requests(self):
        return load_requests(self.root / 'deletion_requests.csv')

    def test_valid_fixture_loads(self):
        context = load_context(self.root)
        self.assertEqual(context.evidence['fx_file_A'].kind, 'FILE')
        self.assertEqual([r.case_id for r in self.requests()], ['fx_case_A'])

    def test_wrong_header(self):
        self.rewrite('deletion_requests.csv', lambda rows: rows[0].reverse())
        self.assert_rejected(self.requests)

    def test_ragged_row(self):
        self.rewrite('deletion_requests.csv', lambda rows: rows[1].append('extra'))
        self.assert_rejected(self.requests)

    def test_duplicate_case(self):
        self.rewrite('deletion_requests.csv', lambda rows: rows.append(list(rows[1])))
        self.assert_rejected(self.requests)

    def test_naive_timestamp(self):
        self.rewrite('deletion_requests.csv', lambda rows: rows[1].__setitem__(5, '2026-01-03T12:00:00'))
        self.assert_rejected(self.requests)

    def test_unknown_reported_status(self):
        self.rewrite('deletion_requests.csv', lambda rows: rows[1].__setitem__(3, 'DONE'))
        self.assert_rejected(self.requests)

    def test_manifest_path_escape(self):
        for bad in ['../outside.json', '/etc/hosts', 'evidence/../../outside.json']:
            with self.subTest(bad=bad):
                self.rewrite('evidence_files.csv', lambda rows, bad=bad: rows[1].__setitem__(1, bad))
                self.assert_rejected()

    def test_manifest_symlink_escape(self):
        outside = Path(self.tmp.name) / 'outside.json'
        outside.write_text('{}')
        (self.root / 'evidence/records/link.json').symlink_to(outside)
        self.rewrite('evidence_files.csv', lambda rows: rows[1].__setitem__(1, 'evidence/records/link.json'))
        self.assert_rejected()

    def test_manifest_missing_file(self):
        (self.root / 'evidence/records/profile.json').unlink()
        self.assert_rejected()

    def test_duplicate_file_id(self):
        self.rewrite('evidence_files.csv', lambda rows: rows.append(list(rows[1])))
        self.assert_rejected()

    def test_evidence_id_collision_across_kinds(self):
        self.rewrite('system_activity.csv', lambda rows: rows.append(
            ['fx_file_A', '2026-01-02T00:00:00Z', 'DELETE_RECORD', 'a', 'b', 'fx_account_A', 'SUCCESS', '']))
        self.assert_rejected()

    def test_unknown_rule_action(self):
        self.rewrite('deletion_rules.csv', lambda rows: rows[1].__setitem__(3, 'PURGE'))
        self.assert_rejected()

    def test_unknown_activity_result(self):
        self.rewrite('system_activity.csv', lambda rows: rows.append(
            ['act_x', '2026-01-02T00:00:00Z', 'DELETE_RECORD', 'a', 'b', 'r', 'MAYBE', '']))
        self.assert_rejected()

    def test_utf8_bom_is_tolerated(self):
        path = self.root / 'deletion_requests.csv'
        path.write_bytes(b'\xef\xbb\xbf' + path.read_bytes())
        self.assertEqual(len(self.requests()), 1)


if __name__ == '__main__':
    unittest.main()
