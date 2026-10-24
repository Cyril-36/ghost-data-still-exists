"""Read-only discovery operations for the future investigator (plan section 14.2).

Every operation:
- takes registry handles (file_id, resource_id, identifier strings), never paths or SQL;
- filters by the case cutoff (source available AND state observed at or before it);
- returns at most `limit` items with an explicit `truncated` flag.
Nothing here writes to the dataset or decides a deletion status.
"""
from __future__ import annotations

from datetime import datetime

from .corpus import Corpus, eligible
from .identity import IdentityIndex, resolve_subject
from .observations import Observation, classify_token
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
