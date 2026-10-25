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

    def run_script(self, initial='DELETE', final='DELETE', then=None):
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
                p['authorization']['decision'] = then if then and len(self.bodies) > 3 else final
                if p['authorization']['decision'] != 'DELETE':
                    p['authorization']['scope'] = 'NONE'
                calls = [scripted.call('submit_audit', p)]
            return {'choices': [{'message': {'content': None, 'tool_calls': calls}}]}
        root = self.actor.root
        with patch('ghostaudit.audit.plan_case', side_effect=AssertionError('planner used')), \
             patch('ghostaudit.audit.decide_intents', side_effect=AssertionError('classifier used')), \
             patch('ghostaudit.investigator.plan_case', side_effect=AssertionError('replanner used')):
            self.runs = getattr(self, 'runs', 0) + 1
            name = 'result' if self.runs == 1 else f'result{self.runs}'
            summary = run_audit(root, root / 'deletion_requests.csv', self.actor.base / f'{name}.csv',
                self.actor.base / name, decision_mode='agent-first', agent_gateway=ReplayGateway(reply),
                agent_config=InvestigatorConfig('fixture-agent', max_rounds=4 if then else 3, max_tool_calls=10,
                    max_case_tokens=120000), agent_request_first=True)
        return summary, read_output(self.actor.base / f'{name}.csv')[0]

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

    def echoed(self):
        result = json.loads(self.bodies[3]['messages'][-2]['content'])
        self.assertIn('AUTHORIZATION_UNRESOLVED', {f['code'] for f in result['verifier_feedback']})
        return result.get('your_recorded_interpretation')

    def test_unresolved_rejection_echoes_the_agents_own_recorded_reading_without_applying_it(self):
        summary, row = self.run_script(final='UNRESOLVED', then='DELETE')
        self.assertEqual(self.echoed()['decision'], 'DELETE')
        self.assertEqual((summary['fallback_count'], len(row.steps)), (0, 3))   # the agent chose to revise
        summary, row = self.run_script(final='UNRESOLVED', then='UNRESOLVED')
        self.assertEqual(self.echoed()['decision'], 'DELETE')
        self.assertEqual((summary['fallback_count'], row.steps), (1, ()))       # code never substitutes it

    def test_no_echo_when_the_recorded_reading_was_itself_unresolved(self):
        summary, row = self.run_script(initial='UNRESOLVED', final='UNRESOLVED', then='UNRESOLVED')
        self.assertIsNone(self.echoed())
        self.assertEqual((summary['fallback_count'], row.steps), (1, ()))
