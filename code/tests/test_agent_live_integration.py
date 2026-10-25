"""Live integration is exercised exclusively with mocked transports, never paid providers."""
import contextlib
import copy
import io
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch

import helpers  # noqa: F401
import test_agent_first as scripted
from evaluation.fixtures import _request
from ghostaudit.audit import run_audit
from ghostaudit.contracts import ContractError
from ghostaudit.investigator import InvestigatorConfig
from ghostaudit.output import read_output
from ghostaudit.providers import Gateway, ProviderConfig
from phase4_world import EMAIL, T_AUDIT, world


class MockLiveAgent(unittest.TestCase):
    def setUp(self):
        self.actor = scripted.AgentFirstTests()
        self.actor.setUp()
        self.addCleanup(self.actor.doCleanups)
        self.base, self.root = self.actor.base, self.actor.root
        self.config = InvestigatorConfig('fixture-agent', max_rounds=2, max_case_tokens=120000)
        self.bodies = []

    def gateway(self, *, budget=240, calls=5, cache=None, review=None, turns=None, provider='tokenbazaar'):
        gateway = Gateway(ProviderConfig(provider=provider, max_cost_paise=budget, max_calls=calls, max_retries=0,
            max_rate_limit_retries=0, chat_cost_reserve_paise=120, cache_dir=str(cache) if cache else None))
        sequence = iter(turns or [self.actor.inspect(), [scripted.call('submit_audit', self.actor.good)]])

        def post(path, body):
            self.bodies.append(copy.deepcopy(body))
            if body['model'] == 'independent-reviewer':
                return {'choices': [{'message': {'content': json.dumps(review)}}]}, 5 if provider == 'tokenbazaar' else None
            return {'choices': [{'message': {'content': None, 'tool_calls': next(sequence)}}],
                    'usage': {'prompt_tokens': 321, 'completion_tokens': 17}}, 5 if provider == 'tokenbazaar' else None
        gateway._post = Mock(side_effect=post)
        return gateway

    def audit(self, gateway, *, root=None, name='live', opt_in=True, reviewer=None, config=None):
        root = root or self.root
        with patch('ghostaudit.audit.plan_case', side_effect=AssertionError('planner used')), \
             patch('ghostaudit.investigator.plan_case', side_effect=AssertionError('replanner used')):
            return run_audit(root, root / 'deletion_requests.csv', self.base / f'{name}.csv', self.base / name,
                decision_mode='agent-first', agent_gateway=gateway, agent_config=config or self.config,
                agent_live=opt_in, authorization_model=reviewer)

    def test_explicit_live_opt_in_runs_the_mocked_primary_agent(self):
        g = self.gateway()
        summary = self.audit(g)
        self.assertEqual(summary['fallback_count'], 0)
        self.assertEqual(g._post.call_count, 2)
        self.assertEqual(g.spent_paise, 10)
        self.assertEqual(summary['agent_first']['execution_mode'], 'live')
        self.assertEqual(summary['agent_first']['live_opt_in'], True)

    def test_both_provider_profiles_use_the_same_live_primary_protocol(self):
        for provider in ('tokenbazaar', 'roar'):
            with self.subTest(provider=provider):
                g = self.gateway(provider=provider)
                summary = self.audit(g, name=provider)
                self.assertEqual(summary['fallback_count'], 0)
                self.assertEqual(g._post.call_count, 2)
                self.assertEqual(g.spent_paise, 10 if provider == 'tokenbazaar' else 240)

    def test_live_gate_requires_opt_in_positive_limits_and_no_automatic_retries(self):
        for label, g, opted in [('no opt-in', self.gateway(), False), ('zero budget', self.gateway(budget=0), True),
                                ('zero calls', self.gateway(calls=0), True)]:
            with self.subTest(label=label), self.assertRaises(ContractError):
                self.audit(g, name=label.replace(' ', '_'), opt_in=opted)
            g._post.assert_not_called()
        g = Gateway(ProviderConfig(cache_dir=None))  # default automatic retries are not accepted here
        with self.assertRaises(ContractError):
            self.audit(g, name='retry-gate')

    def test_budget_stops_before_a_second_paid_attempt(self):
        g = self.gateway(budget=120)
        summary = self.audit(g)
        self.assertEqual(g._post.call_count, 1)
        self.assertEqual(g.spent_paise, 5)
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(read_output(self.base / 'live.csv')[0].steps, ())

    def test_call_limit_stops_before_an_extra_attempt(self):
        g = self.gateway(budget=1000, calls=1)
        summary = self.audit(g)
        self.assertEqual(g._post.call_count, 1)
        self.assertEqual(summary['fallback_count'], 1)

    def test_transport_failure_does_not_retry_or_substitute_a_baseline_plan(self):
        g = self.gateway()
        g._post.side_effect = urllib.error.URLError('mock transport failure')
        summary = self.audit(g)
        self.assertEqual(g._post.call_count, 1)
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(read_output(self.base / 'live.csv')[0].steps, ())

    def test_live_responses_can_be_replayed_with_zero_transport_calls(self):
        cache = self.base / 'response-cache'
        g = self.gateway(cache=cache)
        self.audit(g)
        replay = Gateway(ProviderConfig(max_cost_paise=0, max_calls=0, max_retries=0, max_rate_limit_retries=0,
                                        chat_cost_reserve_paise=120, cache_dir=str(cache)))
        replay.mode = 'replay'
        with patch.object(replay, '_post', side_effect=AssertionError('network used')):
            summary = self.audit(replay, name='cached', opt_in=False)
        self.assertEqual(summary['model_cache_hits'], 2)
        self.assertEqual(read_output(self.base / 'live.csv'), read_output(self.base / 'cached.csv'))

    def multilingual(self, review, *, budget=240, rounds=1, case_paise=400):
        text = 'Veuillez envoyer une copie de mes données sans supprimer mon compte.'
        root = world(self.base / 'french', requests=[_request('fx_case', EMAIL, T_AUDIT, text=text)])
        p = {'authorization': {'decision': 'NON_DELETION', 'scope': 'NONE', 'target': '', 'whole_category': False,
                              'request_quote': text, 'reason': 'PRIMARY_PRIVATE_RATIONALE'},
             'deletion_status': 'NOT_APPLICABLE', 'steps': [], 'witnesses': []}
        review = {**review, 'quote': review.get('quote', text)}
        g = self.gateway(budget=budget, review=review, turns=[[scripted.call('submit_audit', p)] for _ in range(rounds)])
        return root, g, self.audit(g, root=root, reviewer='independent-reviewer',
                                  config=InvestigatorConfig('fixture-agent', max_rounds=rounds, max_case_tokens=120000,
                                                            max_case_paise=case_paise))

    def test_multilingual_confirmation_uses_independent_request_only_model(self):
        _, g, summary = self.multilingual({'decision': 'NON_DELETION', 'confidence': 0.99, 'conflicting': False})
        self.assertEqual(summary['fallback_count'], 0)
        self.assertEqual(g._post.call_count, 2)
        self.assertEqual(g.spent_paise, 10)
        review_body = self.bodies[-1]
        self.assertEqual(review_body['model'], 'independent-reviewer')
        self.assertNotIn('PRIMARY_PRIVATE_RATIONALE', json.dumps(review_body))
        self.assertEqual(set(json.loads(review_body['messages'][-1]['content'])), {'request'})
        trace = json.loads((self.base / 'live/case_files.jsonl').read_text())
        self.assertEqual(trace['agent_proposals'][0]['authorization_review']['decision'], 'NON_DELETION')

    def test_review_shares_the_primary_budget(self):
        _, g, summary = self.multilingual({'decision': 'NON_DELETION', 'confidence': 0.99, 'conflicting': False}, budget=120)
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(g._post.call_count, 1)
        self.assertEqual(g.spent_paise, 5)

    def test_review_also_respects_the_remaining_case_allowance(self):
        _, g, summary = self.multilingual({'decision': 'NON_DELETION', 'confidence': 0.99, 'conflicting': False},
                                          budget=1000, case_paise=120)
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(g._post.call_count, 1)
        self.assertEqual(g.spent_paise, 5)

    def test_review_is_not_repeated_for_unchanged_request_during_feedback(self):
        _, g, summary = self.multilingual({'decision': 'DELETE', 'confidence': 0.99, 'conflicting': False},
                                          budget=500, rounds=2)
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(g._post.call_count, 3)
        self.assertEqual(sum(b['model'] == 'independent-reviewer' for b in self.bodies), 1)

    def test_review_disagreement_conflict_low_confidence_and_bad_quote_fail_closed(self):
        reviews = [dict(decision='DELETE', confidence=0.99, conflicting=False),
                   dict(decision='NON_DELETION', confidence=0.5, conflicting=False),
                   dict(decision='NON_DELETION', confidence=0.99, conflicting=True),
                   dict(decision='NON_DELETION', confidence=0.99, conflicting=False, quote='invented translation')]
        for i, review in enumerate(reviews):
            with self.subTest(review=review):
                # Each scenario has fresh paths/cache and a distinct synthetic root.
                old_base = self.base
                self.base = old_base / f'review-{i}'
                self.base.mkdir()
                try:
                    _, _, summary = self.multilingual(review)
                    self.assertEqual(summary['fallback_count'], 1)
                finally:
                    self.base = old_base

    def test_review_model_cannot_be_the_primary_model(self):
        g = self.gateway()
        with self.assertRaises(ContractError):
            self.audit(g, reviewer='fixture-agent')
        g._post.assert_not_called()

    def test_cli_requires_opt_in_budget_reserve_and_a_preexisting_probe(self):
        from main import main
        probe = self.base / 'probes.json'
        probe.write_text(json.dumps({'tokenbazaar:fixture-agent': {'ok': True}}))
        common = ['audit', '--dataset', str(self.root), '--requests', str(self.root / 'deletion_requests.csv'),
                  '--output', str(self.base / 'cli.csv'), '--run-dir', str(self.base / 'cli'),
                  '--decision-mode', 'agent-first', '--agent-mode', 'live', '--agent-model', 'fixture-agent']
        variants = [[], ['--allow-agent-live'], ['--allow-agent-live', '--agent-budget-paise', '240'],
                    ['--allow-agent-live', '--agent-budget-paise', '240', '--agent-call-reserve-paise', 'nan']]
        for flags in variants:
            with self.subTest(flags=flags), patch('main.PROBES', probe), \
                 patch.object(Gateway, '_post', side_effect=AssertionError('paid API')), \
                 patch('ghostaudit.providers.load_env_file', side_effect=AssertionError('secret loading')), \
                 contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(common + flags), 1)
        probe.write_text('{}')
        with patch('main.PROBES', probe), patch.object(Gateway, '_post', side_effect=AssertionError('paid API')), \
             contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(common + ['--allow-agent-live', '--agent-budget-paise', '240',
                                           '--agent-call-reserve-paise', '120']), 1)

    def test_cli_mock_live_persists_used_cache_without_overwriting_original(self):
        from main import main
        probe = self.base / 'probes.json'
        probe.write_text(json.dumps({'tokenbazaar:fixture-agent': {'ok': True}}))
        original = self.base / 'original-cache'
        original.mkdir()
        (original / 'marker').write_bytes(b'preserve')
        replies = iter([self.actor.inspect(), [scripted.call('submit_audit', self.actor.good)]])
        calls = []
        def post(gateway, endpoint, body):
            calls.append(body)
            return {'choices': [{'message': {'content': None, 'tool_calls': next(replies)}}]}, 5
        args = ['audit', '--dataset', str(self.root), '--requests', str(self.root / 'deletion_requests.csv'),
                '--output', str(self.base / 'cli.csv'), '--run-dir', str(self.base / 'cli'), '--decision-mode', 'agent-first',
                '--agent-mode', 'live', '--allow-agent-live', '--agent-model', 'fixture-agent',
                '--agent-budget-paise', '240', '--agent-call-reserve-paise', '120', '--agent-max-calls', '3',
                '--agent-cache', str(original)]
        with patch('main.PROBES', probe), patch.object(Gateway, '_post', new=post), \
             patch.object(Gateway, '_get', side_effect=AssertionError('catalog used')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual((original / 'marker').read_bytes(), b'preserve')
        self.assertEqual(len(list(original.rglob('*'))), 1)
        self.assertEqual(len(list((self.base / 'cli/model_cache').rglob('*.json'))), 2)
