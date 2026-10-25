"""Independent-review regressions for parent verification and authorization guards."""
import unittest
from unittest.mock import patch

import helpers  # noqa: F401
from evaluation.fixtures import _request
from ghostaudit.intent import DELETE, ITEM, classify
from ghostaudit.inputs import load_context
from ghostaudit.tools import SourceText
from phase4_world import EMAIL, T_AUDIT, T_CAPTURE, world
import test_agent_first as scripted
from test_agent_first import call, proposal


class AuthorizationReviewFixes(unittest.TestCase):
    def setUp(self):
        self.actor = scripted.AgentFirstTests()
        self.actor.setUp()
        self.addCleanup(self.actor.doCleanups)

    def run_proposal(self, root, p, name='review'):
        return self.actor.run_agent([self.actor.inspect('fx_file_L'), [call('submit_audit', p)]], root=root, name=name)

    def parent_case(self, absent=True, parent_rid='fx_account_P'):
        rows = [(T_CAPTURE, 'search_service', 'fx_search_P', 'SEARCH_INDEX_ENTRY', 'FOUND', EMAIL)]
        if absent:
            rows.append((T_CAPTURE, 'account_service', parent_rid, 'USER_PROFILE', 'NOT_FOUND', EMAIL))
        root = world(self.actor.base / ('absent' if absent else 'present'), requests=[_request('fx_case', EMAIL, T_AUDIT,
            text='Please remove that search result. My account was already deleted; keep it deleted.')], lookup_rows=rows)
        p = proposal(root, 'fx_search_P', 'fx_file_L', system='search_service', scope='ITEM', target='SEARCH_INDEX_ENTRY')
        p['steps'].insert(2, dict(step_no=3, phase='VERIFICATION', system_id='account_service',
                                 resource_id=parent_rid, operation='VERIFY_RECORD_ABSENT', state='ABSENT', evidence_id=''))
        for n, step in enumerate(p['steps'], 1):
            step['step_no'] = n
        lines = SourceText(load_context(root)).pages('fx_file_L')['']
        marker = 'NOT_FOUND' if absent else 'fx_account_P'
        end = next(n for n, line in enumerate(lines, 1) if marker in line)
        start = max(1, end - 2) if absent else end
        p['witnesses'].append(dict(step_no=3, source_id='fx_file_L', locator=f'L{start}-L{end}',
                                    literal='\n'.join(lines[start - 1:end])))
        return root, p

    def test_search_deletion_may_verify_a_proven_absent_owned_parent(self):
        root, p = self.parent_case()
        summary, row, _ = self.run_proposal(root, p)
        self.assertEqual(summary['fallback_count'], 0)
        self.assertEqual(len(row.steps), 4)
        parent = row.steps[2]
        self.assertEqual((parent.resource_id, parent.operation, parent.evidence_id),
                         ('fx_account_P', 'VERIFY_RECORD_ABSENT', ''))

    def test_parent_exception_does_not_authorize_parent_deletion(self):
        root, p = self.parent_case()
        p['steps'][2].update(phase='REMEDIATION', operation='DELETE_RECORD')
        summary, row, _ = self.run_proposal(root, p)
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(row.steps, ())

    def test_present_parent_cannot_be_reported_absent(self):
        root, p = self.parent_case(absent=False)
        _, row, _ = self.run_proposal(root, p)
        self.assertEqual(row.steps, ())

    def test_uncited_parent_check_still_requires_a_retrieved_absence_witness(self):
        root, p = self.parent_case()
        p['witnesses'] = p['witnesses'][:1]
        _, row, _ = self.run_proposal(root, p)
        self.assertEqual(row.steps, ())

    def test_not_found_query_input_is_not_parent_ownership(self):
        root, p = self.parent_case(parent_rid='fx_parent_884')
        _, row, _ = self.run_proposal(root, p)
        self.assertEqual(row.steps, ())

    def test_parent_exception_does_not_cover_other_categories(self):
        root, p = self.parent_case()
        p['steps'][2].update(system_id='export_service', resource_id='fx_export_unknown')
        _, row, _ = self.run_proposal(root, p)
        self.assertEqual(row.steps, ())

    def test_incidental_question_does_not_cancel_an_account_deletion_request(self):
        root = world(self.actor.base / 'question', requests=[_request('fx_case', EMAIL, T_AUDIT,
            text='Please delete my account. Can I download a receipt afterward?')])
        p = proposal(root, 'fx_account_P', 'fx_file_L')
        summary, row, _ = self.run_proposal(root, p)
        self.assertEqual(summary['fallback_count'], 0)
        self.assertEqual(len(row.steps), 3)

    def test_existence_condition_is_a_real_deletion_request_not_a_hypothetical_question(self):
        root = world(self.actor.base / 'condition', requests=[_request('fx_case', EMAIL, T_AUDIT,
            text='If my account is still present, please delete it.')])
        p = proposal(root, 'fx_account_P', 'fx_file_L')
        summary, _, _ = self.run_proposal(root, p)
        self.assertEqual(summary['fallback_count'], 0)

    def test_incidental_question_does_not_erase_literal_item_scope(self):
        intent = classify('Please remove that search result. Can I download a receipt?')
        self.assertEqual((intent.decision, intent.scope, intent.target), (DELETE, ITEM, 'SEARCH_INDEX_ENTRY'))

    def test_genuine_capability_question_remains_unresolved(self):
        self.assertNotEqual(classify('Can I delete my account?').decision, DELETE)

    def test_requested_inspection_of_prior_removal_is_not_a_hypothetical(self):
        text = 'Please check the records and confirm whether my profile has already been erased.'
        self.assertEqual(classify(text).decision, DELETE)

    def test_embedded_or_quoted_hypotheticals_do_not_become_instructions(self):
        for text in ('Could you tell me what would happen if I want you to delete my account?',
                     'Suppose I said, please delete my account?',
                     'If I asked you to remove my data export link, would that affect orders?'):
            with self.subTest(text=text):
                self.assertNotEqual(classify(text).decision, DELETE)

    def test_actual_request_after_an_incidental_question_is_preserved(self):
        self.assertEqual(classify('Can I download a receipt? Please delete my account.').decision, DELETE)

    def test_independent_multilingual_non_deletion_confirmation_is_accepted(self):
        text = 'Veuillez envoyer une copie de mes données sans supprimer mon compte.'
        root = world(self.actor.base / 'french', requests=[_request('fx_case', EMAIL, T_AUDIT, text=text)])
        p = {'authorization': {'decision': 'NON_DELETION', 'scope': 'NONE', 'target': '',
                              'whole_category': False, 'request_quote': text, 'reason': 'Access request, no deletion.'},
             'deletion_status': 'NOT_APPLICABLE', 'steps': [], 'witnesses': []}
        with patch('ghostaudit.agent_first.ProposalVerifier.review_authorization', create=True,
                   return_value={'decision': 'NON_DELETION', 'quote': text, 'confidence': 0.99,
                                 'conflicting': False}):
            summary, row, _ = self.actor.run_agent([[call('submit_audit', p)]], root=root)
        self.assertEqual(summary['fallback_count'], 0)
        self.assertEqual(row.deletion_status, 'NOT_APPLICABLE')

    def test_multilingual_primary_interpretation_alone_is_not_accepted(self):
        text = 'Veuillez envoyer une copie de mes données sans supprimer mon compte.'
        root = world(self.actor.base / 'unreviewed', requests=[_request('fx_case', EMAIL, T_AUDIT, text=text)])
        p = {'authorization': {'decision': 'NON_DELETION', 'scope': 'NONE', 'target': '',
                              'whole_category': False, 'request_quote': text, 'reason': 'Access request.'},
             'deletion_status': 'NOT_APPLICABLE', 'steps': [], 'witnesses': []}
        _, row, _ = self.actor.run_agent([[call('submit_audit', p)]], root=root)
        self.assertEqual((row.deletion_status, row.steps), ('INCOMPLETE', ()))
