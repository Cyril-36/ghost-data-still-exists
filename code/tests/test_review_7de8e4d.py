"""Regressions for the review of 7de8e4d: exact item-target identifiers, strict tool-call JSON,
tool schemas validated before sending, and validator failures that settle their reservation."""
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from evaluation.fixtures import Fixture, _identity, _lookups, _manifest, _request, _rules, write_fixture
from ghostaudit.audit import run_audit
from ghostaudit.contracts import OutputRow, Step
from ghostaudit.output import read_output
from ghostaudit.planner import id_tokens
from ghostaudit.providers import ProviderConfig, ProviderError, ReplayGateway, strict_json, tool_call_problem
from test_hardening_regressions import target_payload
from test_intent_models import jev_payload
from test_provider_compat import MESSAGES, TOOLS, call, completion, ok, tool_message

TWO, ONE = 'person.x@example.test', 'person.y@example.test'
TEXTS = {
    'fx_case_X1': (TWO, 'Please delete my data export fx_export_1.'),
    'fx_case_X10': (TWO, 'Please delete the export fx_export_10, nothing else.'),
    'fx_case_Y7': (ONE, 'Please delete my data export fx_export_7.'),      # names a record that is not linked
}


def exports_fixture():
    rows = [('2026-01-03T11:00:00Z', 'export_service', rid, 'USER_EXPORT', 'FOUND', email)
            for rid, email in (('fx_export_1', TWO), ('fx_export_10', TWO), ('fx_export_2', ONE))]

    def steps(rid):
        return (Step('FINDING', 'export_service', rid, 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_X'),
                Step('REMEDIATION', 'export_service', rid, 'DELETE_RECORD', 'ABSENT', ''),
                Step('VERIFICATION', 'export_service', rid, 'VERIFY_RECORD_ABSENT', 'ABSENT', ''))
    return Fixture(
        name='fixture_prefix_ids', description='SYNTHETIC: prefix-sharing export IDs and an ungrounded name',
        tables={'deletion_requests.csv': [_request(c, e, '2026-01-03T12:00:00Z', text=t) for c, (e, t) in TEXTS.items()],
                'user_identities.csv': [_identity('fx_user_X', 'Fixture Person X', TWO, '+12025550141'),
                                        _identity('fx_user_Y', 'Fixture Person Y', ONE, '+12025550142')],
                'deletion_rules.csv': _rules(),
                'evidence_files.csv': [_manifest('fx_file_X', 'evidence/records/lookups.json', '2026-01-03T11:00:00Z')]},
        evidence={'evidence/records/lookups.json': _lookups('2026-01-03T11:00:00Z', *rows)},
        expected=[OutputRow('fx_case_X1', 'INCOMPLETE', steps('fx_export_1')),
                  OutputRow('fx_case_X10', 'INCOMPLETE', steps('fx_export_10')),
                  OutputRow('fx_case_Y7', 'INCOMPLETE', ())],          # fx_export_2 is never substituted
        fallback_ids=frozenset({'fx_case_Y7'}))


class ExactTargetIdentifierRegression(unittest.TestCase):
    def test_identifier_tokens_are_exact(self):
        tokens = id_tokens('Delete fx_export_1, and fx_export_10. Mail me at fx_export_3@example.test')
        self.assertEqual({t for t in tokens if t.startswith('fx_')}, {'fx_export_1', 'fx_export_10'})

    def test_prefix_ids_and_an_ungrounded_name_select_nothing_else(self):
        def respond(path, body):
            if 'target' in body['questions']:
                return target_payload('USER_EXPORT', whole=0.05)
            return jev_payload('SCOPED_DELETION')
        with tempfile.TemporaryDirectory() as tmp:
            base = write_fixture(exports_fixture(), Path(tmp))
            summary = run_audit(base / 'input', base / 'input/deletion_requests.csv', base / 'p.csv', base / 'run',
                                gateway=ReplayGateway(respond))
            self.assertEqual(read_output(base / 'p.csv'), read_output(base / 'expected_output.csv'))
        self.assertEqual(set(summary['fallback_ids']), {'fx_case_Y7'})


class StrictToolArgumentsRegression(unittest.TestCase):
    def test_non_standard_json_is_rejected(self):
        for raw in ('{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}', '{"a": 1e999}', '{"a": 1, "a": 2}',
                    '{"o": {"k": 1, "k": 1}}', '[' * 100000):
            with self.subTest(raw=raw[:20]):
                self.assertRaises((ValueError, RecursionError), strict_json, raw)
                args = completion(tool_message(call(arguments=raw)))
                self.assertIn('strict JSON', tool_call_problem(args, TOOLS))
        self.assertEqual(strict_json('{"a": 1.5, "b": [1, "x"]}'), {'a': 1.5, 'b': [1, 'x']})


class ToolSchemaBeforeSendingRegression(unittest.TestCase):
    def test_unsupported_schemas_are_refused_before_any_call_or_reservation(self):
        def tool(params):
            return [{'type': 'function', 'function': {'name': 'lookup', 'parameters': params}}]
        bad = {
            'unsupported keyword': {'type': 'object', 'properties': {'q': {'type': 'string', 'pattern': '^a'}}},
            'combinator': {'type': 'object', 'anyOf': []},
            'unknown type': {'type': 'object', 'properties': {'q': {'type': 'text'}}},
            'required undeclared': {'type': 'object', 'properties': {}, 'required': ['q']},
            'schema additionalProperties': {'type': 'object', 'additionalProperties': {'type': 'string'}},
            'top level not object': {'type': 'string'},
            'properties not object': {'type': 'object', 'properties': ['q']},
            'parameters not object': 'q',
        }
        for label, params in bad.items():
            with self.subTest(label):
                gw = ReplayGateway(lambda p, b: self.fail('sent'))
                with self.assertRaisesRegex(ProviderError, 'before sending'):
                    gw.chat('r', 'm', MESSAGES, 'v1', ok, tools=tool(params))
                self.assertEqual((gw._calls, gw._reserved, gw.usage), (0, 0.0, []))


class ValidatorFailureSettlesReservationRegression(unittest.TestCase):
    def test_raising_validator_or_usage_extractor_releases_the_reserve(self):
        def boom(_payload):
            raise KeyError('x')
        for label, payload, validate in (
                ('validator raises', completion({'content': 'x'}), boom),
                ('usage is not an object', {**completion({'content': 'x'}), 'usage': ['x']}, ok)):
            with self.subTest(label):
                gw = ReplayGateway(lambda p, b, r=payload: r)
                with self.assertRaises(ProviderError):
                    gw.chat('r', 'm', MESSAGES, 'v1', validate)
                self.assertEqual(gw._reserved, 0.0)
                self.assertEqual(gw.usage[-1].status, 'invalid')

    def test_raising_validator_on_a_cache_hit_discards_the_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw = ReplayGateway(lambda p, b: completion({'content': 'fresh'}), ProviderConfig(cache_dir=tmp))
            gw.chat('r', 'm', MESSAGES, 'v1', ok)
            calls = []

            def picky(payload):
                calls.append(1)
                if len(calls) == 1:
                    raise TypeError('bad cache shape')
            out = gw.chat('r', 'm', MESSAGES, 'v1', picky)
        self.assertEqual(out['choices'][0]['message']['content'], 'fresh')
        self.assertEqual([r.status for r in gw.usage[1:]], ['invalid', 'ok'])
        self.assertEqual(gw._reserved, 0.0)


if __name__ == '__main__':
    unittest.main()
