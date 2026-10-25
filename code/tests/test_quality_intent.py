"""Explicit English item requests and prior-deletion checks without account-scope widening."""
import unittest
from dataclasses import replace
from unittest.mock import patch

import helpers  # noqa: F401
from ghostaudit.intent import ACCOUNT, DELETE, ITEM, UNRESOLVED, classify
from ghostaudit.intent_models import _agrees, decide_intents
from ghostaudit.providers import ReplayGateway
from test_intent_models import jev_payload


class ExplicitIntent(unittest.TestCase):
    def test_specific_search_and_export_removal_remain_item_scoped(self):
        for text, target in (
            ('Can you remove that search result too?', 'SEARCH_INDEX_ENTRY'),
            ('Please delete my data export link.', 'USER_EXPORT'),
            ('Please remove that search listing. Keep my account active.', 'SEARCH_INDEX_ENTRY'),
        ):
            with self.subTest(text=text):
                intent = classify(text)
                self.assertEqual((intent.decision, intent.scope, intent.target), (DELETE, ITEM, target))
                self.assertFalse(intent.whole_category)

    def test_verification_of_prior_account_deletion_is_not_an_item_request(self):
        intent = classify('Please verify that my account was deleted. A search result belongs to someone else.')
        self.assertEqual((intent.decision, intent.scope), (DELETE, ACCOUNT))

    def test_hypothetical_or_multiple_item_targets_abstain(self):
        for text in ('If I remove that search result, will it return?',
                     'Can I delete my data export link?',
                     'Please remove the search result for someone else.',
                     'Please remove that search result and delete my data export link.'):
            with self.subTest(text=text):
                self.assertEqual(classify(text).decision, UNRESOLVED)

    def test_models_cannot_widen_an_explicit_item_request_to_account_scope(self):
        item = classify('Please remove that search result.')
        account = classify('Please delete my account.')
        self.assertFalse(_agrees(item, account))

    def test_account_deletion_and_account_preservation_still_conflict(self):
        self.assertEqual(classify('Please delete my account but keep my account active.').decision, UNRESOLVED)

    def test_negated_item_removal_never_authorizes_deletion(self):
        self.assertNotEqual(classify('Please do not delete my data export link. Keep my account.').decision, DELETE)

    def test_model_target_cannot_override_the_literal_item_category(self):
        gateway = ReplayGateway(lambda *_: jev_payload('SCOPED_DELETION'))
        with patch('ghostaudit.intent_models.ground_target',
                   side_effect=lambda g, t, intent: replace(intent, target='USER_EXPORT')):
            result = decide_intents({'fx': 'Please remove that search result.'}, gateway, use_generative=False)
        self.assertEqual(result['fx'].intent.decision, UNRESOLVED)
