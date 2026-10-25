"""Transport lifecycle regressions; all requests are synthetic or loopback-only."""
import io
import unittest
import urllib.error
import urllib.request
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
from ghostaudit.providers import BudgetExceeded, Gateway, ProviderConfig, ProviderError
from test_provider_compat import serve


class ErrorBodyLifecycle(unittest.TestCase):
    def test_error_body_is_bounded_and_closed(self):
        class Body(io.BytesIO):
            consumed = 0

            def close(self):
                self.consumed = self.tell()
                super().close()
        body = Body(b'x' * 200_000)
        error = urllib.error.HTTPError('https://example.test', 503, 'down', {}, body)
        Gateway(ProviderConfig(cache_dir=None))._error_detail(error)
        self.assertTrue(body.closed)
        self.assertLessEqual(body.consumed, 65536)

    def test_loopback_post_replies_are_framed(self):
        server = serve([(503, {}, {'error': {'message': 'temporarily unavailable'}})], [])
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/test', data=b'x' * 100_000)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        with caught.exception as error:
            self.assertIsNotNone(error.headers.get('Content-Length'))
            self.assertIn(b'temporarily unavailable', error.read())

    def test_unreadable_error_body_is_still_closed(self):
        error = urllib.error.HTTPError('https://example.test', 503, 'down', {}, io.BytesIO())
        with mock.patch.object(error, 'read', side_effect=ConnectionResetError):
            self.assertEqual(Gateway(ProviderConfig(cache_dir=None))._error_detail(error), '')
        self.assertTrue(error.fp.closed)


class AccountingAndCache(unittest.TestCase):
    def test_invalid_catalog_price_cannot_poison_budget_accounting(self):
        gateway = Gateway(ProviderConfig(cache_dir=None, max_cost_paise=120))
        gateway.pricing = {'m': {'inputPer1M': float('nan'), 'outputPer1M': 0}}
        gateway._post = lambda *_: ({'choices': [{'message': {'content': 'ok'}}],
                                     'usage': {'prompt_tokens': 10, 'completion_tokens': 1}}, None)
        gateway.chat('r', 'm', [], 'v', lambda _: None)
        self.assertEqual(gateway.spent_paise, 120)
        self.assertIsNone(gateway.usage[0].cost_estimate_paise)
        with self.assertRaises(BudgetExceeded):
            gateway._reserve(1)

    def test_multibyte_prompts_reserve_for_their_larger_wire_payload(self):
        gateway = Gateway(ProviderConfig(cache_dir=None))
        gateway.pricing = {'m': {'inputPer1M': 1, 'outputPer1M': 0}}
        self.assertGreater(gateway._worst_case('m', {'content': '日' * 100}, 0),
                           gateway._worst_case('m', {'content': 'a' * 100}, 0))

    def test_missing_credentials_release_the_reservation(self):
        gateway = Gateway(ProviderConfig(cache_dir=None))
        with mock.patch.dict('os.environ', {}, clear=True), self.assertRaises(ProviderError):
            gateway.chat('r', 'm', [], 'v', lambda _: None)
        self.assertEqual(gateway._reserved, 0)

    def test_nonfinite_budget_is_rejected(self):
        for value in (float('nan'), float('inf'), -1):
            with self.subTest(value=value), self.assertRaises(ProviderError):
                Gateway(ProviderConfig(max_cost_paise=value))

    def test_nonfinite_billing_cannot_disable_the_ceiling(self):
        gateway = Gateway(ProviderConfig(cache_dir=None, max_cost_paise=120))
        gateway._post = lambda *_: ({'choices': [{'message': {'content': 'ok'}}]}, float('nan'))
        with self.assertRaises(ProviderError):
            gateway.chat('r', 'm', [], 'v', lambda _: None)
        self.assertEqual(gateway.spent_paise, 120)
        with self.assertRaises(BudgetExceeded):
            gateway._reserve(1)

    def test_concurrent_same_key_cache_writes_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            gateway = Gateway(ProviderConfig(cache_dir=tmp))
            rendezvous = threading.Barrier(2)
            original = Path.write_text

            def simultaneous(path, *args, **kwargs):
                result = original(path, *args, **kwargs)
                rendezvous.wait(timeout=3)
                return result

            with mock.patch.object(Path, 'write_text', simultaneous), ThreadPoolExecutor(2) as pool:
                tasks = [pool.submit(gateway._cache_put, 'a' * 64, {'response': {'ok': True}}) for _ in range(2)]
                for task in tasks:
                    task.result()
            self.assertEqual(gateway._cache_get('a' * 64), {'response': {'ok': True}})
