"""Phase 4 milestone 1: read-only investigation tools and the validated case file."""
import tempfile
import unittest

import helpers  # noqa: F401
from evaluation.fixtures import _sql
from ghostaudit.casefile import CaseFile, FactValidator, fact_observations
from ghostaudit.corpus import build_corpus
from ghostaudit.identity import IdentityIndex
from ghostaudit.inputs import load_context, load_requests
from ghostaudit.intent import classify
from ghostaudit.planner import VIOLATED, plan_case
from ghostaudit.policy import Policy
from ghostaudit.tools import InvestigationTools, SourceText, ToolError, Tools
from phase4_world import EMAIL, OTHER, T_CAPTURE, world
from test_media import make_pdf

LATE = '2026-03-05T10:00:00Z'      # after T_AUDIT


def setup_case(tmp, **kw):
    root = world(tmp, **kw)
    context = load_context(root)
    corpus, policy, index = build_corpus(context), Policy(context.rules), IdentityIndex(context)
    request = load_requests(root / 'deletion_requests.csv')[0]
    plan = plan_case(request, corpus, policy, index, classify(request.request_text))
    case = CaseFile.from_plan(plan)
    texts = SourceText(context)
    return dict(context=context, corpus=corpus, policy=policy, index=index, request=request, plan=plan, case=case,
                texts=texts, tools=InvestigationTools(Tools(corpus, index, policy), texts, case),
                validator=FactValidator(corpus, index, policy, texts))


def ownership(**kw):
    return {'kind': 'OWNERSHIP', 'system_id': 'support_platform', 'resource_id': 'fx_attach_P',
            'source_id': 'fx_file_PDF', 'locator': 'p1:L1-L3',
            'literal': f'SUPPORT CASE CS-1 / fx_attach_P Detail Fixture Person P Detail {EMAIL}', 'value': '', **kw}


class ToolTests(unittest.TestCase):
    def test_search_is_scoped_to_the_subject_and_the_cutoff(self):
        late_pdf = make_pdf([[f'Late record fx_attach_late {EMAIL}']])
        with tempfile.TemporaryDirectory() as tmp:
            s = setup_case(tmp, files={'evidence/documents/late.pdf': ('fx_file_LATE', LATE, 'support_platform',
                                                                       late_pdf)})
            out = s['tools'].call('search_identifiers', {'values': [EMAIL]})
            self.assertEqual({m['source_id'] for m in out['matches']}, {'fx_file_L', 'fx_file_PDF'})
            self.assertNotIn('fx_attach_P', s['case'].handles)    # not on a matching line: not yet shown
            s['tools'].call('inspect_media', {'file_id': 'fx_file_PDF'})
            self.assertIn('fx_attach_P', s['case'].handles)       # shown verbatim -> a handle
            self.assertNotIn('fx_attach_late', s['case'].handles)
            for values in ([OTHER], ['fx_unknown_9'], []):
                with self.subTest(values=values), self.assertRaises(ToolError):
                    s['tools'].call('search_identifiers', {'values': values})
            with self.assertRaisesRegex(ToolError, 'after audit_at'):
                s['tools'].call('inspect_source', {'source_id': 'fx_file_LATE'})

    def test_inspect_source_and_media_are_bounded_and_handle_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = setup_case(tmp)
            out = s['tools'].call('inspect_source', {'source_id': 'fx_file_L', 'start_line': 1, 'max_lines': 3})
            self.assertEqual((len(out['lines']), out['truncated']), (3, True))
            media = s['tools'].call('inspect_media', {'file_id': 'fx_file_PDF', 'page': 'p1'})
            self.assertIn(EMAIL, media['identifiers'])
            self.assertEqual(media['lines'][0]['locator'], 'p1:L1')
            for name, args in (('inspect_source', {'source_id': '../../etc/passwd'}),
                               ('inspect_media', {'file_id': 'fx_file_L'}),
                               ('inspect_media', {'file_id': 'fx_file_PDF', 'page': 'p9'}),
                               ('activity_timeline', {'resource_id': 'fx_never_seen_1'}),
                               ('drop_table', {})):
                with self.subTest(name=name), self.assertRaises(ToolError):
                    s['tools'].call(name, args)

    def test_sql_queries_are_allowlisted_parameter_lookups(self):
        sql = _sql(T_CAPTURE, {'profiles': [('fx_account_P', 'account_service', 'profiles', {'email': EMAIL})]})
        with tempfile.TemporaryDirectory() as tmp:
            s = setup_case(tmp, files={'evidence/databases/snap.sql': ('fx_file_SQL', T_CAPTURE, 'db', sql)})
            out = s['tools'].call('query_sql_snapshot', {'file_id': 'fx_file_SQL', 'table': 'profiles',
                                                         'column': 'resource_id', 'value': 'fx_account_P'})
            self.assertEqual((out['returned'], out['items'][0]['locator']), (1, 'L6'))
            for args in ({'table': 'profiles; DROP TABLE profiles', 'column': 'resource_id', 'value': 'fx_account_P'},
                         {'table': 'profiles', 'column': 'nope', 'value': 'fx_account_P'},
                         {'table': 'profiles', 'column': 'resource_id', 'value': OTHER}):
                with self.subTest(args=args), self.assertRaises(ToolError):
                    s['tools'].call('query_sql_snapshot', {'file_id': 'fx_file_SQL', **args})


class FactValidationTests(unittest.TestCase):
    def inspected(self, tmp, **kw):
        s = setup_case(tmp, **kw)
        s['tools'].call('inspect_media', {'file_id': 'fx_file_PDF', 'page': 'p1'})
        return s

    def test_rejections(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self.inspected(tmp)
            s['case'].handles.add('fx_attach_P1')
            cases = {
                'INVENTED_RESOURCE': ownership(resource_id='fx_attach_X'),
                'RESOURCE_NOT_IN_LITERAL': ownership(resource_id='fx_attach_P1'),       # prefix-similar
                'INVENTED_CITATION': ownership(source_id='evidence_999'),
                'UNINSPECTED_SOURCE': ownership(source_id='fx_file_L'),
                'LITERAL_NOT_IN_SOURCE': ownership(literal='fx_attach_P belongs to ' + EMAIL),
                'BAD_LOCATOR': ownership(locator='p1:L1-L40'),
                'UNKNOWN_SYSTEM': ownership(system_id='made_up_service'),
                'NO_SUBJECT_IDENTIFIER': ownership(locator='p1:L1', literal='SUPPORT CASE CS-1 / fx_attach_P'),
                'UNSUPPORTED_STATE': ownership(kind='STATE', value='ABSENT', locator='p1:L1',
                                               literal='SUPPORT CASE CS-1 / fx_attach_P'),
                'UNSUPPORTED_CATEGORY': ownership(kind='CATEGORY', value='SUPPORT_ATTACHMENT', locator='p1:L1',
                                                  literal='SUPPORT CASE CS-1 / fx_attach_P'),
                'UNSUPPORTED_RETENTION': ownership(kind='RETENTION', value='2031-01-01', locator='p1:L1',
                                                   literal='SUPPORT CASE CS-1 / fx_attach_P'),
                'MALFORMED': {'kind': 'OWNERSHIP'},
            }
            for code, proposal in cases.items():
                with self.subTest(code):
                    fact, reason = s['validator'].validate(proposal, s['case'])
                    self.assertIsNone(fact)
                    self.assertTrue(reason.startswith(code), reason)

    def test_post_cutoff_and_cross_person_sources_are_refused(self):
        shared = make_pdf([[f'Ticket fx_attach_S {EMAIL}', f'cc {OTHER}']])
        with tempfile.TemporaryDirectory() as tmp:
            s = setup_case(tmp, files={'evidence/documents/shared.pdf': ('fx_file_SH', T_CAPTURE, 'support_platform',
                                                                         shared)})
            s['tools'].call('inspect_media', {'file_id': 'fx_file_SH'})
            fact, reason = s['validator'].validate(ownership(resource_id='fx_attach_S', source_id='fx_file_SH',
                                                             locator='p1:L1-L2', literal=f'fx_attach_S {EMAIL}'),
                                                   s['case'])
            self.assertTrue(reason.startswith('CROSS_PERSON'), reason)
            s['case'].inspected.add('fx_file_LATE')
            fact, reason = s['validator'].validate(ownership(source_id='fx_file_LATE'), s['case'])
            self.assertTrue(reason.startswith('INVENTED_CITATION'), reason)

    def test_accepted_facts_flow_into_the_obligation_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self.inspected(tmp)
            proposals = [ownership(),
                         ownership(kind='STATE', value='PRESENT', locator='p1:L4',
                                   literal='uploaded attachment identity_scan.pdf stored for ticket CS-1 '),
                         ownership(kind='CATEGORY', value='SUPPORT_ATTACHMENT', locator='p1:L4',
                                   literal='uploaded attachment identity_scan.pdf')]
            facts = []
            for p in proposals:
                fact, reason = s['validator'].validate(p, s['case'])
                if p['kind'] == 'STATE':                 # the state line names no resource: refused
                    self.assertTrue(reason.startswith('RESOURCE_NOT_IN_LITERAL'), reason)
                    continue
                self.assertIsNotNone(fact, reason)
                facts.append(fact)
            state, reason = s['validator'].validate(ownership(kind='STATE', value='PRESENT', locator='p1:L1-L4',
                                                              literal='fx_attach_P Detail Fixture Person P Detail '
                                                              f'{EMAIL} Detail uploaded attachment'), s['case'])
            self.assertIsNotNone(state, reason)
            corpus = s['corpus'].extended(fact_observations(facts + [state]))
            plan = plan_case(s['request'], corpus, s['policy'], s['index'], classify(s['request'].request_text))
            attach = [o for o in plan.obligations if o.resource_id == 'fx_attach_P']
            self.assertEqual([(o.category, o.result, o.proof.source_id) for o in attach],
                             [('SUPPORT_ATTACHMENT', VIOLATED, 'fx_file_PDF')])
            self.assertNotIn('fx_attach_P', {o.resource_id for o in s['plan'].obligations})   # Phase 3 missed it


if __name__ == '__main__':
    unittest.main()
