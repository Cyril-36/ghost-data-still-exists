"""Request-first remains one primary agent; its initial interpretation never overwrites final semantics."""
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


class RequestFirst(unittest.TestCase):
    def setUp(self):
        self.actor = scripted.AgentFirstTests()
        self.actor.setUp()
        self.addCleanup(self.actor.doCleanups)

    def run_script(self, initial='DELETE', final='DELETE'):
        self.bodies = []
        def reply(endpoint, body):
            self.bodies.append(copy.deepcopy(body))
            if len(self.bodies) == 1:
                a = copy.deepcopy(self.actor.good['authorization'])
                a['decision'] = initial
                if initial != 'DELETE':
                    a['scope'] = 'NONE'
                calls = [scripted.call('interpret_request', a)]
            elif len(self.bodies) == 2:
                calls = self.actor.inspect()
            else:
                p = copy.deepcopy(self.actor.good)
                p['authorization']['decision'] = final
                if final != 'DELETE':
                    p['authorization']['scope'] = 'NONE'
                calls = [scripted.call('submit_audit', p)]
            return {'choices': [{'message': {'content': None, 'tool_calls': calls}}]}
        root = self.actor.root
        with patch('ghostaudit.audit.plan_case', side_effect=AssertionError('planner used')), \
             patch('ghostaudit.audit.decide_intents', side_effect=AssertionError('classifier used')), \
             patch('ghostaudit.investigator.plan_case', side_effect=AssertionError('replanner used')):
            summary = run_audit(root, root / 'deletion_requests.csv', self.actor.base / 'result.csv',
                self.actor.base / 'result', decision_mode='agent-first', agent_gateway=ReplayGateway(reply),
                agent_config=InvestigatorConfig('fixture-agent', max_rounds=3, max_tool_calls=10,
                    max_case_tokens=120000), agent_request_first=True)
        return summary, read_output(self.actor.base / 'result.csv')[0]

    def test_request_only_first_turn_then_dynamic_evidence_and_verified_final_steps(self):
        summary, row = self.run_script()
        self.assertEqual(summary['fallback_count'], 0)
        first = self.bodies[0]
        self.assertEqual(first['tool_choice']['function']['name'], 'interpret_request')
        self.assertNotIn('source_inventory', json.dumps(first['messages']))
        self.assertNotIn('fx_account_A', json.dumps(first['messages']))
        self.assertIn('source_inventory', json.dumps(self.bodies[1]['messages']))
        self.assertEqual([s.operation for s in row.steps], [s['operation'] for s in self.actor.good['steps']])

    def test_agent_may_revise_its_own_initial_uncertainty_after_investigation(self):
        summary, row = self.run_script(initial='UNRESOLVED')
        self.assertEqual(summary['fallback_count'], 0)
        self.assertEqual(len(row.steps), 3)

    def test_initial_delete_never_overwrites_unresolved_final_authorization(self):
        summary, row = self.run_script(final='UNRESOLVED')
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(row.steps, ())
