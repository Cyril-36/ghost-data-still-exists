"""Pipe-separated operational handover text.

    - TIMESTAMP | system | resource_id | RECORD_TYPE | RESULT | subject

Rows carry explicit lookup semantics (same vocabulary as JSON family A).
Non-row lines (titles, prose) are counted, not interpreted.
"""
from __future__ import annotations

import re

from ..contracts import parse_utc
from ..observations import STATE, Observation, reference_tokens
from .records import result_facts

_ROW = re.compile(r'^\s*-\s*(\S+)\s*\|\s*([\w-]+)\s*\|\s*([\w-]+)\s*\|\s*([A-Z_]+)\s*\|\s*([A-Z_]+)\s*\|\s*(.+?)\s*$')


def parse_text(source_id, available_at, raw: bytes):
    observations, issues = [], []
    rowlike = 0
    for n, line in enumerate(raw.decode('utf-8').splitlines(), start=1):
        if line.count('|') >= 5:
            rowlike += 1
        m = _ROW.match(line)
        if not m:
            continue
        ts, system, resource, record_type, result, subject = m.groups()
        try:
            facts = result_facts(record_type, result)
            when = parse_utc(ts, f'line {n}')
        except ValueError as exc:
            issues.append(f'PARSE_ERROR line {n}: {exc}')
            continue
        observations.append(Observation(
            source_id=source_id, available_at=available_at, observed_at=when, locator=f'line {n}',
            excerpt=line.strip()[:400], kind=STATE, proof='EXACT_LOOKUP', system_id=system,
            resource_id=resource, category=record_type, tokens=reference_tokens(subject), **facts))
    if rowlike != len(observations) + sum(1 for i in issues if i.startswith('PARSE_ERROR')):
        issues.append(f'PARTIAL_SCOPE: {rowlike} pipe rows, {len(observations)} parsed')
    if not observations:
        issues.append('UNRECOGNIZED_FORMAT: no handover rows found')
    return observations, [], issues
