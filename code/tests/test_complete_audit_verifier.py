"""Independent adversarial regressions for the complete repository audit."""
import tempfile
import unittest

import helpers  # noqa: F401
from evaluation.fixtures import _lookups
from ghostaudit.output import canonical_steps
from ghostaudit.contracts import Step
from ghostaudit.observations import Observation, PRESENT, STATE
from ghostaudit.intent import Intent, DELETE, ITEM
from ghostaudit.planner import plan_case
from ghostaudit.verifier import Verifier
from phase4_world import EMAIL, world
from test_phase4_casefile import setup_case
from test_phase4_verifier import ROWS, codes, plans_for


class MissingOutputCoverage(unittest.TestCase):
    def test_retained_obligation_does_not_hide_dropped_output_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            plans, verifier = plans_for(world(tmp, lookup_rows=ROWS))
            plan = plans[0]
            self.assertTrue(any(o.resource_id == 'fx_search_P' for o in plan.obligations))
            plan.steps = canonical_steps(s for s in plan.steps if s.resource_id != 'fx_search_P')
            self.assertIn('OMITTED_RESOURCE', codes(verifier.verify(plan)))


class FactMetadataCutoff(unittest.TestCase):
    def test_unindexed_identifier_is_not_a_set_arithmetic_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = setup_case(tmp)
            token = ('email', 'unindexed@example.test')
            self.assertEqual(s['validator']._other_people({token}, s['case']), [])
            verifier = Verifier(s['corpus'], s['index'], s['context'], s['texts'])
            at = s['request'].audit_at
            obs = Observation('fx_file_L', at, at, 'L1', '', STATE, 'account_service', 'fx_unknown',
                              tokens=frozenset({token}))
            self.assertEqual(verifier._foreign([obs], s['plan'].subject), [])

    def test_post_cutoff_system_and_category_do_not_reject_an_earlier_fact(self):
        for system in ('other_system', 'support_platform'):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as tmp:
                later = '2026-03-03T10:00:00Z'
                payload = _lookups(later, (later, system, 'fx_attach_P', 'USER_EXPORT', 'FOUND', EMAIL))
                s = setup_case(tmp, files={'evidence/records/later.json': (
                    'fx_file_LATER', later, system, payload)})
                s['tools'].call('inspect_media', {'file_id': 'fx_file_PDF'})
                fact, reason = s['validator'].validate({
                    'kind': 'CATEGORY', 'system_id': 'support_platform', 'resource_id': 'fx_attach_P',
                    'source_id': 'fx_file_PDF', 'locator': 'p1:L1-L4', 'value': 'SUPPORT_ATTACHMENT',
                    'literal': 'SUPPORT CASE CS-1 / fx_attach_P Detail Fixture Person P Detail '
                               + EMAIL + ' Detail uploaded attachment',
                }, s['case'])
                self.assertIsNotNone(fact, reason)


class TreatmentCopies(unittest.TestCase):
    def test_clean_primary_does_not_verify_an_exposing_replica(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = setup_case(tmp)
            at = s['context'].evidence['fx_file_L'].available_at
            def observation(location, exposed):
                return Observation('fx_file_L', at, at, 'L1', '', STATE, 'identity_service', 'fx_tombstone_P',
                                   location=location, category='ACCOUNT_TOMBSTONE', presence=PRESENT,
                                   exposed=exposed, tokens=frozenset({('email', EMAIL)}))
            corpus = s['corpus'].extended([observation('primary', ()), observation('replica', ('email',))])
            intent = Intent(DELETE, 'synthetic explicit tombstone treatment', scope=ITEM,
                            target='ACCOUNT_TOMBSTONE', whole_category=True)
            plan = plan_case(s['request'], corpus, s['policy'], s['index'], intent)
            plan.status = 'COMPLETE'
            plan.steps = (Step('VERIFICATION', 'identity_service', 'fx_tombstone_P',
                               'VERIFY_RECORD_REDACTED', 'REDACTED', 'fx_file_L'),)
            verifier = Verifier(corpus, s['index'], s['context'], s['texts'])
            self.assertIn('REPLICA_DISAGREEMENT', codes(verifier.verify(plan)))


if __name__ == '__main__':
    unittest.main()
