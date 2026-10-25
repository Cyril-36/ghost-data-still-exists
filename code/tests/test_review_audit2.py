"""Regressions for the second independent review (e858907): ceiling, grounding, planner, verifier,
evaluation and provider findings. Each test reproduces one claimed defect."""
import contextlib
import http.server
import io
import json
import shutil
import tempfile
import threading
import unittest
import unittest.mock
from dataclasses import replace
from pathlib import Path

import helpers  # noqa: F401
import main as cli
from evaluation.error_analysis import analyze_errors
from evaluation.fixtures import Fixture, _identity, _lookups, _manifest, _request, _rules, write_fixture
from evaluation.gap_analysis import run_corpus
from ghostaudit.audit import run_audit
from ghostaudit.contracts import OutputRow, Step
from ghostaudit.inputs import load_context
from ghostaudit.output import read_output
from ghostaudit.providers import BudgetExceeded, Gateway, ProviderConfig, ReplayGateway
from phase4_world import EMAIL, OTHER, world
from test_hardening_regressions import target_payload
from test_intent_models import jev_payload
from test_media import make_pdf
from test_phase4_casefile import ownership, setup_case
from test_phase4_verifier import codes, plans_for
from test_providers_dual import roar_answer

ROAR = ProviderConfig(provider='roar', cache_dir=None)


def serve(status, body, seen):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get('Authorization'))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def at(gateway, srv):
    gateway.config = replace(gateway.config, base_url=f'http://127.0.0.1:{srv.server_port}/v1')
    return gateway


# --- P0: one combined ceiling ----------------------------------------------------------------------

class CombinedCeiling(unittest.TestCase):
    def test_shared_ledger_refuses_concurrent_reservations_beyond_the_ceiling(self):
        intent = Gateway(ProviderConfig(cache_dir=None, max_cost_paise=100.0))
        inv = Gateway(replace(ROAR, max_cost_paise=100.0))
        inv.share_budget(intent)
        intent._reserve(60.0)                         # in flight on the intent gateway
        with self.assertRaises(BudgetExceeded):
            inv._reserve(60.0)
        inv._reserve(40.0)
        self.assertEqual(intent._ledger._reserved, 100.0)

    def test_main_gives_mixed_providers_one_ledger(self):
        captured = {}
        with tempfile.TemporaryDirectory() as tmp:
            root, probes = world(tmp), Path(tmp) / 'probes.json'
            probes.write_text(json.dumps({'roar:gpt-oss-20b': {'ok': True}}))
            with unittest.mock.patch.object(cli, 'PROBES', probes), \
                    unittest.mock.patch.object(cli, 'run_audit', lambda *a, **k: captured.update(k) or {}), \
                    unittest.mock.patch('ghostaudit.providers.Gateway.load_pricing', lambda self: {}), \
                    contextlib.redirect_stdout(io.StringIO()):
                cli.main(['audit', '--dataset', str(root), '--requests', str(root / 'deletion_requests.csv'),
                          '--output', str(Path(tmp) / 'o.csv'), '--run-dir', str(Path(tmp) / 'r'), '--intent', 'jev',
                          '--intent-provider', 'tokenbazaar', '--investigator-provider', 'roar',
                          '--investigator', 'live', '--budget-paise', '300'])
        self.assertIsNot(captured['gateway'], captured['investigator_gateway'])
        self.assertIs(captured['investigator_gateway']._ledger, captured['gateway']._ledger)


# --- P1: grounding of investigator facts in what was actually shown -------------------------------

class ShownLinesOnly(unittest.TestCase):
    def test_a_fact_citing_lines_never_shown_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = setup_case(tmp)
            s['tools'].call('inspect_source', {'source_id': 'fx_file_PDF', 'page': 'p1', 'start_line': 1, 'max_lines': 2})
            s['case'].handles.add('fx_attach_P')
            fact, reason = s['validator'].validate(ownership(), s['case'])    # cites p1:L1-L3; L3 never shown
            self.assertIsNone(fact)
            self.assertTrue(reason.startswith('UNSHOWN_LINES'), reason)

    def test_sql_rows_beyond_the_returned_page_register_no_handles(self):
        rows = ''.join(f"INSERT INTO t VALUES('fx_row_{n}','{EMAIL}');\n" for n in range(1, 26))
        sql = (f'-- snapshot\nBEGIN TRANSACTION;\nCREATE TABLE t (resource_id TEXT, email TEXT);\n{rows}COMMIT;\n').encode()
        with tempfile.TemporaryDirectory() as tmp:
            s = setup_case(tmp, files={'evidence/databases/t.sql': ('fx_file_T', '2026-03-01T10:00:00Z', 'db', sql)})
            out = s['tools'].call('query_sql_snapshot', {'file_id': 'fx_file_T', 'table': 't', 'column': 'email',
                                                         'value': EMAIL})
            self.assertEqual((out['returned'], out['truncated']), (20, True))
            self.assertIn('fx_row_20', s['case'].handles)
            self.assertNotIn('fx_row_21', s['case'].handles)

    def test_pdf_identifiers_come_only_from_displayed_lines(self):
        lines = [f'line {n}' for n in range(1, 71)]
        lines[64] = f'hidden fx_hidden_9 {OTHER}'
        pdf = make_pdf([lines])
        with tempfile.TemporaryDirectory() as tmp:
            s = setup_case(tmp, files={'evidence/documents/long.pdf': ('fx_file_LONG', '2026-03-01T10:00:00Z',
                                                                       'support_platform', pdf)})
            out = s['tools'].call('inspect_media', {'file_id': 'fx_file_LONG'})
            self.assertNotIn('fx_hidden_9', json.dumps(out))
            self.assertNotIn('fx_hidden_9', s['case'].handles)

    def test_a_clipped_result_registers_only_what_survives_clipping(self):
        log = ''.join(f'{"x" * 250} row {n}\n' for n in range(1, 40)) + 'tail fx_tail_7 present\n'
        with tempfile.TemporaryDirectory() as tmp:
            s = setup_case(tmp, files={'evidence/logs/wide.log': ('fx_file_W', '2026-03-01T10:00:00Z', 'db', log.encode())})
            out = s['tools'].call('inspect_source', {'source_id': 'fx_file_W', 'max_lines': 40})
            self.assertTrue(out['truncated'])
            self.assertNotIn('fx_tail_7', s['case'].handles)
            shown = s['case'].shown.get('fx_file_W', set())
            self.assertIn('L1', shown)
            self.assertNotIn('L40', shown)


# --- P1: planner item targets ---------------------------------------------------------------------

def target_world(texts):
    email, uid = 'person.z@example.test', 'fx_user_9'
    rows = [('2026-01-03T11:00:00Z', 'export_service', rid, 'USER_EXPORT', 'FOUND', email)
            for rid in ('fx_export_1', 'fx_export_2')]
    rows.append(('2026-01-03T11:00:00Z', 'account_service', 'fx_account_9', 'USER_PROFILE', 'FOUND', email))
    return Fixture(name='fixture_targets', description='SYNTHETIC: item targets with a named user ID',
                   tables={'deletion_requests.csv': [_request(c, email, '2026-01-03T12:00:00Z', text=t)
                                                     for c, t in texts.items()],
                           'user_identities.csv': [_identity(uid, 'Fixture Person Z', email, '+12025550161')],
                           'deletion_rules.csv': _rules(),
                           'evidence_files.csv': [_manifest('fx_file_Z', 'evidence/records/l.json', '2026-01-03T11:00:00Z')]},
                   evidence={'evidence/records/l.json': _lookups('2026-01-03T11:00:00Z', *rows)},
                   expected=[OutputRow(c, 'INCOMPLETE', ()) for c in texts], fallback_ids=frozenset(texts))


class ItemTargets(unittest.TestCase):
    TEXTS = {'fx_case_1': 'Delete only my export fx_export_1. My customer ID is fx_user_9.',
             'fx_case_2': 'Delete only my export fx_export_1 and order fx_order_5.',
             'fx_case_3': 'Delete my data export, please; keep my account.'}

    def test_named_target_with_own_user_id_unrelated_id_and_several_candidates(self):
        def respond(path, body):
            return target_payload('USER_EXPORT', whole=0.05) if 'target' in body['questions'] \
                else jev_payload('SCOPED_DELETION')
        with tempfile.TemporaryDirectory() as tmp:
            base = write_fixture(target_world(self.TEXTS), Path(tmp))
            summary = run_audit(base / 'input', base / 'input/deletion_requests.csv', base / 'p.csv', base / 'run',
                                gateway=ReplayGateway(respond))
            rows = {r.case_id: {s.resource_id for s in r.steps} for r in read_output(base / 'p.csv')}
        self.assertEqual(rows['fx_case_1'], {'fx_export_1'})              # own user ID is not an unknown target
        self.assertEqual(rows['fx_case_2'], set())                        # an unrelated named ID: unresolved
        self.assertEqual(rows['fx_case_3'], set())                        # two candidates, none named: unresolved
        self.assertNotIn('fx_account_9', set().union(*rows.values()))     # item scope never authorizes the account
        self.assertEqual(set(summary['fallback_ids']), {'fx_case_2', 'fx_case_3'})


# --- P1: verifier traversal and stale citations -----------------------------------------------------

class VerifierTraversal(unittest.TestCase):
    def test_a_reference_to_another_persons_record_is_not_an_omission(self):
        rows = [('2026-03-01T09:00:00Z', 'search_service', 'fx_search_P', 'SEARCH_INDEX_ENTRY', 'FOUND', EMAIL),
                ('2026-03-01T09:00:00Z', 'export_service', 'fx_export_Q', 'USER_EXPORT', 'FOUND', OTHER)]
        acts = [('fx_act_1', '2026-03-01T09:30:00Z', 'SYNC_RECORD', 'export_service', 'search_service', 'fx_export_Q',
                 'SUCCESS', 'Sync repopulated downstream resource fx_search_P.')]
        with tempfile.TemporaryDirectory() as tmp:
            plans, verifier = plans_for(world(tmp, lookup_rows=rows, activities=acts))
            self.assertNotIn('fx_export_Q', {o.resource_id for o in plans[0].obligations})
            self.assertEqual(codes(verifier.verify(plans[0])), set())
            owned = verifier._owned(plans[0].subject, plans[0].request.audit_at, [])
            self.assertNotIn('fx_export_Q', {r for _, r in owned})

    def test_no_traversal_from_an_id_reused_across_systems(self):
        rows = [('2026-03-01T09:00:00Z', 'search_service', 'fx_dup_1', 'SEARCH_INDEX_ENTRY', 'FOUND', EMAIL),
                ('2026-03-01T09:00:00Z', 'export_service', 'fx_dup_1', 'USER_EXPORT', 'FOUND', ''),
                ('2026-03-01T09:00:00Z', 'cache_service', 'fx_cache_7', 'CACHE_ENTRY', 'FOUND', '')]
        acts = [('fx_act_1', '2026-03-01T09:30:00Z', 'SYNC_RECORD', 'export_service', 'cache_service', 'fx_dup_1',
                 'SUCCESS', 'Sync repopulated downstream resource fx_cache_7.')]
        with tempfile.TemporaryDirectory() as tmp:
            plans, verifier = plans_for(world(tmp, lookup_rows=rows, activities=acts))
            self.assertNotIn('fx_cache_7', {o.resource_id for o in plans[0].obligations})
            self.assertEqual(codes(verifier.verify(plans[0])), set())

    def test_stale_citation_uses_the_latest_support_in_the_cited_source(self):
        found = ('search_service', 'fx_search_P', 'SEARCH_INDEX_ENTRY', 'FOUND', EMAIL)
        rows = [('2026-03-01T08:00:00Z', *found), ('2026-03-01T11:00:00Z', *found)]
        gone = {'evidence/records/gone.json': ('fx_file_G', '2026-03-01T10:00:00Z', 'fixture', _lookups(
            '2026-03-01T10:00:00Z', ('2026-03-01T10:00:00Z', 'search_service', 'fx_search_P', 'SEARCH_INDEX_ENTRY',
                                     'NOT_FOUND', EMAIL)))}
        with tempfile.TemporaryDirectory() as tmp:
            plans, verifier = plans_for(world(tmp, lookup_rows=rows, files=gone))
            plan = plans[0]
            cited = [s for s in plan.steps if s.resource_id == 'fx_search_P' and s.phase == 'FINDING']
            self.assertEqual([s.evidence_id for s in cited], ['fx_file_L'])         # the 11:00 copy is the latest
            self.assertNotIn('STALE_CITATION', codes(verifier.verify(plan)))


# --- P1: evaluation correctness ---------------------------------------------------------------------

class CitationAccounting(unittest.TestCase):
    def test_only_exact_cited_matches_count(self):
        def s(op, state, ev, rid='r1'):
            phase = 'FINDING' if op.startswith('REPORT') else 'VERIFICATION'
            return Step(phase, 'svc', rid, op, state, ev)
        gold = OutputRow('c1', 'INCOMPLETE', (s('REPORT_REMAINING_RECORD', 'PRESENT', 'e1'),
                                              s('REPORT_EXPOSED_PII', 'PRESENT', 'e2', 'r2'),
                                              s('REPORT_REMAINING_RECORD', 'PRESENT', 'e3', 'r3'),
                                              s('REPORT_REMAINING_RECORD', 'PRESENT', 'e4', 'r4')))
        pred = OutputRow('c1', 'INCOMPLETE', (s('REPORT_REMAINING_RECORD', 'PRESENT', 'e1'),       # exact
                                              s('REPORT_REMAINING_RECORD', 'PRESENT', 'e2', 'r2'),  # wrong operation
                                              s('REPORT_REMAINING_RECORD', 'PRESENT', 'e9', 'r3')))  # wrong citation
        out = analyze_errors([pred], [gold], {'details': []}, {})
        self.assertEqual(out['citations'], {'expected_cited': 4, 'correct': 1, 'wrong': 1})


class GapReconstruction(unittest.TestCase):
    def test_a_phase4_run_without_a_provider_record_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = world(tmp), Path(tmp)
            run_audit(root, root / 'deletion_requests.csv', out / 'o.csv', out / 'run',
                      gateway=ReplayGateway(lambda p, b: jev_payload('DELETE_ACCOUNT')), use_generative=False)
            m = json.loads((out / 'run/run_manifest.json').read_text())
            m['inference_modes'].pop('providers')
            (out / 'run/run_manifest.json').write_text(json.dumps(m))
            with self.assertRaisesRegex(ValueError, 'provider'):
                run_corpus(load_context(root), out / 'run')

    def test_a_roar_run_is_rebuilt_from_its_own_recorded_responses(self):
        kinds = {'kind': {'type': 'choice', 'choice': 'SUPPORT_ATTACHMENT', 'probabilities': None}}

        def respond(path, body):
            if 'kind' not in body['questions']:
                return roar_answer(body)
            labels = list(body['questions']['kind']['criteria'])
            return {'answers': {'kind': {**kinds['kind'], 'probabilities': {
                lab: (0.95 if lab == 'SUPPORT_ATTACHMENT' else round(0.05 / (len(labels) - 1), 6)) for lab in labels}}},
                'usage': {'inputTokens': 10, 'outputTokens': 1}}
        with tempfile.TemporaryDirectory() as tmp:
            root, out, cache = world(tmp), Path(tmp), Path(tmp) / 'cache'
            gw = ReplayGateway(respond, replace(ROAR, cache_dir=str(cache)))
            run_audit(root, root / 'deletion_requests.csv', out / 'o.csv', out / 'run', gateway=gw, use_generative=False)
            shutil.rmtree(cache)                                   # no global cache: only the run's own artifacts
            corpus = run_corpus(load_context(root), out / 'run')
            self.assertEqual(corpus.reports[[r.source_id for r in corpus.reports].index('fx_file_PDF')].status,
                             'INTERPRETED')


# --- P2: provider reliability -------------------------------------------------------------------------

class ProviderReliability(unittest.TestCase):
    def test_catalog_failure_means_unpriced_not_a_crash(self):
        seen = []
        srv = serve(500, {'error': {'message': 'down'}}, seen)
        try:
            gw = at(Gateway(ROAR), srv)
            self.assertEqual(gw.load_pricing(), {})
        finally:
            srv.shutdown()
            srv.server_close()

    def test_malformed_cache_json_is_a_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw = ReplayGateway(lambda p, b: {'choices': [{'message': {'content': 'ok'}}]}, ProviderConfig(cache_dir=tmp))
            gw.chat('r', 'm', [{'role': 'user', 'content': 'x'}], 'v', lambda p: None)
            path = next(Path(tmp).rglob('*.json'))
            path.write_text('{not json')
            out = gw.chat('r', 'm', [{'role': 'user', 'content': 'x'}], 'v', lambda p: None)
            self.assertEqual(out['choices'][0]['message']['content'], 'ok')
            self.assertEqual(gw.usage[-1].mode, 'replay')

    def test_explicit_proxy_auth_never_sends_a_local_key(self):
        seen = []
        srv = serve(200, {'data': []}, seen)
        try:
            with unittest.mock.patch.dict('os.environ', {'ROAR_API_KEY': 'local-secret', 'TOKENBAZAAR_API_KEY': 'tb'}):
                at(Gateway(replace(ROAR, proxy_auth=True)), srv).list_models()
                at(Gateway(ROAR), srv).list_models()                                  # profile default: local key
                at(Gateway(ProviderConfig(cache_dir=None, proxy_auth=True)), srv).list_models()
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(seen, [None, 'Bearer local-secret', None])

    def test_reported_actual_cost_excludes_estimates(self):
        class Live(ReplayGateway):
            mode = 'live'

            def _post(self, path, body):
                return self.responder(path, body), None
        with tempfile.TemporaryDirectory() as tmp:
            root, out = world(tmp), Path(tmp)
            gw = Live(lambda p, b: {**jev_payload('DELETE_ACCOUNT'), 'usage': {'inputTokens': 1000, 'outputTokens': 0}},
                      replace(ROAR, max_cost_paise=500.0))
            gw.pricing = {'jev': {'inputPer1M': 1000.0, 'outputPer1M': 0}}
            with unittest.mock.patch('ghostaudit.providers.adapt_roar_answers', lambda p: p):
                summary = run_audit(root, root / 'deletion_requests.csv', out / 'o.csv', out / 'run', gateway=gw,
                                    use_generative=False)
        self.assertEqual(summary['model_spent_paise_header'], 0.0)
        self.assertGreater(summary['model_cost_paise']['estimated'], 0.0)
        self.assertGreater(summary['budget_charged_paise'], 0.0)


if __name__ == '__main__':
    unittest.main()


class ReplayNeverShares(unittest.TestCase):
    def test_a_replay_investigator_keeps_its_zero_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = world(tmp), Path(tmp)
            intent = ReplayGateway(lambda p, b: jev_payload('DELETE_ACCOUNT'), ProviderConfig(cache_dir=None))
            inv = Gateway(replace(ROAR, max_cost_paise=0.0))
            inv.mode = 'replay'
            from ghostaudit.investigator import InvestigatorConfig
            with unittest.mock.patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network')):
                run_audit(root, root / 'deletion_requests.csv', out / 'o.csv', out / 'run', gateway=intent,
                          use_generative=False, investigator=InvestigatorConfig('m'), investigator_gateway=inv)
            self.assertIs(inv._ledger, inv)
            self.assertEqual(inv.spent_paise, 0.0)
