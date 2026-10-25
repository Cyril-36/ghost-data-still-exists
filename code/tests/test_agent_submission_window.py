"""The optional execution deadline requests a proposal; the agent still authors every semantic field."""
import copy
import json
import unittest
from unittest.mock import patch

import helpers  # noqa: F401
import test_agent_first as scripted
from ghostaudit.audit import run_audit
from ghostaudit.investigator import InvestigatorConfig
from ghostaudit.output import read_output
from ghostaudit.providers import ReplayGateway


class SubmissionWindow(unittest.TestCase):
    def setUp(self):
        self.actor = scripted.AgentFirstTests()
        self.actor.setUp()
        self.addCleanup(self.actor.doCleanups)

    def run_script(self, *, enabled=True, unresolved=False, fabricate=False):
        self.choices = []
        def responder(endpoint, body):
            self.choices.append(copy.deepcopy(body['tool_choice']))
            if isinstance(body['tool_choice'], dict):
                p = copy.deepcopy(self.actor.good)
                if unresolved:
                    p['authorization'].update(decision='UNRESOLVED', scope='NONE')
                if fabricate:
                    p['witnesses'][0]['literal'] = 'invented evidence mentioning fx_account_A'
                calls = [scripted.call('submit_audit', p)]
            else:
                calls = self.actor.inspect()
            return {'choices': [{'message': {'content': None, 'tool_calls': calls}}]}
        root = self.actor.root
        with patch('ghostaudit.audit.plan_case', side_effect=AssertionError('planner used')), \
             patch('ghostaudit.investigator.plan_case', side_effect=AssertionError('replanner used')):
            summary = run_audit(root, root / 'deletion_requests.csv', self.actor.base / 'result.csv',
                self.actor.base / 'result', decision_mode='agent-first', agent_gateway=ReplayGateway(responder),
                agent_config=InvestigatorConfig('fixture-agent', max_rounds=3, max_tool_calls=10,
                                                max_case_tokens=120000), agent_finalize_on_pressure=enabled)
        trace = json.loads((self.actor.base / 'result/case_files.jsonl').read_text())
        return summary, read_output(self.actor.base / 'result.csv')[0], trace

    def test_submission_window_preserves_verified_agent_authored_steps(self):
        summary, row, trace = self.run_script()
        self.assertEqual(summary['fallback_count'], 0)
        self.assertEqual(self.choices[:2], ['required', 'required'])
        self.assertEqual(self.choices[-1], {'type': 'function', 'function': {'name': 'submit_audit'}})
        self.assertEqual([s.operation for s in row.steps], [s['operation'] for s in self.actor.good['steps']])
        self.assertTrue(trace['agent_proposals'][0]['accepted'])

    def test_deadline_cannot_turn_unresolved_authorization_into_deletion(self):
        summary, row, trace = self.run_script(unresolved=True)
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(row.steps, ())
        self.assertEqual(trace['agent_proposals'][0]['proposal']['authorization']['decision'], 'UNRESOLVED')
        self.assertIn('AUTHORIZATION_UNRESOLVED', {f['code'] for f in trace['agent_proposals'][0]['findings']})

    def test_deadline_cannot_authorize_a_fabricated_witness(self):
        summary, row, trace = self.run_script(fabricate=True)
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(row.steps, ())
        self.assertIn('UNSHOWN_WITNESS', {f['code'] for f in trace['agent_proposals'][0]['findings']})

    def test_disabling_window_retains_dynamic_tool_choice(self):
        summary, row, _ = self.run_script(enabled=False)
        self.assertEqual(self.choices, ['required'] * 3)
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(row.steps, ())
