import unittest
from datetime import datetime, timezone

import helpers  # noqa: F401  (sets sys.path)
from ghostaudit.contracts import OPERATION_RANK, OPERATION_TABLE, ContractError, Step, parse_utc


class OperationTableTests(unittest.TestCase):
    def test_matches_official_table_exactly(self):
        # Transcribed from problem_statement.md "Allowed Operations", in order.
        official = """FINDING REPORT_REMAINING_RECORD PRESENT
FINDING REPORT_EXPOSED_PII PRESENT
FINDING REPORT_ACTIVE_DATA_FLOW ACTIVE
FINDING REPORT_RESTORE_RISK ACTIVE
REMEDIATION DISABLE_DATA_FLOW DISABLED
REMEDIATION BLOCK_RESTORE BLOCKED
REMEDIATION DELETE_RECORD ABSENT
REMEDIATION REDACT_RECORD REDACTED
REMEDIATION ANONYMIZE_RECORD ANONYMIZED
VERIFICATION VERIFY_RECORD_ABSENT ABSENT
VERIFICATION VERIFY_RECORD_PRESENT PRESENT
VERIFICATION VERIFY_RECORD_REDACTED REDACTED
VERIFICATION VERIFY_RECORD_ANONYMIZED ANONYMIZED
VERIFICATION VERIFY_DATA_FLOW_DISABLED DISABLED
VERIFICATION VERIFY_RESTORE_BLOCKED BLOCKED"""
        self.assertEqual(OPERATION_TABLE, tuple(tuple(line.split()) for line in official.splitlines()))

    def test_rank_follows_table_order(self):
        ranks = [OPERATION_RANK[op] for _, op, _ in OPERATION_TABLE]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(OPERATION_RANK['DISABLE_DATA_FLOW'], (1, 0))
        self.assertEqual(OPERATION_RANK['VERIFY_RESTORE_BLOCKED'], (2, 5))


class TimestampTests(unittest.TestCase):
    def test_accepts_zoned_instants(self):
        self.assertEqual(parse_utc('2026-01-03T12:00:00Z'), datetime(2026, 1, 3, 12, tzinfo=timezone.utc))
        self.assertEqual(parse_utc('2026-01-03T17:30:00+05:30'), datetime(2026, 1, 3, 12, tzinfo=timezone.utc))

    def test_rejects_naive_partial_or_invalid(self):
        for bad in ['2026-01-03T12:00:00', '2026-01-03', '', 'nan', '2026-13-03T12:00:00Z', ' 2026-01-03T12:00:00Z']:
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                parse_utc(bad)


class StepTests(unittest.TestCase):
    def test_valid_step(self):
        Step('FINDING', 'account_service', 'account_005', 'REPORT_REMAINING_RECORD', 'PRESENT', 'evidence_01')

    def test_rejects_illegal_values(self):
        cases = {
            'wrong state': ('FINDING', 's', 'r', 'REPORT_REMAINING_RECORD', 'ABSENT', 'e'),
            'wrong phase': ('VERIFICATION', 's', 'r', 'DELETE_RECORD', 'ABSENT', ''),
            'unknown operation': ('FINDING', 's', 'r', 'REPORT_EVERYTHING', 'PRESENT', 'e'),
            'unknown phase': ('CLEANUP', 's', 'r', 'DELETE_RECORD', 'ABSENT', ''),
            'empty system': ('REMEDIATION', '', 'r', 'DELETE_RECORD', 'ABSENT', ''),
            'empty resource': ('REMEDIATION', 's', '', 'DELETE_RECORD', 'ABSENT', ''),
            'padded resource': ('REMEDIATION', 's', 'r ', 'DELETE_RECORD', 'ABSENT', ''),
            'non-string evidence': ('REMEDIATION', 's', 'r', 'DELETE_RECORD', 'ABSENT', None),
            'list operation': ('REMEDIATION', 's', 'r', ['DELETE_RECORD'], 'ABSENT', ''),
        }
        for name, args in cases.items():
            with self.subTest(name), self.assertRaises(ContractError):
                Step(*args)


if __name__ == '__main__':
    unittest.main()
