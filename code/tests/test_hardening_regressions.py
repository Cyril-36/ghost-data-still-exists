"""Regressions for the independent audit of commit e0aaef4 (correctness-hardening pass).

Every test here fails on e0aaef4; decisions.md D044-D056 describe each fix.
"""
import http.server
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import helpers  # noqa: F401
from evaluation.fixtures import Fixture, _identity, _lookups, _manifest, _request, _rules, write_fixture
from ghostaudit.audit import run_audit
from ghostaudit.contracts import ManifestEntry, OutputRow, Step
from ghostaudit.corpus import Corpus
from ghostaudit.intent import DELETE, UNRESOLVED, classify
from ghostaudit.intent_models import decide_intents, jev_signals
from ghostaudit.media import interpret_pdf, interpret_screenshot
from ghostaudit.output import read_output
from ghostaudit.providers import BudgetExceeded, Gateway, ProviderConfig, ProviderError, ReplayGateway
from test_intent_models import chat_payload, jev_payload
from test_media import PNG, make_pdf, vision_reply

UTC = timezone.utc
T = datetime(2026, 3, 1, tzinfo=UTC)
LABELS = ('DELETE_ACCOUNT', 'VERIFY_PRIOR_DELETION', 'SCOPED_DELETION', 'NON_DELETION', 'AMBIGUOUS')
TARGETS = ('SEARCH_INDEX_ENTRY', 'USER_EXPORT', 'SUPPORT_ATTACHMENT', 'SUPPORT_MESSAGE', 'MARKETING_PROFILE',
           'ANALYTICS_PROFILE', 'SEVERAL_OR_UNCLEAR')


def target_payload(target, p=0.95, whole=0.05):
    probs = {t: (p if t == target else round((1 - p) / (len(TARGETS) - 1), 4)) for t in TARGETS}
    return {'model': 'typesafe/jev-1.13-test', 'answers': {
        'target': {'type': 'choice', 'choice': target, 'probabilities': probs},
        'whole_category': {'type': 'noul', 'noul': whole}}}


# --- P0-A: scoped deletion keeps its scope ---------------------------------------------------------

EMAIL = 'person.s@example.test'
SCOPED_TEXTS = {
    'fx_case_S1': ('Please delete the data export file you made for me.', 'USER_EXPORT', 0.02),
    'fx_case_S2': ('Remove that one search result about me, please.', 'SEARCH_INDEX_ENTRY', 0.02),
    'fx_case_S3': ('Delete the attachment I sent to support, but keep my account open.', 'SUPPORT_ATTACHMENT', 0.92),
}


def scoped_fixture():
    lookups = _lookups('2026-01-03T11:00:00Z', *[
        ('2026-01-03T11:00:00Z', system, rid, category, 'FOUND', EMAIL) for system, rid, category in (
            ('account_service', 'fx_account_S', 'USER_PROFILE'), ('export_service', 'fx_export_S', 'USER_EXPORT'),
            ('search_service', 'fx_search_S', 'SEARCH_INDEX_ENTRY'),
            ('support_platform', 'fx_attach_S', 'SUPPORT_ATTACHMENT'))])
    return Fixture(
        name='fixture_scoped', description='SYNTHETIC: item-scoped deletion requests next to a present account',
        tables={'deletion_requests.csv': [_request(c, EMAIL, '2026-01-03T12:00:00Z', text=t)
                                          for c, (t, _, _) in SCOPED_TEXTS.items()],
                'user_identities.csv': [_identity('fx_user_S', 'Fixture Person S', EMAIL, '+12025550111')],
                'deletion_rules.csv': _rules(),
                'evidence_files.csv': [_manifest('fx_file_S', 'evidence/records/lookups.json', '2026-01-03T11:00:00Z')]},
        evidence={'evidence/records/lookups.json': lookups},
        expected=[OutputRow(case_id, 'INCOMPLETE', (
            Step('FINDING', system, rid, 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_S'),
            Step('REMEDIATION', system, rid, 'DELETE_RECORD', 'ABSENT', ''),
            Step('VERIFICATION', system, rid, 'VERIFY_RECORD_ABSENT', 'ABSENT', ''))) for case_id, system, rid in (
            ('fx_case_S1', 'export_service', 'fx_export_S'), ('fx_case_S2', 'search_service', 'fx_search_S'),
            ('fx_case_S3', 'support_platform', 'fx_attach_S'))])


def scoped_gateway():
    def respond(path, body):
        text, target, preserve = next(v for v in SCOPED_TEXTS.values() if v[0] in body.get('state', ''))
        if 'target' in body['questions']:
            return target_payload(target)
        return jev_payload('SCOPED_DELETION', preserve=preserve)
    return ReplayGateway(respond)


class ScopedDeletionRegression(unittest.TestCase):
    def test_item_request_only_touches_the_requested_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = write_fixture(scoped_fixture(), Path(tmp))
            run_audit(base / 'input', base / 'input/deletion_requests.csv', base / 'p.csv', base / 'run',
                      gateway=scoped_gateway())
            self.assertEqual(read_output(base / 'p.csv'), read_output(base / 'expected_output.csv'))

    def test_item_request_without_a_grounded_target_stays_unresolved(self):
        text = 'Please delete that thing you still have.'
        gw = ReplayGateway(lambda p, b: target_payload('SEVERAL_OR_UNCLEAR') if 'target' in b['questions']
                           else jev_payload('SCOPED_DELETION'))
        out = decide_intents({'k': text}, gw, use_generative=False)['k']
        self.assertEqual(out.intent.decision, UNRESOLVED)

    def test_offline_item_request_does_not_authorize_the_account(self):
        intent = classify('Please delete the data export file you made for me.')
        self.assertNotEqual((intent.decision, intent.scope), (DELETE, 'ACCOUNT'))
        self.assertEqual(classify('Please delete my account and its personal data.').scope, 'ACCOUNT')


# --- P0-B: credentials never follow a redirect ------------------------------------------------------

def _server(handler):
    srv = http.server.HTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class RedirectRegression(unittest.TestCase):
    def test_cross_origin_redirect_does_not_receive_the_bearer_token(self):
        seen = []

        class Foreign(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get('Authorization'))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')
            do_POST = do_GET

            def log_message(self, *a):
                pass
        foreign = _server(Foreign)

        class Redirector(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header('Location', f'http://127.0.0.1:{foreign.server_port}/steal')
                self.end_headers()

            def log_message(self, *a):
                pass
        origin = _server(Redirector)
        try:
            gw = Gateway(ProviderConfig(cache_dir=None, max_retries=0))
            gw.config = replace(gw.config, base_url=f'http://127.0.0.1:{origin.server_port}/v1')
            with unittest.mock.patch.dict('os.environ', {'TOKENBAZAAR_API_KEY': 'test-secret'}):
                with self.assertRaises(ProviderError):
                    jev_signals(gw, 'Please delete my account.')
            self.assertEqual(seen, [])
        finally:
            for srv in (origin, foreign):
                srv.shutdown()
                srv.server_close()


# --- P0-C: PDF facts bind to their own record ---------------------------------------------------------

class PdfAssociationRegression(unittest.TestCase):
    def test_two_people_two_records_one_retention_date(self):
        raw = make_pdf([['Record Event reference audit_record_7', 'Detail subject reference user_77',
                         'Record Event reference audit_record_8', 'Detail subject reference user_88',
                         'retain until 25 Aug 2031 04 03 UTC']])
        gw = ReplayGateway(lambda p, b: {'model': 'jev-test', 'answers': {'kind': {
            'type': 'choice', 'choice': 'SECURITY_AUDIT_RECORD',
            'probabilities': {'SECURITY_AUDIT_RECORD': 0.97, 'SUPPORT_ATTACHMENT': 0.01, 'OTHER': 0.01,
                              'LEGAL_RETENTION_REGISTER': 0.005, 'CUSTOMER_STATEMENT_OR_EXPORT': 0.005}}}})
        entry = ManifestEntry('fx_doc', 'evidence/x', 'security_service', T)
        from ghostaudit.observations import PRESENT, STATE, Observation
        known = Observation('fx_s', T, T, 'r', '', STATE, 'security_service', 'audit_record_9',
                            category='SECURITY_AUDIT_LOG', presence=PRESENT)
        observations, _, _ = interpret_pdf(entry, raw, Corpus(SimpleNamespace(), [known], [], []), gw)
        by_id = {o.resource_id: o for o in observations}
        self.assertEqual(by_id['audit_record_7'].tokens, frozenset({('user', 'user_77')}))
        self.assertEqual(by_id['audit_record_8'].tokens, frozenset({('user', 'user_88')}))
        self.assertIsNone(by_id['audit_record_7'].retention_until)
        self.assertEqual(by_id['audit_record_8'].retention_until, datetime(2031, 8, 25, 4, 3, tzinfo=UTC))


# --- P0-D: a Jev authorization concern cannot be overridden by the fallback -----------------------------

class EscalationRegression(unittest.TestCase):
    TEXTS = {'es': 'Eliminen mi cuenta, pero mantengan activo mi perfil para mi hermana.',
             'ja': 'アカウントを削除してください。ただし、プロフィールは有効なままにしてください。',
             'ko': '계정을 삭제해 주세요. 다만 프로필은 활성 상태로 남겨 주세요.'}

    def test_conflict_detected_by_jev_stays_unresolved(self):
        for lang, text in self.TEXTS.items():
            for flags in ({'conflict': 0.9}, {'preserve': 0.9}, {'marketing': 0.85}):
                with self.subTest(lang=lang, flags=flags):
                    def respond(path, body, flags=flags):
                        if path == 'decisions':
                            return jev_payload('DELETE_ACCOUNT', **flags)
                        handles = [r['handle'] for r in json.loads(body['messages'][1]['content'])['requests']]
                        return chat_payload([{'handle': h, 'intent': 'DELETE_ACCOUNT', 'preserve_account': False,
                                              'marketing_only': False, 'conflicting_instructions': False,
                                              'spans': [text[:6]]} for h in handles])
                    out = decide_intents({'k': text}, ReplayGateway(respond))['k']
                    self.assertEqual(out.intent.decision, UNRESOLVED)


    def test_fallback_cannot_widen_past_a_keep_account_answer(self):
        text = 'Please delete my account and its personal data.'

        def respond(path, body):
            if path == 'decisions':
                return jev_payload('NON_DELETION', preserve=0.9)
            handles = [r['handle'] for r in json.loads(body['messages'][1]['content'])['requests']]
            return chat_payload([{'handle': h, 'intent': 'DELETE_ACCOUNT', 'preserve_account': False,
                                  'marketing_only': False, 'conflicting_instructions': False,
                                  'spans': ['delete my account']} for h in handles])
        out = decide_intents({'k': text}, ReplayGateway(respond))['k']
        self.assertEqual(out.intent.decision, UNRESOLVED)


class PartialScreenshotRegression(unittest.TestCase):
    def test_rejected_entities_make_the_report_partial(self):
        gw = ReplayGateway(vision_reply('DATA_FLOWS', [
            {'region': 'table_row', 'resource_id': 'data_flow1121', 'fields': {'Selector': 'a@example.test'}}]))
        _, report, _ = interpret_screenshot(ManifestEntry('fx_png', 'evidence/x.png', 'x', T), PNG,
                                            Corpus(SimpleNamespace(), [], [], []), gw)
        self.assertEqual(report.status, 'PARTIAL')


# --- P1: media enrichment, NOT_FOUND ownership, log traces ---------------------------------------------

class MediaProvenanceRegression(unittest.TestCase):
    def test_later_catalog_link_does_not_backdate_screenshot_enrichment(self):
        from ghostaudit.observations import LINK, Observation
        later = T + timedelta(days=10)
        link = Observation('fx_act', later, later, 'row', 'LINK_RESOURCE_IDENTITY SUCCESS: linked fx_tomb_9 (user_9) to x',
                           LINK, 'identity_service', 'fx_tomb_9')
        gw = ReplayGateway(vision_reply('CUSTOMER_REFERENCE', [
            {'region': 'header', 'resource_id': 'user_9', 'fields': {'Status': 'Retained reference'}}]))
        [obs], _, _ = interpret_screenshot(ManifestEntry('fx_png', 'evidence/x.png', 'x', T), PNG,
                                           Corpus(SimpleNamespace(), [link], [], []), gw)
        self.assertGreaterEqual(obs.available_at, later)


class NotFoundOwnershipRegression(unittest.TestCase):
    def test_not_found_lookup_does_not_claim_another_persons_record(self):
        from ghostaudit.contracts import Identity
        from ghostaudit.identity import IdentityIndex, resolve_subject
        from ghostaudit.observations import ABSENT, PRESENT, STATE, Observation, reference_tokens, tokens_from
        from ghostaudit.planner import discover
        index = IdentityIndex(SimpleNamespace(identities=[
            Identity('fx_u1', 'A', 'alice@example.test', '+12025550121', 'h1', T - timedelta(days=30)),
            Identity('fx_u2', 'B', 'bob@example.test', '+12025550122', 'h2', T - timedelta(days=30))], audit_log=[]))
        lookup = Observation('fx_l', T, T, 'line 1', '', STATE, 'account_service', 'fx_acct_bob',
                             category='USER_PROFILE', presence=ABSENT, tokens=reference_tokens('alice@example.test'))
        bobs_row = Observation('fx_s', T - timedelta(days=5), T - timedelta(days=5), 'row', '', STATE,
                               'account_service', 'fx_acct_bob', category='USER_PROFILE', presence=PRESENT,
                               tokens=tokens_from('bob@example.test'))
        corpus = Corpus(SimpleNamespace(), [lookup, bobs_row], [], [])
        subject = resolve_subject('alice@example.test', T + timedelta(days=1), index, corpus)
        linked, conflicts = discover(subject, corpus, index, T + timedelta(days=1))
        self.assertTrue(conflicts.get(('account_service', 'fx_acct_bob')))


class ReusedIdTraversalRegression(unittest.TestCase):
    def test_an_id_reused_by_two_systems_is_not_traversed(self):
        from ghostaudit.contracts import Identity
        from ghostaudit.identity import IdentityIndex, resolve_subject
        from ghostaudit.observations import PRESENT, STATE, Observation, tokens_from
        from ghostaudit.planner import discover
        index = IdentityIndex(SimpleNamespace(identities=[
            Identity('fx_u1', 'A', 'alice@example.test', '+12025550121', 'h1', T - timedelta(days=30))], audit_log=[]))
        mine = Observation('fx_a', T, T, 'r1', '', STATE, 'export_service', 'fx_shared_1', category='USER_EXPORT',
                           presence=PRESENT, tokens=tokens_from('alice@example.test'))
        theirs = Observation('fx_b', T, T, 'r2', '', STATE, 'search_service', 'fx_shared_1',
                             category='SEARCH_INDEX_ENTRY', presence=PRESENT, refs=('fx_copy_9',))
        copy = Observation('fx_c', T, T, 'r3', '', STATE, 'analytics_platform', 'fx_copy_9',
                           category='ANALYTICS_PROFILE', presence=PRESENT)
        corpus = Corpus(SimpleNamespace(), [mine, theirs, copy], [], [])
        subject = resolve_subject('alice@example.test', T + timedelta(days=1), index, corpus)
        linked, _ = discover(subject, corpus, index, T + timedelta(days=1))
        self.assertNotIn(('analytics_platform', 'fx_copy_9'), linked)


class LogTraceRegression(unittest.TestCase):
    def test_identity_logged_after_a_lookup_does_not_attach_to_it(self):
        from ghostaudit.parsers.logsrc import parse_log
        raw = ('2026-03-01T10:00:00Z [INFO] [account_service] [tr-1] profile_lookup_completed: resource_id=fx_a1 rows=1\n'
               '2026-03-01T11:00:00Z [INFO] [account_service] [tr-1] profile_lookup_started: lookup_key=late@example.test\n')
        observations, _, _ = parse_log('fx_log', T + timedelta(days=1), raw.encode())
        [obs] = observations
        self.assertNotIn(('email', 'late@example.test'), obs.tokens)


class SqlTemporalConflictRegression(unittest.TestCase):
    def test_conflicting_snapshot_times_are_quarantined(self):
        from ghostaudit.parsers import parse_source
        sql = """-- Captured at: 2026-01-10T00:00:00Z
BEGIN TRANSACTION;
CREATE TABLE snapshot_scope (origin_system TEXT, captured_at TEXT);
INSERT INTO snapshot_scope VALUES('account_service','2026-02-20T00:00:00Z');
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT, collection_name TEXT,
  record_json TEXT CHECK(json_valid(record_json)), updated_at TEXT);
INSERT INTO customer_data_primary VALUES('a1','account_service','customer_profiles','{"resource_id":"a1","email":"x@example.test"}','x');
COMMIT;
"""
        obs, _, report = parse_source(ManifestEntry('fx_db', 'evidence/x.sql', 'x', T), sql.encode())
        self.assertEqual(obs, [])
        self.assertEqual(report.status, 'PARTIAL')


class SubjectLevelAbsenceRegression(unittest.TestCase):
    def test_listing_row_without_any_identifier_blocks_subject_level_absence(self):
        from ghostaudit.observations import PRESENT, STATE, Observation, Scope, tokens_from
        from ghostaudit.planner import profile_obligation
        listed = T - timedelta(days=1)
        rows = [Observation('fx_db', listed, listed, 'r1', '', STATE, 'account_service', 'fx_acct_other',
                            location='primary', category='USER_PROFILE', presence=PRESENT,
                            tokens=tokens_from('bob@example.test')),
                Observation('fx_db', listed, listed, 'r2', '', STATE, 'account_service', 'fx_acct_blank',
                            location='primary', category='USER_PROFILE', presence=PRESENT)]
        scope = Scope('fx_db', listed, listed, 'account_service', 'USER_PROFILE', 'primary',
                      frozenset({'fx_acct_other', 'fx_acct_blank'}), tokens_from('bob@example.test'),
                      coverage='FULL_TABLE')
        corpus = Corpus(SimpleNamespace(), rows, [scope], [])
        subject = SimpleNamespace(tokens=tokens_from('alice@example.test'))
        self.assertEqual(profile_obligation(subject, corpus, T).result, 'UNRESOLVED')
        clean = Corpus(SimpleNamespace(), rows[:1], [replace(scope, members=frozenset({'fx_acct_other'}))], [])
        self.assertEqual(profile_obligation(subject, clean, T).result, 'SATISFIED')


# --- P1: budget, cache, malformed responses --------------------------------------------------------------

class ConcurrentBudgetRegression(unittest.TestCase):
    def test_in_flight_calls_reserve_budget(self):
        class Slow(ReplayGateway):
            mode = 'live'

            def _post(self, path, body):
                time.sleep(0.2)
                return jev_payload('DELETE_ACCOUNT'), 20.0
        gw = Slow(None, ProviderConfig(cache_dir=None, max_cost_paise=50, cost_reserve_paise=25))
        results = []

        def call(i):
            try:
                jev_signals(gw, f'request {i}')
                results.append('ok')
            except BudgetExceeded:
                results.append('refused')
        threads = [threading.Thread(target=call, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertLessEqual(gw.spent_paise, 50)
        self.assertEqual(results.count('ok'), 2)


class CacheValidationRegression(unittest.TestCase):
    def test_corrupt_cache_entry_is_revalidated_and_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw = ReplayGateway(lambda p, b: jev_payload('NON_DELETION'), ProviderConfig(cache_dir=tmp))
            jev_signals(gw, 'Stop the newsletter only.')
            [entry] = Path(tmp).rglob('*.json')
            cached = json.loads(entry.read_text())
            cached['response']['answers']['intent']['choice'] = 'ERASE_EVERYTHING'
            entry.write_text(json.dumps(cached))
            gw2 = ReplayGateway(lambda p, b: jev_payload('NON_DELETION'), ProviderConfig(cache_dir=tmp))
            self.assertEqual(jev_signals(gw2, 'Stop the newsletter only.').label, 'NON_DELETION')


class MalformedResponseRegression(unittest.TestCase):
    def test_malformed_jev_answers_raise_provider_errors_not_crashes(self):
        good = jev_payload('DELETE_ACCOUNT')
        bad_payloads = [
            {**good, 'answers': {**good['answers'], 'intent': 'DELETE_ACCOUNT'}},
            {**good, 'answers': {**good['answers'], 'preserve_account': None}},
            {**good, 'answers': {**good['answers'], 'marketing_only': {'type': 'noul', 'noul': True}}},
        ]
        for payload in bad_payloads:
            with self.subTest(str(payload['answers'])[:80]), self.assertRaises(ProviderError):
                jev_signals(ReplayGateway(lambda p, b, payload=payload: payload), 'x')


import unittest.mock  # noqa: E402

if __name__ == '__main__':
    unittest.main()
