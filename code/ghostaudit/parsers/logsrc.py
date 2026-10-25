"""Operational logs: wrapper normalization first, event semantics second.

Wrappers: plain key=value, bracket, pipe, JSON, RFC 5424 syslog, and cloud
(`requestId=... message="k=\\"v\\" ..."`). Identity on a `*_started` line is
attached to resources on lines of the same (file, service, trace) only, and only
from lines logged at or before the line it is attached to; traces are never
merged across files.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from ..contracts import parse_utc
from ..observations import ABSENT, PRESENT, STATE, Observation, reference_tokens, text_tokens, tokens_from
from .records import exposed_fields, result_facts

_KV = re.compile(r'([A-Za-z_][\w.-]*)=("((?:[^"\\]|\\.)*)"|[^\s"]+)')
_BRACKET = re.compile(r'\[(\w+)\] \[([\w-]+)\] \[([\w-]+)\] ([\w-]+):\s?(.*)$')
_PIPE = re.compile(r'\|? ?(\w+) \| ([\w-]+) \| ([\w-]+) \| ([\w-]+) \| (.*)$')
_SYSLOG = re.compile(r'<\d+>1 [\w-]+ ([\w-]+) ([\w-]+) - - (.*)$')
_PLAIN_LEVEL = re.compile(r'(INFO|DEBUG|WARN|ERROR) (.*)$')

IDENTITY_KEYS = ('lookup_key', 'match_key', 'subject_key', 'owner_key', 'scan_key', 'correlation_key',
                 'customer_email', 'owner_email', 'requester_email', 'matched_value', 'email', 'phone', 'email_hash')
REFERENCE_KEYS = ('subject_ref', 'subject_reference', 'customer_reference')


def _identity(d):
    return tokens_from(*(d.get(k) for k in IDENTITY_KEYS)) | reference_tokens(*(d.get(k) for k in REFERENCE_KEYS))
# Events that never describe a customer record's state.
NOISE = {'connection_acquired', 'dependency_retry', 'dependency_recovered', 'cache_lookup', 'cache_refresh',
         'lease_extended', 'lease_renewed', 'consumer_lag', 'pool_sample', 'connection_pool_pressure',
         'slow_call_rate', 'disk_latency', 'properties_reloaded', 'telemetry_flushed', 'flag_snapshot_loaded',
         'timer_tick', 'certificate_checked', 'delivery_abandoned', 'thumbnail_failed', 'checkpoint_saved',
         'batch_started', 'export_started', 'export_completed', 'deletion_marker_check'}
SERVICE_ALIASES = {'profile_service': 'account_service', 'support_replica': 'support_platform'}


def _kv(text):
    return {m.group(1): (m.group(3).replace('\\"', '"') if m.group(3) is not None else m.group(2))
            for m in _KV.finditer(text)}


def parse_line(line: str) -> tuple[str, dict]:
    """Return (timestamp text, normalized fields with service/trace/event)."""
    ts, rest = line.split(' ', 1)
    if rest.startswith('{'):
        d = json.loads(rest)
        d = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in d.items()}
        d.setdefault('level', d.pop('severity', ''))
        d.setdefault('trace', d.pop('trace_id', ''))
        return ts, d
    for pattern, order in ((_BRACKET, ('level', 'service', 'trace', 'event')),
                           (_PIPE, ('level', 'service', 'trace', 'event'))):
        m = pattern.match(rest)
        if m:
            d = _kv(m.group(5))
            d.update(zip(order, m.groups()[:4]))
            return ts, d
    m = _SYSLOG.match(rest)
    if m:
        d = _kv(m.group(3))
        d.update(service=m.group(1), trace=m.group(2))
        return ts, d
    d = _kv(rest)
    if 'requestId' in d and 'message' in d:
        inner = _kv(d.pop('message'))
        inner.update(service=d.pop('function', ''), trace=d.pop('requestId'), level=d.get('level', ''))
        return ts, inner
    m = _PLAIN_LEVEL.match(rest)
    if m:
        d = _kv(m.group(2))
        d['level'] = m.group(1)
    d.setdefault('trace', d.pop('trace_id', ''))
    return ts, d


def _bool(v):
    return {'true': True, 'false': False}.get(str(v).lower())


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _facts(event, d):
    """Map a completed event to (category, system override, state facts); None if not state-bearing."""
    rows, status = _int(d.get('rows')), _int(d.get('http_status'))
    if event == 'profile_lookup_completed':
        present = (rows or 0) > 0 or status == 200
        absent = rows == 0 or status == 404
        if present == absent:
            return None
        return 'USER_PROFILE', 'account_service', dict(presence=PRESENT if present else ABSENT)
    if event == 'index_query_completed':
        result, hits = d.get('result'), _int(d.get('total_hits'))
        if result == 'hit' or (hits or 0) > 0:
            return 'SEARCH_INDEX_ENTRY', None, dict(presence=PRESENT)
        if result == 'miss' or hits == 0:
            return 'SEARCH_INDEX_ENTRY', None, dict(presence=ABSENT)
        return None
    if event == 'audit_record_read_completed':
        if rows is None:
            return None
        facts = dict(presence=PRESENT if rows > 0 else ABSENT)
        if d.get('retention_until'):
            facts['retention_until'] = parse_utc(d['retention_until'], 'retention_until')
        return 'SECURITY_AUDIT_LOG', None, facts
    if event == 'analytics_profile_read_completed':
        return 'ANALYTICS_PROFILE', None, dict(presence=PRESENT, exposed=exposed_fields('ANALYTICS_PROFILE', d))
    if event in ('analytics_profile_read', 'analytics_identity_scan'):
        n = _int(d.get('direct_identifiers'))
        exposed = ('direct_identifiers',) if (n or 0) > 0 else ()
        if n is None and event == 'analytics_identity_scan':
            exposed = ('matched_value',)
        return 'ANALYTICS_PROFILE', None, dict(presence=PRESENT, exposed=exposed)
    if event in ('connector_config_read_completed', 'connector_config_loaded', 'connector_heartbeat'):
        enabled = _bool(d.get('enabled'))
        if event == 'connector_heartbeat' and enabled and _bool(d.get('scheduler_registered')) is False:
            enabled = None   # enabled flag without a registered scheduler is not established activity
        return 'DATA_FLOW', None, dict(presence=PRESENT, enabled=enabled)
    if event == 'restore_policy_read_completed':
        return 'BACKUP_ARCHIVE', None, dict(presence=PRESENT if _bool(d.get('archive_present')) is not False else ABSENT,
                                            restore_allowed=_bool(d.get('restore_allowed')))
    if event == 'restore_candidate_evaluated':
        eligible, block = _bool(d.get('eligible')), _bool(d.get('deletion_block'))
        allowed = None if eligible is None else (eligible and block is not True)
        return 'BACKUP_ARCHIVE', None, dict(presence=PRESENT, restore_allowed=allowed)
    if event == 'restore_authorization_completed':
        allowed = {'allow': True, 'deny': False}.get(d.get('decision'))
        return 'BACKUP_ARCHIVE', None, dict(presence=PRESENT, restore_allowed=allowed)
    if event in ('export_metadata_read_completed', 'export_download_check_completed'):
        available = _bool(d.get('available'))
        if available is None:
            return None
        return 'USER_EXPORT', None, dict(presence=PRESENT if available else ABSENT)
    if event == 'support_ticket_read_completed':
        return 'SUPPORT_MESSAGE', None, dict(presence=PRESENT, exposed=exposed_fields('SUPPORT_MESSAGE', d))
    if event == 'message_pii_scan':
        return 'SUPPORT_MESSAGE', None, dict(presence=PRESENT, exposed=('matched_value',))
    if event == 'message_read':
        exposed = ('message_text',) if text_tokens(d.get('message_text')) else ()
        return 'SUPPORT_MESSAGE', None, dict(presence=PRESENT, exposed=exposed)
    if event == 'attachment_pii_scan':
        return 'SUPPORT_ATTACHMENT', None, dict(presence=PRESENT)
    if event == 'contact_lookup_completed':      # marketing contact record (has email_opt_in)
        if rows is None:
            return None
        return 'MARKETING_PROFILE', None, dict(presence=PRESENT if rows > 0 else ABSENT)
    if event == 'contact_query_completed':       # legacy contact index entry
        if rows is None:
            return None
        return 'SECONDARY_IDENTIFIER', None, dict(presence=PRESENT if rows > 0 else ABSENT)
    if event == 'retained_order_loaded':
        return 'LEGAL_HOLD_RECORD', None, dict(presence=PRESENT)
    return None


def parse_log(source_id, available_at, raw: bytes):
    observations, issues = [], []
    parsed = []
    for n, line in enumerate(raw.decode('utf-8').splitlines(), start=1):
        if not line.strip():
            continue
        try:
            ts, d = parse_line(line)
            parsed.append((n, parse_utc(ts, f'line {n}'), d, line))
        except (ValueError, json.JSONDecodeError) as exc:
            issues.append(f'PARSE_ERROR line {n}: {exc}')

    # Completeness of the export itself.
    started = [d for _, _, d, _ in parsed if d.get('event') == 'export_started']
    completed = [d for _, _, d, _ in parsed if d.get('event') == 'export_completed']
    for d in completed:
        if (_int(d.get('dropped')) or 0) > 0:
            issues.append(f'PARTIAL_SCOPE: export dropped {d["dropped"]} source events')
    for d in completed:
        expected = [_int(s.get('expected_source_events')) for s in started]
        if expected and _int(d.get('source_events')) not in expected:
            issues.append(f'PARTIAL_SCOPE: {d.get("source_events")} source events, expected {expected}')
    if started and not completed:
        # Not degraded: logs only contribute explicit lookups, never omission-based absence.
        issues.append('NOTE: export_started without export_completed marker')

    trace_tokens = defaultdict(list)       # (service, trace) -> [(logged at, identity tokens)]
    trace_targets = defaultdict(set)
    for _, when, d, _ in parsed:
        if d.get('trace'):
            trace_tokens[(d.get('service'), d['trace'])].append((when, _identity(d)))
            if d.get('resource_id') and not d.get('event', '').endswith('_started'):
                trace_targets[(d.get('service'), d['trace'])].add(d['resource_id'])

    unknown = 0
    for n, when, d, line in parsed:
        event = d.get('event', '')
        if event in NOISE or event.endswith('_started'):
            continue
        if event == 'record_lookup':
            try:
                facts = result_facts(d['record_type'], d['result'])
            except (KeyError, ValueError) as exc:
                issues.append(f'PARSE_ERROR line {n}: {exc}')
                continue
            category, system = d['record_type'], d.get('system')
        else:
            mapped = _facts(event, d)
            if mapped is None:
                unknown += 1
                continue
            category, system, facts = mapped
        if not d.get('resource_id'):
            continue
        service = d.get('service', '')
        own = _identity(d)
        key = (service, d.get('trace'))
        # A trace's lookup keys describe its single target; if one trace touches several
        # resources the keys cannot be assigned to any of them.
        single_target = bool(d.get('trace')) and len(trace_targets[key]) == 1
        earlier = set().union(*(t for at, t in trace_tokens[key] if at <= when)) if single_target else set()
        tokens = own | earlier
        if facts.get('presence') == ABSENT and event != 'record_lookup':
            # A query key is the lookup's input, not an attribute of a returned record:
            # a not-found lookup cannot show that the target belongs to that person.
            tokens = frozenset()
        if when > available_at:
            issues.append(f'TEMPORAL_CONFLICT line {n}: event after source capture time')
        observations.append(Observation(
            source_id=source_id, available_at=available_at, observed_at=when, locator=f'line {n}',
            excerpt=line[:400], kind=STATE, proof='READBACK',
            system_id=system or SERVICE_ALIASES.get(service, service), resource_id=d['resource_id'],
            category=category, tokens=frozenset(tokens), **facts))
    if unknown:
        issues.append(f'UNRECOGNIZED_EVENTS: {unknown} lines with unmapped events')
    return observations, [], issues
