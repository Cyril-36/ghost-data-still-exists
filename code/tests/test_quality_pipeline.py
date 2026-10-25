"""Independent complete-row cases for the retained accuracy improvements."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import helpers  # noqa: F401
from ghostaudit.audit import run_audit
from ghostaudit.contracts import Step
from ghostaudit.output import canonical_steps, read_output
from phase4_world import EMAIL, OTHER, T_AUDIT, T_CAPTURE, world
from evaluation.fixtures import _request
from test_media import make_pdf


def deletion_steps(system, rid, source):
    return [Step('FINDING', system, rid, 'REPORT_REMAINING_RECORD', 'PRESENT', source),
            Step('REMEDIATION', system, rid, 'DELETE_RECORD', 'ABSENT', ''),
            Step('VERIFICATION', system, rid, 'VERIFY_RECORD_ABSENT', 'ABSENT', '')]


class ImprovedExactRows(unittest.TestCase):
    def test_native_document_adds_all_required_steps_without_a_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            raw = make_pdf([['SUPPORT REQUEST', 'Support reference TICKET-4 / support_8043',
                             EMAIL, 'Attachment properties', 'uploader customer']])
            root = world(tmp, lookup_rows=[(T_CAPTURE, 'support_platform', 'support_9999',
                                            'SUPPORT_ATTACHMENT', 'FOUND', OTHER)], files={
                'evidence/documents/attachment.pdf': ('fx_file_PDF', T_CAPTURE, 'support_platform', raw)})
            expected = canonical_steps(deletion_steps('account_service', 'fx_account_P', 'fx_file_L')
                                       + deletion_steps('support_platform', 'support_8043', 'fx_file_PDF'))
            # An ablation proves this is a complete-row improvement, not just new parser output.
            with patch('ghostaudit.media._native_doc_kind', return_value=None):
                run_audit(root, root / 'deletion_requests.csv', base / 'before.csv', base / 'before')
            summary = run_audit(root, root / 'deletion_requests.csv', base / 'after.csv', base / 'after')
            self.assertNotEqual(read_output(base / 'before.csv')[0].steps, expected)
            row = read_output(base / 'after.csv')[0]
            self.assertEqual((row.deletion_status, row.steps), ('INCOMPLETE', expected))
            self.assertEqual(summary['model_calls'], 0)

    def test_explicit_item_request_never_remediates_the_account_or_another_person(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            request = _request('fx_item_case', EMAIL, T_AUDIT,
                               text='Please remove that search result. Keep my account active.')
            root = world(tmp, requests=[request], lookup_rows=[
                (T_CAPTURE, 'search_service', 'fx_search_P', 'SEARCH_INDEX_ENTRY', 'FOUND', EMAIL),
                (T_CAPTURE, 'search_service', 'fx_search_Q', 'SEARCH_INDEX_ENTRY', 'FOUND', OTHER)])
            summary = run_audit(root, root / 'deletion_requests.csv', base / 'p.csv', base / 'run')
            row = read_output(base / 'p.csv')[0]
            self.assertEqual((row.deletion_status, row.steps), ('INCOMPLETE', canonical_steps(
                deletion_steps('search_service', 'fx_search_P', 'fx_file_L'))))
            self.assertEqual(summary['model_calls'], 0)
