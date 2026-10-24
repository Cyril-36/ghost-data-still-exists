import json
import sqlite3
import unittest
from datetime import datetime, timezone

import helpers  # noqa: F401
from ghostaudit.contracts import Activity, ManifestEntry
from ghostaudit.observations import ABSENT, CLAIM, LINK, PRESENT, STATE
from ghostaudit.parsers import parse_source, sniff
from ghostaudit.parsers.activity import activity_observations
from ghostaudit.parsers.jsonsrc import parse_json
from ghostaudit.parsers.logsrc import parse_line, parse_log
from ghostaudit.parsers.sqlsrc import load_snapshot, parse_sql
from ghostaudit.parsers.textsrc import parse_text

T = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)


def by_resource(observations):
    return {o.resource_id: o for o in observations}


class JsonFamilyTests(unittest.TestCase):
    def test_family_a_explicit_lookups(self):
        raw = json.dumps({'exported_at': '2026-03-01T10:00:00Z', 'origin': 'x', 'record_count': 2, 'records': [
            {'recorded_at': '2026-03-01T09:00:00Z', 'system': 'search_service', 'resource_id': 'fx_s',
             'record_type': 'SEARCH_INDEX_ENTRY', 'result': 'FOUND', 'subject_reference': 'A@Example.test'},
            {'recorded_at': '2026-03-01T09:00:00Z', 'system': 'integration_service', 'resource_id': 'fx_f',
             'record_type': 'DATA_FLOW', 'result': 'ACTIVE', 'subject_reference': 'fx_ref_1'}]}).encode()
        obs, scopes, issues = parse_json('fx_file', T, raw)
        o = by_resource(obs)
        self.assertEqual((o['fx_s'].presence, o['fx_s'].category), (PRESENT, 'SEARCH_INDEX_ENTRY'))
        self.assertIn(('email', 'a@example.test'), o['fx_s'].tokens)       # casefolded matching key
        self.assertIs(o['fx_f'].enabled, True)
        self.assertIn(('user', 'fx_ref_1'), o['fx_f'].tokens)               # reference field, any format
        self.assertEqual((scopes, issues), ([], []))

    def test_family_a_record_count_mismatch_is_partial(self):
        raw = json.dumps({'records': [], 'record_count': 3}).encode()
        _, _, issues = parse_json('fx_file', T, raw)
        self.assertTrue(any(i.startswith('PARTIAL_SCOPE') for i in issues))

    def test_family_c_listing_gives_rows_and_scope(self):
        raw = json.dumps({'exported_at': 'x', 'snapshots': [
            {'captured_at': '2026-03-01T08:00:00Z', 'data_store': 'identity_service',
             'collection': 'deleted_customer_references', 'records': [
                 {'resource_id': 'fx_t', 'subject_reference': 'fx_u', 'name': None, 'email': None,
                  'phone': '+1 202 555 0100', 'email_hash': None, 'account_status': 'DELETED'}]},
            {'captured_at': '2026-03-01T08:00:00Z', 'data_store': 'account_service',
             'collection': 'customer_profiles', 'records': []}]}).encode()
        obs, scopes, _ = parse_json('fx_file', T, raw)
        self.assertEqual(obs[0].category, 'ACCOUNT_TOMBSTONE')
        self.assertEqual(obs[0].exposed, ('phone',))                        # stable reference is permitted
        self.assertEqual({(s.category, len(s.members)) for s in scopes},
                         {('ACCOUNT_TOMBSTONE', 1), ('USER_PROFILE', 0)})

    def test_family_d_readbacks(self):
        raw = json.dumps({'exported_at': 'x', 'region': 'r', 'source': 's', 'service_exports': {
            'account_service': {'profile_lookups': [
                {'observed_at': '2026-03-01T08:00:00Z', 'resource_id': 'fx_a', 'status_code': 404, 'profile': None}]},
            'analytics_platform': {'profiles': [
                {'observed_at': '2026-03-01T08:00:00Z', 'resource_id': 'fx_an', 'customer_reference': 'fx_u',
                 'email': None, 'phone': None, 'email_hash': None, 'audience_cohort': 'c'}]},
            'search_service': {'index_queries': [
                {'observed_at': '2026-03-01T08:00:00Z', 'query': 'q@example.test', 'resource_id': 'fx_s',
                 'hits': {'total': 2, 'documents': [{'resource_id': 'fx_s'}]}}]}}}).encode()
        obs, _, issues = parse_json('fx_file', T, raw)
        o = by_resource(obs)
        self.assertEqual((o['fx_a'].presence, o['fx_a'].proof), (ABSENT, 'EXACT_LOOKUP'))
        self.assertEqual(o['fx_an'].exposed, ('customer_reference',))      # reference keeps it attributable
        self.assertNotIn('fx_s', o)                                          # truncated hit list: no facts
        self.assertTrue(any(i.startswith('PARTIAL_SCOPE') for i in issues))

    def test_unrecognized_structure_is_reported(self):
        _, _, issues = parse_json('fx_file', T, b'{"something": 1}')
        self.assertTrue(issues[0].startswith('UNRECOGNIZED_FORMAT'))


class SqlTests(unittest.TestCase):
    SQL = """-- Captured at: 2026-03-01T08:00:00Z
BEGIN TRANSACTION;
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL,
  collection_name TEXT NOT NULL, record_json TEXT NOT NULL CHECK(json_valid(record_json)), updated_at TEXT NOT NULL);
CREATE TABLE customer_data_read_replica (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL,
  collection_name TEXT NOT NULL, record_json TEXT NOT NULL CHECK(json_valid(record_json)), updated_at TEXT NOT NULL);
INSERT INTO customer_data_read_replica VALUES('fx_a','account_service','customer_profiles',
  '{"resource_id":"fx_a","customer_reference":"fx_u","email":"a@example.test"}','2026-01-01T00:00:00Z');
COMMIT;
"""

    def test_primary_and_replica_are_separate_locations(self):
        obs, scopes, issues = parse_sql('fx_db', T, self.SQL.encode())
        self.assertEqual(issues, [])
        self.assertEqual([(o.resource_id, o.location) for o in obs], [('fx_a', 'read_replica')])
        self.assertEqual(obs[0].observed_at, datetime(2026, 3, 1, 8, tzinfo=timezone.utc))  # snapshot time, not updated_at
        # Only the table that holds the collection is a listing for it (no empty primary scope).
        self.assertEqual({(s.location, len(s.members), s.coverage) for s in scopes},
                         {('read_replica', 1, 'FULL_TABLE')})

    def test_restricted_connection(self):
        for hostile in ["ATTACH DATABASE '/tmp/x.db' AS x;", 'CREATE TABLE t(a); DROP TABLE t;',
                        'PRAGMA writable_schema=ON;', 'CREATE TABLE t(a); DELETE FROM t;']:
            with self.subTest(hostile), self.assertRaises(sqlite3.DatabaseError):
                load_snapshot(hostile)
        con = load_snapshot('CREATE TABLE t(a); INSERT INTO t VALUES (1);')
        with self.assertRaises(sqlite3.DatabaseError):
            con.execute('INSERT INTO t VALUES (2)')                          # read-only after load
        self.assertEqual(con.execute('SELECT a FROM t').fetchall(), [(1,)])

    def test_failed_snapshot_yields_no_observations(self):
        entry = ManifestEntry('fx_db', 'x.sql', 'o', T)
        obs, scopes, report = parse_source(entry, b'-- x\nBEGIN TRANSACTION;\nCREATE TABLE t(a);\nINSERT INTO nope VALUES(1);\n')
        self.assertEqual((obs, scopes, report.status), ([], [], 'FAILED'))


class LogTests(unittest.TestCase):
    WRAPPERS = [
        '2026-03-01T08:00:00Z level=INFO service=search_service trace_id=tr-1 resource_id=fx_s event=index_query_completed result=hit',
        '2026-03-01T08:00:00Z {"severity":"INFO","service":"search_service","trace_id":"tr-1","resource_id":"fx_s","event":"index_query_completed","result":"hit"}',
        '2026-03-01T08:00:00Z [INFO] [search_service] [tr-1] index_query_completed: resource_id="fx_s" result="hit"',
        '2026-03-01T08:00:00Z | INFO | search_service | tr-1 | index_query_completed | resource_id="fx_s" result="hit"',
        '2026-03-01T08:00:00Z <14>1 node-1 search_service tr-1 - - level=INFO resource_id="fx_s" event="index_query_completed" result="hit"',
        '2026-03-01T08:00:00Z requestId=tr-1 level=INFO function=search_service message="resource_id=\\"fx_s\\" event=\\"index_query_completed\\" result=\\"hit\\""',
    ]

    def test_equivalent_wrappers_normalize_identically(self):
        for line in self.WRAPPERS:
            with self.subTest(line[21:40]):
                _, d = parse_line(line)
                self.assertEqual((d['service'], d['trace'], d['event'], d['resource_id'], d['result']),
                                 ('search_service', 'tr-1', 'index_query_completed', 'fx_s', 'hit'))

    def test_trace_identity_links_only_within_the_same_trace(self):
        log = '\n'.join([
            '2026-03-01T08:00:00Z level=DEBUG service=search_service trace_id=tr-1 event=index_query_started match_key="a@example.test"',
            '2026-03-01T08:00:01Z level=DEBUG service=search_service trace_id=tr-2 event=index_query_started match_key="b@example.test"',
            '2026-03-01T08:00:02Z level=WARN service=search_service trace_id=tr-1 event=dependency_retry attempt=1',
            '2026-03-01T08:00:03Z level=INFO service=search_service trace_id=tr-2 resource_id=fx_b event=index_query_completed result=hit',
            '2026-03-01T08:00:04Z level=INFO service=search_service trace_id=tr-1 resource_id=fx_a event=index_query_completed result=hit',
        ]).encode()
        obs, _, issues = parse_log('fx_log', T, log)
        o = by_resource(obs)
        self.assertEqual(o['fx_a'].tokens, {('email', 'a@example.test')})
        self.assertEqual(o['fx_b'].tokens, {('email', 'b@example.test')})
        self.assertEqual(o['fx_a'].locator, 'line 5')
        self.assertEqual(issues, [])

    def test_not_found_lookup_does_not_link_its_query_key(self):
        log = ('2026-03-01T08:00:00Z level=DEBUG service=account_service trace_id=t event=profile_lookup_started '
               'resource_id=fx_a lookup_key=a@example.test\n'
               '2026-03-01T08:00:01Z level=INFO service=account_service trace_id=t event=profile_lookup_completed '
               'resource_id=fx_a rows=0 http_status=404\n').encode()
        obs, _, _ = parse_log('fx_log', T, log)
        self.assertEqual((obs[0].presence, obs[0].tokens), (ABSENT, frozenset()))

    def test_noise_and_marker_checks_are_not_state(self):
        log = ('2026-03-01T08:00:00Z level=WARN service=support_replica trace_id=t event=deletion_marker_check '
               'resource_id=fx_s marker_found=false\n').encode()
        obs, _, _ = parse_log('fx_log', T, log)
        self.assertEqual(obs, [])


class TextAndActivityTests(unittest.TestCase):
    def test_handover_rows(self):
        text = ('TITLE\n- 2026-03-01T08:00:00Z | support_platform | fx_s | SUPPORT_MESSAGE | IDENTIFIABLE | a@example.test\n'
                '- 2026-03-01T08:00:00Z | analytics_platform | fx_an | ANALYTICS_PROFILE | ANONYMIZED | no direct identifier\n')
        obs, _, issues = parse_text('fx_txt', T, text.encode())
        o = by_resource(obs)
        self.assertEqual(o['fx_s'].exposed, ('identifiers',))
        self.assertEqual((o['fx_an'].exposed, o['fx_an'].tokens), ((), frozenset()))
        self.assertEqual(issues, [])

    def test_activity_claims_never_set_state(self):
        def act(kind, details, result='SUCCESS'):
            return activity_observations(Activity('fx_act', T, kind, 'w', 'integration_service', 'fx_flow', result, details))
        claim = act('DELETE_RECORD', 'Deletion worker reported the requested operation as completed.')
        self.assertEqual((claim.kind, claim.presence), (CLAIM, None))
        check = act('CHECK_DATA_FLOW', 'Connector status check confirmed that data flow fx_flow remains enabled.')
        self.assertEqual((check.kind, check.enabled), (STATE, True))
        failed = act('CHECK_DATA_FLOW', 'Connector status check confirmed that data flow fx_flow remains enabled.', 'FAILED')
        self.assertEqual(failed.kind, CLAIM)
        link = act('LINK_RESOURCE_IDENTITY', 'Data catalog linked fx_flow (fx_flow) to a@example.test and ref-9.')
        self.assertEqual((link.kind, link.tokens), (LINK, {('email', 'a@example.test'), ('user', 'ref-9')}))

    def test_media_is_an_explicit_gap(self):
        entry = ManifestEntry('fx_png', 'x.png', 'o', T)
        obs, _, report = parse_source(entry, b'\x89PNG\r\n\x1a\n....')
        self.assertEqual((obs, report.status), ([], 'UNSUPPORTED_MEDIA'))
        self.assertEqual(sniff(b'%PDF-1.7'), 'pdf')


if __name__ == '__main__':
    unittest.main()
