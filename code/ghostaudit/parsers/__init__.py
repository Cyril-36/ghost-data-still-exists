"""Evidence parsers. `parse_source` picks a parser from the file's content signature."""
from __future__ import annotations

from ..observations import SourceReport
from .jsonsrc import parse_json
from .logsrc import parse_log
from .sqlsrc import parse_sql
from .textsrc import parse_text

_PNG = b'\x89PNG\r\n\x1a\n'


def sniff(raw: bytes) -> str:
    """Content signature first; extensions are not trusted."""
    if raw.startswith(_PNG):
        return 'image'
    if raw.startswith(b'%PDF-'):
        return 'pdf'
    head = raw[:4096].lstrip()
    if head.startswith((b'{', b'[')):
        return 'json'
    text = head.decode('utf-8', errors='replace')
    if 'CREATE TABLE' in text and ('BEGIN TRANSACTION' in text or text.startswith('--')):
        return 'sql'
    first = text.splitlines()[0] if text else ''
    if len(first) > 20 and first[:4].isdigit() and first[4] == '-' and 'T' in first[:20]:
        return 'log'
    return 'text'


PARSERS = {'json': parse_json, 'sql': parse_sql, 'log': parse_log, 'text': parse_text}


def parse_source(entry, raw: bytes):
    """Return (observations, scopes, SourceReport). Never raises for bad content."""
    kind = sniff(raw)
    report = SourceReport(entry.file_id, entry.file_path, kind)
    if kind in ('image', 'pdf'):
        report.status = 'UNSUPPORTED_MEDIA'
        report.issues.append(f'UNREADABLE_MEDIA: {kind} interpretation not implemented; facts not extracted')
        return [], [], report
    try:
        observations, scopes, issues = PARSERS[kind](entry.file_id, entry.captured_at, raw)
    except Exception as exc:  # any parser failure is reported, never treated as "no records"
        report.status = 'FAILED'
        report.issues.append(f'PARSE_ERROR: {type(exc).__name__}: {exc}')
        return [], [], report
    report.observations, report.scopes, report.issues = len(observations), len(scopes), issues
    if any(i.startswith(('PARSE_ERROR', 'PARTIAL_SCOPE', 'UNRECOGNIZED_FORMAT', 'CONTRADICTORY_STATE',
                         'TEMPORAL_CONFLICT'))
           for i in issues):
        report.status = 'PARTIAL'
    return observations, scopes, report
