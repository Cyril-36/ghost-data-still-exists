import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import helpers
from helpers import complete_row, evidence, incomplete_row, not_applicable_row, request, step
from ghostaudit.contracts import ContractError, OutputRow
from ghostaudit.inputs import load_context, load_requests
from ghostaudit.output import (
    canonical_steps, check_row, parse_steps_json, read_output, steps_to_json, validate_output, write_output,
)

EV = evidence('fx_ev1')


def check(row, ev=EV):
    check_row(row, request(row.case_id), ev)


class SerializationTests(unittest.TestCase):
    def test_round_trip_canonicalizes_steps_and_rows(self):
        rows = [not_applicable_row('case_100'), complete_row('case_34'), incomplete_row('case_33')]
        shuffled = [replace(rows[2], steps=tuple(reversed(rows[2].steps))), rows[0], rows[1]]
        requests = [request(r.case_id) for r in rows]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'out.csv'
            write_output(path, shuffled, requests, EV)
            back = read_output(path)
            text = path.read_text(encoding='utf-8')
        self.assertEqual([r.case_id for r in back], ['case_33', 'case_34', 'case_100'])  # numeric, not lexical
        self.assertEqual(back[0], incomplete_row('case_33'))
        self.assertTrue(text.startswith('case_id,deletion_status,steps_json\n'))
        self.assertIn('case_100,NOT_APPLICABLE,[]\n', text)

    def test_step_numbers_are_assigned_consecutively(self):
        objs = json.loads(steps_to_json(incomplete_row().steps))
        self.assertEqual([o['step_no'] for o in objs], [1, 2, 3, 4])
        self.assertEqual(list(objs[0]), ['step_no', 'phase', 'system_id', 'resource_id',
                                         'operation', 'state', 'evidence_id'])

    def test_canonical_order_is_phase_operation_system_resource(self):
        steps = [step('VERIFICATION', 'VERIFY_RESTORE_BLOCKED', 'BLOCKED', resource='a'),
                 step('VERIFICATION', 'VERIFY_RECORD_ABSENT', 'ABSENT', resource='b', system='z_sys'),
                 step('VERIFICATION', 'VERIFY_RECORD_ABSENT', 'ABSENT', resource='c', system='a_sys'),
                 step('REMEDIATION', 'DELETE_RECORD', 'ABSENT', resource='q'),
                 step('REMEDIATION', 'DISABLE_DATA_FLOW', 'DISABLED', resource='z'),
                 step('FINDING', 'REPORT_RESTORE_RISK', 'ACTIVE', resource='a', evidence_id='e')]
        ordered = canonical_steps(steps)
        self.assertEqual([(s.operation, s.system_id) for s in ordered], [
            ('REPORT_RESTORE_RISK', 'fx_sys'), ('DISABLE_DATA_FLOW', 'fx_sys'), ('DELETE_RECORD', 'fx_sys'),
            ('VERIFY_RECORD_ABSENT', 'a_sys'), ('VERIFY_RECORD_ABSENT', 'z_sys'), ('VERIFY_RESTORE_BLOCKED', 'fx_sys')])

    def test_identical_duplicates_merge_and_conflicts_fail(self):
        s = step('REMEDIATION', 'DELETE_RECORD', 'ABSENT')
        self.assertEqual(canonical_steps([s, s]), (s,))
        with self.assertRaises(ContractError):
            canonical_steps([s, step('REMEDIATION', 'REDACT_RECORD', 'REDACTED')])

    def test_failed_write_keeps_previous_file_and_leaves_no_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'out.csv'
            path.write_text('previous artifact', encoding='utf-8')
            bad = incomplete_row()
            bad = replace(bad, steps=(replace(bad.steps[0], evidence_id='invented_id'),) + bad.steps[1:])
            with self.assertRaises(ContractError):
                write_output(path, [bad], [request('case_33')], EV)
            self.assertEqual(path.read_text(encoding='utf-8'), 'previous artifact')
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ['out.csv'])


class StepsJsonParsingTests(unittest.TestCase):
    VALID = steps_to_json(incomplete_row().steps)

    def mutate(self, fn):
        data = json.loads(self.VALID)
        fn(data)
        return json.dumps(data)

    def test_valid(self):
        self.assertEqual(parse_steps_json(self.VALID), incomplete_row().steps)
        self.assertEqual(parse_steps_json('[]'), ())

    def test_rejections(self):
        cases = {
            'extra field': self.mutate(lambda d: d[0].update(reason='x')),
            'missing field': self.mutate(lambda d: d[0].pop('state')),
            'bool step_no': self.mutate(lambda d: d[0].update(step_no=True)),
            'string step_no': self.mutate(lambda d: d[0].update(step_no='1')),
            'step_no gap': self.mutate(lambda d: d[1].update(step_no=3)),
            'starts at zero': self.mutate(lambda d: [s.update(step_no=i) for i, s in enumerate(d)]),
            'illegal tuple': self.mutate(lambda d: d[0].update(state='ABSENT')),
            'numeric evidence': self.mutate(lambda d: d[0].update(evidence_id=7)),
            'object not array': '{"step_no":1}',
            'array of strings': '["step"]',
            'duplicate key': '[{"step_no":1,"step_no":1}]',
            'NaN constant': '[NaN]',
            'not json': '[{]',
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(ContractError):
                parse_steps_json(text)


class RowRuleTests(unittest.TestCase):
    def test_valid_rows(self):
        for row in (incomplete_row(), complete_row(), not_applicable_row()):
            check(row)

    def test_status_step_combinations(self):
        inc, comp = incomplete_row(), complete_row()
        cases = {
            'unknown status': replace(comp, deletion_status='DONE'),
            'NOT_APPLICABLE with steps': replace(comp, deletion_status='NOT_APPLICABLE'),
            'COMPLETE with no steps': replace(comp, steps=()),
            'COMPLETE with finding': replace(inc, deletion_status='COMPLETE'),
            'INCOMPLETE without finding': replace(comp, deletion_status='INCOMPLETE'),
            'INCOMPLETE with empty array': replace(inc, steps=()),
        }
        for name, row in cases.items():
            with self.subTest(name), self.assertRaises(ContractError):
                check(row)

    def test_citation_rules(self):
        inc, comp = incomplete_row(), complete_row()
        late = evidence('fx_ev1', at=datetime(2026, 3, 1, 12, 0, 1, tzinfo=timezone.utc))
        cases = {
            'finding without evidence': (replace(inc, steps=(replace(inc.steps[0], evidence_id=''),) + inc.steps[1:]), EV),
            'remediation with evidence': (replace(inc, steps=inc.steps[:1] + (replace(inc.steps[1], evidence_id='fx_ev1'),) + inc.steps[2:]), EV),
            'future verification with evidence': (replace(inc, steps=inc.steps[:2] + (replace(inc.steps[2], evidence_id='fx_ev1'),) + inc.steps[3:]), EV),
            'COMPLETE verification without evidence': (replace(comp, steps=(replace(comp.steps[0], evidence_id=''),)), EV),
            'unknown evidence id': (comp, evidence('other')),
            'evidence after audit_at': (comp, late),
        }
        for name, (row, ev) in cases.items():
            with self.subTest(name), self.assertRaises(ContractError):
                check(row, ev)

    def test_evidence_exactly_at_audit_at_is_eligible(self):
        check(complete_row(), evidence('fx_ev1', at=helpers.AUDIT_AT))

    def test_plan_chain_consistency(self):
        f = step('FINDING', 'REPORT_REMAINING_RECORD', 'PRESENT', evidence_id='fx_ev1')
        d = step('REMEDIATION', 'DELETE_RECORD', 'ABSENT')
        v = step('VERIFICATION', 'VERIFY_RECORD_ABSENT', 'ABSENT')
        cases = {
            'finding without remediation': (f, v),
            'remediation without verification': (f, d),
            'wrong remediation for finding': (f, step('REMEDIATION', 'REDACT_RECORD', 'REDACTED'),
                                              step('VERIFICATION', 'VERIFY_RECORD_REDACTED', 'REDACTED')),
            'wrong verification for remediation': (f, d, step('VERIFICATION', 'VERIFY_RECORD_PRESENT', 'PRESENT')),
            'remediation without finding': (f, d, v, step('REMEDIATION', 'DELETE_RECORD', 'ABSENT', resource='fx_r9'),
                                            step('VERIFICATION', 'VERIFY_RECORD_ABSENT', 'ABSENT', resource='fx_r9')),
            'non-canonical order': (d, f, v),
            'duplicate step': (f, d, v, v),
        }
        for name, steps in cases.items():
            with self.subTest(name), self.assertRaises(ContractError):
                check(OutputRow('case_33', 'INCOMPLETE', tuple(steps)))

    def test_exposed_pii_accepts_redact_or_anonymize(self):
        for remedy, verify, state in [('REDACT_RECORD', 'VERIFY_RECORD_REDACTED', 'REDACTED'),
                                      ('ANONYMIZE_RECORD', 'VERIFY_RECORD_ANONYMIZED', 'ANONYMIZED')]:
            check(OutputRow('case_33', 'INCOMPLETE', (
                step('FINDING', 'REPORT_EXPOSED_PII', 'PRESENT', evidence_id='fx_ev1'),
                step('REMEDIATION', remedy, state), step('VERIFICATION', verify, state))))


class CaseSetTests(unittest.TestCase):
    REQUESTS = [request('case_33'), request('case_34'), request('case_100')]
    ROWS = [incomplete_row('case_33'), complete_row('case_34'), not_applicable_row('case_100')]

    def test_valid_set(self):
        validate_output(self.ROWS, self.REQUESTS, EV)

    def test_rejections(self):
        cases = {
            'missing case': self.ROWS[:2],
            'duplicate case': self.ROWS + [self.ROWS[2]],
            'unexpected case': self.ROWS + [not_applicable_row('case_999')],
            'lexical order': [self.ROWS[2], self.ROWS[0], self.ROWS[1]],
        }
        for name, rows in cases.items():
            with self.subTest(name), self.assertRaises(ContractError):
                validate_output(rows, self.REQUESTS, EV)


@unittest.skipUnless((helpers.DATASET / 'sample_output.csv').exists(), 'supplied dataset not present')
class OfficialSampleFormatTests(unittest.TestCase):
    """The validator must accept the organizer's own answers (format check, not inference)."""

    def test_samples_pass_strict_validation_and_are_already_canonical(self):
        requests = load_requests(helpers.DATASET / 'sample_deletion_requests.csv')
        rows = read_output(helpers.DATASET / 'sample_output.csv')
        validate_output(rows, requests, load_context(helpers.DATASET).evidence)
        for row in rows:
            self.assertEqual(canonical_steps(row.steps), row.steps, row.case_id)
        with tempfile.TemporaryDirectory() as tmp:
            rewritten = write_output(Path(tmp) / 'samples.csv', rows, requests)
        self.assertEqual(rewritten, rows)


if __name__ == '__main__':
    unittest.main()
