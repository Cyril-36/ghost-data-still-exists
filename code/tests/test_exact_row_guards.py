"""Guards for the shortcuts measured and rejected in evaluation/EXACT_ROW_OPPORTUNITIES.md.

The public labels expect the subject's account record and anonymized analytics records even when
the only mention of them is a workflow claim that names no person. Linking them by timing (the
claim lands seconds before the request's reported_at) matched the label in 14/32 samples and was
wrong for two requests reported at the same moment. These tests keep that inference out.
"""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import helpers  # noqa: F401
from evaluation.fixtures import ALL_RULES
from ghostaudit.contracts import Activity, Identity, Request, Rule
from ghostaudit.corpus import Corpus
from ghostaudit.identity import IdentityIndex
from ghostaudit.intent import ACCOUNT, DELETE, Intent
from ghostaudit.observations import PRESENT, STATE, Observation, Scope, tokens_from
from ghostaudit.parsers.activity import activity_observations
from ghostaudit.planner import plan_case
from ghostaudit.policy import Policy

UTC = timezone.utc
REPORTED = datetime(2026, 3, 1, 12, tzinfo=UTC)


def claim(aid, kind, system, rid, seconds_before_report):
    return activity_observations(Activity(aid, REPORTED - timedelta(seconds=seconds_before_report), kind,
                                          'deletion_worker', system, rid, 'SUCCESS',
                                          f'Deletion worker reported the requested operation as completed. '
                                          f'Target resource: {rid}.'))


class TimingIsNotOwnership(unittest.TestCase):
    def setUp(self):
        self.index = IdentityIndex(SimpleNamespace(identities=[
            Identity('fx_u1', 'A', 'alice@example.test', '+12025550121', 'h1', REPORTED - timedelta(days=60)),
            Identity('fx_u2', 'B', 'bob@example.test', '+12025550122', 'h2', REPORTED - timedelta(days=60))],
            audit_log=[]))
        listed = REPORTED + timedelta(days=1)
        bobs_profile = Observation('fx_db', listed, listed, 'r1', '', STATE, 'account_service', 'fx_acct_bob',
                                   location='primary', category='USER_PROFILE', presence=PRESENT,
                                   tokens=tokens_from('bob@example.test'))
        anonymized = Observation('fx_an', listed, listed, 'r2', '', STATE, 'analytics_platform', 'fx_analytics_9',
                                 category='ANALYTICS_PROFILE', presence=PRESENT, exposed=())
        scope = Scope('fx_db', listed, listed, 'account_service', 'USER_PROFILE', 'primary',
                      frozenset({'fx_acct_bob'}), tokens_from('bob@example.test'), coverage='FULL_TABLE')
        self.corpus = Corpus(SimpleNamespace(), [
            bobs_profile, anonymized,
            claim('fx_act_1', 'DELETE_RECORD', 'account_service', 'fx_acct_9', 10),       # 10 s before the report
            claim('fx_act_2', 'ANONYMIZE_RECORD', 'analytics_platform', 'fx_analytics_9', 5)], [scope], [])
        self.request = Request('fx_case', 'alice@example.test', REPORTED - timedelta(days=2), 'COMPLETE', REPORTED,
                               REPORTED + timedelta(days=2), 'Please delete my account and its personal data.')

    def test_claims_next_to_the_report_do_not_link_the_subject(self):
        plan = plan_case(self.request, self.corpus, Policy([Rule(*r) for r in ALL_RULES]), self.index,
                         Intent(DELETE, 'test', scope=ACCOUNT))
        named = {o.resource_id for o in plan.obligations} | {s.resource_id for s in plan.steps}
        self.assertFalse(named & {'fx_acct_9', 'fx_analytics_9'})
        # The subject is absent from the full-table listing, but nothing citable was established.
        self.assertEqual((plan.status, plan.fallback), ('INCOMPLETE', True))
        self.assertTrue(plan.fallback_reason.startswith('NO_REPRESENTABLE_OBLIGATION'))


if __name__ == '__main__':
    unittest.main()
