"""Per-case admission must account for the entire next priced model turn."""
import tempfile
import unittest

import helpers  # noqa: F401
from ghostaudit.investigator import InvestigatorConfig
from ghostaudit.providers import ProviderConfig, ReplayGateway
from test_phase4_investigator import investigate


class CaseAdmission(unittest.TestCase):
    def test_catalog_reserve_cannot_exceed_the_case_budget(self):
        class LocalLive(ReplayGateway):
            mode = 'live'

            def _post(self, path, body):
                return self.responder(path, body), None

        def gateway(responder):
            result = LocalLive(responder, ProviderConfig(cache_dir=None, max_cost_paise=1e9))
            result.pricing = {'fx-investigator': {'inputPer1M': 1000, 'outputPer1M': 1000}}
            return result

        with tempfile.TemporaryDirectory() as tmp:
            _, _, case, model, _ = investigate(tmp, gateway=gateway,
                config=InvestigatorConfig('fx-investigator', max_case_paise=400))
        self.assertEqual(len(model.bodies), 0)
        self.assertEqual(case.stop_reason, 'CASE_BUDGET')

    def test_oversized_initial_context_is_refused_before_a_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, case, model, _ = investigate(tmp, config=InvestigatorConfig('fx-investigator', max_case_tokens=1))
        self.assertEqual(len(model.bodies), 0)
        self.assertEqual(case.stop_reason, 'CASE_BUDGET')
