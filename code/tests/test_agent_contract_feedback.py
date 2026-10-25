"""Field-specific feedback permits agent repairs without changing permission or supplying a plan."""
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


class ContractFeedback(unittest.TestCase):
    def test_agent_repairs_account_target_encoding_without_abandoning_its_authorization(self):
        actor = scripted.AgentFirstTests()
        actor.setUp()
        self.addCleanup(actor.doCleanups)
        calls = 0
        def reply(endpoint, body):
            nonlocal calls
            calls += 1
            if calls == 1:
                tools = actor.inspect()
            else:
                p = copy.deepcopy(actor.good)
                if calls == 2:
                    p['authorization']['target'] = 'description of the requested account'
                else:
                    feedback = '\n'.join(m['content'] for m in body['messages'] if m['role'] == 'tool')
                    if 'authorization.target must be \\"\\"' not in feedback:
                        p['authorization'].update(decision='UNRESOLVED', scope='NONE')
                tools = [scripted.call('submit_audit', p)]
            return {'choices': [{'message': {'content': None, 'tool_calls': tools}}]}
        root = actor.root
        with patch('ghostaudit.audit.plan_case', side_effect=AssertionError('planner used')), \
             patch('ghostaudit.investigator.plan_case', side_effect=AssertionError('replanner used')):
            summary = run_audit(root, root / 'deletion_requests.csv', actor.base / 'result.csv', actor.base / 'result',
                decision_mode='agent-first', agent_gateway=ReplayGateway(reply),
                agent_config=InvestigatorConfig('fixture-agent', max_rounds=3, max_tool_calls=10,
                                                max_case_tokens=120000))
        self.assertEqual(summary['fallback_count'], 0)
        trace = json.loads((actor.base / 'result/case_files.jsonl').read_text())
        self.assertEqual([a['accepted'] for a in trace['agent_proposals']], [False, True])
        self.assertEqual({a['proposal']['authorization']['scope'] for a in trace['agent_proposals']}, {'ACCOUNT'})
        self.assertEqual(len(read_output(actor.base / 'result.csv')[0].steps), 3)
