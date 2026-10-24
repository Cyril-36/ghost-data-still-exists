"""Team-created SYNTHETIC fixtures (plan section 19). Evaluation-only.

Each fixture is written as:
    <out>/<name>/input/            inputs in the official file formats
    <out>/<name>/expected_output.csv   hand-authored answer, outside input/

Inference is pointed at input/ only and opens files by fixed name, so the
expected answer is unreachable from the inference path. These fixtures are not
copies of, or claims about, the supplied corpus.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from ghostaudit.contracts import (
    ACTIVITY_HEADER, AUDIT_HEADER, IDENTITY_HEADER, MANIFEST_HEADER, REQUEST_HEADER,
    RULE_HEADER, OutputRow, Step,
)
from ghostaudit.inputs import load_context, load_requests
from ghostaudit.output import write_output

HEADERS = {
    'deletion_requests.csv': REQUEST_HEADER,
    'user_identities.csv': IDENTITY_HEADER,
    'user_audit_log.csv': AUDIT_HEADER,
    'system_activity.csv': ACTIVITY_HEADER,
    'deletion_rules.csv': RULE_HEADER,
    'evidence_files.csv': MANIFEST_HEADER,
}


@dataclass(frozen=True)
class Fixture:
    name: str
    description: str
    tables: dict[str, list[dict[str, str]]]  # missing tables are written header-only
    evidence: dict[str, bytes]  # dataset-relative path -> content
    expected: list[OutputRow] = field(default_factory=list)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _json_bytes(obj) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def fixture_a() -> Fixture:
    """Plan 19.2: one residual account profile after a deletion request."""
    email = 'person.a@example.test'
    readback = {
        'exported_at': '2026-01-03T11:00:00Z',
        'region': 'fixture',
        'source': 'fixture_readback',
        'service_exports': {'account_service': {'profile_lookups': [{
            'observed_at': '2026-01-03T11:00:00Z',
            'resource_id': 'fx_account_A',
            'status_code': 200,
            'profile': {'customer_reference': 'fx_user_A', 'full_name': 'Fixture Person A',
                        'email': email, 'phone': '+12025550101'},
        }]}},
    }
    return Fixture(
        name='fixture_a',
        description='SYNTHETIC: account profile still returned by readback after deletion request',
        tables={
            'deletion_requests.csv': [{
                'case_id': 'fx_case_A', 'email': email,
                'requested_at': '2026-01-01T12:00:00Z', 'reported_status': 'COMPLETE',
                'reported_at': '2026-01-02T12:00:00Z', 'audit_at': '2026-01-03T12:00:00Z',
                'request_text': 'Please delete my account and its personal data.'}],
            'user_identities.csv': [{
                'user_id': 'fx_user_A', 'name': 'Fixture Person A', 'email': email,
                'phone': '+12025550101', 'email_hash': _sha256(email),
                'updated_at': '2025-12-01T09:00:00Z'}],
            'deletion_rules.csv': [{'rule_id': 'rule_01', 'data_category': 'USER_PROFILE',
                                    'condition': 'Always', 'required_action': 'DELETE'}],
            'evidence_files.csv': [{'file_id': 'fx_file_A', 'file_path': 'evidence/records/profile.json',
                                    'origin': 'fixture_readback', 'captured_at': '2026-01-03T11:00:00Z'}],
        },
        evidence={'evidence/records/profile.json': _json_bytes(readback)},
        expected=[OutputRow('fx_case_A', 'INCOMPLETE', (
            Step('FINDING', 'account_service', 'fx_account_A', 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_A'),
            Step('REMEDIATION', 'account_service', 'fx_account_A', 'DELETE_RECORD', 'ABSENT', ''),
            Step('VERIFICATION', 'account_service', 'fx_account_A', 'VERIFY_RECORD_ABSENT', 'ABSENT', ''),
        ))],
    )


def fixture_c() -> Fixture:
    """Plan 19.4: marketing-only opt-out does not authorize account deletion.

    The workflow claims COMPLETE and personal data is still present, so a system
    that ignores intent would wrongly emit findings.
    """
    email = 'person.c@example.test'
    records = {
        'exported_at': '2026-02-10T09:00:00Z',
        'origin': 'fixture_records',
        'record_count': 2,
        'records': [
            {'recorded_at': '2026-02-10T09:00:00Z', 'system': 'account_service',
             'resource_id': 'fx_account_C', 'record_type': 'USER_PROFILE', 'result': 'FOUND',
             'subject_reference': email},
            {'recorded_at': '2026-02-10T09:00:00Z', 'system': 'marketing_service',
             'resource_id': 'fx_marketing_C', 'record_type': 'MARKETING_PROFILE', 'result': 'FOUND',
             'subject_reference': email},
        ],
    }
    return Fixture(
        name='fixture_c',
        description='SYNTHETIC: marketing-only opt-out with data present and a COMPLETE workflow claim',
        tables={
            'deletion_requests.csv': [{
                'case_id': 'fx_case_C', 'email': email,
                'requested_at': '2026-02-01T10:00:00Z', 'reported_status': 'COMPLETE',
                'reported_at': '2026-02-02T10:00:00Z', 'audit_at': '2026-02-10T12:00:00Z',
                'request_text': 'Stop promotional emails only. Keep my account and purchase history.'}],
            'user_identities.csv': [{
                'user_id': 'fx_user_C', 'name': 'Fixture Person C', 'email': email,
                'phone': '+12025550103', 'email_hash': _sha256(email),
                'updated_at': '2025-12-15T09:00:00Z'}],
            'deletion_rules.csv': [
                {'rule_id': 'rule_01', 'data_category': 'USER_PROFILE',
                 'condition': 'Always', 'required_action': 'DELETE'},
                {'rule_id': 'rule_10', 'data_category': 'MARKETING_PROFILE',
                 'condition': 'Always', 'required_action': 'DELETE'}],
            'evidence_files.csv': [{'file_id': 'fx_file_C', 'file_path': 'evidence/records/records.json',
                                    'origin': 'fixture_records', 'captured_at': '2026-02-10T09:00:00Z'}],
        },
        evidence={'evidence/records/records.json': _json_bytes(records)},
        expected=[OutputRow('fx_case_C', 'NOT_APPLICABLE', ())],
    )


FIXTURES = {f.name: f for f in (fixture_a(), fixture_c())}


def write_fixture(fixture: Fixture, out: Path) -> Path:
    """Write inputs and the expected answer; the answer is validated like real output."""
    base = Path(out) / fixture.name
    input_dir = base / 'input'
    for name, header in HEADERS.items():
        path = input_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=header, lineterminator='\n')
            writer.writeheader()
            writer.writerows(fixture.tables.get(name, []))
    for relative, content in fixture.evidence.items():
        target = input_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    requests = load_requests(input_dir / 'deletion_requests.csv')
    write_output(base / 'expected_output.csv', fixture.expected, requests,
                 load_context(input_dir).evidence)
    return base
