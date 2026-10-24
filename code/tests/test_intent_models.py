"""Offline tests of the model-backed intent layer using recorded-response doubles.

These test validation, reconciliation, escalation, budgets and caching. They say
nothing about live model accuracy (see evaluation/intent_benchmark.py).
"""
import json
import tempfile
import unittest

import helpers  # noqa: F401
from ghostaudit.intent import DELETE, NON_DELETION, UNRESOLVED
from ghostaudit.intent_models import Signals, decide_intents, generative_signals, jev_signals, reconcile
from ghostaudit.providers import BudgetExceeded, Gateway, ProviderConfig, ProviderError, ReplayGateway

LABELS = ('DELETE_ACCOUNT', 'VERIFY_PRIOR_DELETION', 'SCOPED_DELETION', 'NON_DELETION', 'AMBIGUOUS')


def jev_payload(label, p=0.97, preserve=0.02, marketing=0.02, conflict=0.03):
    probs = {lab: (p if lab == label else round((1 - p) / 4, 4)) for lab in LABELS}
    return {'model': 'typesafe/jev-1.13-test', 'answers': {
        'intent': {'type': 'choice', 'choice': label, 'probabilities': probs, 'confidence': p},
        'preserve_account': {'type': 'noul', 'noul': preserve},
        'marketing_only': {'type': 'noul', 'noul': marketing},
        'conflicting_instructions': {'type': 'noul', 'noul': conflict}},
        'usage': {'input_tokens': 300, 'output_tokens': 40, 'cost': 1e-5}}


def chat_payload(results):
    return {'model': 'google/gemini-test', 'choices': [{'message': {'content': json.dumps({'results': results})}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50}}


def sig(label, p=0.97, **flags):
    base = {'preserve_account': 0.02, 'marketing_only': 0.02, 'conflicting_instructions': 0.02}
    base.update(flags)
    return Signals('jev', label, p, tuple(base.items()))


class ReconcileTests(unittest.TestCase):
    def test_decisions(self):
        cases = {
            'consistent deletion': (sig('DELETE_ACCOUNT'), DELETE),
            'verify prior deletion': (sig('VERIFY_PRIOR_DELETION'), DELETE),
            'scoped deletion': (sig('SCOPED_DELETION'), DELETE),
            'non-deletion': (sig('NON_DELETION', marketing_only=0.95), NON_DELETION),
            'ambiguous': (sig('AMBIGUOUS'), UNRESOLVED),
            'deletion but keep account': (sig('DELETE_ACCOUNT', preserve_account=0.9), UNRESOLVED),
            'deletion but marketing only': (sig('DELETE_ACCOUNT', marketing_only=0.9), UNRESOLVED),
            'conflicting instructions': (sig('DELETE_ACCOUNT', conflicting_instructions=0.8), UNRESOLVED),
            'uncertain conflict flag': (sig('DELETE_ACCOUNT', conflicting_instructions=0.5), UNRESOLVED),
            'uncertain preserve flag': (sig('DELETE_ACCOUNT', preserve_account=0.5), UNRESOLVED),
            'low choice probability': (sig('DELETE_ACCOUNT', p=0.55), UNRESOLVED),
        }
        for name, (signals, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(reconcile(signals).decision, expected)

    def test_signals_are_kept_for_audit(self):
        intent = reconcile(sig('DELETE_ACCOUNT'))
        self.assertIn(('label', 'DELETE_ACCOUNT'), intent.signals)


class JevContractTests(unittest.TestCase):
    def test_decisions_endpoint_shape_and_validation(self):
        seen = []

        def responder(path, body):
            seen.append((path, body))
            return jev_payload('NON_DELETION')
        signals = jev_signals(ReplayGateway(responder), 'Stop the newsletter only.')
        path, body = seen[0]
        self.assertEqual(path, 'decisions')
        self.assertEqual(set(body), {'model', 'state', 'questions'})
        self.assertEqual(set(body['questions']), {'intent', 'preserve_account', 'marketing_only',
                                                  'conflicting_instructions'})
        self.assertIn('Stop the newsletter only.', body['state'])
        self.assertEqual((signals.label, signals.model), ('NON_DELETION', 'typesafe/jev-1.13-test'))

    def test_invalid_jev_answers_are_rejected(self):
        bad = [
            {**jev_payload('DELETE_ACCOUNT'), 'answers': {}},
            {**jev_payload('DELETE_ACCOUNT'), 'answers': {**jev_payload('DELETE_ACCOUNT')['answers'],
                                                          'intent': {'choice': 'ERASE', 'probabilities': {}}}},
        ]
        for payload in bad:
            with self.subTest(str(payload)[:60]), self.assertRaises(ProviderError):
                jev_signals(ReplayGateway(lambda p, b, payload=payload: payload), 'x')


class GenerativeTests(unittest.TestCase):
    def test_spans_must_be_verbatim_and_handles_exact(self):
        good = chat_payload([{'handle': 'h1', 'intent': 'DELETE_ACCOUNT', 'preserve_account': False,
                              'marketing_only': False, 'conflicting_instructions': False,
                              'spans': ['delete my account']}])
        out = generative_signals(ReplayGateway(lambda p, b: good), [('h1', 'Please delete my account now.')])
        self.assertEqual(out['h1'].spans, ('delete my account',))
        invented = chat_payload([{'handle': 'h1', 'intent': 'DELETE_ACCOUNT', 'preserve_account': False,
                                  'marketing_only': False, 'conflicting_instructions': False,
                                  'spans': ['erase everything about me']}])
        out = generative_signals(ReplayGateway(lambda p, b: invented), [('h1', 'Please delete my account now.')])
        self.assertTrue(str(out['h1']).startswith('INVALID_MODEL_OUTPUT'))
        extra = chat_payload([{'handle': 'h9', 'intent': 'NON_DELETION', 'preserve_account': False,
                               'marketing_only': False, 'conflicting_instructions': False, 'spans': ['x']}])
        out = generative_signals(ReplayGateway(lambda p, b: extra), [('h1', 'x')])
        self.assertTrue(str(out['h1']).startswith('PROVIDER_FAILURE'))


    def test_one_bad_item_does_not_discard_its_neighbours(self):
        mixed = chat_payload([
            {'handle': 'h1', 'intent': 'DELETE_ACCOUNT', 'preserve_account': False, 'marketing_only': False,
             'conflicting_instructions': False, 'spans': ['delete my account']},
            {'handle': 'h2', 'intent': 'NON_DELETION', 'preserve_account': False, 'marketing_only': True,
             'conflicting_instructions': False, 'spans': ['not in the text']}])
        out = generative_signals(ReplayGateway(lambda p, b: mixed),
                                 [('h1', 'Please delete my account.'), ('h2', 'Unsubscribe me from offers.')])
        self.assertIsInstance(out['h1'], Signals)
        self.assertTrue(str(out['h2']).startswith('INVALID_MODEL_OUTPUT'))

    def test_unparseable_batch_is_retried_once_and_not_cached(self):
        calls = []

        def respond(p, b):
            calls.append(1)
            if len(calls) == 1:
                return {'model': 'g', 'choices': [{'message': {'content': ''}}]}
            return chat_payload([{'handle': 'h1', 'intent': 'NON_DELETION', 'preserve_account': False,
                                  'marketing_only': True, 'conflicting_instructions': False,
                                  'spans': ['offers']}])
        out = generative_signals(ReplayGateway(respond), [('h1', 'Unsubscribe me from offers.')])
        self.assertEqual(len(calls), 2)
        self.assertIsInstance(out['h1'], Signals)


class FamilyProbabilityTests(unittest.TestCase):
    def test_probability_split_between_deletion_sub_labels_still_authorizes(self):
        payload = jev_payload('VERIFY_PRIOR_DELETION')
        payload['answers']['intent']['probabilities'] = {'DELETE_ACCOUNT': 0.04, 'VERIFY_PRIOR_DELETION': 0.50,
                                                         'SCOPED_DELETION': 0.46, 'NON_DELETION': 0.0,
                                                         'AMBIGUOUS': 0.0}
        signals = jev_signals(ReplayGateway(lambda p, b: payload), 'remove that search result too')
        self.assertAlmostEqual(signals.probability, 1.0)
        self.assertEqual(reconcile(signals).decision, DELETE)

    def test_split_across_families_stays_unresolved(self):
        payload = jev_payload('DELETE_ACCOUNT')
        payload['answers']['intent']['probabilities'] = {'DELETE_ACCOUNT': 0.45, 'VERIFY_PRIOR_DELETION': 0.05,
                                                         'SCOPED_DELETION': 0.0, 'NON_DELETION': 0.45,
                                                         'AMBIGUOUS': 0.05}
        signals = jev_signals(ReplayGateway(lambda p, b: payload), 'x')
        self.assertEqual(reconcile(signals).decision, UNRESOLVED)


class StagedDecisionTests(unittest.TestCase):
    def responder(self, jev_by_text, gen_results=None):
        def respond(path, body):
            if path == 'decisions':
                for text, payload in jev_by_text.items():
                    if text in body['state']:
                        return payload
                raise AssertionError('unexpected request')
            return chat_payload(gen_results(json.loads(body['messages'][1]['content'])['requests']))
        return respond

    def test_jev_answers_when_it_agrees_with_the_deterministic_check(self):
        texts = {'k1': 'Please delete my account and its personal data.', 'k2': 'Elimine mi cuenta, por favor.'}
        gw = ReplayGateway(self.responder({texts['k1']: jev_payload('DELETE_ACCOUNT'),
                                           texts['k2']: jev_payload('DELETE_ACCOUNT')}))
        out = decide_intents(texts, gw)
        self.assertEqual({k: (o.intent.decision, o.stage) for k, o in out.items()},
                         {'k1': (DELETE, 'jev'), 'k2': (DELETE, 'jev')})
        self.assertTrue(all(r.mode == 'replay' for r in gw.usage))

    def test_disagreement_escalates_and_generative_cannot_override_a_definite_check(self):
        text = 'Stop promotional emails only. Keep my account and purchase history.'

        def gen(reqs):   # the generative model wrongly authorizes deletion
            return [{'handle': r['handle'], 'intent': 'DELETE_ACCOUNT', 'preserve_account': False,
                     'marketing_only': False, 'conflicting_instructions': False, 'spans': ['Keep my account']}
                    for r in reqs]
        gw = ReplayGateway(self.responder({text: jev_payload('DELETE_ACCOUNT')}, gen))
        out = decide_intents({'k': text}, gw)['k']
        self.assertEqual((out.intent.decision, out.stage), (UNRESOLVED, 'unresolved'))
        self.assertIn('disagrees', out.escalated_because)

    def test_generative_resolves_an_escalated_case_with_verbatim_support(self):
        text = 'Je souhaite la suppression de mon compte.'

        def gen(reqs):
            self.assertTrue(all(r['handle'].startswith('r') and 'case_' not in r['handle'] for r in reqs))
            return [{'handle': r['handle'], 'intent': 'DELETE_ACCOUNT', 'preserve_account': False,
                     'marketing_only': False, 'conflicting_instructions': False,
                     'spans': ['suppression de mon compte']} for r in reqs]
        gw = ReplayGateway(self.responder({text: jev_payload('AMBIGUOUS')}, gen))
        out = decide_intents({'k': text}, gw)['k']
        self.assertEqual((out.intent.decision, out.stage), (DELETE, 'generative'))
        self.assertEqual(out.intent.spans, ('suppression de mon compte',))

    def test_offline_mode_is_deterministic_only(self):
        out = decide_intents({'k': 'Elimine mi cuenta.'}, None)['k']
        self.assertEqual((out.intent.decision, out.stage), (UNRESOLVED, 'deterministic'))


class GatewayAccountingTests(unittest.TestCase):
    def test_budget_ceiling_stops_calls_before_sending(self):
        gw = ReplayGateway(lambda p, b: jev_payload('DELETE_ACCOUNT'), ProviderConfig(cache_dir=None, max_calls=1))
        jev_signals(gw, 'one')
        with self.assertRaises(BudgetExceeded):
            jev_signals(gw, 'two')
        cost_capped = ReplayGateway(lambda p, b: jev_payload('DELETE_ACCOUNT'),
                                    ProviderConfig(cache_dir=None, max_cost_paise=10, cost_reserve_paise=25))
        with self.assertRaises(BudgetExceeded):
            jev_signals(cost_capped, 'three')

    def test_only_valid_responses_are_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def respond(p, b):
                calls.append(1)
                return jev_payload('DELETE_ACCOUNT') if len(calls) > 1 else {'answers': {}}
            gw = ReplayGateway(respond, ProviderConfig(cache_dir=tmp))
            with self.assertRaises(ProviderError):
                jev_signals(gw, 'same text')            # invalid: not cached
            jev_signals(gw, 'same text')                # valid: cached
            jev_signals(gw, 'same text')                # served from cache
            self.assertEqual(len(calls), 2)
            self.assertEqual([r.mode for r in gw.usage][-1], 'cache')
            self.assertEqual([r.status for r in gw.usage][0], 'invalid')

    def test_live_gateway_refuses_foreign_origins_and_missing_key(self):
        with self.assertRaises(ProviderError):
            Gateway(ProviderConfig(base_url='https://example.com/v1'))


if __name__ == '__main__':
    unittest.main()


class ChooseCandidateTests(unittest.TestCase):
    def test_selection_must_come_from_the_candidate_set(self):
        from ghostaudit.intent_models import choose_candidate
        candidates = {'src_a': 'snapshot captured after the readback', 'src_b': 'earlier log readback'}

        def answer(choice):
            return lambda p, b: {'model': 'jev', 'answers': {'choice': {
                'type': 'choice', 'choice': choice, 'probabilities': {'src_a': 0.8, 'src_b': 0.2}}}}
        handle, _ = choose_candidate(ReplayGateway(answer('src_a')), 'route', 'Which source?', 'ctx', candidates)
        self.assertEqual(handle, 'src_a')
        with self.assertRaises(ProviderError):
            choose_candidate(ReplayGateway(answer('src_invented')), 'route', 'Which source?', 'ctx', candidates)
        with self.assertRaises(ValueError):
            choose_candidate(ReplayGateway(answer('src_a')), 'route', 'q', 'ctx', {'only': 'one'})
