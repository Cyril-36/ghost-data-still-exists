"""Focused independent-review regressions; destructive paths exist only in temporary fixtures."""
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import helpers  # noqa: F401
from evaluation.fixtures import FIXTURES, write_fixture
from ghostaudit.audit import run_audit
from ghostaudit.contracts import ContractError, Step
from ghostaudit.corpus import Corpus
from ghostaudit.intent import DELETE, ITEM, Intent
from ghostaudit.observations import Observation, PRESENT, STATE, Scope
from ghostaudit.output import read_output
from ghostaudit.verifier import Verifier
from phase4_world import EMAIL
from test_phase4_casefile import setup_case
from test_phase4_verifier import codes


class DestinationSafety(unittest.TestCase):
    def test_existing_project_files_cannot_be_replaced_with_predictions(self):
        for relative in ('.env', 'AGENTS.md', 'code/main.py', 'notes.csv'):
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as tmp:
                base = write_fixture(FIXTURES['fixture_a'], Path(tmp))
                target = base / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                original = b'synthetic protected project content\n'
                target.write_bytes(original)
                with self.assertRaises(ContractError):
                    run_audit(base / 'input', base / 'input/deletion_requests.csv', target, base / 'run')
                self.assertEqual(target.read_bytes(), original)
                self.assertFalse((base / 'run').exists())

    def test_fresh_prediction_paths_and_deliberate_output_publication_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = write_fixture(FIXTURES['fixture_a'], Path(tmp))
            for number, relative in enumerate(('fresh/predictions.csv', 'output.csv', 'output.csv')):
                target = base / relative
                run_audit(base / 'input', base / 'input/deletion_requests.csv', target, base / f'run{number}')
                self.assertEqual(read_output(target), read_output(base / 'expected_output.csv'))

    def test_symlink_alias_cannot_replace_a_protected_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = write_fixture(FIXTURES['fixture_a'], Path(tmp))
            protected = base / '.env'
            protected.write_bytes(b'synthetic protected content\n')
            alias = base / 'predictions.csv'
            alias.symlink_to(protected)
            with self.assertRaises(ContractError):
                run_audit(base / 'input', base / 'input/deletion_requests.csv', alias, base / 'run')
            self.assertEqual(protected.read_bytes(), b'synthetic protected content\n')


class CategoryScopedAbsence(unittest.TestCase):
    def scenario(self, tmp, operation, *, matching=False, late=None):
        s = setup_case(tmp)
        category = {'VERIFY_RECORD_ABSENT': 'USER_PROFILE', 'VERIFY_RECORD_REDACTED': 'ACCOUNT_TOMBSTONE',
                    'VERIFY_RECORD_ANONYMIZED': 'ANALYTICS_PROFILE'}[operation]
        at = s['context'].evidence['fx_file_L'].available_at
        system, rid = 'account_service', 'fx_victim_P'
        exposed = ('email',)
        victim = Observation('fx_file_L', at, at, 'L1', '', STATE, system, rid, location='replica',
                             category=category, presence=PRESENT, exposed=exposed,
                             tokens=frozenset({('email', EMAIL)}))
        # One system has two distinct categories; this later empty listing is for the other one.
        decoy = replace(victim, resource_id='fx_unrelated', category='USER_EXPORT', tokens=frozenset())
        scope = Scope('fx_file_L', at, at + timedelta(hours=1), system,
                      category if matching else 'USER_EXPORT', 'replica', frozenset(), coverage='FULL_TABLE')
        if late:
            scope = replace(scope, **{late: s['request'].audit_at + timedelta(seconds=1)})
        observations = [victim, decoy]
        if operation != 'VERIFY_RECORD_ABSENT':
            observations.append(replace(victim, location='primary', exposed=()))
        corpus = Corpus(s['context'], observations, [scope], [])
        intent = Intent(DELETE, 'synthetic item request', scope=ITEM, target=category)
        state = {'VERIFY_RECORD_ABSENT': 'ABSENT', 'VERIFY_RECORD_REDACTED': 'REDACTED',
                 'VERIFY_RECORD_ANONYMIZED': 'ANONYMIZED'}[operation]
        step = Step('VERIFICATION', system, rid, operation, state, 'fx_file_L')
        plan = replace(s['plan'], intent=intent, obligations=[], status='COMPLETE', steps=(step,), fallback=False)
        verifier = Verifier(corpus, s['index'], s['context'], s['texts'])
        return codes(verifier.verify(plan))

    def test_wrong_category_empty_snapshot_cannot_verify_absence_or_treatment(self):
        for operation in ('VERIFY_RECORD_ABSENT', 'VERIFY_RECORD_REDACTED', 'VERIFY_RECORD_ANONYMIZED'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as tmp:
                findings = self.scenario(tmp, operation)
                self.assertIn('REPLICA_DISAGREEMENT', findings)
                if operation == 'VERIFY_RECORD_ABSENT':
                    self.assertIn('UNSUPPORTED_CITATION', findings)

    def test_matching_category_empty_snapshot_still_proves_the_copy_absent(self):
        for operation in ('VERIFY_RECORD_ABSENT', 'VERIFY_RECORD_REDACTED', 'VERIFY_RECORD_ANONYMIZED'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(self.scenario(tmp, operation, matching=True), set())

    def test_category_match_does_not_waive_observation_or_availability_cutoff(self):
        for operation in ('VERIFY_RECORD_ABSENT', 'VERIFY_RECORD_REDACTED', 'VERIFY_RECORD_ANONYMIZED'):
            for late in ('available_at', 'observed_at'):
                with self.subTest(operation=operation, late=late), tempfile.TemporaryDirectory() as tmp:
                    findings = self.scenario(tmp, operation, matching=True, late=late)
                    self.assertIn('REPLICA_DISAGREEMENT', findings)
                    if operation == 'VERIFY_RECORD_ABSENT':
                        self.assertIn('UNSUPPORTED_CITATION', findings)
