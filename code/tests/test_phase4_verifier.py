"""Phase 4 milestone 3: the independent verifier (clean plans pass; each corruption is caught) and the
investigator-verifier feedback loop."""
import copy
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import helpers  # noqa: F401
from evaluation.fixtures import FIXTURES, write_fixture
from ghostaudit.casefile import CaseFile, Fact
from ghostaudit.contracts import Step
from ghostaudit.corpus import build_corpus
from ghostaudit.identity import IdentityIndex
from ghostaudit.inputs import load_context, load_requests
from ghostaudit.intent import classify
from ghostaudit.output import canonical_steps
from ghostaudit.planner import plan_case
from ghostaudit.policy import Policy
from ghostaudit.tools import SourceText
from ghostaudit.verifier import Verifier
from phase4_world import EMAIL, OTHER, T_AUDIT, world
from test_media import make_pdf
from test_phase4_investigator import INSPECT_PDF, PDF_FACTS, SUBMIT, investigate

ROWS = [('2026-03-01T09:00:00Z', 'search_service', 'fx_search_P', 'SEARCH_INDEX_ENTRY', 'FOUND', EMAIL),
        ('2026-03-01T09:00:00Z', 'search_service', 'fx_search_Q', 'SEARCH_INDEX_ENTRY', 'FOUND', OTHER),
        ('2026-03-01T09:00:00Z', 'legal_service', 'fx_audit_P', 'LEGAL_HOLD_RECORD', 'FOUND', EMAIL),
        ('2026-03-01T09:00:00Z', 'export_service', 'fx_export_P', 'USER_EXPORT', 'NOT_FOUND', EMAIL)]


def plans_for(root):
    context = load_context(root)
    corpus, policy, index = build_corpus(context), Policy(context.rules), IdentityIndex(context)
    verifier = Verifier(corpus, index, context, SourceText(context))
    plans = [plan_case(r, corpus, policy, index, classify(r.request_text))
             for r in load_requests(root / 'deletion_requests.csv')]
    return plans, verifier


def codes(findings, blocking_only=True):
    return {f.code for f in findings if f.blocking or not blocking_only}


def with_steps(plan, steps, status=None):
    p = copy.copy(plan)
    p.steps, p.status = canonical_steps(steps), status or plan.status
    return p


class CleanPlansPass(unittest.TestCase):
    def test_every_fixture_plan_and_the_world_plan_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, fixture in FIXTURES.items():
                plans, verifier = plans_for(write_fixture(fixture, Path(tmp)) / 'input')
                for plan in plans:
                    with self.subTest(fixture=name, case=plan.request.case_id):
                        self.assertEqual(codes(verifier.verify(plan)), set())
            plans, verifier = plans_for(world(tmp, lookup_rows=ROWS))
            self.assertEqual(codes(verifier.verify(plans[0])), set())
            self.assertIn('UNINSPECTED_MENTION', codes(verifier.verify(plans[0]), blocking_only=False))


class MutationsAreCaught(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        plans, self.verifier = plans_for(world(self._tmp.name, lookup_rows=ROWS))
        self.plan = plans[0]

    def mutate(self, fn, status=None):
        return codes(self.verifier.verify(with_steps(self.plan, fn(list(self.plan.steps)), status)))

    def test_swapped_and_post_cutoff_citations(self):
        swap = lambda steps: [replace(s, evidence_id='fx_file_PDF') if s.phase == 'FINDING' else s for s in steps]  # noqa: E731
        self.assertIn('UNSUPPORTED_CITATION', self.mutate(swap))
        late = lambda steps: [replace(s, evidence_id='evidence_unregistered') if s.phase == 'FINDING' else s  # noqa: E731
                              for s in steps]
        self.assertTrue({'INTEGRITY', 'INELIGIBLE_CITATION'} <= self.mutate(late))

    def test_dropped_obligation_is_an_omitted_resource(self):
        p = copy.copy(self.plan)
        p.obligations = [o for o in p.obligations if o.resource_id != 'fx_search_P']
        p.steps = canonical_steps(s for s in p.steps if s.resource_id != 'fx_search_P')
        self.assertIn('OMITTED_RESOURCE', codes(self.verifier.verify(p)))

    def test_wrong_treatment_decoy_record_and_lookup_only_finding(self):
        redact = {'REPORT_REMAINING_RECORD': ('REPORT_EXPOSED_PII', 'PRESENT'), 'DELETE_RECORD': ('REDACT_RECORD', 'REDACTED'),
                  'VERIFY_RECORD_ABSENT': ('VERIFY_RECORD_REDACTED', 'REDACTED')}
        as_redact = lambda steps: [replace(s, operation=redact[s.operation][0], state=redact[s.operation][1])  # noqa: E731
                                   if s.resource_id == 'fx_account_P' else s for s in steps]
        self.assertIn('POLICY_MISMATCH', self.mutate(as_redact))
        decoy = [Step('FINDING', 'search_service', 'fx_search_Q', 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_L'),
                 Step('REMEDIATION', 'search_service', 'fx_search_Q', 'DELETE_RECORD', 'ABSENT', ''),
                 Step('VERIFICATION', 'search_service', 'fx_search_Q', 'VERIFY_RECORD_ABSENT', 'ABSENT', '')]
        self.assertTrue({'UNSUPPORTED_ASSOCIATION', 'CROSS_PERSON'} <= self.mutate(lambda s: s + decoy))
        lookup = [Step('FINDING', 'export_service', 'fx_export_P', 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_L'),
                  Step('REMEDIATION', 'export_service', 'fx_export_P', 'DELETE_RECORD', 'ABSENT', '')]
        self.assertIn('UNSUPPORTED_ASSOCIATION', self.mutate(
            lambda s: [x for x in s if x.resource_id != 'fx_export_P'] + lookup +
            [Step('VERIFICATION', 'export_service', 'fx_export_P', 'VERIFY_RECORD_ABSENT', 'ABSENT', '')]))

    def test_retention_without_a_supplied_period(self):
        keep = Step('VERIFICATION', 'legal_service', 'fx_audit_P', 'VERIFY_RECORD_PRESENT', 'PRESENT', '')
        self.assertIn('UNPROVEN_CONDITION', self.mutate(lambda s: s + [keep]))

    def test_undated_security_audit_log_may_be_verified_present(self):
        root = world(Path(self._tmp.name) / 'd081', lookup_rows=[
            ('2026-03-01T09:00:00Z', 'security_service', 'fx_sec_P', 'SECURITY_AUDIT_LOG', 'FOUND', EMAIL)])
        plans, verifier = plans_for(root)
        keep = [s for s in plans[0].steps if s.resource_id == 'fx_sec_P']
        self.assertEqual([(s.operation, s.state) for s in keep], [('VERIFY_RECORD_PRESENT', 'PRESENT')])
        self.assertFalse({'UNPROVEN_CONDITION', 'POLICY_MISMATCH'} & codes(verifier.verify(plans[0])))
        deleted = copy.copy(plans[0])   # removing it is still a policy mismatch
        deleted.steps = canonical_steps([s for s in plans[0].steps if s.resource_id != 'fx_sec_P'] + [
            Step('FINDING', 'security_service', 'fx_sec_P', 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_L'),
            Step('REMEDIATION', 'security_service', 'fx_sec_P', 'DELETE_RECORD', 'ABSENT', ''),
            Step('VERIFICATION', 'security_service', 'fx_sec_P', 'VERIFY_RECORD_ABSENT', 'ABSENT', '')])
        self.assertIn('POLICY_MISMATCH', codes(verifier.verify(deleted)))

    def test_item_scope_cannot_grow(self):
        p = copy.copy(self.plan)
        p.intent = replace(p.intent, scope='ITEM', target='SEARCH_INDEX_ENTRY')
        self.assertIn('SCOPE_EXCEEDED', codes(self.verifier.verify(p)))

    def test_tampered_or_contradicted_facts(self):
        case = CaseFile.from_plan(self.plan)
        at = self.plan.request.audit_at
        tampered = Fact('OWNERSHIP', 'support_platform', 'fx_attach_P', 'fx_file_PDF', 'p1:L1-L3',
                        f'fx_attach_P belongs to {EMAIL}', '', at, at, ('email', EMAIL))
        case.facts = [tampered]
        self.assertIn('FACT_UNSUPPORTED', codes(self.verifier.verify(self.plan, case)))

    def test_replica_disagreement_and_stale_citation(self):
        with tempfile.TemporaryDirectory() as tmp:
            plans, verifier = plans_for(write_fixture(FIXTURES['fixture_replica'], Path(tmp)) / 'input')
            plan = plans[0]
            absent = [replace(s, phase='VERIFICATION', operation='VERIFY_RECORD_ABSENT', state='ABSENT')
                      for s in plan.steps if s.phase == 'FINDING']
            self.assertIn('REPLICA_DISAGREEMENT', codes(verifier.verify(with_steps(plan, absent, 'COMPLETE'))))
        rows = [('2026-03-01T09:00:00Z', 'search_service', 'fx_search_P', 'SEARCH_INDEX_ENTRY', 'FOUND', EMAIL)]
        later = {'evidence/records/later.json': ('fx_file_L2', '2026-03-02T09:00:00Z', 'fixture', __import__(
            'evaluation.fixtures', fromlist=['_lookups'])._lookups('2026-03-02T09:00:00Z', (
                '2026-03-02T09:00:00Z', 'search_service', 'fx_search_P', 'SEARCH_INDEX_ENTRY', 'NOT_FOUND', EMAIL)))}
        with tempfile.TemporaryDirectory() as tmp:
            plans, verifier = plans_for(world(tmp, lookup_rows=rows, files=later))
            stale = [Step('FINDING', 'search_service', 'fx_search_P', 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_L'),
                     Step('REMEDIATION', 'search_service', 'fx_search_P', 'DELETE_RECORD', 'ABSENT', ''),
                     Step('VERIFICATION', 'search_service', 'fx_search_P', 'VERIFY_RECORD_ABSENT', 'ABSENT', '')]
            plan = plans[0]
            got = codes(verifier.verify(with_steps(plan, [s for s in plan.steps if s.resource_id != 'fx_search_P']
                                                   + stale)))
            self.assertIn('STALE_CITATION', got)

    def test_record_recreated_by_an_active_flow(self):
        rows = [('2026-03-01T08:00:00Z', 'search_service', 'fx_search_P', 'SEARCH_INDEX_ENTRY', 'FOUND', EMAIL)]
        acts = [('fx_act_1', '2026-03-01T09:30:00Z', 'SYNC_RECORD', 'integration_service', 'search_service', 'fx_flow_P',
                 'SUCCESS', 'Connector fx_flow_P repopulated downstream resource fx_search_P.'),
                ('fx_act_2', '2026-03-01T09:40:00Z', 'CHECK_DATA_FLOW', 'integration_service', 'integration_service',
                 'fx_flow_P', 'SUCCESS', 'Monitor confirmed that data flow fx_flow_P remains enabled.')]
        with tempfile.TemporaryDirectory() as tmp:
            plans, verifier = plans_for(world(tmp, lookup_rows=rows, activities=acts))
            plan = plans[0]
            ops = [(s.phase, s.operation, s.resource_id) for s in plan.steps]
            self.assertIn(('FINDING', 'REPORT_ACTIVE_DATA_FLOW', 'fx_flow_P'), ops)
            remediation = [r for p, _, r in ops if p == 'REMEDIATION']
            self.assertLess(remediation.index('fx_flow_P'), remediation.index('fx_search_P'))   # stop the copy first
            self.assertEqual(codes(verifier.verify(plan)), set())
            p = copy.copy(plan)
            p.obligations = [o for o in plan.obligations if o.resource_id != 'fx_flow_P']
            p.steps = canonical_steps(s for s in plan.steps if s.resource_id != 'fx_flow_P')
            self.assertIn('RECREATION_PATH_IGNORED', codes(verifier.verify(p)))


class FeedbackLoop(unittest.TestCase):
    def verified_run(self, tmp, *turns, world_kw=None, **kw):
        holder = {}

        def verifier(plan, case):
            return holder['v'].verify(plan, case)
        root = world(tmp, **(world_kw or {}))
        context = load_context(root)
        holder['v'] = Verifier(build_corpus(context), IdentityIndex(context), context, SourceText(context))
        return investigate(tmp, *turns, world_kw=world_kw, verifier=verifier, **kw)

    def test_6_verifier_feedback_recovers_an_omitted_resource(self):
        early_stop = [('submit_case_file', {'resolution': 'NO_SUPPORTED_ACTION'})]
        with tempfile.TemporaryDirectory() as tmp:
            base, final, case, model, _ = self.verified_run(tmp, early_stop, INSPECT_PDF, PDF_FACTS + SUBMIT)
        feedback = model.bodies[1]['messages'][-1]['content']
        self.assertIn('UNINSPECTED_MENTION', feedback)
        self.assertIn('fx_file_PDF', feedback)
        self.assertEqual((case.feedback_rounds, case.outcome, case.stop_reason), (1, 'ACCEPTED', 'SUBMITTED: RESOLVED'))
        self.assertIn('fx_attach_P', {s.resource_id for s in final.steps})
        self.assertNotIn('fx_attach_P', {s.resource_id for s in base.steps})

    def test_verifier_rejects_semantically_contradicted_evidence(self):
        removed = {'evidence/documents/attachment.pdf': ('fx_file_PDF', '2026-03-01T10:00:00Z', 'support_platform', make_pdf([
            ['SUPPORT CASE CS-1 / fx_attach_P', 'Detail  Fixture Person P', f'Detail  {EMAIL}',
             'Detail  uploaded attachment identity_scan.pdf stored for ticket CS-1',
             'Attachment fx_attach_P deleted by retention job on 1 Mar 2026']]))}
        with tempfile.TemporaryDirectory() as tmp:
            base, final, case, model, _ = self.verified_run(tmp, INSPECT_PDF, PDF_FACTS + SUBMIT, PDF_FACTS + SUBMIT,
                                                            PDF_FACTS + SUBMIT, world_kw={'files': removed})
        rejected = [f for v in case.verifier_findings for f in v if f['code'] == 'FACT_UNSUPPORTED']
        self.assertTrue(rejected and 'removed' in rejected[0]['message'], case.verifier_findings)
        self.assertEqual((case.outcome, final.row), ('PHASE3_KEPT', base.row))
        self.assertTrue(case.stop_reason in ('VERIFIER_REJECTED', 'ROUND_LIMIT'), case.stop_reason)
        self.assertLessEqual(case.feedback_rounds, 2)
        self.assertEqual(T_AUDIT[:10], base.request.audit_at.date().isoformat())


if __name__ == '__main__':
    unittest.main()
