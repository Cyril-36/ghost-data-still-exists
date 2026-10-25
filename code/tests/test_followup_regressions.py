"""Regressions for the review of commit 7b8c216 (follow-up patch). Every test fails on 7b8c216."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import helpers  # noqa: F401
from evaluation.fixtures import ALL_RULES, Fixture, _identity, _lookups, _manifest, _request, _rules, write_fixture
from ghostaudit.audit import run_audit
from ghostaudit.contracts import Identity, ManifestEntry, OutputRow, Request, Rule, Step
from ghostaudit.corpus import Corpus
from ghostaudit.identity import IdentityIndex
from ghostaudit.intent import ACCOUNT, DELETE, Intent
from ghostaudit.media import interpret_pdf
from ghostaudit.observations import ABSENT, PRESENT, STATE, Observation, reference_tokens
from ghostaudit.output import read_output
from ghostaudit.planner import plan_case
from ghostaudit.policy import Policy
from ghostaudit.providers import BudgetExceeded, ProviderConfig, ReplayGateway
from test_hardening_regressions import target_payload
from test_intent_models import jev_payload
from test_media import make_pdf

UTC = timezone.utc
T = datetime(2026, 3, 1, tzinfo=UTC)
EMAIL = 'person.t@example.test'
TEXTS = {
    'fx_case_T1': ('Please delete the attachment I sent to support last week.', 0.05),
    'fx_case_T2': ('Please delete the support attachment fx_attach_T2, nothing else.', 0.05),
    'fx_case_T3': ('Please delete every file I ever attached to a support ticket.', 0.95),
}


def two_attachments():
    rows = [('2026-01-03T11:00:00Z', 'support_platform', rid, 'SUPPORT_ATTACHMENT', 'FOUND', EMAIL)
            for rid in ('fx_attach_T1', 'fx_attach_T2')]
    rows.append(('2026-01-03T11:00:00Z', 'account_service', 'fx_account_T', 'USER_PROFILE', 'FOUND', EMAIL))

    def steps(*rids):
        out = [Step('FINDING', 'support_platform', r, 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_T') for r in rids]
        out += [Step('REMEDIATION', 'support_platform', r, 'DELETE_RECORD', 'ABSENT', '') for r in rids]
        out += [Step('VERIFICATION', 'support_platform', r, 'VERIFY_RECORD_ABSENT', 'ABSENT', '') for r in rids]
        return tuple(out)
    return Fixture(
        name='fixture_two_attachments', description='SYNTHETIC: one person, two attachments, one authorized',
        tables={'deletion_requests.csv': [_request(c, EMAIL, '2026-01-03T12:00:00Z', text=t) for c, (t, _) in TEXTS.items()],
                'user_identities.csv': [_identity('fx_user_T', 'Fixture Person T', EMAIL, '+12025550131')],
                'deletion_rules.csv': _rules(),
                'evidence_files.csv': [_manifest('fx_file_T', 'evidence/records/lookups.json', '2026-01-03T11:00:00Z')]},
        evidence={'evidence/records/lookups.json': _lookups('2026-01-03T11:00:00Z', *rows)},
        expected=[OutputRow('fx_case_T1', 'INCOMPLETE', ()),                       # which attachment: not established
                  OutputRow('fx_case_T2', 'INCOMPLETE', steps('fx_attach_T2')),    # named verbatim
                  OutputRow('fx_case_T3', 'INCOMPLETE', steps('fx_attach_T1', 'fx_attach_T2'))],
        fallback_ids=frozenset({'fx_case_T1'}))


class ResourceScopeRegression(unittest.TestCase):
    def test_one_of_two_attachments_never_authorizes_the_other(self):
        def respond(path, body):
            text, whole = next(v for v in TEXTS.values() if v[0] in body['state'])
            if 'target' in body['questions']:
                return target_payload('SUPPORT_ATTACHMENT', whole=whole)
            return jev_payload('SCOPED_DELETION')
        fixture = two_attachments()
        with tempfile.TemporaryDirectory() as tmp:
            base = write_fixture(fixture, Path(tmp))
            summary = run_audit(base / 'input', base / 'input/deletion_requests.csv', base / 'p.csv', base / 'run',
                                gateway=ReplayGateway(respond))
            self.assertEqual(read_output(base / 'p.csv'), read_output(base / 'expected_output.csv'))
        self.assertEqual(set(summary['fallback_ids']), {'fx_case_T1'})


class AtomicSettlementRegression(unittest.TestCase):
    def test_no_call_is_admitted_between_releasing_and_charging_a_reservation(self):
        class Paid(ReplayGateway):
            mode = 'live'

            def _post(self, path, body):
                return jev_payload('DELETE_ACCOUNT'), 25.0
        gw = Paid(None, ProviderConfig(cache_dir=None, max_cost_paise=40, cost_reserve_paise=25))
        admitted = []

        def validate_first(payload):
            # Runs after the first call's response arrived, before it is charged.
            try:
                gw._reserve(25)
                admitted.append(True)
            except BudgetExceeded:
                admitted.append(False)
            return None
        gw.decide('intent', 'm', 'first', {'q': {}}, 'v', validate_first)
        self.assertEqual(admitted, [False])
        self.assertEqual(gw.spent_paise, 25)


class PdfTwoEmailsRegression(unittest.TestCase):
    def test_two_peoples_emails_in_one_section_bind_no_identity(self):
        raw = make_pdf([['Record Support reference support_77', 'Detail ana.one@example.test',
                         'Detail bo.two@example.test', 'Detail +1 202 555 0177']])
        gw = ReplayGateway(lambda p, b: {'model': 'jev-test', 'answers': {'kind': {
            'type': 'choice', 'choice': 'SUPPORT_ATTACHMENT',
            'probabilities': {'SUPPORT_ATTACHMENT': 0.97, 'SECURITY_AUDIT_RECORD': 0.01, 'OTHER': 0.01,
                              'LEGAL_RETENTION_REGISTER': 0.005, 'CUSTOMER_STATEMENT_OR_EXPORT': 0.005}}}})
        known = Observation('fx_s', T, T, 'r', '', STATE, 'support_platform', 'support_9',
                            category='SUPPORT_MESSAGE', presence=PRESENT)
        entry = ManifestEntry('fx_doc', 'evidence/x.pdf', 'support_platform', T)
        [obs], report, _ = interpret_pdf(entry, raw, Corpus(SimpleNamespace(), [known], [], []), gw)
        self.assertEqual(obs.tokens, frozenset())
        self.assertTrue(any('several' in i for i in report.issues))


class NotFoundOwnershipLimitsRegression(unittest.TestCase):
    def setUp(self):
        self.index = IdentityIndex(SimpleNamespace(identities=[
            Identity('fx_u1', 'A', 'alice@example.test', '+12025550121', 'h1', T - timedelta(days=30))], audit_log=[]))
        self.policy = Policy([Rule(*r) for r in ALL_RULES])
        self.request = Request('fx_case', 'alice@example.test', T, 'COMPLETE', T, T + timedelta(days=1),
                               'Please delete my account.')
        self.intent = Intent(DELETE, 'test', scope=ACCOUNT)

    def lookup(self, rid):
        return Observation('fx_l', T, T, f'l {rid}', '', STATE, 'export_service', rid, category='USER_EXPORT',
                           presence=ABSENT, tokens=reference_tokens('alice@example.test'))

    def test_lookup_context_alone_does_not_justify_remediation(self):
        later_present = Observation('fx_r', T + timedelta(hours=1), T + timedelta(hours=1), 'r', '', STATE,
                                    'export_service', 'fx_export_1', category='USER_EXPORT', presence=PRESENT)
        corpus = Corpus(SimpleNamespace(), [self.lookup('fx_export_1'), later_present], [], [])
        plan = plan_case(self.request, corpus, self.policy, self.index, self.intent)
        self.assertNotIn('DELETE_RECORD', [s.operation for s in plan.steps])

    def test_lookup_context_does_not_propagate_ownership(self):
        copy = Observation('fx_c', T, T, 'c', '', STATE, 'search_service', 'fx_search_9',
                           category='SEARCH_INDEX_ENTRY', presence=PRESENT, refs=('fx_export_1',))
        corpus = Corpus(SimpleNamespace(), [self.lookup('fx_export_1'), copy], [], [])
        plan = plan_case(self.request, corpus, self.policy, self.index, self.intent)
        self.assertNotIn('fx_search_9', {o.resource_id for o in plan.obligations})

    def test_lookup_still_verifies_absence(self):
        corpus = Corpus(SimpleNamespace(), [self.lookup('fx_export_1')], [], [])
        plan = plan_case(self.request, corpus, self.policy, self.index, self.intent)
        [ob] = [o for o in plan.obligations if o.resource_id == 'fx_export_1']
        self.assertEqual((ob.result, ob.verify_op), ('SATISFIED', 'VERIFY_RECORD_ABSENT'))


if __name__ == '__main__':
    unittest.main()
