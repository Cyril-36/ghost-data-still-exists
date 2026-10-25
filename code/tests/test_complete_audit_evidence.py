"""Recreated records and incomplete exposure scans cannot prove compliance."""
import unittest
import zlib
from unittest import mock
from datetime import timedelta
from types import SimpleNamespace

import helpers  # noqa: F401
from ghostaudit.corpus import Corpus
from ghostaudit.observations import ABSENT, PRESENT, STATE, Observation
from ghostaudit.parsers.logsrc import parse_log
from ghostaudit.parsers.sqlsrc import parse_sql, load_snapshot
from ghostaudit.parsers.pdfsrc import extract_pdf_text, PdfError, _stream
from ghostaudit.media import interpret_pdf
from test_media import make_pdf, entry, corpus_with
from ghostaudit.policy import Policy, PolicyError
from ghostaudit.contracts import Rule
from evaluation.fixtures import _sql
from ghostaudit.state import reconstruct
from test_phase3_regressions import T0, AUDIT


class RecreationExposure(unittest.TestCase):
    def test_future_system_category_does_not_classify_an_earlier_record(self):
        old = Observation('fx_old', T0, T0, 'L1', '', STATE, 'fx_system', 'fx_earlier', presence=PRESENT)
        later = AUDIT + timedelta(days=1)
        future = Observation('fx_future', later, later, 'L1', '', STATE, 'fx_system', 'fx_later',
                             category='USER_PROFILE', presence=PRESENT)
        state = reconstruct(Corpus(SimpleNamespace(), [old, future], [], []), 'fx_system', 'fx_earlier', AUDIT)
        self.assertEqual(state.categories, set())

    def test_old_clean_state_is_not_borrowed_across_recreation(self):
        def obs(days, presence, exposed):
            when = T0 + timedelta(days=days)
            return Observation('fx_evidence', when, when, f'L{days}', '', STATE,
                               'analytics_platform', 'fx_an', category='ANALYTICS_PROFILE',
                               presence=presence, exposed=exposed)
        corpus = Corpus(SimpleNamespace(), [obs(1, PRESENT, ()), obs(2, ABSENT, None),
                                           obs(3, PRESENT, None)], [], [])
        state = reconstruct(corpus, 'analytics_platform', 'fx_an', AUDIT)
        self.assertEqual(state.presence, PRESENT)
        self.assertIsNone(state.exposed)


class IncompletePiiScans(unittest.TestCase):
    def test_missing_or_invalid_identifier_counts_do_not_assert_anonymization(self):
        for field in ('', 'direct_identifiers=unknown', 'direct_identifiers=-1'):
            with self.subTest(field=field):
                raw = ('2026-01-03T08:00:00Z level=INFO service=analytics_platform trace_id=t '
                       f'event=analytics_profile_read resource_id=fx_an {field}')
                observations, _, _ = parse_log('fx_log', AUDIT, raw.encode())
                self.assertEqual(len(observations), 1)
                self.assertIsNone(observations[0].exposed)

    def test_visible_email_cannot_be_overridden_by_zero_count(self):
        raw = ('2026-01-03T08:00:00Z level=INFO service=analytics_platform trace_id=t '
               'event=analytics_profile_read resource_id=fx_an direct_identifiers=0 email=a@example.test')
        observations, _, _ = parse_log('fx_log', AUDIT, raw.encode())
        self.assertTrue(observations[0].exposed)


class SnapshotSafety(unittest.TestCase):
    def test_inconsistent_sql_row_cannot_support_exhaustive_absence(self):
        raw = _sql('2026-01-03T08:00:00Z', {'customer_data_primary': [
            ('fx_good', 'account_service', 'customer_profiles', {'resource_id': 'fx_good', 'email': 'good@example.test'}),
            ('fx_bad', 'account_service', 'customer_profiles', {'resource_id': 'fx_bad', 'email': 'bad@example.test'})]})
        raw = raw.replace(b'"resource_id": "fx_bad"', b'"resource_id": "fx_other"')
        observations, scopes, issues = parse_sql('fx_sql', AUDIT, raw)
        self.assertTrue(any(i.startswith('CONTRADICTORY_STATE') for i in issues))
        self.assertTrue(observations)
        self.assertFalse(scopes, 'a skipped inconsistent row invalidates completeness for its table')

    def test_failed_sql_load_closes_its_connection(self):
        import sqlite3
        connection = sqlite3.connect(':memory:')
        proxy = mock.Mock(wraps=connection)
        with mock.patch('ghostaudit.parsers.sqlsrc.sqlite3.connect', return_value=proxy):
            with self.assertRaises(sqlite3.Error):
                load_snapshot('CREATE TABLE t (a TEXT); INSERT INTO missing VALUES (1);')
        self.assertTrue(proxy.close.called)

    def test_malformed_pdf_has_a_domain_error(self):
        with self.assertRaises(PdfError):
            extract_pdf_text(b'%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n')


class ConflictingPolicy(unittest.TestCase):
    def test_duplicate_category_rules_cannot_select_the_last_policy(self):
        rules = [Rule('fx_a', 'USER_PROFILE', 'Always', 'DELETE'),
                 Rule('fx_b', 'USER_PROFILE', 'Only with additional approval', 'DELETE')]
        with self.assertRaises(PolicyError):
            Policy(rules).treatment('USER_PROFILE', retention_until=None, audit_at=AUDIT)


class MalformedMedia(unittest.TestCase):
    def test_binary_stream_checksum_bytes_are_not_stripped_as_whitespace(self):
        payload = zlib.compress(b'\t')
        self.assertEqual(payload[-1], 10)
        raw = b'<< /Length %d /Filter /FlateDecode >>\nstream\n' % len(payload) + payload + b'\nendstream'
        self.assertEqual(_stream(raw), b'\t')

    def test_bad_retention_date_does_not_crash_the_pipeline(self):
        raw = make_pdf([['Record audit_record_7', 'retain until 99 Aug 2031']])
        observations, report, _ = interpret_pdf(entry(), raw, corpus_with(), None)
        self.assertEqual(observations, [])
        self.assertEqual(report.status, 'UNSUPPORTED_MEDIA')

    def test_corrupt_compressed_stream_is_reported_unreadable(self):
        raw = make_pdf([['record']], compress=True)
        start, end = raw.index(b'stream\n') + 7, raw.index(b'endstream')
        raw = raw[:start] + b'not-zlib\n' + raw[end:]
        _, report, _ = interpret_pdf(entry(), raw, corpus_with(), None)
        self.assertEqual(report.status, 'UNSUPPORTED_MEDIA')

    def test_compressed_stream_expansion_is_bounded(self):
        stream = b'<< /Filter /FlateDecode >>\nstream\n' + zlib.compress(b'x' * (9 * 1024 * 1024)) + b'\nendstream'
        with self.assertRaises(PdfError):
            _stream(stream)
