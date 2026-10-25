"""Recorded-decision replay: bound to exact inputs, re-reconciled, and reproducing the submission."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from ghostaudit.audit import run_audit
from ghostaudit.contracts import ContractError
from ghostaudit.inputs import load_context, load_requests
from ghostaudit.recorded import FORMAT, RecordedDecisions, sha256
from phase4_world import world

REPO = Path(__file__).resolve().parents[2]
ARTIFACT = REPO / 'code' / 'recorded_decisions.json'


def recorded(tmp, root, **intent):
    request = load_requests(root / 'deletion_requests.csv')[0]
    data = {'format': FORMAT, 'provenance': {'note': 'synthetic'}, 'media': {}, 'intents': {request.case_id: {
        'request_sha256': sha256(request.request_text), 'label': 'DELETE_ACCOUNT', 'p': 0.98, 'preserve_account': 0.01,
        'marketing_only': 0.01, 'conflicting_instructions': 0.02, 'model': 'jev-fixture', **intent}}}
    path = Path(tmp) / 'recorded.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


class Replay(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.root = world(self.tmp / 'w')

    def audit(self, path, name='run'):
        return run_audit(self.root, self.root / 'deletion_requests.csv', self.tmp / f'{name}.csv', self.tmp / name,
                         recorded=RecordedDecisions.load(path))

    def test_a_recorded_deletion_is_reconciled_like_a_fresh_jev_answer(self):
        summary = self.audit(recorded(self.tmp, self.root))
        self.assertEqual((summary['intent_stages'], summary['model_calls']), ({'jev': 1}, 0))
        self.assertTrue(summary['inference_modes']['intent'].startswith('recorded Jev decisions'))

    def test_a_recorded_answer_that_the_deterministic_check_contradicts_stays_unresolved(self):
        summary = self.audit(recorded(self.tmp, self.root, label='NON_DELETION'))
        self.assertEqual(summary['intent_stages'], {'unresolved': 1})
        summary = self.audit(recorded(self.tmp, self.root, conflicting_instructions=0.9), name='flag')
        self.assertEqual(summary['intent_stages'], {'unresolved': 1})

    def test_changed_request_text_missing_cases_or_changed_evidence_are_refused(self):
        path = recorded(self.tmp, self.root)
        data = json.loads(path.read_text())
        for name, mutate in {
                'text': lambda d: d['intents'][next(iter(d['intents']))].update(request_sha256='0' * 64),
                'missing': lambda d: d['intents'].clear(),
                'evidence': lambda d: d['media'].update(fx_file_L={'file_sha256': '0' * 64,
                                                                   'document_kind': {'choice': 'X', 'p': 1, 'model': 'm'}}),
                'unknown file': lambda d: d['media'].update(fx_nope={'file_sha256': '0' * 64}),
                'format': lambda d: d.update(format='other')}.items():
            with self.subTest(name):
                bad = copy.deepcopy(data)
                mutate(bad)
                path.write_text(json.dumps(bad), encoding='utf-8')
                with self.assertRaises(ContractError):
                    self.audit(path, name=name.replace(' ', '_'))


@unittest.skipUnless(ARTIFACT.exists() and (REPO / 'dataset' / 'deletion_requests.csv').exists(), 'needs the dataset')
class SubmissionReproduces(unittest.TestCase):
    def test_the_shipped_artifact_reproduces_root_output_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_audit(REPO / 'dataset', REPO / 'dataset' / 'deletion_requests.csv', Path(tmp) / 'output.csv',
                      Path(tmp) / 'run', recorded=RecordedDecisions.load(ARTIFACT))
            produced = (Path(tmp) / 'output.csv').read_bytes()
        self.assertEqual(hashlib.sha256(produced).hexdigest(),
                         hashlib.sha256((REPO / 'output.csv').read_bytes()).hexdigest())

    def test_the_artifact_holds_no_label_or_expected_output(self):
        data = json.loads(ARTIFACT.read_text(encoding='utf-8'))
        sample = {r.case_id for r in load_requests(REPO / 'dataset' / 'sample_deletion_requests.csv')}
        self.assertFalse(sample & set(data['intents']))
        self.assertEqual({k for v in data['intents'].values() for k in v},
                         {'request_sha256', 'label', 'p', 'preserve_account', 'marketing_only',
                          'conflicting_instructions', 'model', 'recorded_decision'})
        context = load_context(REPO / 'dataset')
        self.assertTrue(set(data['media']) <= {e.file_id for e in context.manifest})


if __name__ == '__main__':
    unittest.main()
