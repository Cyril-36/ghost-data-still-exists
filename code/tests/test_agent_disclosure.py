"""Agent-disclosed unresolved records, complete contract feedback and cited-remediation robustness."""
import copy
import json
import unittest

import helpers  # noqa: F401
import test_agent_first as scripted
from evaluation.fixtures import _lookups
from ghostaudit.audit import run_audit
from ghostaudit.output import read_output
from phase4_world import EMAIL, OTHER, T_CAPTURE, world

AUDIT = (T_CAPTURE, 'legal_service', 'fx_audit_P', 'LEGAL_HOLD_RECORD', 'FOUND', EMAIL)
ATTACH = (T_CAPTURE, 'support_platform', 'fx_attach_L', 'SUPPORT_ATTACHMENT', 'FOUND', EMAIL)
FOREIGN = (T_CAPTURE, 'legal_service', 'fx_audit_Q', 'LEGAL_HOLD_RECORD', 'FOUND', OTHER)
GONE = (T_CAPTURE, 'legal_service', 'fx_audit_N', 'LEGAL_HOLD_RECORD', 'NOT_FOUND', EMAIL)
SECURITY = (T_CAPTURE, 'security_service', 'fx_sec_P', 'SECURITY_AUDIT_LOG', 'FOUND', EMAIL)
UNTYPED = (T_CAPTURE, 'ledger_service', 'fx_misc_P', '', 'FOUND', EMAIL)


def undecided(*keys, reason='No legal hold period is supplied for this record.'):
    return [{'system_id': system, 'resource_id': rid, 'reason': reason} for system, rid in keys]


class DisclosedUnresolvedRecords(unittest.TestCase):
    def setUp(self):
        self.actor = scripted.AgentFirstTests()
        self.actor.setUp()
        self.addCleanup(self.actor.doCleanups)
        self.root = world(self.actor.base / 'retention', lookup_rows=[AUDIT])

    def submit(self, p, root=None, name='agent'):
        summary, row, _ = self.actor.run_agent(
            [self.actor.inspect('fx_file_L'), [scripted.call('submit_audit', p)]], root=root or self.root, name=name)
        trace = json.loads((self.actor.base / f'{name}/case_files.jsonl').read_text())
        codes = {f['code'] for f in trace['agent_proposals'][-1]['findings'] if f['severity'] == 'BLOCKING'}
        return summary, row, codes

    def test_undecidable_owned_record_can_be_disclosed_instead_of_deadlocking(self):
        p = scripted.proposal(self.root, 'fx_account_P', 'fx_file_L')
        p['policy_undecided'] = undecided(('legal_service', 'fx_audit_P'))
        summary, row, codes = self.submit(p)
        self.assertEqual((summary['fallback_count'], codes), (0, set()))
        self.assertEqual((row.deletion_status, {s.resource_id for s in row.steps}), ('INCOMPLETE', {'fx_account_P'}))
        plan = json.loads((self.actor.base / 'agent/case_plans.jsonl').read_text())
        self.assertEqual([(o['resource_id'], o['result']) for o in plan['obligations']], [('fx_audit_P', 'UNRESOLVED')])
        self.assertTrue(plan['obligations'][0]['issues'][0].startswith('AGENT_DISCLOSED_UNRESOLVED'))
        # The deterministic baseline publishes the same row for the same unresolved retention record.
        run_audit(self.root, self.root / 'deletion_requests.csv', self.actor.base / 'b.csv', self.actor.base / 'b')
        self.assertEqual(read_output(self.actor.base / 'b.csv')[0], row)

    def test_omission_without_disclosure_is_still_rejected(self):
        summary, row, codes = self.submit(scripted.proposal(self.root, 'fx_account_P', 'fx_file_L'))
        self.assertIn('UNPROVEN_POLICY', codes)
        self.assertEqual((summary['fallback_count'], row.steps), (1, ()))

    def test_disclosure_never_supports_complete_or_another_scope(self):
        p = scripted.proposal(self.root, 'fx_account_P', 'fx_file_L')
        p['deletion_status'] = 'COMPLETE'
        p['steps'] = [{**p['steps'][0], 'phase': 'VERIFICATION', 'operation': 'VERIFY_RECORD_ABSENT', 'state': 'ABSENT'}]
        p['policy_undecided'] = undecided(('legal_service', 'fx_audit_P'))
        item = scripted.proposal(self.root, 'fx_account_P', 'fx_file_L', scope='ITEM', target='USER_PROFILE')
        item['policy_undecided'] = undecided(('legal_service', 'fx_audit_P'))
        for name, proposal in (('complete', p), ('item', item)):
            with self.subTest(name):
                summary, row, codes = self.submit(proposal, name=name)
                self.assertIn('DISCLOSURE_UNSUPPORTED', codes)
                self.assertEqual((summary['fallback_count'], row.steps), (1, ()))

    def test_disclosure_cannot_skip_a_decidable_foreign_stepped_or_unseen_record(self):
        root = world(self.actor.base / 'mixed', lookup_rows=[AUDIT, ATTACH, FOREIGN, GONE, UNTYPED])
        audit = ('legal_service', 'fx_audit_P')
        cases = {'decidable': ([audit, ('support_platform', 'fx_attach_L')], 'DISCLOSURE_UNSUPPORTED'),
                 'foreign': ([audit, ('legal_service', 'fx_audit_Q')], 'DISCLOSURE_UNSUPPORTED'),
                 'stepped': ([audit], 'DISCLOSURE_UNSUPPORTED'),
                 'unseen': ([audit, ('legal_service', 'fx_audit_Z')], 'INVENTED_RESOURCE'),
                 'repeated': ([audit, audit], 'DISCLOSURE_UNSUPPORTED'),
                 'lookup_only': ([audit, ('legal_service', 'fx_audit_N')], 'DISCLOSURE_UNSUPPORTED'),
                 'untyped': ([audit, ('ledger_service', 'fx_misc_P')], 'DISCLOSURE_UNSUPPORTED')}
        for name, (keys, code) in cases.items():
            with self.subTest(name):
                p = scripted.proposal(root, 'fx_account_P', 'fx_file_L')
                attach = scripted.proposal(root, 'fx_attach_L', 'fx_file_L', system='support_platform')
                p['steps'] = [{**s, 'step_no': n} for n, s in enumerate(p['steps'] + attach['steps'], 1)]
                p['witnesses'] += [{**attach['witnesses'][0], 'step_no': 4}]
                if name == 'decidable':  # the attachment is disclosed instead of planned
                    p['steps'], p['witnesses'] = p['steps'][:3], p['witnesses'][:1]
                if name == 'stepped':  # the undecidable record is both planned and disclosed
                    p['steps'].append({**p['steps'][-1], 'step_no': 7, 'system_id': 'legal_service',
                                       'resource_id': 'fx_audit_P', 'operation': 'VERIFY_RECORD_PRESENT',
                                       'state': 'PRESENT'})
                p['policy_undecided'] = undecided(*keys)
                summary, row, codes = self.submit(p, root=root, name=name)
                self.assertIn(code, codes)
                self.assertEqual((summary['fallback_count'], row.steps), (1, ()))


class ContractFeedback(unittest.TestCase):
    def setUp(self):
        self.actor = scripted.AgentFirstTests()
        self.actor.setUp()
        self.addCleanup(self.actor.doCleanups)

    def findings(self, p, root=None, source='fx_file_A'):
        summary, row, _ = self.actor.run_agent([self.actor.inspect(source), [scripted.call('submit_audit', p)]],
                                               root=root)
        trace = json.loads((self.actor.base / 'agent/case_files.jsonl').read_text())
        return summary, row, [f for f in trace['agent_proposals'][0]['findings'] if f['code'] == 'INTEGRITY']

    def test_every_contract_problem_is_returned_in_one_round(self):
        root = world(self.actor.base / 'two', files={'evidence/records/second.json': (
            'fx_file_Z', T_CAPTURE, 'support_platform', _lookups(T_CAPTURE, ATTACH))})
        p = scripted.proposal(root, 'fx_account_P', 'fx_file_L')
        q = scripted.proposal(root, 'fx_attach_L', 'fx_file_Z', system='support_platform')
        p['steps'] = [{**s, 'step_no': n} for n, s in enumerate(p['steps'][:2] + q['steps'][:2], 1)]
        p['witnesses'] += [{**q['witnesses'][0], 'step_no': 3}]
        summary, row, _ = self.actor.run_agent([
            self.actor.inspect('fx_file_L') + self.actor.inspect('fx_file_Z'),
            [scripted.call('submit_audit', p)]], root=root)
        trace = json.loads((self.actor.base / 'agent/case_files.jsonl').read_text())
        messages = [f['message'] for f in trace['agent_proposals'][0]['findings'] if f['code'] == 'INTEGRITY']
        self.assertEqual(len(messages), 2, messages)
        self.assertTrue(any('fx_account_P' in m for m in messages) and any('fx_attach_L' in m for m in messages))
        self.assertEqual((summary['fallback_count'], row.steps), (1, ()))

    def test_a_cited_remediation_is_rejected_without_crashing_the_verifier(self):
        p = copy.deepcopy(self.actor.good)
        p['steps'][1]['evidence_id'] = 'fx_file_A'
        summary, row, integrity = self.findings(p)
        self.assertEqual([f['message'].split(': ', 1)[1] for f in integrity],
                         ['proposed DELETE_RECORD fx_account_A must have empty evidence_id'])
        self.assertEqual((summary['fallback_count'], row.steps), (1, ()))



class PolicyTreatmentAffordances(unittest.TestCase):
    def setUp(self):
        self.actor = scripted.AgentFirstTests()
        self.actor.setUp()
        self.addCleanup(self.actor.doCleanups)

    def test_brief_states_each_supplied_category_chain_as_the_verifier_applies_it(self):
        root = world(self.actor.base / 'chains', lookup_rows=[SECURITY])
        p = scripted.proposal(root, 'fx_account_P', 'fx_file_L')
        p['policy_undecided'] = undecided(('security_service', 'fx_sec_P'))
        summary, row, received = self.actor.run_agent(
            [self.actor.inspect('fx_file_L'), [scripted.call('submit_audit', p)]], root=root)
        policy = json.loads(received[0]['messages'][1]['content'])['policy']
        brief = [r['verifier_treatment'] for r in policy['rules']] + [policy['flows']]
        security = next(c for c in brief if c.startswith('SECURITY_AUDIT_LOG'))
        self.assertTrue(security.startswith('SECURITY_AUDIT_LOG (RETAIN): VERIFY_RECORD_PRESENT only, never a finding'))
        self.assertIn('without a supplied retention period', security)
        self.assertIn('USER_PROFILE (DELETE): REPORT_REMAINING_RECORD -> DELETE_RECORD -> VERIFY_RECORD_ABSENT', brief)
        self.assertTrue(any(c.startswith('LEGAL_HOLD_RECORD: undecidable') for c in brief))
        trace = json.loads((self.actor.base / 'agent/case_files.jsonl').read_text())
        rejected = [f['message'] for f in trace['agent_proposals'][0]['findings'] if f['code'] == 'DISCLOSURE_UNSUPPORTED']
        self.assertIn('VERIFY_RECORD_PRESENT only', rejected[0])     # a decidable record names its treatment
        self.assertEqual((summary['fallback_count'], row.steps), (1, ()))

    def test_a_finding_without_remediation_is_told_the_whole_chain(self):
        p = copy.deepcopy(self.actor.good)
        p['steps'] = p['steps'][:1]
        self.actor.run_agent([self.actor.inspect(), [scripted.call('submit_audit', p)]])
        trace = json.loads((self.actor.base / 'agent/case_files.jsonl').read_text())
        message = next(f['message'] for f in trace['agent_proposals'][0]['findings'] if f['code'] == 'INTEGRITY')
        self.assertIn('REPORT_REMAINING_RECORD -> DELETE_RECORD -> VERIFY_RECORD_ABSENT', message)


if __name__ == '__main__':
    unittest.main()
