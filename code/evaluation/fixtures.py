"""Team-created SYNTHETIC fixtures (plan section 19). Evaluation-only.

Each fixture is written as:
    <out>/<name>/input/                inputs in the official file formats
    <out>/<name>/expected_output.csv   hand-authored answer, outside input/

Inference is pointed at input/ only and opens files by fixed name, so the
expected answer is unreachable from the inference path. These fixtures are not
copies of, or claims about, the supplied corpus. They deliberately use a
different identifier vocabulary (fx_ prefixes, non-numeric user references,
example.test emails) so the pipeline cannot depend on the supplied ID formats.
Expected rows are written by hand from each scenario's description, never by
running the pipeline.
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

# The supplied policy semantics, restated for fixtures (team-authored copy, not read from the corpus).
ALL_RULES = [
    ('rule_01', 'USER_PROFILE', 'Always', 'DELETE'),
    ('rule_02', 'SECONDARY_IDENTIFIER', 'Always', 'DELETE'),
    ('rule_03', 'SEARCH_INDEX_ENTRY', 'Always', 'DELETE'),
    ('rule_04', 'CACHE_ENTRY', 'Always', 'DELETE'),
    ('rule_05', 'SUPPORT_MESSAGE', 'Conversation contains shared customer, agent, or order context that must be retained', 'REDACT'),
    ('rule_06', 'SUPPORT_ATTACHMENT', 'Attachment belongs only to the deletion subject', 'DELETE'),
    ('rule_07', 'ANALYTICS_PROFILE', 'Record is still attributable to a user', 'ANONYMIZE'),
    ('rule_08', 'PRODUCT_TELEMETRY', 'Record is still attributable to a user', 'ANONYMIZE'),
    ('rule_09', 'USER_EXPORT', 'Always', 'DELETE'),
    ('rule_10', 'MARKETING_PROFILE', 'Always', 'DELETE'),
    ('rule_11', 'EMAIL_DELIVERY_LOG', 'Log contains a user email address', 'REDACT'),
    ('rule_12', 'SECURITY_AUDIT_LOG', 'Record is within the supplied retention period', 'RETAIN'),
    ('rule_13', 'LEGAL_HOLD_RECORD', 'A supplied legal hold is active', 'RETAIN'),
    ('rule_14', 'BACKUP_ARCHIVE', 'Restore access can be blocked', 'RETAIN'),
    ('rule_15', 'ACCOUNT_TOMBSTONE', 'Account is deleted and only a stable internal user_id is needed to preserve '
                'references to retained records', 'REDACT'),
]
DELETE_TEXT = 'Please delete my account and its personal data.'


@dataclass(frozen=True)
class Fixture:
    name: str
    description: str
    tables: dict[str, list[dict[str, str]]]  # missing tables are written header-only
    evidence: dict[str, bytes]  # dataset-relative path -> content
    expected: list[OutputRow] = field(default_factory=list)
    fallback_ids: frozenset = frozenset()   # expected rows that are disclosed best-effort fallbacks


# --- builders ------------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _json_bytes(obj) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def _rules(*categories):
    return [dict(zip(RULE_HEADER, r)) for r in ALL_RULES if not categories or r[1] in categories]


def _request(case_id, email, audit_at, text=DELETE_TEXT, requested_at='2026-01-01T12:00:00Z',
             reported_status='COMPLETE', reported_at='2026-01-02T12:00:00Z'):
    return {'case_id': case_id, 'email': email, 'requested_at': requested_at, 'reported_status': reported_status,
            'reported_at': reported_at, 'audit_at': audit_at, 'request_text': text}


def _identity(user_id, name, email, phone, updated_at='2025-12-01T09:00:00Z'):
    return {'user_id': user_id, 'name': name, 'email': email, 'phone': phone, 'email_hash': _sha256(email),
            'updated_at': updated_at}


def _manifest(file_id, path, captured_at, origin='fixture'):
    return {'file_id': file_id, 'file_path': path, 'origin': origin, 'captured_at': captured_at}


def _lookups(exported_at, *rows):
    """Family A: explicit (system, resource, record_type, result, subject) lookups."""
    return _json_bytes({'exported_at': exported_at, 'origin': 'fixture_lookups', 'record_count': len(rows),
                        'records': [dict(zip(('recorded_at', 'system', 'resource_id', 'record_type', 'result',
                                              'subject_reference'), r)) for r in rows]})


def _bundle(exported_at, *snapshots):
    """Family C: (captured_at, data_store, collection, records) snapshots."""
    return _json_bytes({'exported_at': exported_at, 'source': 'fixture_assurance', 'snapshots': [
        {'captured_at': c, 'data_store': d, 'collection': k, 'records': r} for c, d, k, r in snapshots]})


def _readback(exported_at, exports):
    """Family D: {service: {group: [items]}}."""
    return _json_bytes({'exported_at': exported_at, 'region': 'fixture', 'source': 'fixture_readback',
                        'service_exports': exports})


def _sql(captured_at, tables):
    """JSON-in-SQL snapshot: {table: [(resource_id, origin_system, collection, record)]}."""
    lines = ['-- Fixture point-in-time database snapshot', f'-- Captured at: {captured_at}',
             '-- Database topology: failover_replica', 'BEGIN TRANSACTION;']
    for table, rows in tables.items():
        lines.append(f'CREATE TABLE {table} (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, '
                     'collection_name TEXT NOT NULL, record_json TEXT NOT NULL CHECK(json_valid(record_json)), '
                     'updated_at TEXT NOT NULL);')
        for rid, system, collection, record in rows:
            payload = json.dumps(record).replace("'", "''")
            lines.append(f"INSERT INTO {table} VALUES('{rid}','{system}','{collection}','{payload}','{captured_at}');")
    lines.append('COMMIT;')
    return ('\n'.join(lines) + '\n').encode('utf-8')


def _audit_event(rid, subject, retention_until='2031-01-01T00:00:00Z'):
    event = {'resource_id': rid, 'subject_reference': subject, 'event_name': 'account_deletion_reviewed',
             'actor_type': 'automated_privacy_workflow', 'integrity_seal': 'sha256:fixture'}
    if retention_until:
        event['retention_until'] = retention_until
    return event


def _row(case_id, status, *steps):
    return OutputRow(case_id, status, tuple(Step(*s) for s in steps))


F, R, V = 'FINDING', 'REMEDIATION', 'VERIFICATION'


# --- plan section 19 scenarios --------------------------------------------------------------------

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
            'deletion_requests.csv': [_request('fx_case_A', email, '2026-01-03T12:00:00Z')],
            'user_identities.csv': [_identity('fx_user_A', 'Fixture Person A', email, '+12025550101')],
            'deletion_rules.csv': _rules('USER_PROFILE'),
            'evidence_files.csv': [_manifest('fx_file_A', 'evidence/records/profile.json', '2026-01-03T11:00:00Z',
                                             'fixture_readback')],
        },
        evidence={'evidence/records/profile.json': _json_bytes(readback)},
        expected=[_row('fx_case_A', 'INCOMPLETE',
                       (F, 'account_service', 'fx_account_A', 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_A'),
                       (R, 'account_service', 'fx_account_A', 'DELETE_RECORD', 'ABSENT', ''),
                       (V, 'account_service', 'fx_account_A', 'VERIFY_RECORD_ABSENT', 'ABSENT', ''))],
    )


def fixture_b() -> Fixture:
    """Plan 19.3: already-compliant records stay in the verification list."""
    email = 'person.a@example.test'
    readback = _readback('2026-01-03T11:00:00Z', {
        'account_service': {'profile_lookups': [
            {'observed_at': '2026-01-03T10:00:00Z', 'resource_id': 'fx_account_A', 'status_code': 404, 'profile': None}]},
        'search_service': {'index_queries': [
            {'observed_at': '2026-01-03T10:05:00Z', 'query': email, 'resource_id': 'fx_search_A',
             'hits': {'total': 1, 'documents': [{'resource_id': 'fx_search_A', 'display_name': 'Fixture Person A',
                                                 'email': email, 'customer_reference': None}]}}]},
        'security_service': {'audit_records': [
            {'observed_at': '2026-01-03T10:10:00Z', 'resource_id': 'fx_audit_A', 'subject_reference': 'fx_user_A',
             'event_code': 'ACCOUNT_DELETION_RECORDED', 'retention_until': '2031-01-01T00:00:00Z'}]},
    })
    link = {'activity_id': 'fx_act_B1', 'event_time': '2026-01-02T08:00:00Z', 'event_type': 'LINK_RESOURCE_IDENTITY',
            'source_system': 'data_catalog', 'target_system': 'account_service', 'resource_id': 'fx_account_A',
            'reported_result': 'SUCCESS',
            'details': f'Data catalog linked fx_account_A (fx_account_A) to {email} and fx_user_A.'}
    claim = {'activity_id': 'fx_act_B2', 'event_time': '2026-01-02T09:00:00Z', 'event_type': 'DELETE_RECORD',
             'source_system': 'deletion_worker', 'target_system': 'search_service', 'resource_id': 'fx_search_A',
             'reported_result': 'SUCCESS', 'details': 'Deletion worker reported the requested operation as completed.'}
    return Fixture(
        name='fixture_b',
        description='SYNTHETIC: absent account and retained security record verified next to a remaining search entry',
        tables={
            'deletion_requests.csv': [_request('fx_case_B', email, '2026-01-03T12:00:00Z')],
            'user_identities.csv': [_identity('fx_user_A', 'Fixture Person A', email, '+12025550101')],
            'system_activity.csv': [link, claim],
            'deletion_rules.csv': _rules(),
            'evidence_files.csv': [_manifest('fx_file_B', 'evidence/records/readback.json', '2026-01-03T11:00:00Z')],
        },
        evidence={'evidence/records/readback.json': readback},
        expected=[_row('fx_case_B', 'INCOMPLETE',
                       (F, 'search_service', 'fx_search_A', 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_B'),
                       (R, 'search_service', 'fx_search_A', 'DELETE_RECORD', 'ABSENT', ''),
                       (V, 'account_service', 'fx_account_A', 'VERIFY_RECORD_ABSENT', 'ABSENT', ''),
                       (V, 'search_service', 'fx_search_A', 'VERIFY_RECORD_ABSENT', 'ABSENT', ''),
                       (V, 'security_service', 'fx_audit_A', 'VERIFY_RECORD_PRESENT', 'PRESENT', ''))],
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
            'deletion_requests.csv': [_request(
                'fx_case_C', email, '2026-02-10T12:00:00Z',
                text='Stop promotional emails only. Keep my account and purchase history.',
                requested_at='2026-02-01T10:00:00Z', reported_at='2026-02-02T10:00:00Z')],
            'user_identities.csv': [_identity('fx_user_C', 'Fixture Person C', email, '+12025550103',
                                              updated_at='2025-12-15T09:00:00Z')],
            'deletion_rules.csv': _rules('USER_PROFILE', 'MARKETING_PROFILE'),
            'evidence_files.csv': [_manifest('fx_file_C', 'evidence/records/records.json', '2026-02-10T09:00:00Z',
                                             'fixture_records')],
        },
        evidence={'evidence/records/records.json': _json_bytes(records)},
        expected=[OutputRow('fx_case_C', 'NOT_APPLICABLE', ())],
    )


def fixture_d() -> Fixture:
    """Plan 19.5: absence proven by exhaustive primary AND replica listings, not by any literal mention.

    The snapshot also holds the subject's retained security event, which is what
    establishes that this capture covers the subject (see decisions.md).
    """
    email = 'person.d@example.test'
    decoy = {'resource_id': 'fx_account_other', 'customer_reference': 'fx_user_other', 'full_name': 'Other Person',
             'email': 'other.person@example.test', 'phone': '+12025550999', 'account_status': 'ACTIVE'}
    rows = [('fx_account_other', 'account_service', 'customer_profiles', decoy),
            ('fx_audit_D', 'security_service', 'security_audit_events', _audit_event('fx_audit_D', 'fx_user_D'))]
    return Fixture(
        name='fixture_d',
        description='SYNTHETIC: profile absent from exhaustive primary and failover listings -> COMPLETE',
        tables={
            'deletion_requests.csv': [_request('fx_case_D', email, '2026-01-05T12:00:00Z')],
            'user_identities.csv': [_identity('fx_user_D', 'Fixture Person D', email, '+12025550104')],
            'deletion_rules.csv': _rules(),
            'evidence_files.csv': [
                _manifest('fx_file_D1', 'evidence/records/history.json', '2025-12-20T10:00:00Z'),
                _manifest('fx_file_D2', 'evidence/databases/snapshot.sql', '2026-01-04T10:00:00Z')],
        },
        evidence={
            'evidence/records/history.json': _lookups(
                '2025-12-20T10:00:00Z', ('2025-12-20T10:00:00Z', 'account_service', 'fx_account_D', 'USER_PROFILE',
                                         'FOUND', email)),
            'evidence/databases/snapshot.sql': _sql('2026-01-04T10:00:00Z', {
                'customer_data_primary': rows, 'customer_data_failover_replica': rows}),
        },
        expected=[_row('fx_case_D', 'COMPLETE',
                       (V, 'account_service', 'fx_account_D', 'VERIFY_RECORD_ABSENT', 'ABSENT', 'fx_file_D2'),
                       (V, 'security_service', 'fx_audit_D', 'VERIFY_RECORD_PRESENT', 'PRESENT', 'fx_file_D2'))],
    )


def _tombstone_fixture(name, clean: bool) -> Fixture:
    email = 'person.e@example.test'
    case_id = 'fx_case_E_clean' if clean else 'fx_case_E'
    tomb = {'resource_id': 'fx_tombstone_E', 'subject_reference': 'fx_user_E', 'account_status': 'DELETED',
            'name': None if clean else 'Fixture Person E', 'email': None,
            'phone': None if clean else '+12025550105', 'email_hash': None if clean else _sha256(email)}
    bundle = _bundle('2026-01-04T10:00:00Z',
                     ('2026-01-04T10:00:00Z', 'identity_service', 'deleted_customer_references', [tomb]),
                     ('2026-01-04T10:00:00Z', 'security_service', 'security_audit_events',
                      [_audit_event('fx_audit_E', 'fx_user_E')]))
    common = [(V, 'account_service', 'fx_account_E', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
              (V, 'security_service', 'fx_audit_E', 'VERIFY_RECORD_PRESENT', 'PRESENT'),
              (V, 'identity_service', 'fx_tombstone_E', 'VERIFY_RECORD_REDACTED', 'REDACTED')]
    cites = ['fx_file_E3', 'fx_file_E2', 'fx_file_E2']
    if clean:
        expected = _row(case_id, 'COMPLETE', *[(*s, c) for s, c in zip(common, cites)])
    else:
        expected = _row(case_id, 'INCOMPLETE',
                        (F, 'identity_service', 'fx_tombstone_E', 'REPORT_EXPOSED_PII', 'PRESENT', 'fx_file_E2'),
                        (R, 'identity_service', 'fx_tombstone_E', 'REDACT_RECORD', 'REDACTED', ''),
                        *[(*s, '') for s in common])
    return Fixture(
        name=name,
        description='SYNTHETIC: deleted-account tombstone ' + (
            'keeps only its stable reference -> COMPLETE' if clean else 'still exposes name, phone and hash'),
        tables={
            'deletion_requests.csv': [_request(case_id, email, '2026-01-05T12:00:00Z')],
            'user_identities.csv': [_identity('fx_user_E', 'Fixture Person E', email, '+12025550105')],
            'deletion_rules.csv': _rules(),
            'evidence_files.csv': [
                _manifest('fx_file_E1', 'evidence/records/history.json', '2025-12-20T10:00:00Z'),
                _manifest('fx_file_E2', 'evidence/records/bundle.json', '2026-01-04T10:00:00Z'),
                _manifest('fx_file_E3', 'evidence/records/lookup.json', '2026-01-04T10:00:00Z')],
        },
        evidence={
            'evidence/records/history.json': _lookups(
                '2025-12-20T10:00:00Z', ('2025-12-20T10:00:00Z', 'account_service', 'fx_account_E', 'USER_PROFILE',
                                         'FOUND', email)),
            'evidence/records/bundle.json': bundle,
            'evidence/records/lookup.json': _lookups(
                '2026-01-04T10:00:00Z', ('2026-01-04T09:30:00Z', 'account_service', 'fx_account_E', 'USER_PROFILE',
                                         'NOT_FOUND', email))},
        expected=[expected],
    )


def fixture_e() -> Fixture:
    """Plan 19.6: tombstone exposing direct identifiers is REDACTED, never deleted."""
    return _tombstone_fixture('fixture_e', clean=False)


def fixture_e_clean() -> Fixture:
    """Plan 19.6 clean variation: only the permitted stable reference remains."""
    return _tombstone_fixture('fixture_e_clean', clean=True)


def fixture_f() -> Fixture:
    """Plan 19.7: request at the current email; data survives under the OLD email and its hash only."""
    old, new = 'old.person@example.test', 'new.person@example.test'
    readback = _readback('2026-01-04T10:00:00Z', {
        'export_service': {'exports': [
            {'observed_at': '2026-01-04T09:00:00Z', 'resource_id': 'fx_export_F', 'owner_email': None,
             'owner_phone': None, 'customer_reference': None, 'email_hash': _sha256(old),
             'object_key': 'exports/fx_export_F.zip'}]},
        'search_service': {'index_queries': [
            {'observed_at': '2026-01-04T09:05:00Z', 'query': old, 'resource_id': 'fx_search_F',
             'hits': {'total': 1, 'documents': [{'resource_id': 'fx_search_F', 'display_name': 'Fixture Person F',
                                                 'email': old, 'source_resource_id': 'fx_export_F'}]}}]},
    })
    audit = [{'audit_id': 'fx_audit_log_F1', 'user_id': 'fx_user_F', 'event_time': '2025-12-10T10:00:00Z',
              'field_name': 'email', 'old_value': old, 'new_value': new},
             {'audit_id': 'fx_audit_log_F2', 'user_id': 'fx_user_F', 'event_time': '2025-12-10T10:00:00Z',
              'field_name': 'email_hash', 'old_value': _sha256(old), 'new_value': _sha256(new)}]
    return Fixture(
        name='fixture_f',
        description='SYNTHETIC: historical email -> hash-only export -> search document',
        tables={
            'deletion_requests.csv': [_request('fx_case_F', new, '2026-01-05T12:00:00Z')],
            'user_identities.csv': [_identity('fx_user_F', 'Fixture Person F', new, '+12025550106',
                                              updated_at='2025-12-10T10:00:00Z')],
            'user_audit_log.csv': audit,
            'deletion_rules.csv': _rules(),
            'evidence_files.csv': [_manifest('fx_file_F', 'evidence/records/readback.json', '2026-01-04T10:00:00Z')],
        },
        evidence={'evidence/records/readback.json': readback},
        expected=[_row('fx_case_F', 'INCOMPLETE',
                       (F, 'export_service', 'fx_export_F', 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_F'),
                       (F, 'search_service', 'fx_search_F', 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_F'),
                       (R, 'export_service', 'fx_export_F', 'DELETE_RECORD', 'ABSENT', ''),
                       (R, 'search_service', 'fx_search_F', 'DELETE_RECORD', 'ABSENT', ''),
                       (V, 'export_service', 'fx_export_F', 'VERIFY_RECORD_ABSENT', 'ABSENT', ''),
                       (V, 'search_service', 'fx_search_F', 'VERIFY_RECORD_ABSENT', 'ABSENT', ''))],
    )


# --- adversarial scenarios ------------------------------------------------------------------------

def fixture_decoy() -> Fixture:
    """A near-identical person (same name, similar email) must not be pulled into the case."""
    email = 'person.g@example.test'
    lookups = _lookups('2026-01-04T10:00:00Z',
                       ('2026-01-04T09:00:00Z', 'account_service', 'fx_account_G', 'USER_PROFILE', 'NOT_FOUND', email),
                       ('2026-01-04T09:00:00Z', 'search_service', 'fx_search_decoy', 'SEARCH_INDEX_ENTRY', 'FOUND',
                        'person.g2@example.test'),
                       ('2026-01-04T09:00:00Z', 'account_service', 'fx_account_decoy', 'USER_PROFILE', 'FOUND',
                        'Person.G@example.test.co'))
    return Fixture(
        name='fixture_decoy',
        description='SYNTHETIC ADVERSARIAL: same-name decoy with similar emails stays out of the plan',
        tables={
            'deletion_requests.csv': [_request('fx_case_G', email, '2026-01-05T12:00:00Z')],
            'user_identities.csv': [_identity('fx_user_G', 'Fixture Person G', email, '+12025550107'),
                                    _identity('fx_user_G2', 'Fixture Person G', 'person.g2@example.test',
                                              '+12025550170')],
            'deletion_rules.csv': _rules(),
            'evidence_files.csv': [_manifest('fx_file_G', 'evidence/records/lookups.json', '2026-01-04T10:00:00Z'),
                                   _manifest('fx_file_G2', 'evidence/records/security.json', '2026-01-04T10:00:00Z')],
        },
        evidence={'evidence/records/lookups.json': lookups,
                  'evidence/records/security.json': _readback('2026-01-04T10:00:00Z', {'security_service': {
                      'audit_records': [{'observed_at': '2026-01-04T09:00:00Z', 'resource_id': 'fx_audit_G',
                                         'subject_reference': 'fx_user_G', 'event_code': 'ACCOUNT_DELETION_RECORDED',
                                         'retention_until': '2031-01-01T00:00:00Z'}]}})},
        expected=[_row('fx_case_G', 'COMPLETE',
                       (V, 'account_service', 'fx_account_G', 'VERIFY_RECORD_ABSENT', 'ABSENT', 'fx_file_G'),
                       (V, 'security_service', 'fx_audit_G', 'VERIFY_RECORD_PRESENT', 'PRESENT', 'fx_file_G2'))],
    )


def fixture_future() -> Fixture:
    """Evidence captured after audit_at must not change the answer or be cited."""
    email = 'person.h@example.test'
    return Fixture(
        name='fixture_future',
        description='SYNTHETIC ADVERSARIAL: a remaining record visible only in a post-cutoff file is ignored',
        tables={
            'deletion_requests.csv': [_request('fx_case_H', email, '2026-01-05T12:00:00Z')],
            'user_identities.csv': [_identity('fx_user_H', 'Fixture Person H', email, '+12025550108')],
            'deletion_rules.csv': _rules(),
            'evidence_files.csv': [
                _manifest('fx_file_H1', 'evidence/records/before.json', '2026-01-04T10:00:00Z'),
                _manifest('fx_file_H2', 'evidence/records/after.json', '2026-01-05T12:00:01Z')],
        },
        evidence={
            'evidence/records/before.json': _lookups(
                '2026-01-04T10:00:00Z',
                ('2026-01-04T09:00:00Z', 'account_service', 'fx_account_H', 'USER_PROFILE', 'NOT_FOUND', email)),
            # Observed before the cutoff but exported one second after it: ineligible.
            'evidence/records/after.json': _lookups(
                '2026-01-05T12:00:01Z',
                ('2026-01-05T11:00:00Z', 'search_service', 'fx_search_H', 'SEARCH_INDEX_ENTRY', 'FOUND', email))},
        expected=[_row('fx_case_H', 'COMPLETE',
                       (V, 'account_service', 'fx_account_H', 'VERIFY_RECORD_ABSENT', 'ABSENT', 'fx_file_H1'))],
    )


def fixture_replica() -> Fixture:
    """Primary deleted, failover replica still holds the profile: the copy is a remaining record."""
    email = 'person.i@example.test'
    profile = {'resource_id': 'fx_account_I', 'customer_reference': 'fx_user_I', 'full_name': 'Fixture Person I',
               'email': email, 'phone': '+12025550109', 'account_status': 'ACTIVE'}
    audit = ('fx_audit_I', 'security_service', 'security_audit_events', _audit_event('fx_audit_I', 'fx_user_I'))
    return Fixture(
        name='fixture_replica',
        description='SYNTHETIC ADVERSARIAL: clean primary cannot hide a failover replica copy',
        tables={
            'deletion_requests.csv': [_request('fx_case_I', email, '2026-01-05T12:00:00Z')],
            'user_identities.csv': [_identity('fx_user_I', 'Fixture Person I', email, '+12025550109')],
            'deletion_rules.csv': _rules(),
            'evidence_files.csv': [_manifest('fx_file_I', 'evidence/databases/snapshot.sql', '2026-01-04T10:00:00Z')],
        },
        evidence={'evidence/databases/snapshot.sql': _sql('2026-01-04T10:00:00Z', {
            'customer_data_primary': [audit],
            'customer_data_failover_replica': [audit, ('fx_account_I', 'account_service', 'customer_profiles', profile)]})},
        expected=[_row('fx_case_I', 'INCOMPLETE',
                       (F, 'account_service', 'fx_account_I', 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_I'),
                       (R, 'account_service', 'fx_account_I', 'DELETE_RECORD', 'ABSENT', ''),
                       (V, 'account_service', 'fx_account_I', 'VERIFY_RECORD_ABSENT', 'ABSENT', ''),
                       (V, 'security_service', 'fx_audit_I', 'VERIFY_RECORD_PRESENT', 'PRESENT', ''))],
    )


def fixture_no_absence() -> Fixture:
    """A delete claim and an unrelated listing that omits the record do not prove absence."""
    email = 'person.j@example.test'
    other = {'resource_id': 'fx_search_other', 'index_name': 'directory',
             'document': {'display_name': 'Other', 'email': 'other.j@example.test', 'customer_reference': 'fx_user_x'}}
    claim = {'activity_id': 'fx_act_J', 'event_time': '2026-01-03T09:00:00Z', 'event_type': 'DELETE_RECORD',
             'source_system': 'deletion_worker', 'target_system': 'search_service', 'resource_id': 'fx_search_J',
             'reported_result': 'SUCCESS', 'details': 'Deletion worker reported the requested operation as completed.'}
    return Fixture(
        name='fixture_no_absence',
        description='SYNTHETIC ADVERSARIAL: success claim + listing that does not cover the subject != absence',
        tables={
            'deletion_requests.csv': [_request('fx_case_J', email, '2026-01-05T12:00:00Z')],
            'user_identities.csv': [_identity('fx_user_J', 'Fixture Person J', email, '+12025550110')],
            'system_activity.csv': [claim],
            'deletion_rules.csv': _rules(),
            'evidence_files.csv': [
                _manifest('fx_file_J1', 'evidence/records/lookups.json', '2026-01-02T10:00:00Z'),
                _manifest('fx_file_J2', 'evidence/records/bundle.json', '2026-01-04T10:00:00Z')],
        },
        evidence={
            'evidence/records/lookups.json': _lookups(
                '2026-01-02T10:00:00Z',
                ('2026-01-02T09:00:00Z', 'account_service', 'fx_account_J', 'USER_PROFILE', 'NOT_FOUND', email),
                ('2026-01-02T09:00:00Z', 'search_service', 'fx_search_J', 'SEARCH_INDEX_ENTRY', 'FOUND', email)),
            'evidence/records/bundle.json': _bundle(
                '2026-01-04T10:00:00Z',
                ('2026-01-04T10:00:00Z', 'search_service', 'customer_search_documents', [other]))},
        expected=[_row('fx_case_J', 'INCOMPLETE',
                       (F, 'search_service', 'fx_search_J', 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_J1'),
                       (R, 'search_service', 'fx_search_J', 'DELETE_RECORD', 'ABSENT', ''),
                       (V, 'account_service', 'fx_account_J', 'VERIFY_RECORD_ABSENT', 'ABSENT', ''),
                       (V, 'search_service', 'fx_search_J', 'VERIFY_RECORD_ABSENT', 'ABSENT', ''))],
    )


def fixture_no_retention() -> Fixture:
    """A security record with no supplied retention period: the RETAIN condition is unproven.

    Everything else is compliant, so the case cannot be COMPLETE and must be a disclosed
    fallback that verifies only what is settled (the absent account), without citations.
    """
    email = 'person.k@example.test'
    bundle = _bundle('2026-01-04T10:00:00Z',
                     ('2026-01-04T10:00:00Z', 'security_service', 'security_audit_events',
                      [_audit_event('fx_audit_K', 'fx_user_K', retention_until=None)]))
    return Fixture(
        name='fixture_no_retention',
        description='SYNTHETIC: retained-record condition unproven (no retention period supplied)',
        tables={
            'deletion_requests.csv': [_request('fx_case_K', email, '2026-01-05T12:00:00Z')],
            'user_identities.csv': [_identity('fx_user_K', 'Fixture Person K', email, '+12025550111')],
            'deletion_rules.csv': _rules(),
            'evidence_files.csv': [
                _manifest('fx_file_K1', 'evidence/records/history.json', '2025-12-20T10:00:00Z'),
                _manifest('fx_file_K2', 'evidence/records/bundle.json', '2026-01-04T10:00:00Z')],
        },
        evidence={
            'evidence/records/history.json': _lookups(
                '2025-12-20T10:00:00Z', ('2025-12-20T10:00:00Z', 'account_service', 'fx_account_K', 'USER_PROFILE',
                                         'NOT_FOUND', email)),
            'evidence/records/bundle.json': bundle},
        expected=[_row('fx_case_K', 'INCOMPLETE',
                       (V, 'account_service', 'fx_account_K', 'VERIFY_RECORD_ABSENT', 'ABSENT', ''))],
        fallback_ids=frozenset({'fx_case_K'}),
    )


def fixture_mixed_search() -> Fixture:
    """A search for the subject's email also returns another person's document."""
    email = 'person.m@example.test'
    readback = _readback('2026-01-04T10:00:00Z', {
        'account_service': {'profile_lookups': [
            {'observed_at': '2026-01-04T09:00:00Z', 'resource_id': 'fx_account_M', 'status_code': 200,
             'profile': {'customer_reference': 'fx_user_M', 'email': None, 'full_name': None, 'phone': None}}]},
        'search_service': {'index_queries': [
            {'observed_at': '2026-01-04T09:05:00Z', 'query': email, 'resource_id': 'fx_search_M',
             'hits': {'total': 2, 'documents': [
                 {'resource_id': 'fx_search_M', 'display_name': 'Fixture Person M', 'email': email,
                  'customer_reference': None},
                 {'resource_id': 'fx_search_other', 'display_name': 'Fixture Person Mo',
                  'email': 'person.mo@example.test', 'customer_reference': 'fx_user_other'}]}}]},
    })
    return Fixture(
        name='fixture_mixed_search',
        description='SYNTHETIC ADVERSARIAL: unrelated search hit returned for the subject query stays out',
        tables={
            'deletion_requests.csv': [_request('fx_case_M', email, '2026-01-05T12:00:00Z')],
            'user_identities.csv': [_identity('fx_user_M', 'Fixture Person M', email, '+12025550112')],
            'deletion_rules.csv': _rules(),
            'evidence_files.csv': [_manifest('fx_file_M', 'evidence/records/readback.json', '2026-01-04T10:00:00Z')],
        },
        evidence={'evidence/records/readback.json': readback},
        expected=[_row('fx_case_M', 'INCOMPLETE',
                       (F, 'account_service', 'fx_account_M', 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_M'),
                       (F, 'search_service', 'fx_search_M', 'REPORT_REMAINING_RECORD', 'PRESENT', 'fx_file_M'),
                       (R, 'account_service', 'fx_account_M', 'DELETE_RECORD', 'ABSENT', ''),
                       (R, 'search_service', 'fx_search_M', 'DELETE_RECORD', 'ABSENT', ''),
                       (V, 'account_service', 'fx_account_M', 'VERIFY_RECORD_ABSENT', 'ABSENT', ''),
                       (V, 'search_service', 'fx_search_M', 'VERIFY_RECORD_ABSENT', 'ABSENT', ''))],
    )


FIXTURES = {f.name: f for f in (fixture_a(), fixture_b(), fixture_c(), fixture_d(), fixture_e(), fixture_e_clean(),
                                fixture_f(), fixture_decoy(), fixture_future(), fixture_replica(),
                                fixture_no_absence(), fixture_no_retention(), fixture_mixed_search())}


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
                 load_context(input_dir).evidence, fixture.fallback_ids)
    return base
