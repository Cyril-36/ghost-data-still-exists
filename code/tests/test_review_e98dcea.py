"""Regressions for the review of e98dcea: rejected submissions, unsubmitted facts, feedback turn limits."""
import tempfile
import unittest

import helpers  # noqa: F401
from ghostaudit.investigator import InvestigatorConfig
from ghostaudit.providers import ProviderConfig, ReplayGateway
from test_phase4_investigator import INSPECT_PDF, PDF_FACTS, SUBMIT, completion
from test_phase4_verifier import FeedbackLoop

NO_ACTION = [('submit_case_file', {'resolution': 'NO_SUPPORTED_ACTION'})]


class Paid(ReplayGateway):
    mode = 'live'

    def _post(self, path, body):
        return self.responder(path, body), 100.0


def verified(tmp, *turns, config=None, gateway=None):
    return FeedbackLoop.verified_run(None, tmp, *turns, config=config, gateway=gateway)


class SubmissionMustBeAccepted(unittest.TestCase):
    def test_a_submit_refused_by_the_tool_limit_is_not_a_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, final, case, _, _ = verified(tmp, INSPECT_PDF, PDF_FACTS, SUBMIT,
                                               config=InvestigatorConfig(model='m', max_tool_calls=4))
        self.assertTrue(case.tool_calls[-1].error.startswith('TOOL_LIMIT'))
        self.assertEqual((case.stop_reason, case.outcome, final.row), ('ROUND_LIMIT', 'PHASE3_KEPT', base.row))


class UnsubmittedFactsAreNeverPublished(unittest.TestCase):
    def test_valid_facts_then_a_stop_keep_the_phase3_plan(self):
        stops = {
            'RUN_BUDGET': dict(gateway=lambda m: Paid(m, ProviderConfig(cache_dir=None, max_cost_paise=250.0))),
            'CASE_BUDGET': dict(gateway=lambda m: Paid(m, ProviderConfig(cache_dir=None)),
                                config=InvestigatorConfig(model='m', max_case_paise=250.0)),
            'ROUND_LIMIT': dict(config=InvestigatorConfig(model='m', max_rounds=2)),
            'MODEL_ERROR': dict(extra=[completion([('inspect_source', {'source_id': 'x', 'bad': 1})])] * 2),
        }
        for stop, kw in stops.items():
            with self.subTest(stop), tempfile.TemporaryDirectory() as tmp:
                base, final, case, _, _ = verified(tmp, INSPECT_PDF, PDF_FACTS, *kw.pop('extra', [SUBMIT]), **kw)
                self.assertEqual(len(case.facts), 3)                    # valid facts were accepted ...
                self.assertTrue(case.stop_reason.startswith(stop), case.stop_reason)
                self.assertEqual((case.outcome, final.row), ('PHASE3_KEPT', base.row))   # ... but not published


class FeedbackHasItsOwnTurns(unittest.TestCase):
    ROUTING = [[('inspect_source', {'source_id': 'fx_file_L', 'start_line': n})] for n in (1, 2, 3)]

    def test_feedback_after_four_routing_rounds_still_gets_its_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, final, case, model, _ = verified(tmp, *self.ROUTING, NO_ACTION, INSPECT_PDF, PDF_FACTS + SUBMIT)
        self.assertEqual((case.rounds, case.feedback_rounds, case.feedback_turns), (4, 1, 2))
        self.assertEqual((case.outcome, case.stop_reason), ('ACCEPTED', 'SUBMITTED: RESOLVED'))
        self.assertIn('fx_attach_P', {s.resource_id for s in final.steps})

    def test_feedback_turns_are_bounded(self):
        stall = [('inspect_source', {'source_id': 'fx_file_L', 'start_line': 9})]
        stall2 = [('inspect_source', {'source_id': 'fx_file_L', 'start_line': 10})]
        with tempfile.TemporaryDirectory() as tmp:
            base, final, case, model, _ = verified(tmp, *self.ROUTING, NO_ACTION, stall, stall2, stall, stall2)
        self.assertEqual((case.stop_reason, case.feedback_turns, len(model.bodies)), ('FEEDBACK_TURN_LIMIT', 2, 6))
        self.assertEqual((case.outcome, final.row), ('PHASE3_KEPT', base.row))


if __name__ == '__main__':
    unittest.main()
