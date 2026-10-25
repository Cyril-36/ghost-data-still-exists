"""Offline regressions for trace-confirmed interface failures; no labels or live transports."""
import copy
import json
import unittest
from unittest.mock import patch

import helpers  # noqa: F401
import test_agent_first as scripted
from ghostaudit.agent_protocol import normalize_proposal
from ghostaudit.audit import run_audit
from ghostaudit.contracts import ContractError
from ghostaudit.inputs import load_context
from ghostaudit.investigator import InvestigatorConfig
from ghostaudit.output import read_output
from ghostaudit.providers import ReplayGateway
from ghostaudit.tools import SourceText
from phase4_world import EMAIL, T_CAPTURE, world
from test_phase4_casefile import setup_case


class RootCauseFixes(unittest.TestCase):
    def setUp(self):
        self.actor = scripted.AgentFirstTests()
        self.actor.setUp()
        self.addCleanup(self.actor.doCleanups)

    def test_only_serialization_changes_and_witness_follows_its_step(self):
        p = copy.deepcopy(self.actor.good)
        p['steps'].reverse()
        for n, step in enumerate(p['steps'], 1):
            step['step_no'] = n
        p['witnesses'][0]['step_no'] = 3
        original = copy.deepcopy(p)
        summary, row, _ = self.actor.run_agent([self.actor.inspect(), [scripted.call('submit_audit', p)]])
        self.assertEqual(summary['fallback_count'], 0)
        self.assertEqual([s.operation for s in row.steps], [s['operation'] for s in self.actor.good['steps']])
        self.assertEqual(p, original)
        trace = json.loads((self.actor.base / 'agent/case_files.jsonl').read_text())
        self.assertEqual(trace['agent_proposals'][0]['serialization']['step_number_map']['3'], 1)

    def test_future_step_citations_in_an_incomplete_row_are_cleared_and_recorded(self):
        p = copy.deepcopy(self.actor.good)
        p['steps'][1]['evidence_id'] = p['steps'][2]['evidence_id'] = 'fx_file_A'
        summary, row, _ = self.actor.run_agent([self.actor.inspect(), [scripted.call('submit_audit', p)]])
        self.assertEqual(summary['fallback_count'], 0)
        self.assertEqual([s.evidence_id for s in row.steps], ['fx_file_A', '', ''])   # the finding keeps its proof
        trace = json.loads((self.actor.base / 'agent/case_files.jsonl').read_text())
        cleared = trace['agent_proposals'][0]['serialization']['cleared_future_evidence']
        self.assertEqual([c['operation'] for c in cleared], ['DELETE_RECORD', 'VERIFY_RECORD_ABSENT'])

    def test_source_reference_quotes_only_an_already_retrieved_span(self):
        p = copy.deepcopy(self.actor.good)
        del p['witnesses'][0]['literal']
        summary, _, _ = self.actor.run_agent([self.actor.inspect(), [scripted.call('submit_audit', p)]])
        self.assertEqual(summary['fallback_count'], 0)
        trace = json.loads((self.actor.base / 'agent/case_files.jsonl').read_text())
        w = trace['agent_proposals'][0]['resolved_witnesses'][0]
        self.assertEqual(w['literal'], self.actor.good['witnesses'][0]['literal'])
        self.assertEqual(w['origin'], 'already_retrieved_span')

    def test_omitting_a_literal_never_grants_unseen_lines(self):
        p = copy.deepcopy(self.actor.good)
        del p['witnesses'][0]['literal']
        summary, row, _ = self.actor.run_agent([
            [scripted.call('inspect_source', {'source_id': 'fx_file_A', 'max_lines': 1})],
            [scripted.call('submit_audit', p)]])
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(row.steps, ())

    def test_supplied_fabricated_literal_is_not_silently_replaced(self):
        p = copy.deepcopy(self.actor.good)
        p['witnesses'][0]['literal'] = 'invented presence for fx_account_A'
        summary, row, _ = self.actor.run_agent([self.actor.inspect(), [scripted.call('submit_audit', p)]])
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(row.steps, ())

    def test_ambiguous_step_ids_and_orphan_witnesses_are_not_repaired(self):
        for mode in ('duplicate_id', 'orphan', 'duplicate_step'):
            p = copy.deepcopy(self.actor.good)
            if mode == 'duplicate_id':
                p['steps'][1]['step_no'] = 1
            elif mode == 'orphan':
                p['witnesses'][0]['step_no'] = 90
            else:
                p['steps'].append({**p['steps'][0], 'step_no': 4})
            with self.subTest(mode=mode), self.assertRaises(ContractError):
                normalize_proposal(p)

    def run_request_retry(self, forever=False):
        bodies = []
        def responder(endpoint, body):
            bodies.append(copy.deepcopy(body))
            n = len(bodies)
            if n == 1 or forever:
                calls = [scripted.call('interpret_request', {'decision': 'DELETE'})]
            elif n == 2:
                calls = [scripted.call('interpret_request', self.actor.good['authorization'])]
            elif n == 3:
                calls = self.actor.inspect()
            else:
                calls = [scripted.call('submit_audit', self.actor.good)]
            return {'choices': [{'message': {'content': None, 'tool_calls': calls}}]}
        root = self.actor.root
        summary = run_audit(root, root / 'deletion_requests.csv', self.actor.base / 'result.csv',
            self.actor.base / 'result', decision_mode='agent-first', agent_gateway=ReplayGateway(responder),
            agent_config=InvestigatorConfig('fixture-agent', max_rounds=4, max_case_tokens=120000),
            agent_request_first=True)
        return summary, bodies, json.loads((self.actor.base / 'result/case_files.jsonl').read_text())

    def test_invalid_first_reply_does_not_advance_request_phase(self):
        summary, bodies, _ = self.run_request_retry()
        self.assertEqual(summary['fallback_count'], 0)
        for body in bodies[:2]:
            self.assertEqual(body['tool_choice']['function']['name'], 'interpret_request')
            self.assertNotIn('source_inventory', json.dumps(body['messages']))
        self.assertIn('source_inventory', json.dumps(bodies[2]['messages']))

    def test_repeated_malformed_model_output_is_not_reported_as_api_unavailability(self):
        summary, _, trace = self.run_request_retry(forever=True)
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(trace['stop_reason'], 'SCHEMA_REPAIR_LIMIT')

    def test_clipped_search_preview_is_explicit_and_not_citable(self):
        root = self.actor.base / 'search'
        line = f'fx_long_42 {EMAIL} ' + 'x' * 300
        s = setup_case(root, files={'evidence/logs/long.txt': ('fx_long_file', T_CAPTURE, 'fixture', line.encode())})
        result = s['tools'].call('search_identifiers', {'values': [EMAIL]})
        hit = next(m for m in result['matches'] if m['source_id'] == 'fx_long_file')
        self.assertFalse(hit['text_complete'])
        self.assertNotIn('L1', s['case'].shown.get('fx_long_file', set()))
        self.assertIn('preview', result['citation_note'])

    def test_clipped_inspection_lines_are_explicit_and_not_citable(self):
        root = self.actor.base / 'inspect'
        line = f'fx_long_43 {EMAIL} ' + 'x' * 5200
        s = setup_case(root, files={'evidence/logs/long.txt': ('fx_long_inspect', T_CAPTURE, 'fixture', line.encode())})
        result = s['tools'].call('inspect_source', {'source_id': 'fx_long_inspect'})
        self.assertFalse(result['lines'][0]['text_complete'])
        self.assertNotIn('L1', s['case'].shown.get('fx_long_inspect', set()))

    def test_lookup_hint_is_discovery_only_and_does_not_create_an_association(self):
        s = setup_case(self.actor.base / 'hint')
        s['case'].lookup_handles.add('fx_unproven_42')
        result = s['tools'].call('search_identifiers', {'values': ['fx_unproven_42']})
        self.assertEqual(result['total_matches'], 0)
        self.assertNotIn('fx_unproven_42', s['case'].handles)
        self.assertFalse(s['case'].facts)

    def test_verifier_hint_can_be_investigated_before_corrected_agent_submission(self):
        root = world(self.actor.base / 'repair', lookup_rows=[
            (T_CAPTURE, 'search_service', 'fx_search_P', 'SEARCH_INDEX_ENTRY', 'FOUND', EMAIL)])
        profile = scripted.proposal(root, 'fx_account_P', 'fx_file_L')
        search = scripted.proposal(root, 'fx_search_P', 'fx_file_L', system='search_service')
        lines = SourceText(load_context(root)).pages('fx_file_L')['']
        search_line = next(n for n, line in enumerate(lines, 1) if 'fx_search_P' in line)
        full = copy.deepcopy(profile)
        full['steps'] = []
        for phase in ('FINDING', 'REMEDIATION', 'VERIFICATION'):
            for p in (profile, search):
                full['steps'] += [copy.deepcopy(s) for s in p['steps'] if s['phase'] == phase]
        for n, step in enumerate(full['steps'], 1):
            step['step_no'] = n
        full['witnesses'].append({**search['witnesses'][0], 'step_no': 2})
        turns = iter([
            [scripted.call('inspect_source', {'source_id': 'fx_file_L', 'max_lines': search_line - 1})],
            [scripted.call('submit_audit', profile)],
            [scripted.call('search_identifiers', {'values': ['fx_search_P']})],
            [scripted.call('submit_audit', full)]])
        bodies = []
        def reply(endpoint, body):
            bodies.append(copy.deepcopy(body))
            calls = next(turns)
            return {'choices': [{'message': {'content': None, 'tool_calls': calls}}],
                    'usage': {'prompt_tokens': 15000 if len(bodies) <= 2 else 0, 'completion_tokens': 0}}
        with patch('ghostaudit.audit.plan_case', side_effect=AssertionError('planner used')), \
             patch('ghostaudit.investigator.plan_case', side_effect=AssertionError('replanner used')):
            summary = run_audit(root, root / 'deletion_requests.csv', self.actor.base / 'result.csv',
                self.actor.base / 'result', decision_mode='agent-first', agent_gateway=ReplayGateway(reply),
                agent_config=InvestigatorConfig('fixture-agent', max_rounds=4, max_tool_calls=10,
                    max_case_tokens=60000), agent_finalize_on_pressure=True)
        trace = json.loads((self.actor.base / 'result/case_files.jsonl').read_text())
        self.assertEqual(summary['fallback_count'], 0)
        self.assertEqual(len(read_output(self.actor.base / 'result.csv')[0].steps), 6)
        self.assertEqual(bodies[2]['tool_choice'], 'required')
        self.assertTrue(json.loads(bodies[2]['messages'][-1]['content'])['execution_budget']['evidence_repair_window'])
        self.assertIn('fx_search_P', trace['lookup_handles'])
        self.assertTrue(trace['tool_calls'][2]['ok'])

    def test_catalog_is_paginated_eligible_metadata_without_resource_proof(self):
        bodies = []
        def reply(endpoint, body):
            bodies.append(copy.deepcopy(body))
            calls = ([scripted.call('list_sources', {'kind': 'json'})] if len(bodies) == 1
                     else [scripted.call('inspect_source', {'source_id': 'fx_file_A'})] if len(bodies) == 2
                     else [scripted.call('submit_audit', self.actor.good)])
            return {'choices': [{'message': {'content': None, 'tool_calls': calls}}]}
        root = self.actor.root
        summary = run_audit(root, root / 'deletion_requests.csv', self.actor.base / 'catalog.csv',
            self.actor.base / 'catalog', decision_mode='agent-first', agent_gateway=ReplayGateway(reply),
            agent_config=InvestigatorConfig('fixture-agent', max_rounds=3, max_case_tokens=120000))
        self.assertEqual(summary['fallback_count'], 0)
        inventory = json.loads(bodies[0]['messages'][1]['content'])['source_inventory']
        self.assertLess(len(json.dumps(inventory)), 300)
        returned = json.loads(next(m['content'] for m in bodies[1]['messages'] if m['role'] == 'tool'))
        self.assertTrue(returned['sources'])
        self.assertIn('not ownership', returned['note'])
        self.assertNotIn('resource_id', json.dumps(returned))
