"""Phase 4 milestone 4: investigator and verifier inside the real audit command (run_audit and main.py)."""
import contextlib
import io
import json
import tempfile
import unittest
import unittest.mock
from dataclasses import replace
from pathlib import Path

import helpers  # noqa: F401
import main as cli
from ghostaudit import audit as audit_module
from ghostaudit.audit import run_audit
from ghostaudit.contracts import Step
from ghostaudit.investigator import InvestigatorConfig
from ghostaudit.output import canonical_steps, read_output
from ghostaudit.providers import ReplayGateway
from phase4_world import world
from test_media import make_pdf
from test_phase4_investigator import INSPECT_PDF, PDF_FACTS, SUBMIT, ScriptedModel

CONFIG = InvestigatorConfig(model='fx-investigator')


def audit(root, out, **kw):
    return run_audit(root, root / 'deletion_requests.csv', out / 'output.csv', out / 'run', **kw)


def lines(path):
    return [json.loads(x) for x in Path(path).read_text(encoding='utf-8').splitlines() if x]


class PipelineTests(unittest.TestCase):
    def test_8_a_verified_case_is_unchanged_and_costs_no_call(self):
        quiet_pdf = {'evidence/documents/attachment.pdf': ('fx_file_PDF', '2026-03-01T10:00:00Z', 'support_platform',
                                                           make_pdf([['Unrelated catalogue page', 'Item fx_item_1']]))}
        with tempfile.TemporaryDirectory() as tmp:
            root, out = world(tmp, files=quiet_pdf), Path(tmp)
            off = audit(root, out / 'off')
            model = ScriptedModel()
            on = audit(root, out / 'on', investigator=CONFIG, investigator_gateway=ReplayGateway(model))
            self.assertEqual((out / 'off/output.csv').read_bytes(), (out / 'on/output.csv').read_bytes())
            self.assertEqual((model.bodies, on['phase4']['routed'], on['model_calls']), ([], 0, 0))
            self.assertEqual(off['phase4']['investigator'], None)

    def test_routed_case_is_investigated_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = world(tmp), Path(tmp)
            model = ScriptedModel(INSPECT_PDF, PDF_FACTS, SUBMIT)
            summary = audit(root, out, investigator=CONFIG, investigator_gateway=ReplayGateway(model))
            row = read_output(out / 'output.csv')[0]
            self.assertIn(('FINDING', 'fx_attach_P', 'fx_file_PDF'), {(s.phase, s.resource_id, s.evidence_id)
                                                                      for s in row.steps})
            p4 = summary['phase4']
            self.assertEqual((p4['routed'], p4['investigated'], p4['accepted'], p4['facts_accepted'], p4['rounds']),
                             (1, 1, 1, 3, 3))
            case_file = lines(out / 'run/case_files.jsonl')[0]
            self.assertEqual([c['name'] for c in case_file['tool_calls']][0], 'inspect_media')
            plan = lines(out / 'run/case_plans.jsonl')[0]
            self.assertEqual(plan['investigation']['outcome'], 'ACCEPTED')
            self.assertIn('verifier: UNINSPECTED_MENTION fx_file_PDF', plan['routing'])
            self.assertEqual({u['role'] for u in lines(out / 'run/usage.jsonl')}, {'investigator'})

    def test_a_plan_the_verifier_rejects_is_withheld_and_disclosed(self):
        real = audit_module.plan_case

        def corrupt(request, *a, **k):                   # a planner bug: the subject's record cited wrongly
            plan = real(request, *a, **k)
            plan.steps = canonical_steps(replace(s, evidence_id='fx_file_PDF') if s.phase == 'FINDING' else s
                                         for s in plan.steps)
            return plan
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.object(audit_module, 'plan_case', corrupt):
            root, out = world(tmp), Path(tmp)
            summary = audit(root, out)
            row = read_output(out / 'output.csv')[0]
        self.assertEqual(summary['phase4']['withheld_by_verifier'], 1)
        self.assertEqual(summary['fallback_reasons'], {'VERIFIER_REJECTED': 1})
        self.assertEqual({s.phase for s in row.steps} - {'VERIFICATION'}, set())
        self.assertNotIn(Step('FINDING', 'account_service', 'fx_account_P', 'REPORT_REMAINING_RECORD', 'PRESENT',
                              'fx_file_PDF'), row.steps)


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, err.getvalue()

    def test_replay_mode_makes_no_network_call_and_live_needs_a_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = world(tmp), Path(tmp)
            base = ['audit', '--dataset', str(root), '--requests', str(root / 'deletion_requests.csv')]
            self.assertEqual(self.run_cli(*base, '--output', str(out / 'a.csv'), '--run-dir', str(out / 'a'))[0], 0)
            with unittest.mock.patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network')):
                code, _ = self.run_cli(*base, '--output', str(out / 'b.csv'), '--run-dir', str(out / 'b'),
                                       '--investigator', 'replay', '--investigator-model', 'fx-model')
            self.assertEqual(code, 0)
            self.assertEqual((out / 'a.csv').read_bytes(), (out / 'b.csv').read_bytes())
            manifest = json.loads((out / 'b/run_manifest.json').read_text())
            self.assertEqual((manifest['phase4']['investigator_mode'], manifest['model_calls']), ('replay', 0))
            self.assertEqual(manifest['phase4']['stop_reasons'], {'RUN_BUDGET': 1})
            with unittest.mock.patch.object(cli, 'PROBES', out / 'no_probes.json'):
                code, err = self.run_cli(*base, '--output', str(out / 'c.csv'), '--run-dir', str(out / 'c'),
                                         '--investigator', 'live', '--investigator-model', 'fx-model')
            self.assertEqual(code, 1)
            self.assertIn('probe-tools', err)


if __name__ == '__main__':
    unittest.main()
