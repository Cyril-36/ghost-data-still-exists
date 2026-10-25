"""Input preservation and evaluation provenance failures reproduced on synthetic data."""
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from evaluation.fixtures import FIXTURES, write_fixture
from evaluation.gap_analysis import analyze, run_corpus
from ghostaudit.audit import run_audit
from ghostaudit.contracts import ContractError
from ghostaudit.inputs import load_context


class PublicationSafety(unittest.TestCase):
    def test_predictions_cannot_replace_request_or_dataset_files(self):
        for relative in ('deletion_requests.csv', 'deletion_rules.csv', 'evidence/records/profile.json'):
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as tmp:
                base = write_fixture(FIXTURES['fixture_a'], Path(tmp))
                target = base / 'input' / relative
                before = target.read_bytes()
                with self.assertRaises(ContractError):
                    run_audit(base / 'input', base / 'input/deletion_requests.csv', target, base / 'run')
                self.assertEqual(target.read_bytes(), before)

    def test_completed_run_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = write_fixture(FIXTURES['fixture_a'], Path(tmp))
            req, pred, run = base / 'input/deletion_requests.csv', base / 'p.csv', base / 'run'
            run_audit(base / 'input', req, pred, run)
            before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
            with self.assertRaises(ContractError):
                run_audit(base / 'input', req, pred, run)
            self.assertEqual({p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}, before)

    def test_prediction_cannot_replace_a_run_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = write_fixture(FIXTURES['fixture_a'], Path(tmp))
            with self.assertRaises(ContractError):
                run_audit(base / 'input', base / 'input/deletion_requests.csv',
                          base / 'run/coverage.json', base / 'run')


class EvaluationBinding(unittest.TestCase):
    def test_changed_evidence_body_is_refused_even_when_parse_status_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = write_fixture(FIXTURES['fixture_a'], Path(tmp))
            run_audit(base / 'input', base / 'input/deletion_requests.csv', base / 'p.csv', base / 'run')
            evidence = base / 'input/evidence/records/profile.json'
            evidence.write_text(evidence.read_text().replace('person.a@example.test', 'stranger@example.test'))
            with self.assertRaisesRegex(ValueError, 'hash|changed|differs'):
                run_corpus(load_context(base / 'input'), base / 'run')

    def test_changed_predictions_are_not_scored_as_the_recorded_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = write_fixture(FIXTURES['fixture_a'], Path(tmp))
            req, pred, run = base / 'input/deletion_requests.csv', base / 'p.csv', base / 'run'
            run_audit(base / 'input', req, pred, run)
            pred.write_text(pred.read_text().replace('INCOMPLETE', 'COMPLETE'))
            with self.assertRaisesRegex(ValueError, 'hash|match'):
                analyze(base / 'input', req, pred, base / 'expected_output.csv', run)
