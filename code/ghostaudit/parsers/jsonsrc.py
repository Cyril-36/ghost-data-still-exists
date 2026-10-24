"""JSON evidence: four structural families, selected by shape, not filename.

A  {records:[{record_type, result, system, resource_id, ...}]}   explicit lookups
B  {captured_at, collection, records:[...]}                      one collection listing
C  {snapshots:[{captured_at, data_store, collection, records}]}  multi-collection bundle
D  {service_exports:{service:{group:[...]}}}                     service readbacks
"""
from __future__ import annotations

import json
from dataclasses import replace

from ..contracts import parse_utc
from ..observations import reference_tokens, ABSENT, LOGICAL, STATE, Observation, Scope, tokens_from
from .records import COLLECTIONS, record_facts, result_facts


class _Ctx:
    def __init__(self, source_id, available_at):
        self.source_id, self.available_at = source_id, available_at
        self.observations, self.scopes, self.issues = [], [], []

    def obs(self, pointer, rec, observed_at, **kw):
        self.observations.append(Observation(
            source_id=self.source_id, available_at=self.available_at, observed_at=observed_at,
            locator=pointer, excerpt=json.dumps(rec, ensure_ascii=False, sort_keys=True)[:400], **kw))


def parse_json(source_id, available_at, raw: bytes):
    ctx = _Ctx(source_id, available_at)
    data = json.loads(raw.decode('utf-8'))
    if not isinstance(data, dict):
        ctx.issues.append('UNRECOGNIZED_FORMAT: top level is not an object')
    elif isinstance(data.get('service_exports'), dict):
        _family_d(ctx, data)
    elif isinstance(data.get('snapshots'), list):
        _family_c(ctx, data)
    elif isinstance(data.get('records'), list) and isinstance(data.get('collection'), str):
        _family_b(ctx, data)
    elif isinstance(data.get('records'), list):
        _family_a(ctx, data)
    else:
        ctx.issues.append(f'UNRECOGNIZED_FORMAT: keys {sorted(data)}')
    return ctx.observations, _with_capture_subjects(ctx.scopes, ctx.observations), ctx.issues


def _with_capture_subjects(scopes, observations):
    """A subject-scoped listing covers the subjects referenced by its own capture:
    the listings and rows of the same file observed at the same capture time."""
    by_time = {}
    for o in observations:
        by_time.setdefault(o.observed_at, set()).update(o.tokens)
    for sc in scopes:
        by_time.setdefault(sc.observed_at, set()).update(sc.member_tokens)
    return [replace(sc, subjects=frozenset(by_time.get(sc.observed_at, ()))) for sc in scopes]


def _lookup_record(ctx, pointer, rec, observed_at, link_field='subject_reference'):
    """Family A style row: explicit system/resource/record_type/result."""
    try:
        facts = result_facts(rec['record_type'], rec['result'])
    except (KeyError, ValueError) as exc:
        ctx.issues.append(f'PARSE_ERROR {pointer}: {exc}')
        return
    ctx.obs(pointer, rec, observed_at, kind=STATE, proof='EXACT_LOOKUP', system_id=rec['system'],
            resource_id=rec['resource_id'], category=rec['record_type'],
            tokens=reference_tokens(rec.get(link_field)), **facts)


def _family_a(ctx, data):
    records = data['records']
    if isinstance(data.get('record_count'), int) and data['record_count'] != len(records):
        ctx.issues.append(f'PARTIAL_SCOPE: record_count {data["record_count"]} != {len(records)} listed')
    for i, rec in enumerate(records):
        _lookup_record(ctx, f'/records/{i}', rec, parse_utc(rec['recorded_at'], 'recorded_at'))


def _flatten(rec):
    """Search documents nest their identifying fields under `document`."""
    if isinstance(rec.get('document'), dict):
        return {**{k: v for k, v in rec.items() if k != 'document'}, **rec['document']}
    return rec


def _listing(ctx, pointer, system, collection, observed_at, records):
    """Rows of one exhaustive collection listing, plus its Scope."""
    if collection not in COLLECTIONS:
        ctx.issues.append(f'UNSUPPORTED_CATEGORY {pointer}: collection {collection!r}')
        return
    category, default_system = COLLECTIONS[collection]
    system = system or default_system
    members, member_tokens = set(), set()
    for i, rec in enumerate(records):
        flat = _flatten(rec)
        facts = record_facts(category, flat)
        members.add(rec['resource_id'])
        member_tokens |= facts['tokens']
        ctx.obs(f'{pointer}/records/{i}', rec, observed_at, kind=STATE, proof='SNAPSHOT_ROW',
                system_id=system, resource_id=rec['resource_id'], category=category, **facts)
    ctx.scopes.append(Scope(ctx.source_id, ctx.available_at, observed_at, system, category, LOGICAL,
                            frozenset(members), frozenset(member_tokens), pointer, 'SUBJECT_SCOPED'))


def _family_b(ctx, data):
    _listing(ctx, '', None, data['collection'], parse_utc(data['captured_at'], 'captured_at'), data['records'])


def _family_c(ctx, data):
    for i, snap in enumerate(data['snapshots']):
        pointer, when = f'/snapshots/{i}', parse_utc(snap['captured_at'], 'captured_at')
        if snap['collection'] == 'operational_record_history':
            # A history of explicit lookups, not a listing of the named data store.
            for j, rec in enumerate(snap['records']):
                _lookup_record(ctx, f'{pointer}/records/{j}', rec, parse_utc(rec['recorded_at'], 'recorded_at'),
                               link_field='lookup_value')
        else:
            _listing(ctx, pointer, snap['data_store'], snap['collection'], when, snap['records'])


def _family_d(ctx, data):
    for service, groups in data['service_exports'].items():
        for group, items in groups.items():
            for i, item in enumerate(items):
                pointer = f'/service_exports/{service}/{group}/{i}'
                when = parse_utc(item['observed_at'], 'observed_at')
                if group == 'profile_lookups':
                    _profile_lookup(ctx, pointer, service, item, when)
                elif group == 'index_queries':
                    _index_query(ctx, pointer, service, item, when)
                elif group in COLLECTIONS:
                    category = COLLECTIONS[group][0]
                    ctx.obs(pointer, item, when, kind=STATE, proof='READBACK', system_id=service,
                            resource_id=item['resource_id'], category=category, **record_facts(category, item))
                else:
                    ctx.issues.append(f'UNSUPPORTED_CATEGORY {pointer}: group {group!r}')


def _profile_lookup(ctx, pointer, service, item, when):
    status, profile = item.get('status_code'), item.get('profile')
    if status == 404 and profile is None:
        ctx.obs(pointer, item, when, kind=STATE, proof='EXACT_LOOKUP', system_id=service,
                resource_id=item['resource_id'], category='USER_PROFILE', presence=ABSENT)
    elif status == 200 and isinstance(profile, dict):
        facts = record_facts('USER_PROFILE', profile)
        ctx.obs(pointer, item, when, kind=STATE, proof='READBACK', system_id=service,
                resource_id=item['resource_id'], category='USER_PROFILE', **facts)
    else:
        ctx.issues.append(f'CONTRADICTORY_STATE {pointer}: status {status} with profile {type(profile).__name__}')


def _index_query(ctx, pointer, service, item, when):
    hits = item.get('hits') or {}
    docs, total = hits.get('documents'), hits.get('total')
    query_tokens = tokens_from(item.get('query'))
    if not isinstance(docs, list) or not isinstance(total, int) or total != len(docs):
        ctx.issues.append(f'PARTIAL_SCOPE {pointer}: hits.total={total} documents={len(docs or [])}')
        return
    if total == 0:
        if item.get('resource_id'):
            ctx.obs(pointer, item, when, kind=STATE, proof='EXACT_LOOKUP', system_id=service,
                    resource_id=item['resource_id'], category='SEARCH_INDEX_ENTRY', presence=ABSENT)
        return
    for j, doc in enumerate(docs):
        # The query is lookup context, not document identity: a query for one person can
        # return other people's documents, so ownership comes only from the document's fields.
        facts = record_facts('SEARCH_INDEX_ENTRY', doc)
        facts['attributes'] = (('query_tokens', tuple(sorted(query_tokens))),)
        ctx.obs(f'{pointer}/hits/documents/{j}', doc, when, kind=STATE, proof='READBACK', system_id=service,
                resource_id=doc['resource_id'], category='SEARCH_INDEX_ENTRY', **facts)

