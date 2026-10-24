"""Shared test setup: put code/ on sys.path and build small valid objects."""
import sys
from datetime import datetime, timezone
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
REPO = CODE.parent
DATASET = REPO / 'dataset'
if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))

from ghostaudit.contracts import EvidenceRef, OutputRow, Request, Step  # noqa: E402

AUDIT_AT = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)


def request(case_id, audit_at=AUDIT_AT):
    return Request(case_id, f'{case_id}@example.test', AUDIT_AT, 'COMPLETE', AUDIT_AT, audit_at, 'Delete my account.')


def evidence(*ids, at=datetime(2026, 2, 1, tzinfo=timezone.utc)):
    return {i: EvidenceRef(i, 'FILE', at) for i in ids}


def step(phase, operation, state, resource='fx_r1', system='fx_sys', evidence_id=''):
    return Step(phase, system, resource, operation, state, evidence_id)


def incomplete_row(case_id='case_33'):
    """Valid INCOMPLETE row: one residual record plus an already-compliant retained record."""
    return OutputRow(case_id, 'INCOMPLETE', (
        step('FINDING', 'REPORT_REMAINING_RECORD', 'PRESENT', evidence_id='fx_ev1'),
        step('REMEDIATION', 'DELETE_RECORD', 'ABSENT'),
        step('VERIFICATION', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
        step('VERIFICATION', 'VERIFY_RECORD_PRESENT', 'PRESENT', resource='fx_r2'),
    ))


def complete_row(case_id='case_34'):
    return OutputRow(case_id, 'COMPLETE', (
        step('VERIFICATION', 'VERIFY_RECORD_ABSENT', 'ABSENT', evidence_id='fx_ev1'),))


def not_applicable_row(case_id='case_100'):
    return OutputRow(case_id, 'NOT_APPLICABLE', ())
