"""Read-only discovery operations and the investigator's evidence tools (plan section 14.2).

Every operation:
- takes registry handles (file_id, resource_id, identifier strings), never paths or SQL;
- filters by the case cutoff (source available AND state observed at or before it);
- returns at most `limit` items with an explicit `truncated` flag.
Nothing here writes to the dataset or decides a deletion status.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from .corpus import Corpus, eligible
from .identity import IdentityIndex, resolve_subject
from .observations import EMAIL_IN_TEXT, STATE, Observation, classify_token
from .parsers import sniff
from .parsers.pdfsrc import PdfError, extract_pdf_text
from .parsers.sqlsrc import load_snapshot, rows, tables
from .policy import Policy, PolicyError
from .state import reconstruct

MAX_LIMIT = 200


class ToolError(ValueError):
    """Invalid handle or argument."""


def _bounded(items, limit):
    limit = max(1, min(int(limit), MAX_LIMIT))
    return {'items': items[:limit], 'returned': min(len(items), limit), 'total': len(items),
            'truncated': len(items) > limit}


def _view(o: Observation) -> dict:
    return {'handle': o.obs_id, 'source_id': o.source_id, 'kind': o.kind, 'proof': o.proof,
            'system_id': o.system_id, 'resource_id': o.resource_id, 'location': o.location,
            'category': o.category, 'observed_at': o.observed_at.isoformat(),
            'available_at': o.available_at.isoformat(), 'presence': o.presence,
            'exposed': list(o.exposed) if o.exposed is not None else None, 'enabled': o.enabled,
            'restore_allowed': o.restore_allowed, 'excerpt': o.excerpt}


class Tools:
    def __init__(self, corpus: Corpus, index: IdentityIndex, policy: Policy):
        self.corpus, self.index, self.policy = corpus, index, policy
        self.manifest = {m.file_id: m for m in corpus.context.manifest}

    def _file(self, file_id):
        if file_id not in self.manifest:
            raise ToolError(f'unknown file_id {file_id!r}')
        return self.manifest[file_id]

    def inspect_source(self, file_id: str, cutoff: datetime, limit: int = 50) -> dict:
        entry = self._file(file_id)
        if entry.captured_at > cutoff:
            return {'file_id': file_id, 'eligible': False, 'reason': 'captured after cutoff'}
        report = next(r for r in self.corpus.reports if r.source_id == file_id)
        obs = [_view(o) for o in self.corpus.by_source.get(file_id, ()) if eligible(o, cutoff)]
        return {'file_id': file_id, 'eligible': True, 'path': entry.file_path, 'status': report.status,
                'issues': report.issues, 'observations': _bounded(obs, limit)}

    def search_identifiers(self, identifiers: list[str], cutoff: datetime, limit: int = 50,
                           user_references: list[str] = ()) -> dict:
        """Exact search. `identifiers` are classified by value (email, hash, phone, user_<n>);
        `user_references` are stable user IDs in any format."""
        tokens, unmatched = {('user', r) for r in user_references}, []
        for value in identifiers:
            token = classify_token(value)
            (tokens.add(token) if token else unmatched.append(value))
        found = {o.obs_id: o for t in tokens for o in self.corpus.token_observations(t, cutoff)}
        items = [_view(o) for o in sorted(found.values(), key=lambda o: (o.observed_at, o.obs_id))]
        return {'unrecognized_identifiers': unmatched, 'observations': _bounded(items, limit)}

    def expand_identity(self, email: str, cutoff: datetime) -> dict:
        subject = resolve_subject(email, cutoff, self.index, self.corpus)
        return {'user_ids': subject.user_ids, 'issues': subject.issues,
                'identifiers': [{'kind': i.kind, 'value': i.original, 'valid_from':
                                 i.valid_from.isoformat() if i.valid_from else None,
                                 'provenance': i.provenance} for i in subject.identifiers]}

    def query_sql_snapshot(self, file_id: str, table: str, cutoff: datetime,
                           resource_ids: list[str] | None = None, limit: int = 50) -> dict:
        """Fixed read template over one snapshot table; filtering happens in Python."""
        entry = self._file(file_id)
        if entry.captured_at > cutoff:
            return {'file_id': file_id, 'eligible': False, 'reason': 'captured after cutoff'}
        raw = (self.corpus.context.root / entry.file_path).read_text(encoding='utf-8')
        if 'CREATE TABLE' not in raw:
            raise ToolError(f'{file_id} is not a SQL snapshot')
        con = load_snapshot(raw)
        try:
            if table not in tables(con):
                raise ToolError(f'unknown table {table!r} in {file_id}')
            wanted = set(resource_ids or ())
            found = [r for r in rows(con, table) if not wanted or r.get('resource_id') in wanted]
        finally:
            con.close()
        return {'file_id': file_id, 'table': table, 'exhaustive_table_listing': True,
                'rows': _bounded(found, limit)}

    def activity_timeline(self, resource_id: str, cutoff: datetime, limit: int = 50) -> dict:
        obs = sorted(self.corpus.resource_observations(resource_id, cutoff), key=lambda o: (o.observed_at, o.obs_id))
        return {'resource_id': resource_id, 'note': 'CLAIM entries report work, not resulting state',
                'events': _bounded([_view(o) for o in obs], limit)}

    def trace_flow(self, resource_id: str, cutoff: datetime, limit: int = 50) -> dict:
        """Connector/restore observations for a resource and claims that it repopulated data."""
        own = [o for o in self.corpus.resource_observations(resource_id, cutoff)
               if o.enabled is not None or o.restore_allowed is not None]
        repopulation = [o for o in self.corpus.resource_observations(resource_id, cutoff)
                        if 'repopulated' in o.excerpt]
        return {'resource_id': resource_id,
                'process_state': _bounded([_view(o) for o in own], limit),
                'repopulation_claims': _bounded([_view(o) for o in repopulation], limit)}

    def check_retention(self, system_id: str, resource_id: str, cutoff: datetime) -> dict:
        state = reconstruct(self.corpus, system_id, resource_id, cutoff)
        result = {'resource_id': resource_id, 'categories': sorted(state.categories), 'presence': state.presence,
                  'retention_until': state.retention_until.isoformat() if state.retention_until else None}
        try:
            result['treatments'] = {c: self.policy.treatment(c, retention_until=state.retention_until,
                                                             audit_at=cutoff).__dict__ for c in state.categories}
        except PolicyError as exc:
            result['policy_issue'] = str(exc)
        return result


# --- Phase 4: line-addressable source text and the investigator's tool surface ----------------------

MAX_RANGE_LINES = 12          # longest literal span a fact may cite
MAX_RESULT_CHARS = 6000       # bound on one serialized tool result
_LOCATOR = re.compile(r'^(?:(p\d+):)?L(\d+)(?:-L?(\d+))?$')
_ID_SHAPED = re.compile(r'(?<![\w.@-])[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+(?![\w@-])')   # resource-handle shape


class SourceText:
    """Every citable source as numbered lines. Locators are `L<n>`, `L<a>-L<b>` or, for PDFs,
    `p<page>:L<n>` (ranges stay on one page). File text is verbatim; activity and identity-audit rows
    are one line each; screenshots are their cached reader extraction, one field per line."""

    def __init__(self, context, media_extractions=None):
        self.context = context
        self.media = media_extractions or {}
        self.refs = context.evidence                   # source_id -> EvidenceRef (availability)
        self.manifest = {m.file_id: m for m in context.manifest}
        self.activities = {a.activity_id: a for a in context.activities}
        self.audits = {a.audit_id: a for a in context.audit_log}
        self._cache = {}

    def kind(self, source_id) -> str:
        if source_id in self.activities:
            return 'activity'
        if source_id in self.audits:
            return 'identity_audit'
        entry = self.manifest.get(source_id)
        if entry is None:
            raise ToolError(f'unknown source handle {source_id!r}')
        return sniff((self.context.root / entry.file_path).read_bytes()[:4096])

    def pages(self, source_id) -> dict[str, list[str]]:
        """{page prefix ('' for unpaged): lines}."""
        if source_id in self._cache:
            return self._cache[source_id]
        kind = self.kind(source_id)
        if kind == 'activity':
            a = self.activities[source_id]
            out = {'': [f'{a.event_time.isoformat()} {a.event_type} {a.source_system} -> {a.target_system} '
                        f'{a.resource_id} result={a.reported_result} details: {a.details}']}
        elif kind == 'identity_audit':
            a = self.audits[source_id]
            out = {'': [f'{a.event_time.isoformat()} {a.user_id} {a.field_name}: {a.old_value} -> {a.new_value}']}
        else:
            raw = (self.context.root / self.manifest[source_id].file_path).read_bytes()
            if kind == 'pdf':
                try:
                    out = {f'p{i}': lines for i, lines in enumerate(extract_pdf_text(raw), start=1)}
                except (PdfError, ValueError, KeyError):
                    out = {}
            elif kind == 'image':
                ext = self.media.get(source_id) or {}
                out = {'': [f'{rid} | {field}: {value}' for rid, fields in sorted((ext.get('entities') or {}).items())
                            for field, value in fields.items()]} if ext.get('entities') else {}
            else:
                out = {'': raw.decode('utf-8', 'replace').splitlines()}
        self._cache[source_id] = out
        return out

    def available(self, source_id, cutoff) -> bool:
        ref = self.refs.get(source_id)
        if ref is None:
            raise ToolError(f'unknown source handle {source_id!r}')
        return ref.available_at <= cutoff

    def span(self, source_id, locator) -> str:
        """The verbatim text at `locator` (lines joined by newlines)."""
        m = _LOCATOR.match(locator or '')
        if not m:
            raise ToolError(f'malformed locator {locator!r}')
        page, a, b = m.group(1) or '', int(m.group(2)), int(m.group(3) or m.group(2))
        lines = self.pages(source_id).get(page)
        if lines is None or not 1 <= a <= b <= len(lines) or b - a + 1 > MAX_RANGE_LINES:
            raise ToolError(f'locator {locator!r} outside {source_id} or longer than {MAX_RANGE_LINES} lines')
        return '\n'.join(lines[a - 1:b])

    def lines(self, source_id):
        for page, lines in self.pages(source_id).items():
            for n, line in enumerate(lines, start=1):
                yield (f'{page}:L{n}' if page else f'L{n}'), line


def locator_lines(locator: str) -> set:
    """The individual line locators a (range) locator covers."""
    m = _LOCATOR.match(locator or '')
    if not m:
        raise ToolError(f'malformed locator {locator!r}')
    page, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
    return {f'{page}:L{n}' if page else f'L{n}' for n in range(a, b + 1)}


def _clip(result: dict) -> dict:
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) <= MAX_RESULT_CHARS:
        return result
    return {'truncated': True, 'note': f'result exceeded {MAX_RESULT_CHARS} characters; narrow the request',
            'preview': text[:MAX_RESULT_CHARS]}


def _fn(name, description, properties, required):
    return {'type': 'function', 'function': {'name': name, 'description': description, 'parameters': {
        'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}}}


_STR, _INT = {'type': 'string'}, {'type': 'integer'}
FACT_KINDS = ('OWNERSHIP', 'STATE', 'CATEGORY', 'RETENTION')
TOOL_SCHEMAS = [
    _fn('search_identifiers', 'Exact search of eligible sources for identifiers of this subject or for '
        'resource handles already returned to you.', {'values': {'type': 'array', 'items': _STR}}, ['values']),
    _fn('inspect_source', 'Read numbered lines of one eligible source (file_id, activity_id or audit_id).',
        {'source_id': _STR, 'start_line': _INT, 'max_lines': _INT, 'page': _STR}, ['source_id']),
    _fn('inspect_media', 'Read a PDF page or the cached reader extraction of a screenshot, with recognized '
        'identifiers and unreadable fields.', {'file_id': _STR, 'page': _STR}, ['file_id']),
    _fn('activity_timeline', 'Eligible observations and workflow claims for one resource handle, oldest first.',
        {'resource_id': _STR}, ['resource_id']),
    _fn('query_sql_snapshot', 'Rows of one table of an eligible SQL snapshot whose column equals a value '
        '(an identifier of this subject or a returned resource handle).',
        {'file_id': _STR, 'table': _STR, 'column': _STR, 'value': _STR}, ['file_id', 'table', 'column', 'value']),
    _fn('propose_fact', 'Propose one evidence-backed fact. `literal` must be copied verbatim from the cited '
        'source lines at `locator`.', {
            'kind': {'type': 'string', 'enum': list(FACT_KINDS)}, 'system_id': _STR, 'resource_id': _STR,
            'source_id': _STR, 'locator': _STR, 'literal': _STR, 'value': _STR}, [
            'kind', 'system_id', 'resource_id', 'source_id', 'locator', 'literal', 'value']),
    _fn('submit_case_file', 'Submit the case file for independent verification.', {
        'resolution': {'type': 'string', 'enum': ['RESOLVED', 'NO_SUPPORTED_ACTION']}, 'notes': _STR},
        ['resolution']),
]
EVIDENCE_TOOLS = ('search_identifiers', 'inspect_source', 'inspect_media', 'activity_timeline', 'query_sql_snapshot')


class InvestigationTools:
    """The five read-only evidence tools, bound to one case file (cutoff, subject, scope, handles).
    Arguments arrive already schema-checked (`tool_call_problem`); handles are checked here."""

    def __init__(self, tools: Tools, texts: SourceText, casefile):
        self.tools, self.texts, self.case = tools, texts, casefile

    # -- scope ---------------------------------------------------------------------------------
    def _allowed_value(self, value):
        return value in self.case.identifier_values or value in self.case.handles

    def _eligible_source(self, source_id):
        if not self.texts.available(source_id, self.case.audit_at):
            raise ToolError(f'{source_id} became available after audit_at; it cannot be used for this case')

    def _register(self, result, displayed):
        """Record what the model was actually shown. `displayed` is [(source_id, locator | None, text,
        complete)] for every item in `result`; a clipped result keeps only items whose text survives in
        the preview. Shown sources become inspected, fully shown lines become citable, and resource IDs
        in shown text become handles. Nothing hidden is ever registered."""
        preview = result.get('preview') if result.get('truncated') and 'preview' in result else None
        for source_id, locator, text, complete in displayed:
            if preview is not None and json.dumps(text, ensure_ascii=False)[1:-1] not in preview:
                continue
            self.case.inspected.add(source_id)
            if locator and complete:
                self.case.shown.setdefault(source_id, set()).add(locator)
            self.case.handles.update(_ID_SHAPED.findall(text))

    # -- tools: each returns (result, displayed) -------------------------------------------------
    def search_identifiers(self, values):
        values = [v for v in values if isinstance(v, str) and v.strip()][:5]
        rejected = [v for v in values if not self._allowed_value(v)]
        if rejected or not values:
            raise ToolError(f'not identifiers of this subject or returned handles: {rejected or values}')
        hits = []
        for source_id in sorted(self.texts.refs):
            if not self.texts.available(source_id, self.case.audit_at) or source_id in self.texts.activities \
                    or source_id in self.texts.audits:
                continue
            for locator, line in self.texts.lines(source_id):
                if any(re.search(rf'(?<![\w.@+-]){re.escape(v)}(?![\w@-])', line, re.IGNORECASE) for v in values):
                    hits.append((source_id, locator, line))
        activity = [a.activity_id for a in self.texts.activities.values() if a.event_time <= self.case.audit_at
                    and any(v == a.resource_id for v in values)]
        shown = hits[:25]
        return ({'matches': [{'source_id': s, 'locator': loc, 'text': line[:200]} for s, loc, line in shown],
                 'total_matches': len(hits), 'truncated': len(hits) > 25,
                 'activity_ids_naming_value': activity[:25], 'activity_truncated': len(activity) > 25},
                [(s, loc, line[:200], len(line) <= 200) for s, loc, line in shown])

    def inspect_source(self, source_id, start_line=1, max_lines=40, page=''):
        self._eligible_source(source_id)
        pages = self.texts.pages(source_id)
        page = page or next(iter(pages), '')
        if page not in pages:
            raise ToolError(f'{source_id} has no page {page!r}; pages: {sorted(pages)}')
        lines, start = pages[page], max(1, start_line)
        count = max(1, min(max_lines, 60))
        prefix = f'{page}:' if page else ''
        numbers = range(start, min(len(lines), start + count - 1) + 1)
        entry = self.texts.manifest.get(source_id)
        parsed = [o for o in self.tools.corpus.by_source.get(source_id, ()) if eligible(o, self.case.audit_at)]
        return ({'source_id': source_id, 'origin': entry.origin if entry else self.texts.kind(source_id),
                 'available_at': self.texts.refs[source_id].available_at.isoformat(), 'page': page,
                 'lines': [{'locator': f'{prefix}L{n}', 'text': lines[n - 1][:300]} for n in numbers],
                 'total_lines': len(lines), 'truncated': start + count - 1 < len(lines),
                 'parsed_observations': len(parsed)},
                [(source_id, f'{prefix}L{n}', lines[n - 1][:300], len(lines[n - 1]) <= 300) for n in numbers])

    def inspect_media(self, file_id, page=''):
        self._eligible_source(file_id)
        kind = self.texts.kind(file_id)
        if kind not in ('pdf', 'image'):
            raise ToolError(f'{file_id} is not a PDF or screenshot')
        result = {'file_id': file_id, 'media': kind}
        if kind == 'image':
            ext = self.texts.media.get(file_id) or {}
            if not ext.get('entities'):
                return {**result, 'interpreted': False,
                        'uncertainty': 'no cached reader extraction for this screenshot; nothing can be cited'}, []
            lines = list(self.texts.lines(file_id))[:60]
            return ({**result, 'interpreted': True, 'reader_model': ext.get('model'),
                     'page_kind': ext.get('page_kind'), 'unreadable': ext.get('unreadable', []),
                     'rejected_ids': ext.get('rejected_ids', []),
                     'lines': [{'locator': loc, 'text': t} for loc, t in lines]},
                    [(file_id, loc, t, True) for loc, t in lines])
        pages = self.texts.pages(file_id)
        if not pages:
            return {**result, 'interpreted': False, 'uncertainty': 'native text extraction failed'}, []
        page = page or 'p1'
        if page not in pages:
            raise ToolError(f'{file_id} has no page {page!r}; pages: {sorted(pages)}')
        lines = [(f'{page}:L{n}', t) for n, t in enumerate(pages[page], start=1)][:60]
        text = '\n'.join(t for _, t in lines)
        return ({**result, 'interpreted': True, 'page': page, 'pages': sorted(pages),
                 'lines': [{'locator': loc, 'text': t} for loc, t in lines],
                 'truncated': len(pages[page]) > 60,
                 'identifiers': sorted(set(EMAIL_IN_TEXT.findall(text)) | set(_ID_SHAPED.findall(text))),
                 'note': 'native text; the document kind is not asserted by this tool'},
                [(file_id, loc, t, True) for loc, t in lines])

    def activity_timeline(self, resource_id):
        if resource_id not in self.case.handles:
            raise ToolError(f'{resource_id!r} is not a candidate or returned resource handle')
        events = sorted(self.tools.corpus.resource_observations(resource_id, self.case.audit_at),
                        key=lambda o: (o.observed_at, o.obs_id))[:30]
        items = [{'source_id': o.source_id, 'locator': o.locator, 'kind': o.kind, 'proves_state': o.kind == STATE,
                  'system_id': o.system_id, 'location': o.location, 'presence': o.presence,
                  'observed_at': o.observed_at.isoformat(), 'excerpt': o.excerpt[:160]} for o in events]
        total = len(self.tools.corpus.resource_observations(resource_id, self.case.audit_at))
        # Excerpts are summaries, not source lines: they yield handles but no citable lines.
        return ({'resource_id': resource_id, 'note': 'CLAIM entries report work, never the resulting state',
                 'items': items, 'returned': len(items), 'total': total, 'truncated': total > len(items)},
                [(o.source_id, None, o.excerpt[:160], False) for o in events])

    def query_sql_snapshot(self, file_id, table, column, value):
        self._eligible_source(file_id)
        if not self._allowed_value(value):
            raise ToolError(f'{value!r} is not an identifier of this subject or a returned handle')
        raw = (self.texts.context.root / self.texts.manifest[file_id].file_path).read_text(encoding='utf-8') \
            if file_id in self.texts.manifest else ''
        if 'CREATE TABLE' not in raw:
            raise ToolError(f'{file_id} is not a SQL snapshot')
        con = load_snapshot(raw)
        try:
            schema = tables(con)
            if table not in schema:
                raise ToolError(f'unknown table {table!r}; tables: {sorted(schema)}')
            if column not in schema[table]:
                raise ToolError(f'unknown column {column!r} in {table}; columns: {schema[table]}')
            found = [r for r in rows(con, table) if str(r.get(column)) == value]
        finally:
            con.close()
        lines = raw.splitlines()
        insert = re.compile(rf'INSERT INTO "?{re.escape(table)}"?[ (]')
        for r in found:
            key = str(r.get('resource_id') or value)
            r['locator'] = next((f'L{n}' for n, line in enumerate(lines, start=1)
                                 if insert.match(line) and key in line), None)
            r.pop('rowid', None)
        page = found[:20]
        return ({'file_id': file_id, 'table': table, 'filter': {column: value},
                 'note': 'complete for this table at the snapshot time',
                 'items': page, 'returned': len(page), 'total': len(found), 'truncated': len(found) > 20},
                [(file_id, r['locator'], json.dumps({k: v for k, v in r.items() if k != 'locator'}, default=str), True)
                 for r in page])

    def call(self, name, arguments):
        if name not in EVIDENCE_TOOLS:
            raise ToolError(f'unknown tool {name!r}')
        result, displayed = getattr(self, name)(**arguments)
        result = _clip(result)
        self._register(result, displayed)
        return result
