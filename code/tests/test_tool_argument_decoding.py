"""String-encoded nested tool arguments: decoded only to the exact schema type, then fully validated."""
import json
import unittest

import helpers  # noqa: F401
from ghostaudit.providers import ProviderConfig, ProviderError, ReplayGateway

TOOLS = [{'type': 'function', 'function': {'name': 'submit', 'parameters': {
    'type': 'object', 'additionalProperties': False, 'required': ['auth', 'steps', 'note'], 'properties': {
        'auth': {'type': 'object', 'additionalProperties': False, 'required': ['decision'],
                 'properties': {'decision': {'type': 'string', 'enum': ['DELETE', 'NONE']}}},
        'steps': {'type': 'array', 'items': {'type': 'object', 'properties': {'n': {'type': 'integer'}},
                                             'required': ['n'], 'additionalProperties': False}},
        'note': {'type': 'string'}}}}}]


def chat(arguments):
    reply = {'choices': [{'message': {'content': None, 'tool_calls': [
        {'id': 'c1', 'type': 'function', 'function': {'name': 'submit', 'arguments': json.dumps(arguments)}}]}}]}
    gateway = ReplayGateway(lambda path, body: json.loads(json.dumps(reply)), ProviderConfig(cache_dir=None))
    return gateway.chat('primary_agent', 'm', [{'role': 'user', 'content': 'x'}], 'v', lambda p: None,
                        tools=TOOLS, tool_choice='required')


class NestedJsonStrings(unittest.TestCase):
    def test_string_encoded_object_and_array_are_decoded_and_recorded(self):
        payload = chat({'auth': json.dumps({'decision': 'DELETE'}), 'steps': json.dumps([{'n': 1}]),
                        'note': '{"stays": "a string"}'})
        args = json.loads(payload['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
        self.assertEqual(args, {'auth': {'decision': 'DELETE'}, 'steps': [{'n': 1}], 'note': '{"stays": "a string"}'})
        self.assertEqual(payload['ghostaudit_decoded_arguments'], ['submit.auth', 'submit.steps'])

    def test_well_formed_arguments_are_unchanged(self):
        payload = chat({'auth': {'decision': 'NONE'}, 'steps': [], 'note': ''})
        self.assertNotIn('ghostaudit_decoded_arguments', payload)

    def test_anything_else_is_still_rejected(self):
        for name, arguments in {
                'not json': {'auth': '{decision: DELETE}', 'steps': [], 'note': ''},
                'wrong type': {'auth': '["DELETE"]', 'steps': [], 'note': ''},
                'invalid inside': {'auth': json.dumps({'decision': 'MAYBE'}), 'steps': [], 'note': ''},
                'extra key inside': {'auth': json.dumps({'decision': 'DELETE', 'x': 1}), 'steps': [], 'note': ''}
        }.items():
            with self.subTest(name), self.assertRaisesRegex(ProviderError, 'invalid chat/completions'):
                chat(arguments)


if __name__ == '__main__':
    unittest.main()
