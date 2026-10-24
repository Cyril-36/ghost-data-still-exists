import builtins
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from evaluation.fixtures import FIXTURES, write_fixture
from ghostaudit.inputs import load_context, load_requests
from ghostaudit.output import read_output, validate_output

# Plan section 19.2, copied verbatim: the exact expected array for fixture A.
PLAN_FIXTURE_A = json.loads('''[
  {"step_no":1,"phase":"FINDING","system_id":"account_service","resource_id":"fx_account_A","operation":"REPORT_REMAINING_RECORD","state":"PRESENT","evidence_id":"fx_file_A"},
  {"step_no":2,"phase":"REMEDIATION","system_id":"account_service","resource_id":"fx_account_A","operation":"DELETE_RECORD","state":"ABSENT","evidence_id":""},
  {"step_no":3,"phase":"VERIFICATION","system_id":"account_service","resource_id":"fx_account_A","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":""}
]''')


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.bases = {name: write_fixture(f, self.out) for name, f in FIXTURES.items()}

    def tearDown(self):
        self.tmp.cleanup()

    def expected_text(self, name):
        return (self.bases[name] / 'expected_output.csv').read_text(encoding='utf-8')

    def test_fixture_a_matches_plan_exactly(self):
        lines = self.expected_text('fixture_a').splitlines()
        self.assertEqual(lines[0], 'case_id,deletion_status,steps_json')
        [row] = read_output(self.bases['fixture_a'] / 'expected_output.csv')
        self.assertEqual((row.case_id, row.deletion_status), ('fx_case_A', 'INCOMPLETE'))
        [raw] = list(csv.DictReader(lines))
        self.assertEqual(json.loads(raw['steps_json']), PLAN_FIXTURE_A)

    def test_fixture_c_is_not_applicable_with_empty_array(self):
        self.assertEqual(self.expected_text('fixture_c'),
                         'case_id,deletion_status,steps_json\nfx_case_C,NOT_APPLICABLE,[]\n')

    def test_fixture_c_has_tempting_present_data_and_complete_claim(self):
        root = self.bases['fixture_c'] / 'input'
        [req] = load_requests(root / 'deletion_requests.csv')
        self.assertEqual(req.reported_status, 'COMPLETE')
        self.assertIn('only', req.request_text)
        records = json.loads((root / 'evidence/records/records.json').read_text(encoding='utf-8'))
        self.assertTrue(all(r['result'] == 'FOUND' for r in records['records']))

    def test_fixture_inputs_are_valid_and_citations_eligible(self):
        for name, base in self.bases.items():
            with self.subTest(name):
                root = base / 'input'
                context = load_context(root)
                requests = load_requests(root / 'deletion_requests.csv')
                validate_output(read_output(base / 'expected_output.csv'), requests, context.evidence,
                                FIXTURES[name].fallback_ids)
                for ident in context.identities:  # identity rows are self-consistent
                    self.assertEqual(ident.email_hash, hashlib.sha256(ident.email.encode()).hexdigest())

    def test_generation_is_deterministic(self):
        with tempfile.TemporaryDirectory() as other:
            for name in FIXTURES:
                again = write_fixture(FIXTURES[name], Path(other))
                for path in sorted(self.bases[name].rglob('*')):
                    if path.is_file():
                        twin = again / path.relative_to(self.bases[name])
                        self.assertEqual(path.read_bytes(), twin.read_bytes(), path)


class LabelIsolationTests(unittest.TestCase):
    def test_expected_answer_is_outside_inference_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            for fixture in FIXTURES.values():
                base = write_fixture(fixture, Path(tmp))
                self.assertEqual(list((base / 'input').rglob('expected*')), [])
                self.assertTrue((base / 'expected_output.csv').exists())

    def test_inference_loaders_open_only_allowlisted_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = write_fixture(FIXTURES['fixture_a'], Path(tmp))
            opened = []
            real_open = builtins.open

            def spy(file, *args, **kwargs):
                opened.append(Path(file).name)
                return real_open(file, *args, **kwargs)

            with mock.patch('builtins.open', spy):
                load_context(base / 'input')
                load_requests(base / 'input' / 'deletion_requests.csv')
        self.assertEqual(sorted(opened), sorted([
            'user_identities.csv', 'user_audit_log.csv', 'system_activity.csv',
            'deletion_rules.csv', 'evidence_files.csv', 'deletion_requests.csv']))

    def test_inference_package_never_references_labels_or_evaluation(self):
        forbidden = ('evaluation', 'expected_output', 'sample_output', 'fixtures')
        for path in (helpers.CODE / 'ghostaudit').rglob('*.py'):
            source = path.read_text(encoding='utf-8')
            for word in forbidden:
                self.assertNotIn(word, source, f'{path.name} mentions {word!r}')
        main = (helpers.CODE / 'main.py').read_text(encoding='utf-8')
        self.assertNotIn('import evaluation', main)
        self.assertNotIn('from evaluation', main)


if __name__ == '__main__':
    unittest.main()
