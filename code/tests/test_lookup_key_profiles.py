"""D085: a not-found lookup keyed by the subject verifies the absence of their sole profile candidate only."""
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from evaluation.fixtures import _identity, _request
from ghostaudit.corpus import build_corpus
from ghostaudit.identity import IdentityIndex
from ghostaudit.inputs import load_context, load_requests
from ghostaudit.intent import classify
from ghostaudit.planner import plan_case
from ghostaudit.policy import Policy
from ghostaudit.tools import SourceText
from ghostaudit.verifier import Verifier
from phase4_world import EMAIL, OTHER, T_AUDIT, world

LONE = 'person.r@example.test'   # a subject with no attribute-linked profile

T = '2026-03-01T{}:00Z'


def lookup_log(*lookups):
    lines = []
    for n, (rid, key) in enumerate(lookups):
        lines.append(f'{T.format(f"08:0{n}")} level=DEBUG service=account_service trace_id=t{n} '
                     f'event=profile_lookup_started lookup_key="{key}"')
        lines.append(f'{T.format(f"08:0{n}")} level=INFO service=account_service trace_id=t{n} resource_id={rid} '
                     'event=profile_lookup_completed rows=0 http_status=404')
    return ('\n'.join(lines) + '\n').encode()


class LookupKeyProfiles(unittest.TestCase):
    def plan(self, *lookups, lone=False):
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__('shutil').rmtree, tmp, True)
        extra = {'requests': [_request('fx_case_R', LONE, T_AUDIT, text='Please delete my account and all my data.')],
                     'extra_identities': [_identity('fx_user_R', 'Fixture Person R', LONE, '+12025550159')]} if lone else {}
        root = world(Path(tmp), files={'evidence/logs/lookups.log': (
            'fx_file_G', T.format('09:00'), 'account_service', lookup_log(*lookups))}, **extra)
        context = load_context(root)
        corpus, policy, index = build_corpus(context), Policy(context.rules), IdentityIndex(context)
        request = load_requests(root / 'deletion_requests.csv')[0]
        plan = plan_case(request, corpus, policy, index, classify(request.request_text))
        return plan, Verifier(corpus, index, context, SourceText(context)).verify(plan)

    def profile_steps(self, plan):
        return sorted((s.resource_id, s.operation) for s in plan.steps if s.system_id == 'account_service')

    def test_the_sole_profile_candidate_gets_an_absence_check_and_nothing_else(self):
        plan, findings = self.plan(('fx_account_K', LONE), lone=True)
        self.assertIn(('fx_account_K', 'VERIFY_RECORD_ABSENT'), self.profile_steps(plan))
        self.assertFalse([s for s in plan.steps if s.resource_id == 'fx_account_K' and s.phase != 'VERIFICATION'])
        self.assertFalse([f for f in findings if f.blocking])

    def test_a_lookup_key_never_competes_with_another_profile_candidate(self):
        for name, (lookups, lone) in {
                'two keyed lookups': ((('fx_account_K', LONE), ('fx_account_J', LONE)), True),
                'keyed plus linked profile': ((('fx_account_K', EMAIL),), False)}.items():
            with self.subTest(name):
                plan, findings = self.plan(*lookups, lone=lone)   # the default subject owns fx_account_P
                rows = [r for r, _ in self.profile_steps(plan)]
                self.assertNotIn('fx_account_K', rows)
                self.assertNotIn('fx_account_J', rows)
                self.assertTrue(any(i.startswith('LOOKUP_KEY_ONLY') for i in plan.issues))
                self.assertFalse([f for f in findings if f.blocking])

    def test_another_persons_key_links_nothing(self):
        plan, _ = self.plan(('fx_account_K', OTHER), lone=True)
        self.assertNotIn('fx_account_K', [r for r, _ in self.profile_steps(plan)])


if __name__ == '__main__':
    unittest.main()
