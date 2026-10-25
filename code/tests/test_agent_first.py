"""Scripted primary agents author outputs; planner calls are forbidden on this path."""
import copy
import contextlib
import io
import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import helpers  # noqa: F401
from evaluation.fixtures import FIXTURES, _lookups, _request, write_fixture
from ghostaudit.audit import load_disclosures, run_audit
from ghostaudit.contracts import ContractError
from ghostaudit.inputs import load_context, load_requests
from ghostaudit.investigator import InvestigatorConfig
from ghostaudit.output import read_output, validate_output
from ghostaudit.providers import Gateway, ProviderConfig, ReplayGateway
from ghostaudit.tools import SourceText
from phase4_world import EMAIL, T_AUDIT, T_CAPTURE, world


CALL_IDS = itertools.count()


def call(name, arguments):
    return {'id': f'call_{name}_{next(CALL_IDS)}', 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(arguments)}}


def proposal(root, rid, source, *, system='account_service', scope='ACCOUNT', target=''):
    request = load_requests(root / 'deletion_requests.csv')[0]
    line, text = next((loc, line) for loc, line in SourceText(load_context(root)).lines(source) if rid in line)
    operations = [('FINDING', 'REPORT_REMAINING_RECORD', 'PRESENT', source),
                  ('REMEDIATION', 'DELETE_RECORD', 'ABSENT', ''),
                  ('VERIFICATION', 'VERIFY_RECORD_ABSENT', 'ABSENT', '')]
    return {'authorization': {'decision': 'DELETE', 'scope': scope, 'target': target,
                              'whole_category': False, 'request_quote': request.request_text,
                              'reason': 'The request explicitly authorizes this deletion scope.'},
            'deletion_status': 'INCOMPLETE',
            'steps': [dict(step_no=n, phase=p, system_id=system, resource_id=rid, operation=o, state=s,
                           evidence_id=e) for n, (p, o, s, e) in enumerate(operations, 1)],
            'witnesses': [{'step_no': 1, 'source_id': source, 'locator': line, 'literal': text}]}


class AgentFirstTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = write_fixture(FIXTURES['fixture_a'], self.base) / 'input'
        self.good = proposal(self.root, 'fx_account_A', 'fx_file_A')

    def run_agent(self, turns, *, root=None, name='agent'):
        root = root or self.root
        sequence = iter(turns)
        received = []

        def responder(endpoint, body):
            received.append(body)
            return {'choices': [{'message': {'content': None, 'tool_calls': next(sequence)}}]}
        gateway = ReplayGateway(responder)
        with patch('ghostaudit.audit.plan_case', side_effect=AssertionError('planner used')), \
             patch('ghostaudit.investigator.plan_case', side_effect=AssertionError('replanner used')), \
             patch('ghostaudit.audit.decide_intents', side_effect=AssertionError('Jev intent used')):
            summary = run_audit(root, root / 'deletion_requests.csv', self.base / f'{name}.csv', self.base / name,
                                decision_mode='agent-first', agent_gateway=gateway,
                                agent_config=InvestigatorConfig('fixture-agent', max_rounds=len(turns),
                                    max_tool_calls=30, max_case_tokens=120000))
        validate_output(read_output(self.base / f'{name}.csv'), load_requests(root / 'deletion_requests.csv'),
                        load_context(root).evidence, load_disclosures(self.base / name, self.base / f'{name}.csv'))
        return summary, read_output(self.base / f'{name}.csv')[0], received

    def inspect(self, source='fx_file_A'):
        return [call('inspect_source', {'source_id': source, 'max_lines': 60})]

    def test_authored_output_is_accepted_without_any_planner_or_jev(self):
        summary, row, _ = self.run_agent([self.inspect(), [call('submit_audit', self.good)]])
        self.assertEqual(row.deletion_status, 'INCOMPLETE')
        self.assertEqual([s.resource_id for s in row.steps], ['fx_account_A'] * 3)
        self.assertEqual(summary['agent_first']['accepted_cases'], 1)
        self.assertEqual(summary['fallback_count'], 0)

    def test_different_verified_agent_citation_decisions_survive_serialization(self):
        root = world(self.base / 'choices', files={'evidence/records/second.json': (
            'fx_file_Z', T_CAPTURE, 'account_service', _lookups(T_CAPTURE,
              (T_CAPTURE, 'account_service', 'fx_account_P', 'USER_PROFILE', 'FOUND', EMAIL)))})
        rows = []
        for source in ('fx_file_L', 'fx_file_Z'):
            p = proposal(root, 'fx_account_P', source)
            summary, row, _ = self.run_agent([self.inspect(source), [call('submit_audit', p)]], root=root, name=source)
            self.assertEqual(summary['fallback_count'], 0)
            self.assertEqual(row.steps[0].evidence_id, source)
            rows.append(row)
        self.assertNotEqual(rows[0].steps, rows[1].steps)

    def test_agent_authors_new_resource_operations_from_retrieved_unparsed_evidence(self):
        root = world(self.base / 'discovery')  # PDF intentionally lacks a native recognized structure
        p = proposal(root, 'fx_account_P', 'fx_file_L')
        literal = '\n'.join(SourceText(load_context(root)).pages('fx_file_PDF')['p1'][:4])
        facts = []
        for kind, value in [('OWNERSHIP', ''), ('CATEGORY', 'SUPPORT_ATTACHMENT'), ('STATE', 'PRESENT')]:
            facts.append(call('propose_fact', {'kind': kind, 'system_id': 'support_platform',
                'resource_id': 'fx_attach_P', 'source_id': 'fx_file_PDF', 'locator': 'p1:L1-L4',
                'literal': literal, 'value': value}))
        authored = []
        for step in p['steps']:
            authored.append(dict(step))
            authored.append({**step, 'system_id': 'support_platform', 'resource_id': 'fx_attach_P',
                             'evidence_id': 'fx_file_PDF' if step['phase'] == 'FINDING' else ''})
        p['steps'] = [{**s, 'step_no': n} for n, s in enumerate(authored, 1)]
        p['witnesses'].append({'step_no': 2, 'source_id': 'fx_file_PDF', 'locator': 'p1:L1-L4', 'literal': literal})
        summary, row, _ = self.run_agent([
            self.inspect('fx_file_L') + [call('inspect_media', {'file_id': 'fx_file_PDF', 'page': 'p1'})],
            facts, [call('submit_audit', p)]], root=root)
        self.assertEqual(summary['fallback_count'], 0)
        self.assertEqual(len(row.steps), 6)
        self.assertEqual({s.resource_id for s in row.steps}, {'fx_account_P', 'fx_attach_P'})
        run_audit(root, root / 'deletion_requests.csv', self.base / 'baseline.csv', self.base / 'baseline')
        self.assertEqual(len(read_output(self.base / 'baseline.csv')[0].steps), 3)

    def test_rejected_hallucination_gets_feedback_and_can_be_corrected(self):
        bad = copy.deepcopy(self.good)
        bad['steps'][0]['resource_id'] = 'invented_999'
        summary, row, received = self.run_agent([
            self.inspect(), [call('submit_audit', bad)], [call('submit_audit', self.good)]])
        self.assertEqual(summary['agent_first']['rejected'], 1)
        self.assertEqual(summary['agent_first']['grounding_rejection_rate'], 0.5)
        self.assertIn('INVENTED_RESOURCE', json.dumps(received[-1]['messages']))
        self.assertEqual(row.steps[0].resource_id, 'fx_account_A')

    def test_unverified_proposal_becomes_empty_disclosed_fallback_not_baseline_answer(self):
        bad = copy.deepcopy(self.good)
        bad['deletion_status'] = 'COMPLETE'
        bad['steps'] = [{**bad['steps'][2], 'step_no': 1, 'evidence_id': 'fx_file_A'}]
        summary, row, _ = self.run_agent([self.inspect(), [call('submit_audit', bad)]])
        self.assertEqual((row.deletion_status, row.steps), ('INCOMPLETE', ()))
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(summary['agent_first']['rejected'], 1)

    def test_unretrieved_real_citations_are_rejected(self):
        summary, row, _ = self.run_agent([[call('submit_audit', self.good)]])
        self.assertEqual(row.steps, ())
        self.assertEqual(summary['agent_first']['grounding_rejected'], 1)

    def test_submission_cannot_hide_later_calls_in_the_same_batch(self):
        summary, row, _ = self.run_agent([self.inspect(), [call('submit_audit', self.good),
            call('activity_timeline', {'resource_id': 'fx_account_A'})]])
        self.assertEqual(row.steps, ())
        self.assertEqual(summary['agent_first']['rejected'], 1)

    def test_malformed_native_submission_can_be_repaired_without_a_planner(self):
        summary, row, received = self.run_agent([
            self.inspect(), [call('submit_audit', {})], [call('submit_audit', self.good)]])
        self.assertEqual(summary['fallback_count'], 0)
        self.assertIn('failed schema validation', json.dumps(received[-1]['messages']))
        self.assertEqual(len(row.steps), 3)
        trace = json.loads((self.base / 'agent/case_files.jsonl').read_text())
        self.assertEqual(trace['schema_repairs'], 1)

    def test_late_source_cannot_be_inspected_or_cited(self):
        root = world(self.base / 'late', files={'evidence/records/late.json': (
            'fx_late', '2026-03-03T10:00:00Z', 'account_service', _lookups(T_CAPTURE,
              (T_CAPTURE, 'account_service', 'fx_account_P', 'USER_PROFILE', 'FOUND', EMAIL)))})
        p = proposal(root, 'fx_account_P', 'fx_late')
        summary, row, _ = self.run_agent([self.inspect('fx_late'), [call('submit_audit', p)]], root=root)
        self.assertEqual(row.steps, ())
        self.assertEqual(summary['agent_first']['rejected'], 1)

    def test_explicit_item_agent_selects_search_without_touching_account(self):
        root = world(self.base / 'item', requests=[_request('fx_item', EMAIL, T_AUDIT,
            text='Please remove that search result. Keep my account active.')], lookup_rows=[
            (T_CAPTURE, 'search_service', 'fx_search_P', 'SEARCH_INDEX_ENTRY', 'FOUND', EMAIL)])
        p = proposal(root, 'fx_search_P', 'fx_file_L', system='search_service', scope='ITEM', target='SEARCH_INDEX_ENTRY')
        summary, row, _ = self.run_agent([self.inspect('fx_file_L'), [call('submit_audit', p)]], root=root)
        self.assertEqual(summary['fallback_count'], 0)
        self.assertEqual({s.resource_id for s in row.steps}, {'fx_search_P'})

    def test_agent_cannot_omit_or_presume_an_undated_legal_hold(self):
        root = world(self.base / 'retention', lookup_rows=[
            (T_CAPTURE, 'legal_service', 'fx_audit_P', 'LEGAL_HOLD_RECORD', 'FOUND', EMAIL)])
        p = proposal(root, 'fx_account_P', 'fx_file_L')
        summary, row, received = self.run_agent([self.inspect('fx_file_L'), [call('submit_audit', p)]], root=root)
        self.assertEqual(row.steps, ())
        trace = json.loads((self.base / 'agent/case_files.jsonl').read_text())
        self.assertIn('UNPROVEN_POLICY', {f['code'] for f in trace['agent_proposals'][0]['findings']})
        self.assertEqual(summary['fallback_count'], 1)

    def test_ambiguous_single_item_cannot_be_chosen_arbitrarily(self):
        root = world(self.base / 'ambiguous', requests=[_request('fx_item', EMAIL, T_AUDIT,
            text='Please remove that search result. Keep my account active.')], lookup_rows=[
            (T_CAPTURE, 'search_service', rid, 'SEARCH_INDEX_ENTRY', 'FOUND', EMAIL)
            for rid in ('fx_search_P', 'fx_search_P2')])
        p = proposal(root, 'fx_search_P', 'fx_file_L', system='search_service', scope='ITEM', target='SEARCH_INDEX_ENTRY')
        summary, row, _ = self.run_agent([self.inspect('fx_file_L'), [call('submit_audit', p)]], root=root)
        self.assertEqual(row.steps, ())
        self.assertEqual(summary['agent_first']['rejected'], 1)

    def test_agent_cannot_replace_the_supplied_policy_action(self):
        bad = copy.deepcopy(self.good)
        for step, operation, state in zip(bad['steps'],
            ('REPORT_EXPOSED_PII', 'REDACT_RECORD', 'VERIFY_RECORD_REDACTED'), ('PRESENT', 'REDACTED', 'REDACTED')):
            step.update(operation=operation, state=state)
        summary, row, _ = self.run_agent([self.inspect(), [call('submit_audit', bad)]])
        self.assertEqual(row.steps, ())
        trace = json.loads((self.base / 'agent/case_files.jsonl').read_text())
        self.assertIn('POLICY_MISMATCH', {f['code'] for f in trace['agent_proposals'][0]['findings']})
        self.assertEqual(summary['agent_first']['rejected'], 1)

    def test_agent_cannot_expand_a_single_item_to_the_whole_category(self):
        root = world(self.base / 'whole', requests=[_request('fx_item', EMAIL, T_AUDIT,
            text='Please remove that search result. Keep my account active.')], lookup_rows=[
            (T_CAPTURE, 'search_service', 'fx_search_P', 'SEARCH_INDEX_ENTRY', 'FOUND', EMAIL)])
        p = proposal(root, 'fx_search_P', 'fx_file_L', system='search_service', scope='ITEM', target='SEARCH_INDEX_ENTRY')
        p['authorization']['whole_category'] = True
        _, row, _ = self.run_agent([self.inspect('fx_file_L'), [call('submit_audit', p)]], root=root)
        self.assertEqual(row.steps, ())

    def test_agent_facts_cannot_redate_future_events_from_an_earlier_export(self):
        literal = (f'2026-03-03T10:00:00Z level=INFO service=account_service trace_id=x '
                   f'event=profile_lookup_completed resource_id=fx_account_P email={EMAIL} rows=1 http_status=200 note=found')
        root = world(self.base / 'future', files={'evidence/logs/future.log': (
            'fx_future', T_CAPTURE, 'account_service', (literal + '\n').encode())})
        p = proposal(root, 'fx_account_P', 'fx_future')
        fact = {'kind': 'STATE', 'system_id': 'account_service', 'resource_id': 'fx_account_P',
                'source_id': 'fx_future', 'locator': 'L1', 'literal': literal, 'value': 'PRESENT'}
        _, row, _ = self.run_agent([self.inspect('fx_future'), [call('propose_fact', fact)],
                                    [call('submit_audit', p)]], root=root)
        self.assertEqual(row.steps, ())
        trace = json.loads((self.base / 'agent/case_files.jsonl').read_text())
        self.assertEqual(len(trace['facts']), 1)
        self.assertIn('FACT_UNSUPPORTED', {f['code'] for f in trace['agent_proposals'][0]['findings']})

    def test_empty_cache_never_contacts_a_provider(self):
        gateway = Gateway(ProviderConfig(max_calls=0, max_cost_paise=0, cache_dir=None))
        gateway.mode = 'replay'
        with patch.object(gateway, '_post', side_effect=AssertionError('network used')):
            summary = run_audit(self.root, self.root / 'deletion_requests.csv', self.base / 'p.csv', self.base / 'empty',
                decision_mode='agent-first', agent_gateway=gateway, agent_config=InvestigatorConfig('uncached-model'))
        self.assertEqual(summary['fallback_count'], 1)
        self.assertIsNone(summary['agent_first']['rejection_rate'])

    def test_live_gateway_is_refused_before_loading_sources(self):
        gateway = Gateway(ProviderConfig(cache_dir=None))
        with patch('ghostaudit.audit.build_corpus', side_effect=AssertionError('media calls possible')), \
             self.assertRaises(ContractError):
            run_audit(self.root, self.root / 'deletion_requests.csv', self.base / 'p.csv', self.base / 'live',
                decision_mode='agent-first', agent_gateway=gateway, agent_config=InvestigatorConfig('model'))

    def test_replay_label_alone_cannot_enable_paid_cache_misses(self):
        gateway = Gateway(ProviderConfig(cache_dir=None))
        gateway.mode = 'replay'
        with self.assertRaises(ContractError):
            run_audit(self.root, self.root / 'deletion_requests.csv', self.base / 'p.csv', self.base / 'unsafe',
                decision_mode='agent-first', agent_gateway=gateway, agent_config=InvestigatorConfig('model'))

    def test_cli_replay_preserves_cache_and_avoids_catalogs_keys_and_posts(self):
        from main import main
        cache = self.base / 'original-cache'
        cache.mkdir()
        marker = cache / 'preserved.json'
        marker.write_bytes(b'original cached bytes')
        for intent in ('offline', 'jev'):
            with self.subTest(intent=intent), contextlib.redirect_stdout(io.StringIO()), \
                 patch.object(Gateway, '_post', side_effect=AssertionError('paid call')), \
                 patch.object(Gateway, '_get', side_effect=AssertionError('catalog call')), \
                 patch('ghostaudit.providers.load_env_file', side_effect=AssertionError('secret loading')):
                code = main(['audit', '--dataset', str(self.root), '--requests', str(self.root / 'deletion_requests.csv'),
                             '--output', str(self.base / f'cli-{intent}.csv'), '--run-dir', str(self.base / f'cli-{intent}'),
                             '--decision-mode', 'agent-first', '--agent-model', 'uncached-general-model',
                             '--agent-cache', str(cache), '--intent', intent])
                self.assertEqual(code, 0)
                summary = json.loads((self.base / f'cli-{intent}/run_manifest.json').read_text())
                self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(marker.read_bytes(), b'original cached bytes')
        self.assertEqual(len(list(cache.rglob('*'))), 1)

    def test_complete_status_and_exhaustive_absence_are_agent_authored(self):
        root = write_fixture(FIXTURES['fixture_d'], self.base / 'complete') / 'input'
        request = load_requests(root / 'deletion_requests.csv')[0]
        p = copy.deepcopy(self.good)
        p['authorization']['request_quote'] = request.request_text
        p['deletion_status'] = 'COMPLETE'
        p['steps'] = [dict(step_no=n, phase='VERIFICATION', system_id=system, resource_id=rid,
                          operation=op, state=state, evidence_id='fx_file_D2') for n, (system, rid, op, state) in
                      enumerate([('account_service', 'fx_account_D', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
                                 ('security_service', 'fx_audit_D', 'VERIFY_RECORD_PRESENT', 'PRESENT')], 1)]
        lines = list(SourceText(load_context(root)).lines('fx_file_D2'))
        p['witnesses'] = []
        for n, marker in [(1, 'CREATE TABLE customer_data_primary'), (2, 'fx_audit_D')]:
            locator, literal = next((loc, line) for loc, line in lines if marker in line)
            p['witnesses'].append(dict(step_no=n, source_id='fx_file_D2', locator=locator, literal=literal))
        summary, row, _ = self.run_agent([
            self.inspect('fx_file_D1'), self.inspect('fx_file_D2'), [call('submit_audit', p)]], root=root)
        self.assertEqual(summary['fallback_count'], 0)
        self.assertEqual(row.deletion_status, 'COMPLETE')
        self.assertEqual(len(row.steps), 2)

    def test_non_deletion_is_a_real_agent_decision_not_a_precomputed_status(self):
        root = write_fixture(FIXTURES['fixture_c'], self.base / 'nondelete') / 'input'
        request = load_requests(root / 'deletion_requests.csv')[0]
        p = {'authorization': {'decision': 'NON_DELETION', 'scope': 'NONE', 'target': '',
                              'whole_category': False, 'request_quote': request.request_text,
                              'reason': 'The customer preserves the account and opts out of marketing only.'},
             'deletion_status': 'NOT_APPLICABLE', 'steps': [], 'witnesses': []}
        summary, row, _ = self.run_agent([[call('submit_audit', p)]], root=root)
        self.assertEqual(summary['fallback_count'], 0)
        self.assertEqual((row.deletion_status, row.steps), ('NOT_APPLICABLE', ()))
