"""Budget-aware scripted agents finalize before exhaustion; pressure never bypasses evidence."""
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


class AgentBudgetFeedback(unittest.TestCase):
    def setUp(self):
        self.actor = scripted.AgentFirstTests()
        self.actor.setUp()
        self.addCleanup(self.actor.doCleanups)

    def run_script(self, *, fabricate=False, token_limit=120000):
        self.notices = []
        def reply(endpoint, body):
            notices = [json.loads(m['content'])['execution_budget'] for m in body['messages']
                       if m['role'] == 'user' and m['content'].startswith('{"execution_budget":')]
            notice = notices[-1] if notices else None
            self.notices.append(copy.deepcopy(notice))
            if notice and notice['remaining_rounds'] == 1:
                p = copy.deepcopy(self.actor.good)
                if fabricate:
                    for step in p['steps']:
                        step['resource_id'] = 'invented_parent_999'
                calls = [scripted.call('submit_audit', p)]
            else:
                calls = self.actor.inspect()
            return {'choices': [{'message': {'content': None, 'tool_calls': calls}}]}
        gateway = ReplayGateway(reply)
        root = self.actor.root
        with patch('ghostaudit.audit.plan_case', side_effect=AssertionError('planner used')), \
             patch('ghostaudit.investigator.plan_case', side_effect=AssertionError('replanner used')):
            summary = run_audit(root, root / 'deletion_requests.csv', self.actor.base / 'result.csv',
                self.actor.base / 'result', decision_mode='agent-first', agent_gateway=gateway,
                agent_config=InvestigatorConfig('fixture-agent', max_rounds=3, max_tool_calls=10,
                                                max_case_tokens=token_limit))
        return summary, read_output(self.actor.base / 'result.csv')[0]

    def test_agent_can_use_remaining_round_feedback_to_submit_before_exhaustion(self):
        summary, row = self.run_script()
        self.assertEqual(summary['fallback_count'], 0)
        self.assertEqual(len(row.steps), 3)
        self.assertEqual([n['remaining_rounds'] for n in self.notices], [3, 2, 1])
        self.assertTrue(self.notices[-1]['finalization_urgent'])
        self.assertLess(self.notices[-1]['remaining_tool_calls'], self.notices[0]['remaining_tool_calls'])
        self.assertNotIn('resource_id', json.dumps(self.notices))
        self.assertNotIn('deletion_status', json.dumps(self.notices))

    def test_budget_pressure_does_not_authorize_invented_steps(self):
        summary, row = self.run_script(fabricate=True)
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(row.steps, ())
        self.assertEqual(summary['agent_first']['rejected'], 1)

    def test_admission_limit_still_refuses_an_unaffordable_first_turn(self):
        summary, row = self.run_script(token_limit=1)
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(self.notices, [])
        self.assertEqual(row.steps, ())

    def test_unresolved_authorization_feedback_does_not_call_it_non_deletion(self):
        p = copy.deepcopy(self.actor.good)
        p['authorization'].update(decision='UNRESOLVED', scope='NONE')
        summary, row, _ = self.actor.run_agent([
            self.actor.inspect(), [scripted.call('submit_audit', p)]])
        trace = json.loads((self.actor.base / 'agent/case_files.jsonl').read_text())
        findings = trace['agent_proposals'][0]['findings']
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(row.steps, ())
        self.assertIn('AUTHORIZATION_UNRESOLVED', {f['code'] for f in findings})
        self.assertFalse(any('Non-deletion' in f['message'] for f in findings))
