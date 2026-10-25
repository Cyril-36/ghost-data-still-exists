"""TokenBazaar compatibility (D063): documented chat parameters, optional native tool calls,
bounded 429 waits, cached-input tokens, the model catalog and sanitized HTTP errors. Offline only:
replay doubles and loopback HTTP servers."""
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
import unittest.mock
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ghostaudit.providers import (  # noqa: E402
    MAX_WAIT_S,
    Gateway,
    ProviderConfig,
    ProviderError,
    ReplayGateway,
    fingerprint,
    retry_delay,
    tool_call_problem,
)

MESSAGES = [{'role': 'user', 'content': 'hello'}]
TOOLS = [{'type': 'function', 'function': {
    'name': 'lookup_record',
    'parameters': {'type': 'object', 'required': ['resource_id'], 'additionalProperties': False,
                   'properties': {'resource_id': {'type': 'string'}, 'limit': {'type': 'integer'},
                                  'scope': {'type': 'string', 'enum': ['primary', 'replica']}}}}}]


def completion(message, usage=None):
    return {'id': 'r1', 'model': 'm-returned', 'choices': [{'message': message}], 'usage': usage or {}}


def tool_message(*calls):
    return {'role': 'assistant', 'content': None, 'tool_calls': list(calls)}


def call(name='lookup_record', arguments='{"resource_id": "acct_1"}', call_id='c1'):
    return {'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}


def ok(_payload):
    return None


class ChatRequestTests(unittest.TestCase):
    def capture(self, **kwargs):
        bodies = []

        def respond(path, body):
            bodies.append((path, body))
            return completion({'role': 'assistant', 'content': 'hi'})
        ReplayGateway(respond).chat('r', 'm', MESSAGES, 'v1', ok, **kwargs)
        return bodies[0]

    def test_documented_limit_parameter_and_no_temperature(self):
        path, body = self.capture(max_tokens=1234)
        self.assertEqual(path, 'chat/completions')
        self.assertEqual(body, {'model': 'm', 'messages': MESSAGES, 'max_completion_tokens': 1234})
        self.assertEqual(self.capture()[1]['max_completion_tokens'], 4000)
        self.assertEqual(self.capture(max_tokens=10, max_completion_tokens=20)[1]['max_completion_tokens'], 20)

    def test_cache_key_without_tools_is_the_pre_patch_key(self):
        """Validated responses cached before the patch are still served, with no network call."""
        with tempfile.TemporaryDirectory() as tmp:
            old_fp = fingerprint('chat/completions', 'm', 'v1', MESSAGES, 12000)    # pre-patch formula
            gw = ReplayGateway(lambda p, b: self.fail('network call'), ProviderConfig(cache_dir=tmp))
            gw._cache_put(old_fp, {'response': completion({'role': 'assistant', 'content': 'cached'}),
                                   'returned_model': 'm-returned', 'requested_model': 'm'})
            out = gw.chat('r', 'm', MESSAGES, 'v1', ok, max_tokens=12000)
            self.assertEqual(out['choices'][0]['message']['content'], 'cached')
            self.assertEqual(gw.usage[-1].mode, 'cache')
            self.assertEqual(gw.usage[-1].fingerprint, old_fp)

    def test_tools_are_sent_and_keyed_separately(self):
        _, body = self.capture(tools=TOOLS, tool_choice='auto')
        self.assertEqual((body['tools'], body['tool_choice']), (TOOLS, 'auto'))
        with tempfile.TemporaryDirectory() as tmp:
            gw = ReplayGateway(lambda p, b: completion(tool_message(call())), ProviderConfig(cache_dir=tmp))
            gw.chat('r', 'm', MESSAGES, 'v1', ok, tools=TOOLS)
            gw.chat('r', 'm', MESSAGES, 'v1', ok)
            self.assertNotEqual(gw.usage[0].fingerprint, gw.usage[1].fingerprint)


class ToolCallValidationTests(unittest.TestCase):
    def test_valid_tool_call_is_returned_not_executed(self):
        payload = completion(tool_message(call(arguments='{"resource_id": "a", "limit": 2, "scope": "replica"}')))
        self.assertIsNone(tool_call_problem(payload, TOOLS))
        out = ReplayGateway(lambda p, b: payload).chat('r', 'm', MESSAGES, 'v1', ok, tools=TOOLS)
        self.assertEqual(out, payload)
        self.assertIsNone(tool_call_problem(completion({'role': 'assistant', 'content': 'no call'}), TOOLS))

    def test_malformed_responses_are_rejected_without_exceptions(self):
        cases = {
            'payload not a dict': ['x'],
            'no choices': {'choices': []},
            'choice not a dict': {'choices': ['x']},
            'message not a dict': {'choices': [{'message': 'text'}]},
            'tool_calls not a list': completion({'tool_calls': {'id': 'c1'}}),
            'empty tool_calls': completion({'tool_calls': []}),
            'call not a dict': completion(tool_message('lookup_record')),
            'wrong call type': completion(tool_message({**call(), 'type': 'code'})),
            'function not a dict': completion(tool_message({**call(), 'function': 'lookup_record'})),
            'missing id': completion(tool_message({**call(), 'id': ''})),
            'duplicate id': completion(tool_message(call(), call())),
            'unknown tool': completion(tool_message(call(name='delete_everything'))),
            'arguments not a string': completion(tool_message(call(arguments={'resource_id': 'a'}))),
            'arguments not JSON': completion(tool_message(call(arguments='{resource_id: a'))),
            'arguments not an object': completion(tool_message(call(arguments='["a"]'))),
            'missing required': completion(tool_message(call(arguments='{"limit": 1}'))),
            'wrong type': completion(tool_message(call(arguments='{"resource_id": 5}'))),
            'bool is not integer': completion(tool_message(call(arguments='{"resource_id": "a", "limit": true}'))),
            'not in enum': completion(tool_message(call(arguments='{"resource_id": "a", "scope": "all"}'))),
            'unsupported argument': completion(tool_message(call(arguments='{"resource_id": "a", "path": "/etc"}'))),
        }
        for label, payload in cases.items():
            with self.subTest(label):
                self.assertIsInstance(tool_call_problem(payload, TOOLS), str)

    def test_tool_choice_and_tool_definitions(self):
        plain = completion({'role': 'assistant', 'content': 'text'})
        self.assertIn('required', tool_call_problem(plain, TOOLS, 'required'))
        self.assertIn('none', tool_call_problem(completion(tool_message(call())), TOOLS, 'none'))
        forced = {'type': 'function', 'function': {'name': 'other'}}
        self.assertIsNotNone(tool_call_problem(completion(tool_message(call())), TOOLS, forced))
        for tools in (None, [], ['x'], [{'type': 'function', 'function': {'name': ''}}], TOOLS + TOOLS):
            with self.subTest(tools=tools):
                self.assertIsNotNone(tool_call_problem(completion(tool_message(call())), tools))

    def test_gateway_rejects_bad_tool_calls_and_bad_definitions(self):
        bad = completion(tool_message(call(name='shell')))
        with self.assertRaisesRegex(ProviderError, 'unknown tool'):
            ReplayGateway(lambda p, b: bad).chat('r', 'm', MESSAGES, 'v1', ok, tools=TOOLS)
        with self.assertRaisesRegex(ProviderError, 'before sending'):
            ReplayGateway(lambda p, b: self.fail('sent')).chat('r', 'm', MESSAGES, 'v1', ok, tools=[{'type': 'x'}])


class UsageTests(unittest.TestCase):
    def test_cached_input_tokens_are_recorded_and_summed(self):
        usage = {'prompt_tokens': 900, 'completion_tokens': 50, 'prompt_tokens_details': {'cached_tokens': 640},
                 'completion_tokens_details': {'reasoning_tokens': 30}}
        gw = ReplayGateway(lambda p, b: completion({'role': 'assistant', 'content': 'x'}, usage))
        gw.chat('r', 'm', MESSAGES, 'v1', ok)
        rec = gw.usage[0]
        self.assertEqual((rec.input_tokens, rec.output_tokens, rec.reasoning_tokens, rec.cached_input_tokens),
                         (900, 50, 30, 640))
        self.assertEqual((rec.requested_model, rec.returned_model), ('m', 'm-returned'))
        self.assertEqual(next(iter(gw.usage_summary().values()))['cached_input_tokens'], 640)
        for details in ({'cached_tokens': True}, {'cached_tokens': -1}, {'cached_tokens': '5'}, 'x', None):
            with self.subTest(details=details):
                gw = ReplayGateway(lambda p, b, d=details: completion({'content': 'x'}, {'prompt_tokens_details': d}))
                gw.chat('r', 'm', MESSAGES, 'v1', ok)
                self.assertIsNone(gw.usage[0].cached_input_tokens)


class CatalogTests(unittest.TestCase):
    def test_usable_ids_and_kinds_only(self):
        payload = {'object': 'list', 'data': [
            {'id': 'jev', 'kind': 'decision', 'object': 'model'}, {'id': ''}, {'kind': 'chat'}, 'gpt',
            {'id': 'flash', 'type': 'chat'}, {'id': 'bare', 'object': 'model'}]}
        models = ReplayGateway(lambda p, b: payload if (p, b) == ('models', None) else {}).list_models()
        self.assertEqual(models, [{'id': 'jev', 'kind': 'decision'}, {'id': 'flash', 'kind': 'chat'},
                                  {'id': 'bare', 'kind': None}])
        for bad in ({'data': 'x'}, {'error': 'nope'}, ['jev']):
            with self.subTest(bad=bad), self.assertRaises(ProviderError):
                ReplayGateway(lambda p, b, r=bad: r).list_models()


class RetryDelayTests(unittest.TestCase):
    def test_header_then_body_message_then_none(self):
        self.assertEqual(retry_delay('3', 'Please wait 8s'), 3.0)
        self.assertEqual(retry_delay(None, 'Rate limit exceeded. Please wait 8s before retrying.'), 8.0)
        self.assertEqual(retry_delay('Wed, 21 Oct 2026 07:28:00 GMT', 'please wait 2.5 s'), 2.5)
        self.assertEqual(retry_delay('86400', ''), MAX_WAIT_S)
        for header, message in (('abc', ''), ('-3', 'Please wait -2s'), ('nan', 'Please wait infs'),
                                (None, 'Please wait soon'), ('', None), (None, None)):
            with self.subTest(header=header, message=message):
                self.assertIsNone(retry_delay(header, message))


# --- loopback HTTP: 429 waits, sanitized errors, GET catalog ----------------------------------------

def serve(responses, seen):
    """Answer each request with the next (status, headers, body); record (method, path, auth)."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def reply(self):
            # Drain POST data before closing: unread request bytes cause TCP resets on macOS.
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            seen.append((self.command, self.path, self.headers.get('Authorization')))
            status, headers, body = responses.pop(0) if len(responses) > 1 else responses[0]
            encoded = json.dumps(body).encode()
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        do_GET = do_POST = reply

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class LoopbackTests(unittest.TestCase):
    def gateway(self, responses, seen, **config):
        srv = serve(responses, seen)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        gw = Gateway(ProviderConfig(cache_dir=None, **config))
        gw.config = replace(gw.config, base_url=f'http://127.0.0.1:{srv.server_port}/v1')
        return gw

    def run_chat(self, gw):
        with unittest.mock.patch.dict('os.environ', {'TOKENBAZAAR_API_KEY': 'tb-test-secret-123'}), \
                unittest.mock.patch('ghostaudit.providers.time.sleep') as sleep:
            try:
                return gw.chat('r', 'm', [{'role': 'user', 'content': 'private request text'}], 'v1', ok), sleep
            except ProviderError as exc:
                return exc, sleep

    def test_429_waits_as_the_body_says_then_succeeds(self):
        seen = []
        gw = self.gateway([(429, {}, {'error': {'message': 'Too many requests. Please wait 8s'}}),
                           (200, {}, completion({'content': 'ok'}))], seen)
        out, sleep = self.run_chat(gw)
        self.assertEqual(out['choices'][0]['message']['content'], 'ok')
        sleep.assert_called_once_with(8.0)
        self.assertEqual(gw.usage[-1].retries, 1)

    def test_malformed_wait_falls_back_and_429_retries_are_bounded(self):
        seen = []
        gw = self.gateway([(429, {'Retry-After': 'soon'}, {'error': {'message': 'Please wait NaNs'}})], seen,
                          max_rate_limit_retries=2)
        err, sleep = self.run_chat(gw)
        self.assertIsInstance(err, ProviderError)
        self.assertEqual(len(seen), 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [3.0, 6.0])     # bounded backoff

    def test_http_errors_explain_without_secrets_or_request(self):
        for status, body, expected in (
                (503, {'error': {'message': 'TokenBazaar service is temporarily unavailable'}},
                 'HTTP 503 (service unavailable): TokenBazaar service is temporarily unavailable'),
                (402, {'message': 'Insufficient balance'}, 'HTTP 402 (payment required'),
                (403, {'detail': 'Key tb-test-secret-123 rejected; got Bearer tb-test-secret-123'},
                 'HTTP 403 (forbidden')):
            with self.subTest(status=status):
                seen = []
                gw = self.gateway([(status, {}, body)], seen, max_retries=0)
                err, _ = self.run_chat(gw)
                self.assertIsInstance(err, ProviderError)
                self.assertIn(expected, str(err))
                for text in (str(err), gw.usage[-1].error):
                    self.assertNotIn('tb-test-secret-123', text)
                    self.assertNotIn('private request text', text)
                self.assertEqual(len(seen), 1)

    def test_402_and_403_never_retry(self):
        for status in (402, 403):
            with self.subTest(status=status):
                seen = []
                gw = self.gateway([(status, {}, {'error': 'no'})], seen, max_retries=3)
                err, sleep = self.run_chat(gw)
                self.assertIsInstance(err, ProviderError)
                self.assertEqual((len(seen), sleep.call_count), (1, 0))
                self.assertIn(f'HTTP {status}', gw.usage[-1].error)

    def test_catalog_get_is_authenticated_and_never_follows_redirects(self):
        seen = []
        gw = self.gateway([(200, {}, {'data': [{'id': 'jev', 'kind': 'decision'}]})], seen)
        with unittest.mock.patch.dict('os.environ', {'TOKENBAZAAR_API_KEY': 'k'}):
            self.assertEqual(gw.list_models(), [{'id': 'jev', 'kind': 'decision'}])
            self.assertEqual(seen, [('GET', '/v1/models', 'Bearer k')])
            seen.clear()
            moved = self.gateway([(301, {'Location': 'http://127.0.0.1:9/models'}, {})], seen)
            with self.assertRaisesRegex(ProviderError, 'models HTTP 301'):
                moved.list_models()


if __name__ == '__main__':
    unittest.main()
