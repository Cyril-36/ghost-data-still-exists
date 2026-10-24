"""Interpret one structured record of a known collection.

Category comes from the collection/schema the record was read from (or an
explicit record_type), never from a resource-ID prefix. Shared by the JSON and
SQL parsers so the same schema always means the same thing.
"""
from __future__ import annotations

from ..contracts import parse_utc
from ..observations import ABSENT, PRESENT, reference_tokens, text_tokens, tokens_from

# collection / group / table name -> (category, canonical system)
COLLECTIONS = {
    'customer_profiles': ('USER_PROFILE', 'account_service'),
    'profile_lookups': ('USER_PROFILE', 'account_service'),
    'customer_analytics_reports': ('ANALYTICS_PROFILE', 'analytics_platform'),
    'customer_analytics_profiles': ('ANALYTICS_PROFILE', 'analytics_platform'),
    'profiles': ('ANALYTICS_PROFILE', 'analytics_platform'),
    'backup_catalog': ('BACKUP_ARCHIVE', 'backup_service'),
    'backup_restore_entries': ('BACKUP_ARCHIVE', 'backup_service'),
    'restore_entries': ('BACKUP_ARCHIVE', 'backup_service'),
    'customer_export_jobs': ('USER_EXPORT', 'export_service'),
    'customer_exports': ('USER_EXPORT', 'export_service'),
    'exports': ('USER_EXPORT', 'export_service'),
    'deleted_customer_references': ('ACCOUNT_TOMBSTONE', 'identity_service'),
    'connector_configurations': ('DATA_FLOW', 'integration_service'),
    'connector_subscriptions': ('DATA_FLOW', 'integration_service'),
    'connectors': ('DATA_FLOW', 'integration_service'),
    'retained_order_records': ('LEGAL_HOLD_RECORD', 'legal_service'),
    'customer_search_documents': ('SEARCH_INDEX_ENTRY', 'search_service'),
    'search_documents': ('SEARCH_INDEX_ENTRY', 'search_service'),
    'index_queries': ('SEARCH_INDEX_ENTRY', 'search_service'),
    'security_audit_events': ('SECURITY_AUDIT_LOG', 'security_service'),
    'security_audit_records': ('SECURITY_AUDIT_LOG', 'security_service'),
    'audit_records': ('SECURITY_AUDIT_LOG', 'security_service'),
    'support_conversations': ('SUPPORT_MESSAGE', 'support_platform'),
    'support_records': ('SUPPORT_MESSAGE', 'support_platform'),
    'tickets': ('SUPPORT_MESSAGE', 'support_platform'),
    'ticket_attachments': ('SUPPORT_ATTACHMENT', 'support_platform'),
}

# Fields that identify a person directly. Reference fields (customer_reference,
# subject_reference, user_id) are listed separately because policy treats them
# differently: REDACT keeps a stable reference, ANONYMIZE must remove it.
DIRECT_FIELDS = ('name', 'full_name', 'display_name', 'requester_name', 'email', 'phone',
                 'email_hash', 'owner_email', 'owner_phone', 'requester_email', 'requester_phone',
                 'customer_email')
REFERENCE_FIELDS = ('user_id', 'customer_reference', 'subject_reference')
TEXT_FIELDS = ('message_text', 'message_excerpt')
LINK_FIELDS = DIRECT_FIELDS + ('subject_selector', 'uploaded_by', 'lookup_value')

# Family A / handover / record_lookup result vocabulary.
RESULT_STATES = {
    'FOUND': dict(presence=PRESENT),
    'NOT_FOUND': dict(presence=ABSENT),
    'IDENTIFIABLE': dict(presence=PRESENT, exposed=('identifiers',)),
    'ANONYMIZED': dict(presence=PRESENT, exposed=()),
    'REDACTED': dict(presence=PRESENT, exposed=()),
}


def _flag(value) -> bool | None:
    """Explicit boolean parsing: 'false', '0' and False are false; unknown is None."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ('true', '1', 'yes'):
        return True
    if isinstance(value, str) and value.strip().lower() in ('false', '0', 'no'):
        return False
    return None


def _populated(value) -> bool:
    return value is not None and not (isinstance(value, str) and value.strip().lower() in ('', 'null', '[redacted]'))


def exposed_fields(category: str, rec: dict) -> tuple[str, ...]:
    """Identifier fields still populated that the category's treatment must remove."""
    fields = [f for f in DIRECT_FIELDS if f in rec and _populated(rec[f])]
    if category in ('ANALYTICS_PROFILE', 'PRODUCT_TELEMETRY'):
        fields += [f for f in REFERENCE_FIELDS if f in rec and _populated(rec[f])]
    for f in TEXT_FIELDS:
        if text_tokens(rec.get(f)):
            fields.append(f)
    return tuple(fields)


def record_facts(category: str, rec: dict) -> dict:
    """State-bearing facts for a record that is present in a listing or readback."""
    facts = dict(presence=PRESENT, tokens=tokens_from(*(rec.get(f) for f in LINK_FIELDS))
                 | reference_tokens(*(rec.get(f) for f in REFERENCE_FIELDS)))
    if category in ('ANALYTICS_PROFILE', 'PRODUCT_TELEMETRY', 'SUPPORT_MESSAGE', 'ACCOUNT_TOMBSTONE',
                    'EMAIL_DELIVERY_LOG'):
        facts['exposed'] = exposed_fields(category, rec)
    if category == 'DATA_FLOW' and 'enabled' in rec:
        facts['enabled'] = _flag(rec['enabled'])
    if category == 'BACKUP_ARCHIVE' and 'restore_allowed' in rec:
        facts['restore_allowed'] = _flag(rec['restore_allowed'])
    if category == 'USER_EXPORT' and 'download_available' in rec and _flag(rec['download_available']) is False:
        facts['presence'] = ABSENT  # the export object is no longer retrievable
    for key in ('retention_until', 'retained_until'):
        if _populated(rec.get(key)):
            facts['retention_until'] = parse_utc(rec[key], key)
    if _populated(rec.get('source_resource_id')):
        facts['refs'] = (rec['source_resource_id'],)
    return facts


def result_facts(record_type: str, result: str) -> dict:
    """Facts for an explicit (record_type, result) lookup line."""
    if record_type == 'DATA_FLOW' and result in ('ACTIVE', 'DISABLED'):
        return dict(presence=PRESENT, enabled=result == 'ACTIVE')
    if record_type == 'BACKUP_ARCHIVE' and result in ('ACTIVE', 'BLOCKED'):
        return dict(presence=PRESENT, restore_allowed=result == 'ACTIVE')
    if result in RESULT_STATES:
        return dict(RESULT_STATES[result])
    raise ValueError(f'unrecognized result {result!r} for {record_type}')
