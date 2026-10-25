"""A model cannot waive explicit English account-preservation instructions."""
import unittest

import helpers  # noqa: F401
from ghostaudit.intent_models import decide_intents
from ghostaudit.providers import ReplayGateway
from test_intent_models import jev_payload


class AuthorizationBoundary(unittest.TestCase):
    def test_account_deletion_with_explicit_preservation_is_not_authorized(self):
        text = 'Please delete my account, but keep my account active.'
        gateway = ReplayGateway(lambda *_: jev_payload('DELETE_ACCOUNT'))
        result = decide_intents({'fx_case': text}, gateway, use_generative=False)
        self.assertEqual(result['fx_case'].intent.decision, 'UNRESOLVED')
