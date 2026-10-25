"""Token admission anchored to reported prompt tokens; system_id mismatch feedback."""
import copy
import json
import unittest

import helpers  # noqa: F401
import test_agent_first as scripted
from ghostaudit.agent_first import AgentFirst

TOOLS = [{'type': 'function', 'function': {'name': 't', 'parameters': {'type': 'object', 'properties': {}}}}]


def body(messages, tools=TOOLS, choice='required'):
    return {'model': 'm', 'messages': messages, 'tools': tools, 'tool_choice': choice, 'max_completion_tokens': 10}


def size(value):
    return len(json.dumps(value, ensure_ascii=False).encode())


class RequestBound(unittest.TestCase):
    def setUp(self):
        self.old = [{'role': 'user', 'content': 'x' * 4000}]
        self.new = self.old + [{'role': 'tool', 'tool_call_id': 'c', 'content': 'y' * 100}]

    def test_without_an_anchor_bytes_bound_the_whole_request(self):
        self.assertEqual(AgentFirst._request_bound(body(self.new), 10), size(body(self.new)) + 10)

    def test_an_anchor_counts_reported_tokens_plus_only_the_appended_bytes(self):
        anchor = (1, ('m', size(TOOLS)), 900)
        bound = AgentFirst._request_bound(body(self.new), 10, anchor)
        self.assertEqual(bound, 900 + size(self.new[1:]) + size('required') + 10)
        self.assertLess(bound, size(body(self.new)))

    def test_a_changed_model_tool_list_or_shorter_history_drops_the_anchor(self):
        exact = size(body(self.new)) + 10
        for anchor in [(1, ('other', size(TOOLS)), 900), (1, ('m', size(TOOLS) + 1), 900), (5, ('m', size(TOOLS)), 900)]:
            with self.subTest(anchor=anchor):
                self.assertEqual(AgentFirst._request_bound(body(self.new), 10, anchor), exact)


class AnchoredAdmission(unittest.TestCase):
    """A token cap that the byte bound would hit after one call still admits the next call when the
    provider reports the real prompt size, and stays a hard stop when the reported size is large."""
    def setUp(self):
        self.actor = scripted.AgentFirstTests()
        self.actor.setUp()
        self.addCleanup(self.actor.doCleanups)

    def run_with(self, prompt_tokens, cap):
        from unittest.mock import patch
        from ghostaudit.audit import run_audit
        from ghostaudit.investigator import InvestigatorConfig
        from ghostaudit.providers import ReplayGateway
        turns = iter([self.actor.inspect(), [scripted.call('submit_audit', self.actor.good)]])
        def reply(endpoint, body):
            return {'choices': [{'message': {'content': None, 'tool_calls': next(turns)}}],
                    'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': 50}}
        root = self.actor.root
        name = f'cap{prompt_tokens}'
        with patch('ghostaudit.audit.plan_case', side_effect=AssertionError('planner used')):
            return run_audit(root, root / 'deletion_requests.csv', self.actor.base / f'{name}.csv',
                             self.actor.base / name, decision_mode='agent-first', agent_gateway=ReplayGateway(reply),
                             agent_config=InvestigatorConfig('fixture-agent', max_rounds=2, max_tool_calls=10,
                                                             max_case_tokens=cap, max_completion_tokens=100))

    def test_reported_prompt_size_admits_what_the_byte_bound_refused(self):
        cap = 18000   # the second request is about 17.5 KB: its byte bound plus the first call exceeds the cap
        self.assertEqual(self.run_with(4000, cap)['fallback_count'], 0)
        self.assertEqual(self.run_with(9000, cap)['fallback_count'], 1)


class SystemMismatch(unittest.TestCase):
    def setUp(self):
        self.actor = scripted.AgentFirstTests()
        self.actor.setUp()
        self.addCleanup(self.actor.doCleanups)

    def test_a_table_name_used_as_system_id_is_named_as_such(self):
        p = copy.deepcopy(self.actor.good)
        for step in p['steps']:
            step['system_id'] = 'customer_profiles_replica'
        summary, row, _ = self.actor.run_agent([self.actor.inspect(), [scripted.call('submit_audit', p)]])
        trace = json.loads((self.actor.base / 'agent/case_files.jsonl').read_text())
        messages = [f['message'] for f in trace['agent_proposals'][0]['findings'] if f['code'] == 'SYSTEM_MISMATCH']
        self.assertTrue(messages and "['account_service']" in messages[0], messages)
        self.assertEqual((summary['fallback_count'], row.steps), (1, ()))


if __name__ == '__main__':
    unittest.main()
