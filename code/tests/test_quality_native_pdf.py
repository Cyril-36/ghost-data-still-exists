"""Native document evidence without a provider: positive and abstention controls."""
import unittest

import helpers  # noqa: F401
from ghostaudit.media import interpret_pdf
from ghostaudit.observations import PRESENT, STATE, Observation
from test_media import T, corpus_with, entry, make_pdf


class NativeDocumentEvidence(unittest.TestCase):
    SECURITY = ['COMPLIANCE AUDIT JOURNAL', 'Event reference audit_record_8043',
                'subject reference user_77', 'retain until 25 Aug 2031']
    SUPPORT = ['SUPPORT REQUEST', 'Support reference TICKET-4 / support_8043',
               'person@example.test', 'Attachment properties', 'uploader customer']

    def interpret(self, lines, system='security_service', category='SECURITY_AUDIT_LOG'):
        known = Observation('fx_known', T, T, 'r', '', STATE, system, 'fx_other',
                            category=category, presence=PRESENT)
        return interpret_pdf(entry(origin=system), make_pdf([lines]), corpus_with(known), None)

    def test_native_security_structure_yields_identity_and_literal_retention(self):
        observations, report, extraction = self.interpret(self.SECURITY)
        self.assertEqual(len(observations), 1)
        self.assertEqual(report.status, 'INTERPRETED')
        self.assertEqual(observations[0].category, 'SECURITY_AUDIT_LOG')
        self.assertIn(('user', 'user_77'), observations[0].tokens)
        self.assertEqual(observations[0].retention_until.year, 2031)
        self.assertEqual(extraction['kind']['method'], 'native-structure-v1')
        self.assertIsNone(extraction['kind']['p'])

    def test_native_support_structure_yields_only_its_captured_record(self):
        observations, report, _ = self.interpret(self.SUPPORT, 'support_platform', 'SUPPORT_ATTACHMENT')
        self.assertEqual(report.status, 'INTERPRETED')
        self.assertEqual([o.resource_id for o in observations], ['support_8043'])
        self.assertEqual(observations[0].tokens, {('email', 'person@example.test')})

    def test_ambiguous_or_incomplete_structure_abstains(self):
        for lines in (self.SECURITY + self.SUPPORT, self.SECURITY[1:], self.SUPPORT[:3],
                      ['Example compliance audit journal'] + self.SECURITY[1:],
                      ['Not a compliance audit journal'] + self.SECURITY[1:]):
            with self.subTest(lines=lines):
                observations, report, _ = self.interpret(lines)
                self.assertEqual(observations, [])
                self.assertEqual(report.status, 'TEXT_EXTRACTED')

    def test_native_classification_does_not_bind_a_page_naming_two_people(self):
        observations, _, _ = self.interpret(self.SUPPORT + ['other@example.test'],
                                             'support_platform', 'SUPPORT_ATTACHMENT')
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].tokens, frozenset())

    def test_native_kind_does_not_invent_a_system(self):
        observations, report, _ = interpret_pdf(entry(origin='unrecognized_service'),
                                               make_pdf([self.SECURITY]), corpus_with(), None)
        self.assertEqual(observations, [])
        self.assertTrue(any('system not established' in issue for issue in report.issues))
