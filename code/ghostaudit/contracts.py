"""Typed records and enumerations for the official challenge contract.

Everything here is copied from problem_statement.md / AGENTS.md; nothing is
derived from sample answers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone


class ContractError(ValueError):
    """Input or output violates the challenge contract."""


# --- Input headers (exact order) -------------------------------------------------

REQUEST_HEADER = ('case_id', 'email', 'requested_at', 'reported_status',
                  'reported_at', 'audit_at', 'request_text')
IDENTITY_HEADER = ('user_id', 'name', 'email', 'phone', 'email_hash', 'updated_at')
AUDIT_HEADER = ('audit_id', 'user_id', 'event_time', 'field_name', 'old_value', 'new_value')
ACTIVITY_HEADER = ('activity_id', 'event_time', 'event_type', 'source_system',
                   'target_system', 'resource_id', 'reported_result', 'details')
MANIFEST_HEADER = ('file_id', 'file_path', 'origin', 'captured_at')
RULE_HEADER = ('rule_id', 'data_category', 'condition', 'required_action')

REPORTED_STATUSES = frozenset({'COMPLETE', 'PARTIALLY_COMPLETE', 'NOT_STARTED'})
REPORTED_RESULTS = frozenset({'SUCCESS', 'FAILED', 'PARTIAL'})
RULE_ACTIONS = frozenset({'DELETE', 'REDACT', 'ANONYMIZE', 'RETAIN'})
AUDITED_FIELDS = frozenset({'name', 'email', 'phone', 'email_hash'})

# --- Output contract ---------------------------------------------------------------

OUTPUT_HEADER = ('case_id', 'deletion_status', 'steps_json')
STATUSES = frozenset({'COMPLETE', 'INCOMPLETE', 'NOT_APPLICABLE'})
STEP_FIELDS = ('step_no', 'phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id')
PHASES = ('FINDING', 'REMEDIATION', 'VERIFICATION')

# Allowed (phase, operation, state) in the official table order. Position within a
# phase is the canonical sort rank.
OPERATION_TABLE = (
    ('FINDING', 'REPORT_REMAINING_RECORD', 'PRESENT'),
    ('FINDING', 'REPORT_EXPOSED_PII', 'PRESENT'),
    ('FINDING', 'REPORT_ACTIVE_DATA_FLOW', 'ACTIVE'),
    ('FINDING', 'REPORT_RESTORE_RISK', 'ACTIVE'),
    ('REMEDIATION', 'DISABLE_DATA_FLOW', 'DISABLED'),
    ('REMEDIATION', 'BLOCK_RESTORE', 'BLOCKED'),
    ('REMEDIATION', 'DELETE_RECORD', 'ABSENT'),
    ('REMEDIATION', 'REDACT_RECORD', 'REDACTED'),
    ('REMEDIATION', 'ANONYMIZE_RECORD', 'ANONYMIZED'),
    ('VERIFICATION', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
    ('VERIFICATION', 'VERIFY_RECORD_PRESENT', 'PRESENT'),
    ('VERIFICATION', 'VERIFY_RECORD_REDACTED', 'REDACTED'),
    ('VERIFICATION', 'VERIFY_RECORD_ANONYMIZED', 'ANONYMIZED'),
    ('VERIFICATION', 'VERIFY_DATA_FLOW_DISABLED', 'DISABLED'),
    ('VERIFICATION', 'VERIFY_RESTORE_BLOCKED', 'BLOCKED'),
)
OPERATION_PHASE = {op: phase for phase, op, _ in OPERATION_TABLE}
OPERATION_STATE = {op: state for _, op, state in OPERATION_TABLE}
OPERATION_RANK = {
    op: (PHASES.index(phase), [o for p, o, _ in OPERATION_TABLE if p == phase].index(op))
    for phase, op, _ in OPERATION_TABLE
}

# Finding -> permitted remediation(s) -> final verification (plan section 16.3).
FINDING_REMEDIATIONS = {
    'REPORT_REMAINING_RECORD': ('DELETE_RECORD',),
    'REPORT_EXPOSED_PII': ('REDACT_RECORD', 'ANONYMIZE_RECORD'),
    'REPORT_ACTIVE_DATA_FLOW': ('DISABLE_DATA_FLOW',),
    'REPORT_RESTORE_RISK': ('BLOCK_RESTORE',),
}
REMEDIATION_VERIFICATION = {
    'DELETE_RECORD': 'VERIFY_RECORD_ABSENT',
    'REDACT_RECORD': 'VERIFY_RECORD_REDACTED',
    'ANONYMIZE_RECORD': 'VERIFY_RECORD_ANONYMIZED',
    'DISABLE_DATA_FLOW': 'VERIFY_DATA_FLOW_DISABLED',
    'BLOCK_RESTORE': 'VERIFY_RESTORE_BLOCKED',
}

# Each operation acts on one facet of a resource. Two different steps for the same
# (phase, system, resource, facet) contradict each other (e.g. DELETE and REDACT);
# steps on different facets can coexist (a retained backup whose restore is blocked).
FACET_OPERATIONS = {
    'record': ('REPORT_REMAINING_RECORD', 'REPORT_EXPOSED_PII', 'DELETE_RECORD', 'REDACT_RECORD',
               'ANONYMIZE_RECORD', 'VERIFY_RECORD_ABSENT', 'VERIFY_RECORD_PRESENT', 'VERIFY_RECORD_REDACTED',
               'VERIFY_RECORD_ANONYMIZED'),
    'flow': ('REPORT_ACTIVE_DATA_FLOW', 'DISABLE_DATA_FLOW', 'VERIFY_DATA_FLOW_DISABLED'),
    'restore': ('REPORT_RESTORE_RISK', 'BLOCK_RESTORE', 'VERIFY_RESTORE_BLOCKED'),
}
OPERATION_FACET = {op: facet for facet, ops in FACET_OPERATIONS.items() for op in ops}

# --- Timestamps ----------------------------------------------------------------------

_TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})')


def parse_utc(text: str, what: str = 'timestamp') -> datetime:
    """Parse an ISO-8601 instant with an explicit offset; reject naive or partial values."""
    if not isinstance(text, str) or not _TIMESTAMP.fullmatch(text):
        raise ContractError(f'{what}: expected ISO-8601 timestamp with zone, got {text!r}')
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(timezone.utc)
    except ValueError as exc:
        raise ContractError(f'{what}: invalid timestamp {text!r}') from exc


# --- Typed input records ---------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Request:
    case_id: str
    email: str
    requested_at: datetime
    reported_status: str  # a workflow claim, never a label
    reported_at: datetime
    audit_at: datetime
    request_text: str


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class IdentityChange:
    audit_id: str
    user_id: str
    event_time: datetime
    field_name: str
    old_value: str
    new_value: str


@dataclass(frozen=True, slots=True)
class Activity:
    activity_id: str
    event_time: datetime
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str  # a claim about work, not proof of resulting state
    details: str


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    file_id: str
    file_path: str  # relative to the dataset root
    origin: str
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    data_category: str
    condition: str
    required_action: str


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """A citable participant-visible source and when it became available."""
    source_id: str
    kind: str  # FILE | ACTIVITY | IDENTITY_AUDIT
    available_at: datetime


# --- Typed output records --------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Step:
    """One output step without its number; the serializer assigns step_no."""
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str

    def __post_init__(self):
        for name in ('phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id'):
            if not isinstance(getattr(self, name), str):
                raise ContractError(f'step field {name} must be a string')
        if not self.system_id or not self.resource_id:
            raise ContractError('step system_id and resource_id must be non-empty')
        for name in ('system_id', 'resource_id', 'evidence_id'):
            value = getattr(self, name)
            if value != value.strip():
                raise ContractError(f'step {name} has surrounding whitespace: {value!r}')
        if OPERATION_PHASE.get(self.operation) != self.phase or OPERATION_STATE[self.operation] != self.state:
            raise ContractError(f'illegal step tuple ({self.phase}, {self.operation}, {self.state})')

    @property
    def facet(self) -> str:
        return OPERATION_FACET[self.operation]

    @property
    def sort_key(self):
        return (*OPERATION_RANK[self.operation], self.system_id, self.resource_id)


@dataclass(frozen=True, slots=True)
class OutputRow:
    case_id: str
    deletion_status: str
    steps: tuple[Step, ...]
