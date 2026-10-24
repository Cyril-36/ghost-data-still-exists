"""Regressions for the six correctness defects found in the Phase 2 review (commit f14f284).

Every test here uses only APIs that existed at f14f284 and FAILS on that commit;
`decisions.md` D026-D031 describe each fix.
"""
import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import helpers  # noqa: F401
from ghostaudit.contracts import Identity, IdentityChange, Rule
from ghostaudit.corpus import Corpus
from ghostaudit.identity import IdentityIndex, resolve_subject
from ghostaudit.observations import PRESENT, STATE, Observation
from ghostaudit.parsers.jsonsrc import parse_json
from ghostaudit.parsers.sqlsrc import parse_sql
from ghostaudit.planner import obligation
from ghostaudit.policy import Policy, PolicyError
from ghostaudit.state import reconstruct

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)
AUDIT = datetime(2026, 2, 1, tzinfo=UTC)
RULES = [Rule('rule_07', 'ANALYTICS_PROFILE', 'Record is still attributable to a user', 'ANONYMIZE'),
         Rule('rule_12', 'SECURITY_AUDIT_LOG', 'Record is within the supplied retention period', 'RETAIN'),
         Rule('rule_01', 'USER_PROFILE', 'Always', 'DELETE')]


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


class IdentityTimingRegression(unittest.TestCase):
    def test_earlier_alias_known_only_from_a_post_cutoff_audit_entry_is_not_eligible(self):
        later = AUDIT + timedelta(days=4)
        index = IdentityIndex(SimpleNamespace(
            identities=[Identity('u1', 'N', 'current@example.test', '+1 202 555 0100', sha('current@example.test'), later)],
            audit_log=[IdentityChange('a1', 'u1', later, 'email', 'alias@example.test', 'current@example.test')]))
        subject = resolve_subject('alias@example.test', AUDIT, index)
        self.assertEqual(subject.user_ids, [])
        self.assertNotIn(('email', 'current@example.test'), subject.tokens)


class ReplicaExposureRegression(unittest.TestCase):
    def test_clean_primary_does_not_hide_an_earlier_exposing_replica(self):
        def obs(when, location, exposed):
            return Observation('fx_src', when, when, f'{location}', '', STATE, 'analytics_platform', 'fx_an',
                               location=location, category='ANALYTICS_PROFILE', presence=PRESENT, exposed=exposed)
        corpus = Corpus(SimpleNamespace(), [obs(T0 + timedelta(days=1), 'failover_replica', ('email',)),
                                            obs(T0 + timedelta(days=5), 'primary', ())], [], [])
        state = reconstruct(corpus, 'analytics_platform', 'fx_an', AUDIT, frozenset())
        ob = obligation(state, Policy(RULES), AUDIT)
        self.assertEqual(ob.result, 'VIOLATED')
        self.assertEqual(ob.proof.location, 'failover_replica')


class SearchOwnershipRegression(unittest.TestCase):
    def test_query_identity_is_not_stamped_on_unrelated_hits(self):
        raw = json.dumps({'exported_at': 'x', 'region': 'r', 'source': 's', 'service_exports': {'search_service': {
            'index_queries': [{'observed_at': '2026-01-10T00:00:00Z', 'query': 'me@example.test', 'resource_id': 's1',
                               'hits': {'total': 2, 'documents': [
                                   {'resource_id': 's1', 'email': 'me@example.test'},
                                   {'resource_id': 's2', 'email': 'someone.else@example.test'}]}}]}}}).encode()
        observations, _, _ = parse_json('fx', T0, raw)
        other = next(o for o in observations if o.resource_id == 's2')
        self.assertNotIn(('email', 'me@example.test'), other.tokens)


class SqlScopeRegression(unittest.TestCase):
    def test_no_empty_scope_for_a_collection_the_table_never_held(self):
        sql = """-- Captured at: 2026-01-10T00:00:00Z
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT, collection_name TEXT,
  record_json TEXT CHECK(json_valid(record_json)), updated_at TEXT);
CREATE TABLE customer_data_read_replica (resource_id TEXT PRIMARY KEY, origin_system TEXT, collection_name TEXT,
  record_json TEXT CHECK(json_valid(record_json)), updated_at TEXT);
INSERT INTO customer_data_read_replica VALUES('a1','account_service','customer_profiles','{"resource_id":"a1"}','x');
"""
        _, scopes, _ = parse_sql('fx_db', T0, sql.encode())
        self.assertEqual([(s.location, s.category) for s in scopes], [('read_replica', 'USER_PROFILE')])


class ScopeCoverageRegression(unittest.TestCase):
    def test_subject_named_elsewhere_in_a_file_does_not_make_a_listing_cover_them(self):
        raw = json.dumps({'exported_at': 'x', 'snapshots': [
            {'captured_at': '2026-01-10T00:00:00Z', 'data_store': 'security_service',
             'collection': 'security_audit_events', 'records': [{'resource_id': 'e1', 'subject_reference': 'u9'}]},
            {'captured_at': '2026-01-12T00:00:00Z', 'data_store': 'account_service',
             'collection': 'customer_profiles', 'records': []}]}).encode()
        observations, scopes, _ = parse_json('fx_bundle', T0 + timedelta(days=12), raw)
        earlier = Observation('fx_old', T0, T0, 'r', '', STATE, 'account_service', 'acct', category='USER_PROFILE',
                              presence=PRESENT, tokens=frozenset({('user', 'u9')}))
        corpus = Corpus(SimpleNamespace(), observations + [earlier], scopes, [])
        state = reconstruct(corpus, 'account_service', 'acct', AUDIT, frozenset({('user', 'u9')}))
        self.assertEqual(state.presence, PRESENT)


class RetentionRegression(unittest.TestCase):
    def test_missing_retention_period_is_not_treated_as_active_retention(self):
        with self.assertRaises(PolicyError):
            Policy(RULES).treatment('SECURITY_AUDIT_LOG', retention_until=None, audit_at=AUDIT)


if __name__ == '__main__':
    unittest.main()
