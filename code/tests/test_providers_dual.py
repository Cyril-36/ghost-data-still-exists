"""Dual-provider support: TokenBazaar unchanged, Roar adapters, role models, auth and cache isolation.
Roar payloads are the shapes recorded in the live smoke tests (credentials never recorded)."""
import http.server
import json
import tempfile
import threading
import unittest
import unittest.mock
from dataclasses import replace

import helpers  # noqa: F401
import main as cli
from ghostaudit.intent_models import generative_signals, jev_signals
from ghostaudit.investigator import probe_tool_calling
from ghostaudit.media import interpret_screenshot
from ghostaudit.providers import (PROFILES, Gateway, ProviderConfig, ProviderError, ReplayGateway, adapt_roar_answers,
                                  fingerprint)
from test_intent_models import jev_payload
from test_phase4_investigator import INSPECT_PDF, PDF_FACTS, SUBMIT, investigate

ROAR = ProviderConfig(provider='roar', cache_dir=None)
# Recorded /v1/evaluate answer shape (jev): choice as on TokenBazaar, booleans as {type: boolean, probability}.
ROAR_EVALUATE = {'answers': {'intent': {'type': 'choice', 'choice': 'DELETE_ACCOUNT', 'confidence': 1,
                                        'probabilities': {'DELETE_ACCOUNT': 1, 'NON_DELETION': 0}},
                             'preserve_account': {'type': 'boolean', 'probability': 0.01}},
                 'usage': {'inputTokens': 412, 'outputTokens': 55}}
# Recorded /v1/chat/completions native tool call (gpt-oss-20b).
ROAR_TOOL_CALL = {'id': 'chatcmpl-x', 'object': 'chat.completion', 'model': 'gpt-oss-20b', 'choices': [{
    'index': 0, 'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None, 'tool_calls': [{
        'id': 'call_1', 'type': 'function', 'function': {'name': 'echo_handle', 'arguments': '{"handle":"probe_ok_1"}'}}]}}],
    'usage': {'prompt_tokens': 130, 'completion_tokens': 34, 'total_tokens': 164,
              'completion_tokens_details': {'reasoning_tokens': 8}}}


def roar_answer(body):
    """A Roar-shaped answer to whatever typed questions were asked (choices: first label)."""
    answers = {}
    for name, q in body['questions'].items():
        if q['type'] == 'choice':
            labels = list(q['criteria'])
            answers[name] = {'type': 'choice', 'choice': labels[0], 'confidence': 0.9,
                             'probabilities': {lab: (1.0 if i == 0 else 0.0) for i, lab in enumerate(labels)}}
        else:
            answers[name] = {'type': 'boolean', 'probability': 0.02}
    return {'answers': answers, 'usage': {'inputTokens': 300, 'outputTokens': 40}}


class ProfileTests(unittest.TestCase):
    def test_tokenbazaar_defaults_are_unchanged(self):
        c = Gateway(ProviderConfig(cache_dir=None)).config
        self.assertEqual((c.provider, c.base_url, c.api_key_env, c.proxy_auth, c.cost_reserve_paise,
                          c.chat_cost_reserve_paise),
                         ('tokenbazaar', 'https://tokenbazaar.in/api/public/v1', 'TOKENBAZAAR_API_KEY', False, 25.0, 120.0))
        self.assertEqual((c.jev_model, c.intent_fallback_model, c.vision_model, c.investigator_model),
                         ('typesafe/jev-latest', 'google/gemini-2.5-flash', 'google/gemini-2.5-flash', None))

    def test_roar_profile_and_overrides(self):
        c = Gateway(ROAR).config
        self.assertEqual((c.base_url, c.api_key_env, c.proxy_auth), ('https://api.roar-ai.com/v1', 'ROAR_API_KEY', True))
        self.assertEqual((c.jev_model, c.intent_fallback_model, c.vision_model, c.investigator_model),
                         ('jev', 'gpt-oss-20b', None, 'gpt-oss-20b'))
        self.assertEqual(Gateway(replace(ROAR, vision_model='some-vision')).config.vision_model, 'some-vision')
        with self.assertRaises(ProviderError):
            Gateway(ProviderConfig(provider='other'))
        self.assertEqual(set(PROFILES), {'tokenbazaar', 'roar'})


class TokenBazaarUnchanged(unittest.TestCase):
    def test_decisions_path_body_model_and_cache_key(self):
        seen = []
        payload = jev_payload('DELETE_ACCOUNT')
        gw = ReplayGateway(lambda p, b: seen.append((p, b)) or payload)
        jev_signals(gw, 'Please delete my account.')
        path, body = seen[0]
        self.assertEqual((path, body['model']), ('decisions', 'typesafe/jev-latest'))
        self.assertEqual({q['type'] for q in body['questions'].values()}, {'choice', 'noul'})
        self.assertEqual(gw.usage[0].endpoint, 'decisions')      # historical cache keys: checked by the full run


class RoarAdapters(unittest.TestCase):
    def test_evaluate_answers_adapt_to_the_internal_shape(self):
        out = adapt_roar_answers(ROAR_EVALUATE)['answers']
        self.assertEqual(out['preserve_account'], {'type': 'noul', 'noul': 0.01})
        self.assertEqual(out['intent']['choice'], 'DELETE_ACCOUNT')
        self.assertEqual(adapt_roar_answers({'answers': 'x'}), {'answers': 'x'})     # left for the validator

    def test_jev_intent_runs_through_evaluate(self):
        seen = []
        gw = ReplayGateway(lambda p, b: seen.append((p, b)) or roar_answer(b), ROAR)
        signals = jev_signals(gw, 'Please delete my account.')
        path, body = seen[0]
        self.assertEqual((path, body['model']), ('evaluate', 'jev'))
        self.assertEqual({q['type'] for q in body['questions'].values()}, {'choice', 'boolean'})
        self.assertEqual((signals.source, signals.label), ('jev', 'DELETE_ACCOUNT'))
        self.assertEqual(dict(signals.flags)['preserve_account'], 0.02)
        rec = gw.usage[0]
        self.assertEqual((rec.endpoint, rec.input_tokens, rec.output_tokens, rec.status), ('evaluate', 300, 40, 'ok'))

    def test_malformed_roar_answers_are_rejected(self):
        bad = {'answers': {'intent': {'type': 'boolean', 'probability': 2}}, 'usage': {}}
        with self.assertRaises(ProviderError):
            jev_signals(ReplayGateway(lambda p, b: bad, ROAR), 'Please delete my account.')

    def test_cost_estimates_are_separate_from_actual_billing(self):
        class Live(ReplayGateway):
            mode = 'live'

            def _post(self, path, body):
                return self.responder(path, body), None          # Roar sends no cost header
        gw = Live(lambda p, b: ROAR_TOOL_CALL, replace(ROAR, max_cost_paise=100.0))
        gw.pricing = {'gpt-oss-20b': {'inputPer1M': 0.091, 'outputPer1M': 0.325}}
        self.assertTrue(probe_tool_calling(gw, 'gpt-oss-20b')['ok'])
        rec = gw.usage[0]
        self.assertIsNone(rec.cost_paise_header)
        self.assertAlmostEqual(rec.cost_estimate_paise, (130 * 0.091 + 34 * 0.325) / 1e6 * 8800, places=6)
        self.assertAlmostEqual(gw.spent_paise, rec.cost_estimate_paise)
        summary = next(iter(gw.usage_summary().values()))
        self.assertEqual((summary['cost_paise_header'], summary['unpriced_live_calls']), (0.0, 0))
        unpriced = Live(lambda p, b: ROAR_TOOL_CALL, replace(ROAR, max_cost_paise=100.0))
        probe_tool_calling(unpriced, 'gpt-oss-20b')
        self.assertEqual(next(iter(unpriced.usage_summary().values()))['unpriced_live_calls'], 1)
        self.assertEqual(unpriced.spent_paise, unpriced.config.chat_cost_reserve_paise)   # unknown: charge the reserve

    def test_caches_are_isolated_per_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            tb = ReplayGateway(lambda p, b: ROAR_TOOL_CALL, ProviderConfig(cache_dir=tmp))
            roar = ReplayGateway(lambda p, b: ROAR_TOOL_CALL, replace(ROAR, cache_dir=tmp))
            msgs = [{'role': 'user', 'content': 'hi'}]
            tb.chat('r', 'm', msgs, 'v1', lambda p: None, max_tokens=50)
            roar.chat('r', 'm', msgs, 'v1', lambda p: None, max_tokens=50)
            self.assertEqual(tb.usage[0].fingerprint, fingerprint('chat/completions', 'm', 'v1', msgs, 50))
            self.assertNotEqual(tb.usage[0].fingerprint, roar.usage[0].fingerprint)
            self.assertEqual(roar.usage[0].mode, 'replay')                      # not served from TokenBazaar's entry

    def test_roles_without_a_model_are_skipped_not_substituted(self):
        out = generative_signals(ReplayGateway(lambda p, b: self.fail('called'),
                                               replace(ROAR, intent_fallback_model='')), [('h1', 'x')])
        self.assertIn('no intent fallback model', out['h1'])
        _, report, _ = interpret_screenshot(type('E', (), {'file_id': 'f', 'file_path': 'p'})(), b'', None,
                                            ReplayGateway(lambda p, b: self.fail('called'), ROAR))
        self.assertIn('no vision model configured', report.issues[0])

    def test_proxy_auth_sends_no_authorization_header(self):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get('Authorization'))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'object': 'list', 'data': [{'id': 'jev', 'roar_modalities': ['evaluation'],
                                                                         'roar_pricing': {'inputPer1M': 0.042}}]}).encode())

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with unittest.mock.patch.dict('os.environ', {}, clear=False) as env:
                env.pop('ROAR_API_KEY', None)
                env.pop('TOKENBAZAAR_API_KEY', None)
                gw = Gateway(ROAR)
                gw.config = replace(gw.config, base_url=f'http://127.0.0.1:{srv.server_port}/v1')
                self.assertEqual(gw.load_pricing(), {'jev': {'inputPer1M': 0.042}})
                self.assertEqual(seen, [None])
                tb = Gateway(ProviderConfig(cache_dir=None))
                tb.config = replace(tb.config, base_url=f'http://127.0.0.1:{srv.server_port}/v1')
                with self.assertRaisesRegex(ProviderError, 'TOKENBAZAAR_API_KEY is not set'):
                    tb.list_models()
        finally:
            srv.shutdown()
            srv.server_close()


class SameInvestigatorOnRoar(unittest.TestCase):
    def test_investigator_protocol_runs_on_the_roar_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, final, case, model, gw = investigate(tmp, INSPECT_PDF, PDF_FACTS, SUBMIT,
                                                       gateway=lambda m: ReplayGateway(m, ROAR))
        self.assertEqual((case.outcome, gw.config.provider), ('ACCEPTED', 'roar'))
        self.assertIn('fx_attach_P', {s.resource_id for s in final.steps})
        self.assertTrue(all(u.endpoint == 'chat/completions' for u in gw.usage))

    def test_recorded_roar_tool_call_passes_the_probe_contract(self):
        self.assertTrue(probe_tool_calling(ReplayGateway(lambda p, b: ROAR_TOOL_CALL, ROAR), 'gpt-oss-20b')['ok'])


class CliSelection(unittest.TestCase):
    def test_vision_model_must_be_catalogued_with_image_input(self):
        catalog = {'data': [{'id': 'txt', 'roar_image_input': 'no'}, {'id': 'maybe', 'roar_image_input': 'unknown'},
                            {'id': 'img', 'roar_image_input': 'yes'}]}
        gw = type('G', (), {'_get': lambda self, path: catalog})()
        self.assertEqual([cli.vision_capable(gw, m) for m in ('txt', 'maybe', 'img', 'absent')], [False, None, True, False])


if __name__ == '__main__':
    unittest.main()


class SharedCeiling(unittest.TestCase):
    def test_a_separate_investigator_gateway_gets_only_what_is_left(self):
        from pathlib import Path

        from ghostaudit.audit import run_audit
        from ghostaudit.investigator import InvestigatorConfig
        from phase4_world import world

        class Spent(ReplayGateway):
            pass
        with tempfile.TemporaryDirectory() as tmp:
            root = world(tmp)
            intent = Spent(lambda p, b: jev_payload('DELETE_ACCOUNT'), ProviderConfig(cache_dir=None, max_cost_paise=300.0))
            intent._spent = 120.0                                          # what intent and media calls used
            inv = ReplayGateway(lambda p, b: self.fail('no call expected'), replace(ROAR, max_cost_paise=300.0))
            inv.mode = 'replay'
            run_audit(root, root / 'deletion_requests.csv', Path(tmp) / 'o.csv', Path(tmp) / 'run', gateway=intent,
                      use_generative=False, investigator=InvestigatorConfig('m', max_rounds=0),
                      investigator_gateway=inv)
            self.assertEqual(inv.config.max_cost_paise, 180.0)


class TransportResets(unittest.TestCase):
    def test_a_connection_reset_is_a_bounded_transport_failure(self):
        calls = []

        class Reset(ReplayGateway):
            mode = 'live'

            def _post(self, path, body):
                calls.append(1)
                raise ConnectionResetError(104, 'Connection reset by peer')
        gw = Reset(None, replace(ROAR, max_retries=1))
        with unittest.mock.patch('ghostaudit.providers.time.sleep'), self.assertRaisesRegex(ProviderError, 'transport'):
            gw.chat('r', 'm', [{'role': 'user', 'content': 'x'}], 'v', lambda p: None)
        self.assertEqual((len(calls), gw._reserved, gw.usage[-1].status), (2, 0.0, 'error'))
