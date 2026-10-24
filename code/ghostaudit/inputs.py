"""Strict loaders for the participant-facing CSV inputs.

Only the five named context files plus an explicitly supplied request file are
opened. The loader never scans directories, so labels or expected outputs that
sit next to the inputs cannot leak into inference.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .contracts import (
    ACTIVITY_HEADER, AUDIT_HEADER, AUDITED_FIELDS, IDENTITY_HEADER, MANIFEST_HEADER,
    REPORTED_RESULTS, REPORTED_STATUSES, REQUEST_HEADER, RULE_ACTIONS, RULE_HEADER,
    Activity, ContractError, EvidenceRef, Identity, IdentityChange, ManifestEntry,
    Request, Rule, parse_utc,
)

CONTEXT_FILES = {
    'identities': 'user_identities.csv',
    'audit_log': 'user_audit_log.csv',
    'activities': 'system_activity.csv',
    'rules': 'deletion_rules.csv',
    'manifest': 'evidence_files.csv',
}


def read_csv(path: Path, header: tuple[str, ...]) -> list[dict[str, str]]:
    """Read a UTF-8 CSV (BOM tolerated) and require the exact header and row width."""
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.reader(handle)
        found = next(reader, None)
        if found is None or tuple(found) != header:
            raise ContractError(f'{path.name}: expected header {",".join(header)}, got {found}')
        rows = []
        for line_no, values in enumerate(reader, start=2):
            if len(values) != len(header):
                raise ContractError(f'{path.name}:{line_no}: expected {len(header)} fields, got {len(values)}')
            rows.append(dict(zip(header, values)))
    return rows


def _require(path: Path, line_no: int, row: dict[str, str], fields: tuple[str, ...]):
    for field in fields:
        if not row[field]:
            raise ContractError(f'{path.name}:{line_no}: empty {field}')


def _unique(path: Path, values: list[str], what: str):
    seen = set()
    for value in values:
        if value in seen:
            raise ContractError(f'{path.name}: duplicate {what} {value!r}')
        seen.add(value)


def load_requests(path: Path) -> list[Request]:
    path = Path(path)
    requests = []
    for line_no, row in enumerate(read_csv(path, REQUEST_HEADER), start=2):
        _require(path, line_no, row, ('case_id', 'email', 'requested_at', 'reported_status',
                                      'reported_at', 'audit_at'))
        if row['reported_status'] not in REPORTED_STATUSES:
            raise ContractError(f'{path.name}:{line_no}: unknown reported_status {row["reported_status"]!r}')
        where = f'{path.name}:{line_no}'
        requests.append(Request(
            case_id=row['case_id'], email=row['email'],
            requested_at=parse_utc(row['requested_at'], f'{where} requested_at'),
            reported_status=row['reported_status'],
            reported_at=parse_utc(row['reported_at'], f'{where} reported_at'),
            audit_at=parse_utc(row['audit_at'], f'{where} audit_at'),
            request_text=row['request_text']))
    _unique(path, [r.case_id for r in requests], 'case_id')
    return requests


@dataclass(frozen=True)
class Context:
    """Validated identity, activity, policy and manifest inputs for one dataset root."""
    root: Path
    identities: tuple[Identity, ...]
    audit_log: tuple[IdentityChange, ...]
    activities: tuple[Activity, ...]
    rules: tuple[Rule, ...]
    manifest: tuple[ManifestEntry, ...]
    evidence: dict[str, EvidenceRef]


def _manifest_path(root: Path, relative: str, where: str) -> None:
    candidate = Path(relative)
    if candidate.is_absolute() or '..' in candidate.parts:
        raise ContractError(f'{where}: manifest path must be relative inside the dataset: {relative!r}')
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ContractError(f'{where}: manifest path escapes the dataset: {relative!r}')
    if not resolved.is_file():
        raise ContractError(f'{where}: evidence file missing: {relative!r}')


def load_context(dataset: Path) -> Context:
    root = Path(dataset).resolve()
    paths = {key: root / name for key, name in CONTEXT_FILES.items()}

    identities = []
    for n, row in enumerate(read_csv(paths['identities'], IDENTITY_HEADER), start=2):
        _require(paths['identities'], n, row, ('user_id', 'email', 'updated_at'))
        identities.append(Identity(**{**row, 'updated_at': parse_utc(
            row['updated_at'], f'user_identities.csv:{n} updated_at')}))
    _unique(paths['identities'], [i.user_id for i in identities], 'user_id')

    audit_log = []
    for n, row in enumerate(read_csv(paths['audit_log'], AUDIT_HEADER), start=2):
        _require(paths['audit_log'], n, row, ('audit_id', 'user_id', 'event_time', 'field_name'))
        if row['field_name'] not in AUDITED_FIELDS:
            raise ContractError(f'user_audit_log.csv:{n}: unknown field_name {row["field_name"]!r}')
        audit_log.append(IdentityChange(**{**row, 'event_time': parse_utc(
            row['event_time'], f'user_audit_log.csv:{n} event_time')}))
    _unique(paths['audit_log'], [a.audit_id for a in audit_log], 'audit_id')

    activities = []
    for n, row in enumerate(read_csv(paths['activities'], ACTIVITY_HEADER), start=2):
        _require(paths['activities'], n, row, ('activity_id', 'event_time', 'event_type', 'reported_result'))
        if row['reported_result'] not in REPORTED_RESULTS:
            raise ContractError(f'system_activity.csv:{n}: unknown reported_result {row["reported_result"]!r}')
        activities.append(Activity(**{**row, 'event_time': parse_utc(
            row['event_time'], f'system_activity.csv:{n} event_time')}))
    _unique(paths['activities'], [a.activity_id for a in activities], 'activity_id')

    rules = []
    for n, row in enumerate(read_csv(paths['rules'], RULE_HEADER), start=2):
        _require(paths['rules'], n, row, RULE_HEADER)
        if row['required_action'] not in RULE_ACTIONS:
            raise ContractError(f'deletion_rules.csv:{n}: unknown required_action {row["required_action"]!r}')
        rules.append(Rule(**row))
    _unique(paths['rules'], [r.rule_id for r in rules], 'rule_id')

    manifest = []
    for n, row in enumerate(read_csv(paths['manifest'], MANIFEST_HEADER), start=2):
        _require(paths['manifest'], n, row, ('file_id', 'file_path', 'captured_at'))
        _manifest_path(root, row['file_path'], f'evidence_files.csv:{n}')
        manifest.append(ManifestEntry(**{**row, 'captured_at': parse_utc(
            row['captured_at'], f'evidence_files.csv:{n} captured_at')}))
    _unique(paths['manifest'], [m.file_id for m in manifest], 'file_id')
    _unique(paths['manifest'], [m.file_path for m in manifest], 'file_path')

    # Every citable ID and the instant it became available. An evidence_id must be
    # unambiguous, so IDs may not collide across the three source kinds.
    evidence: dict[str, EvidenceRef] = {}
    sources = ([EvidenceRef(m.file_id, 'FILE', m.captured_at) for m in manifest]
               + [EvidenceRef(a.activity_id, 'ACTIVITY', a.event_time) for a in activities]
               + [EvidenceRef(a.audit_id, 'IDENTITY_AUDIT', a.event_time) for a in audit_log])
    for ref in sources:
        if ref.source_id in evidence:
            raise ContractError(f'evidence ID {ref.source_id!r} is used by more than one source kind')
        evidence[ref.source_id] = ref

    return Context(root, tuple(identities), tuple(audit_log), tuple(activities),
                   tuple(rules), tuple(manifest), evidence)
