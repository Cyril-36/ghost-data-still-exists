"""Media interpretation tests: synthetic PDFs (built here) and recorded vision responses.

Recorded responses test validation and mapping only; they say nothing about real
vision accuracy (see evaluation/vision_ground_truth.py for the live measurement).
"""
import json
import unittest
import zlib
from datetime import datetime, timezone
from types import SimpleNamespace

import helpers  # noqa: F401
from ghostaudit.contracts import ManifestEntry
from ghostaudit.corpus import Corpus
from ghostaudit.media import interpret_pdf, interpret_screenshot
from ghostaudit.observations import LINK, PRESENT, STATE, Observation
from ghostaudit.parsers.pdfsrc import PdfError, extract_pdf_text, harvest
from ghostaudit.providers import ReplayGateway

T = datetime(2026, 3, 1, tzinfo=timezone.utc)
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 32


def make_pdf(pages, compress=False):
    """Minimal valid PDF: one Helvetica font, one content stream per page."""
    objs = {1: b'<< /Type /Catalog /Pages 2 0 R >>', 3: b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'}
    kids = []
    n = 4
    for lines in pages:
        content = b'BT /F1 12 Tf 14 TL 1 0 0 1 50 800 Tm ' + b''.join(
            b'(' + line.replace('(', '\\(').replace(')', '\\)').encode('latin-1') + b') Tj T* ' for line in lines) + b'ET'
        data, filt = (zlib.compress(content), b' /Filter /FlateDecode') if compress else (content, b'')
        objs[n] = b'<< /Length %d%s >>\nstream\n' % (len(data), filt) + data + b'\nendstream'
        objs[n + 1] = b'<< /Type /Page /Parent 2 0 R /Contents %d 0 R /Resources << /Font << /F1 3 0 R >> >> >>' % n
        kids.append(n + 1)
        n += 2
    objs[2] = b'<< /Type /Pages /Kids [' + b' '.join(b'%d 0 R' % k for k in kids) + b'] /Count %d >>' % len(kids)
    out = b'%PDF-1.4\n'
    for num in sorted(objs):
        out += b'%d 0 obj\n' % num + objs[num] + b'\nendobj\n'
    return out + b'%%EOF\n'


def entry(file_id='fx_doc', origin='security_service'):
    return ManifestEntry(file_id, 'evidence/x', origin, T)


def corpus_with(*observations):
    return Corpus(SimpleNamespace(), list(observations), [], [])


class PdfExtractionTests(unittest.TestCase):
    def test_pages_lines_and_literal_harvest(self):
        for compress in (False, True):
            with self.subTest(compress=compress):
                raw = make_pdf([['Record Event reference audit_record_7', 'subject reference user_77'],
                                ['retain until 25 Aug 2031 04 03 UTC', 'contact a.b@example.test (+1 202 555 0100)']],
                               compress)
                pages = extract_pdf_text(raw)
                self.assertEqual(len(pages), 2)
                self.assertEqual(pages[0][0], 'Record Event reference audit_record_7')
                facts = harvest(pages)
                self.assertIn('audit_record_7', [v for v, _ in facts['resource_ids']])
                self.assertEqual(facts['retain_until'][0][0], datetime(2031, 8, 25, 4, 3, tzinfo=timezone.utc))
                self.assertEqual(facts['emails'][0], ('a.b@example.test', 'page 2 line 2'))

    def test_textless_pdf_is_unreadable_not_empty(self):
        with self.assertRaises(PdfError):
            extract_pdf_text(make_pdf([[]]))

    def test_offline_pdf_yields_text_but_no_state(self):
        raw = make_pdf([['Event reference audit_record_7', 'retain until 25 Aug 2031']])
        observations, report, extraction = interpret_pdf(entry(), raw, corpus_with(), None)
        self.assertEqual((observations, report.status), ([], 'TEXT_EXTRACTED'))
        self.assertIn('audit_record_7', extraction['facts']['resource_ids'])

    def test_document_kind_drives_deterministic_mapping(self):
        raw = make_pdf([['Event reference audit_record_7', 'subject reference user_77',
                         'retain until 25 Aug 2031 04 03 UTC']])
        known = Observation('fx_s', T, T, 'r', '', STATE, 'security_service', 'audit_record_9',
                            category='SECURITY_AUDIT_LOG', presence=PRESENT)
        gw = ReplayGateway(lambda p, b: {'model': 'jev-test', 'answers': {'kind': {
            'type': 'choice', 'choice': 'SECURITY_AUDIT_RECORD',
            'probabilities': {'SECURITY_AUDIT_RECORD': 0.97, 'SUPPORT_ATTACHMENT': 0.01, 'OTHER': 0.01,
                              'LEGAL_RETENTION_REGISTER': 0.005, 'CUSTOMER_STATEMENT_OR_EXPORT': 0.005}}}})
        observations, report, _ = interpret_pdf(entry(), raw, corpus_with(known), gw)
        [obs] = observations
        self.assertEqual((obs.resource_id, obs.category, obs.system_id), ('audit_record_7', 'SECURITY_AUDIT_LOG',
                                                                          'security_service'))
        self.assertEqual(obs.retention_until, datetime(2031, 8, 25, 4, 3, tzinfo=timezone.utc))
        self.assertIn(('user', 'user_77'), obs.tokens)
        self.assertEqual(report.status, 'INTERPRETED')


def vision_reply(page_kind, entities, unreadable=()):
    content = json.dumps({'page_title': 't', 'page_kind': page_kind, 'entities': entities, 'unreadable': list(unreadable)})
    return lambda p, b: {'model': 'vision-test', 'choices': [{'message': {'content': content}}],
                         'usage': {'prompt_tokens': 10, 'completion_tokens': 10}}


class ScreenshotMappingTests(unittest.TestCase):
    def test_rows_become_state_with_identity_and_no_inferred_flags(self):
        gw = ReplayGateway(vision_reply('DATA_FLOWS', [
            {'region': 'table_row', 'resource_id': 'fx_flow_1', 'label': None,
             'fields': {'Selector': 'a@example.test', 'Status': 'Healthy', 'Mode': 'Continuous'}},
            {'region': 'table_row', 'resource_id': 'fx_flow_2', 'label': None,
             'fields': {'Selector': 'someone.else@example.test'}}]))
        observations, report, _ = interpret_screenshot(entry('fx_png', 'integration_admin'), PNG, corpus_with(), gw)
        by_id = {o.resource_id: o for o in observations}
        self.assertEqual(set(by_id), {'fx_flow_1', 'fx_flow_2'})
        self.assertEqual(by_id['fx_flow_1'].tokens, {('email', 'a@example.test')})   # rows stay separate
        self.assertNotIn(('email', 'a@example.test'), by_id['fx_flow_2'].tokens)
        self.assertIsNone(by_id['fx_flow_1'].enabled)                                  # "Healthy" is not a flag
        self.assertEqual((by_id['fx_flow_1'].kind, by_id['fx_flow_1'].presence), (STATE, PRESENT))

    def test_malformed_ids_are_rejected_and_reported(self):
        gw = ReplayGateway(vision_reply('DATA_FLOWS', [
            {'region': 'table_row', 'resource_id': 'data_flow1121', 'fields': {'Selector': 'a@example.test'}}],
            unreadable=['last digit of an id']))
        observations, report, extraction = interpret_screenshot(entry('fx_png'), PNG, corpus_with(), gw)
        self.assertEqual(observations, [])
        self.assertEqual(extraction['rejected_ids'], ['data_flow1121'])
        self.assertTrue(any('UNREADABLE_MEDIA' in i for i in report.issues))

    def test_user_key_resolves_only_through_an_exact_catalog_label(self):
        link = Observation('fx_act', T, T, 'row', 'LINK_RESOURCE_IDENTITY SUCCESS: linked fx_tomb_9 (user_9) to x',
                           LINK, 'identity_service', 'fx_tomb_9')
        gw = ReplayGateway(vision_reply('CUSTOMER_REFERENCE', [
            {'region': 'header', 'resource_id': 'user_9', 'fields': {'Status': 'Retained reference'}}]))
        [obs], _, _ = interpret_screenshot(entry('fx_png'), PNG, corpus_with(link), gw)
        self.assertEqual((obs.resource_id, obs.system_id, obs.category), ('fx_tomb_9', 'identity_service',
                                                                          'ACCOUNT_TOMBSTONE'))
        self.assertIsNone(obs.exposed)     # not showing PII on screen does not prove it was removed
        gw2 = ReplayGateway(vision_reply('CUSTOMER_REFERENCE', [
            {'region': 'header', 'resource_id': 'user_10', 'fields': {}}]))
        self.assertEqual(interpret_screenshot(entry('fx_png'), PNG, corpus_with(link), gw2)[0], [])

    def test_inconsistent_system_gives_identity_link_only(self):
        support = Observation('fx_s', T, T, 'r', '', STATE, 'support_platform', 'fx_sup_1',
                              category='SUPPORT_MESSAGE', presence=PRESENT)
        gw = ReplayGateway(vision_reply('ACCOUNT_PROFILE', [
            {'region': 'side_panel', 'resource_id': 'fx_sup_1', 'fields': {'Email': 'a@example.test'}}]))
        [obs], report, _ = interpret_screenshot(entry('fx_png'), PNG, corpus_with(support), gw)
        self.assertEqual((obs.kind, obs.category), (LINK, None))

    def test_offline_screenshot_is_reported_unsupported(self):
        observations, report, _ = interpret_screenshot(entry('fx_png'), PNG, corpus_with(), None)
        self.assertEqual((observations, report.status), ([], 'UNSUPPORTED_MEDIA'))


if __name__ == '__main__':
    unittest.main()
