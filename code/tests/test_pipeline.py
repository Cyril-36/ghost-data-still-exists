"""End-to-end audit behaviour on synthetic fixtures, plus dataset regressions."""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

import helpers
from helpers import evidence, request, step
from evaluation.fixtures import FIXTURES, write_fixture
from evaluation.scoring import score
from ghostaudit.audit import load_disclosures, run_audit
from ghostaudit.contracts import ContractError, OutputRow
from ghostaudit.corpus import build_corpus
from ghostaudit.identity import IdentityIndex, resolve_subject
from ghostaudit.inputs import load_context, load_requests
from ghostaudit.output import check_row, read_output, validate_output
from ghostaudit.planner import discover, obligation
from ghostaudit.policy import Policy
from ghostaudit.state import reconstruct
from ghostaudit.tools import ToolError, Tools

HAS_DATASET = (helpers.DATASET / 'evidence_files.csv').exists()


class FixturePredictionTests(unittest.TestCase):
    """Predictions are generated from fixture inputs; the answer file is unreadable meanwhile."""

    def test_every_fixture_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, fixture in FIXTURES.items():
                with self.subTest(name):
                    base = write_fixture(fixture, Path(tmp))
                    answer = base / 'expected_output.csv'
                    os.chmod(answer, 0)
                    try:
                        summary = run_audit(base / 'input', base / 'input/deletion_requests.csv',
                                            base / 'predicted.csv', base / 'run')
                    finally:
                        os.chmod(answer, 0o644)
                    predicted, expected = read_output(base / 'predicted.csv'), read_output(answer)
                    result = score(predicted, expected)
                    self.assertEqual(result['exact_row_accuracy']['rate'], 1.0, result['diffs'])
                    self.assertEqual(set(summary['fallback_ids']), set(fixture.fallback_ids))

    def test_fallback_rows_need_their_run_disclosures(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = write_fixture(FIXTURES['fixture_a'], Path(tmp))
            requests_csv = base / 'input/deletion_requests.csv'
            # A non-English request: intent unresolved -> disclosed fallback, no steps.
            text = requests_csv.read_text(encoding='utf-8').replace(
                'Please delete my account and its personal data.', 'Por favor eliminen mi cuenta y mis datos.')
            requests_csv.write_text(text, encoding='utf-8')
            summary = run_audit(base / 'input', requests_csv, base / 'p.csv', base / 'run')
            self.assertEqual(summary['fallback_ids'], ['fx_case_A'])
            rows, requests = read_output(base / 'p.csv'), load_requests(requests_csv)
            self.assertEqual(rows[0], OutputRow('fx_case_A', 'INCOMPLETE', ()))
            with self.assertRaises(ContractError):
                validate_output(rows, requests)                                     # undisclosed: rejected
            validate_output(rows, requests, None, load_disclosures(base / 'run', base / 'p.csv'))
            (base / 'p.csv').write_text((base / 'p.csv').read_text() + '\n')
            with self.assertRaises(ValueError):
                load_disclosures(base / 'run', base / 'p.csv')                     # CSV no longer matches run
            disclosure = json.loads((base / 'run/unresolved_cases.jsonl').read_text())
            self.assertFalse(disclosure['confirmed_violation'])


class FacetRuleTests(unittest.TestCase):
    EV = evidence('fx_ev1')

    def test_record_and_restore_steps_may_share_a_resource(self):
        row = OutputRow('case_33', 'INCOMPLETE', (
            step('FINDING', 'REPORT_RESTORE_RISK', 'ACTIVE', resource='fx_b', evidence_id='fx_ev1'),
            step('REMEDIATION', 'BLOCK_RESTORE', 'BLOCKED', resource='fx_b'),
            step('VERIFICATION', 'VERIFY_RECORD_PRESENT', 'PRESENT', resource='fx_b'),
            step('VERIFICATION', 'VERIFY_RESTORE_BLOCKED', 'BLOCKED', resource='fx_b')))
        check_row(row, request('case_33'), self.EV)

    def test_contradictory_record_treatments_are_rejected(self):
        row = OutputRow('case_33', 'INCOMPLETE', (
            step('FINDING', 'REPORT_REMAINING_RECORD', 'PRESENT', evidence_id='fx_ev1'),
            step('REMEDIATION', 'DELETE_RECORD', 'ABSENT'),
            step('REMEDIATION', 'REDACT_RECORD', 'REDACTED'),
            step('VERIFICATION', 'VERIFY_RECORD_ABSENT', 'ABSENT')))
        with self.assertRaises(ContractError):
            check_row(row, request('case_33'), self.EV)

    def test_disclosed_fallback_may_not_contain_remediation(self):
        row = OutputRow('case_33', 'INCOMPLETE', (step('REMEDIATION', 'DELETE_RECORD', 'ABSENT'),
                                                  step('VERIFICATION', 'VERIFY_RECORD_ABSENT', 'ABSENT')))
        with self.assertRaises(ContractError):
            check_row(row, request('case_33'), self.EV, disclosed_fallback=True)
        check_row(OutputRow('case_33', 'INCOMPLETE', (step('VERIFICATION', 'VERIFY_RECORD_ABSENT', 'ABSENT'),)),
                  request('case_33'), self.EV, disclosed_fallback=True)


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = write_fixture(FIXTURES['fixture_replica'], Path(self.tmp.name))
        ctx = load_context(base / 'input')
        self.tools = Tools(build_corpus(ctx), IdentityIndex(ctx), Policy(ctx.rules))
        self.cutoff = load_requests(base / 'input/deletion_requests.csv')[0].audit_at

    def tearDown(self):
        self.tmp.cleanup()

    def test_handles_bounds_and_cutoff(self):
        with self.assertRaises(ToolError):
            self.tools.inspect_source('../etc/passwd', self.cutoff)
        early = self.cutoff.replace(year=2025)
        self.assertFalse(self.tools.inspect_source('fx_file_I', early)['eligible'])
        found = self.tools.search_identifiers(['not an id'], self.cutoff, limit=1, user_references=['fx_user_I'])
        self.assertEqual(found['unrecognized_identifiers'], ['not an id'])
        self.assertEqual(found['observations']['returned'], 1)
        self.assertTrue(found['observations']['truncated'])

    def test_sql_query_is_a_fixed_template(self):
        result = self.tools.query_sql_snapshot('fx_file_I', 'customer_data_failover_replica', self.cutoff,
                                               resource_ids=['fx_account_I'])
        self.assertEqual([r['resource_id'] for r in result['rows']['items']], ['fx_account_I'])
        with self.assertRaises(ToolError):
            self.tools.query_sql_snapshot('fx_file_I', 'sqlite_master; DROP TABLE x', self.cutoff)

    def test_identity_and_retention(self):
        self.assertEqual(self.tools.expand_identity('PERSON.I@example.test', self.cutoff)['user_ids'], ['fx_user_I'])
        retention = self.tools.check_retention('security_service', 'fx_audit_I', self.cutoff)
        self.assertEqual(retention['treatments']['SECURITY_AUDIT_LOG']['action'], 'RETAIN')


@unittest.skipUnless(HAS_DATASET, 'supplied dataset not present')
class DatasetRegressionTests(unittest.TestCase):
    """Regressions on the supplied corpus. Inference never sees these expectations."""

    @classmethod
    def setUpClass(cls):
        cls.ctx = load_context(helpers.DATASET)
        cls.corpus = build_corpus(cls.ctx)
        cls.index = IdentityIndex(cls.ctx)
        cls.policy = Policy(cls.ctx.rules)

    def test_all_structured_sources_parse(self):
        status = {r.source_id: r.status for r in self.corpus.reports}
        # Offline: PDFs yield native text (no state without a document-kind decision);
        # screenshots stay unsupported because no vision reader is configured.
        from collections import Counter
        self.assertEqual(Counter(status.values()), Counter({'PARSED': 79, 'UNSUPPORTED_MEDIA': 11,
                                                            'TEXT_EXTRACTED': 4}))
        self.assertEqual(self.policy.issues, [])

    def test_tombstone_exposing_identifiers_is_discovered_from_inputs(self):
        # plan 12.5: found through identity -> discovery -> state, not by naming the resource.
        req = next(r for r in load_requests(helpers.DATASET / 'deletion_requests.csv') if r.case_id == 'case_546')
        subject = resolve_subject(req.email, req.audit_at, self.index, self.corpus)
        linked, _ = discover(subject, self.corpus, self.index, req.audit_at)
        tombstones = [k for k in linked if k[0] == 'identity_service']
        self.assertEqual(len(tombstones), 1)
        state = reconstruct(self.corpus, *tombstones[0], req.audit_at, subject.tokens)
        ob = obligation(state, self.policy, req.audit_at)
        self.assertEqual((ob.result, ob.action, ob.proof.source_id), ('VIOLATED', 'REDACT', 'evidence_52'))
        self.assertTrue({'name', 'phone', 'email_hash'} <= set(state.exposed))

    def test_sample_run_is_valid_and_label_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, run = Path(tmp) / 'p.csv', Path(tmp) / 'run'
            summary = run_audit(helpers.DATASET, helpers.DATASET / 'sample_deletion_requests.csv', out, run)
            requests = load_requests(helpers.DATASET / 'sample_deletion_requests.csv')
            validate_output(read_output(out), requests, self.ctx.evidence, load_disclosures(run, out))
            self.assertEqual(summary['cases'], 32)
            self.assertEqual(summary['model_calls'], 0)


class GeneralityTests(unittest.TestCase):
    def test_inference_code_contains_no_literal_dataset_ids(self):
        pattern = re.compile(r'\b(?:case|evidence|account|user|search|analytics|support|backup|audit_record|'
                             r'tombstone|data_flow|export|activity|audit|legal_hold)_\d+\b')
        for path in [*(helpers.CODE / 'ghostaudit').rglob('*.py'), helpers.CODE / 'main.py']:
            hits = pattern.findall(path.read_text(encoding='utf-8'))
            self.assertEqual(hits, [], f'{path.name} names dataset identifiers')


if __name__ == '__main__':
    unittest.main()
