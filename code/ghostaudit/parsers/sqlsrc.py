"""SQLite-compatible snapshot exports, loaded into isolated in-memory databases.

Layouts handled (selected by table schema, not by filename):
- typed inventory tables + snapshot_scope(origin_system, captured_at)
- typed primary/replica tables, e.g. customer_profiles_failover_replica
- JSON-in-SQL tables (resource_id, origin_system, collection_name, record_json)

Every table is an exhaustive listing of its collection(s) at its own location
(primary or a named replica) at the snapshot time, so it yields both row
observations and Scopes. A failed load yields no observations at all.
"""
from __future__ import annotations

import json
import re
import sqlite3

from ..contracts import parse_utc
from ..observations import LOGICAL, STATE, Observation, Scope
from .records import COLLECTIONS, record_facts

_LOAD_ACTIONS = {sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_INSERT,
                 sqlite3.SQLITE_READ, sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_REINDEX}
_READ_ACTIONS = {sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT}
_MAX_SQL_BYTES = 20_000_000


def _load_authorizer(action, arg1, arg2, _db, _trigger):
    if action in _LOAD_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_UPDATE and arg1 == 'sqlite_master':
        return sqlite3.SQLITE_OK      # schema catalog bookkeeping for CREATE statements
    if action == sqlite3.SQLITE_PRAGMA and arg1 == 'foreign_keys':
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and arg2 == 'json_valid':
        return sqlite3.SQLITE_OK      # used by CHECK(json_valid(record_json))
    return sqlite3.SQLITE_DENY       # ATTACH, DETACH, DELETE, DROP, other pragmas, ...


def _read_authorizer(action, arg1, arg2, _db, _trigger):
    if action in _READ_ACTIONS or (action == sqlite3.SQLITE_PRAGMA and arg1 == 'table_info'):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def load_snapshot(sql_text: str) -> sqlite3.Connection:
    """Execute a snapshot export in a private in-memory database, then lock it read-only."""
    if len(sql_text) > _MAX_SQL_BYTES:
        raise ValueError('snapshot too large')
    con = sqlite3.connect(':memory:')
    con.set_authorizer(_load_authorizer)
    steps = [0]

    def budget():
        steps[0] += 1
        return 1 if steps[0] > 5_000_000 else 0   # abort runaway statements
    con.set_progress_handler(budget, 1000)
    con.executescript(sql_text)
    con.set_progress_handler(None, 0)
    con.set_authorizer(_read_authorizer)
    return con


def tables(con) -> dict[str, list[str]]:
    names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    return {n: [r[1] for r in con.execute(f'PRAGMA table_info("{n}")')] for n in names}


def rows(con, table: str):
    """Fixed read template; the table name comes from sqlite_master, never from a caller."""
    if table not in tables(con):
        raise KeyError(table)
    cur = con.execute(f'SELECT rowid, * FROM "{table}" ORDER BY rowid')
    cols = [d[0] for d in cur.description]
    for values in cur:
        yield dict(zip(cols, values))


def _header(sql_text):
    def find(pattern):
        m = re.search(pattern, sql_text, re.M)
        return m.group(1) if m else None
    return {'captured_at': find(r'^-- Captured at: (\S+)'),
            'completed_at': find(r'^-- snapshot_completed_at=(\S+)'),
            'topology': find(r'^-- Database topology: (\S+)')}


def _location(table: str) -> str:
    """customer_data_read_replica -> read_replica; *_primary and plain tables -> primary."""
    for prefix in ('customer_data_', 'customer_profiles_', 'search_documents_'):
        if table.startswith(prefix):
            suffix = table[len(prefix):]
            return LOGICAL if suffix == 'primary' else suffix
    return LOGICAL


def _collection(table: str) -> str:
    for suffix in ('_primary', '_failover_replica', '_read_replica', '_reporting_replica'):
        if table.endswith(suffix):
            return table[: -len(suffix)]
    return table


def parse_sql(source_id, available_at, raw: bytes):
    observations, scopes, issues = [], [], []
    text = raw.decode('utf-8')
    con = load_snapshot(text)
    head = _header(text)
    schema = tables(con)

    scope_times = {}
    if 'snapshot_scope' in schema:
        scope_times = {r['origin_system']: r['captured_at'] for r in rows(con, 'snapshot_scope')}
    stamps = {s for s in [head['captured_at'], head['completed_at'], *scope_times.values()] if s}
    if len(stamps) != 1:
        # No single point in time: the snapshot cannot say what was true when, so it is quarantined.
        issues.append(f'TEMPORAL_CONFLICT: snapshot times {sorted(stamps)}; no state emitted')
        return observations, scopes, issues
    observed_at = parse_utc(stamps.pop(), 'snapshot time')

    json_tables = [t for t, cols in schema.items() if 'record_json' in cols]

    def emit(table, row, system, category, record):
        facts = record_facts(category, record)
        observations.append(Observation(
            source_id=source_id, available_at=available_at, observed_at=observed_at,
            locator=f'{table}#rowid={row["rowid"]}', excerpt=json.dumps(record, ensure_ascii=False, sort_keys=True)[:400],
            kind=STATE, proof='SNAPSHOT_ROW', system_id=system, resource_id=record['resource_id'],
            location=_location(table), category=category, **facts))
        return facts['tokens']

    for table in json_tables:
        # A JSON-in-SQL table establishes coverage only for the collections it holds; a
        # collection seen in another table of the same file says nothing about this one.
        members = {}
        for row in rows(con, table):
            record = json.loads(row['record_json'])
            if record.get('resource_id') != row['resource_id']:
                issues.append(f'CONTRADICTORY_STATE {table}#{row["rowid"]}: resource_id mismatch')
                continue
            if row['collection_name'] not in COLLECTIONS:
                issues.append(f'UNSUPPORTED_CATEGORY {table}: collection {row["collection_name"]!r}')
                continue
            if isinstance(record.get('document'), dict):
                record = {**{k: v for k, v in record.items() if k != 'document'}, **record['document']}
            category = COLLECTIONS[row['collection_name']][0]
            toks = emit(table, row, row['origin_system'], category, record)
            ids, tok = members.setdefault((row['origin_system'], row['collection_name']), (set(), set()))
            ids.add(row['resource_id'])
            tok |= toks
        for (system, collection), (ids, tok) in members.items():
            if collection in COLLECTIONS:
                scopes.append(Scope(source_id, available_at, observed_at, system, COLLECTIONS[collection][0],
                                    _location(table), frozenset(ids), frozenset(tok), table, 'FULL_TABLE'))

    for table in schema:
        if table in json_tables or table == 'snapshot_scope':
            continue
        collection = _collection(table)
        if collection not in COLLECTIONS:
            issues.append(f'UNSUPPORTED_CATEGORY: table {table!r}')
            continue
        category, system = COLLECTIONS[collection]
        ids, tok = set(), set()
        for row in rows(con, table):
            record = {k: v for k, v in row.items() if k != 'rowid'}
            tok |= emit(table, row, system, category, record)
            ids.add(row['resource_id'])
        if scope_times and system not in scope_times:
            # Without a declared capture for this system the table cannot prove absence.
            issues.append(f'NOTE: {table} ({len(ids)} rows) not covered by snapshot_scope; no absence scope')
            continue
        scopes.append(Scope(source_id, available_at, observed_at, system, category, _location(table),
                            frozenset(ids), frozenset(tok), table, 'FULL_TABLE'))
    con.close()
    return observations, scopes, issues
