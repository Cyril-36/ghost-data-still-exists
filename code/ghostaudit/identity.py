"""Deterministic, time-aware identity resolution.

1. Rebuild each user's identifier history from the current snapshot plus the
   identity audit log. Each value carries its validity interval AND the time the
   evidence establishing it became available: snapshot values at the snapshot's
   updated_at, audit values at the audit entry's event_time. A value is known at
   a cutoff only if it was valid by then and some establishing source was
   available by then (a later audit entry cannot reveal an earlier alias).
2. Match the request email (casefolded key) against any value valid at or
   before audit_at. Zero or several matching users is reported, never guessed.
3. Close over explicit evidence: profile/tombstone rows and catalog links that
   tie a new identifier to an already-proven one add that identifier, unless it
   belongs to a different known person.

Hashes are SHA-256 of the original email bytes; matching uses casefolded keys.
Names never establish identity.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .corpus import Corpus
from .observations import LINK, email_key, phone_key

FIELD_KIND = {'email': 'email', 'phone': 'phone', 'email_hash': 'hash'}
CLOSURE_CATEGORIES = {'USER_PROFILE', 'ACCOUNT_TOMBSTONE'}


@dataclass(frozen=True)
class Identifier:
    kind: str           # user | email | hash | phone
    key: str            # matching key
    original: str       # literal value as supplied
    valid_from: datetime | None
    valid_to: datetime | None
    provenance: str     # user_identities | audit_id | evidence source id | request
    available_at: datetime | None = None   # earliest availability of a source establishing it; None = request input


@dataclass
class SubjectIdentity:
    request_email: str
    user_ids: list[str] = field(default_factory=list)
    identifiers: list[Identifier] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def tokens(self) -> frozenset:
        return frozenset((i.kind, i.key) for i in self.identifiers)

    @property
    def resolved(self) -> bool:
        return len(self.user_ids) == 1


def _key(kind, value):
    return email_key(value) if kind == 'email' else phone_key(value) if kind == 'phone' else value.lower()


def _sha256(email: str) -> str:
    return hashlib.sha256(email.encode('utf-8')).hexdigest()


class IdentityIndex:
    """Per-user identifier histories built once from the context CSVs."""

    def __init__(self, context):
        self.history = defaultdict(list)          # user_id -> [Identifier]
        self.owner = defaultdict(set)             # (kind, key) -> {user_id}; time-agnostic, for conflict guards
        self.issues = defaultdict(list)
        changes = defaultdict(list)
        for change in context.audit_log:
            changes[change.user_id].append(change)
        for ident in context.identities:
            uid = ident.user_id
            current = {f: getattr(ident, f) for f in FIELD_KIND}
            since = {f: None for f in FIELD_KIND}          # start of the current value's validity
            # Earliest availability of evidence for the value currently being walked back:
            # the snapshot first, then each audit entry that names it.
            evidence = {f: ident.updated_at for f in FIELD_KIND}
            source = {f: 'user_identities' for f in FIELD_KIND}
            own_changes = changes.pop(uid, [])
            user_known = min([ident.updated_at] + [c.event_time for c in own_changes])
            for ch in sorted(own_changes, key=lambda c: c.event_time, reverse=True):
                if ch.field_name not in FIELD_KIND:
                    continue
                f = ch.field_name
                if current[f] != ch.new_value:
                    self.issues[uid].append(f'TEMPORAL_CONFLICT: {ch.audit_id} new_value does not match '
                                            f'the later {f}')
                self._add(uid, f, ch.new_value, ch.event_time, since[f], ch.audit_id,
                          min(evidence[f], ch.event_time))
                # The old value is established by this entry (available at its event_time) and,
                # if an earlier entry set it, by that earlier entry too (handled on the next step).
                current[f], since[f], evidence[f], source[f] = ch.old_value, ch.event_time, ch.event_time, ch.audit_id
            for f in FIELD_KIND:
                self._add(uid, f, current[f], None, since[f], source[f], evidence[f])
            self.history[uid].append(Identifier('user', uid, uid, None, None, 'user_identities', user_known))
            self.owner[('user', uid)].add(uid)
        for uid in changes:
            self.issues[uid].append('INPUT_SCHEMA_ERROR: audit entries for a user missing from user_identities')
        # An earlier audit entry that set a value also establishes it: keep the earliest availability.
        for uid, items in self.history.items():
            earliest = {}
            for i in items:
                k = (i.kind, i.key, i.valid_from, i.valid_to)
                if i.available_at is not None:
                    earliest[k] = min(earliest.get(k, i.available_at), i.available_at)
            self.history[uid] = [Identifier(i.kind, i.key, i.original, i.valid_from, i.valid_to, i.provenance,
                                            earliest.get((i.kind, i.key, i.valid_from, i.valid_to), i.available_at))
                                 for i in items]

    def _add(self, uid, field_name, value, valid_from, valid_to, provenance, available_at):
        if not value:
            return
        kind = FIELD_KIND[field_name]
        ident = Identifier(kind, _key(kind, value), value, valid_from, valid_to, provenance, available_at)
        self.history[uid].append(ident)
        self.owner[(kind, ident.key)].add(uid)
        if kind == 'email':   # the hash a system would store for this exact email
            h = Identifier('hash', _sha256(value), _sha256(value), valid_from, valid_to, f'sha256({provenance})',
                           available_at)
            self.history[uid].append(h)
            self.owner[('hash', h.key)].add(uid)

    def known_by(self, uid, cutoff) -> list[Identifier]:
        """Identifiers valid at or before the cutoff AND established by a source available by then."""
        return [i for i in self.history[uid]
                if (i.valid_from is None or i.valid_from <= cutoff)
                and (i.available_at is None or i.available_at <= cutoff)]


def resolve_subject(email: str, cutoff: datetime, index: IdentityIndex, corpus: Corpus | None = None,
                    max_rounds: int = 5) -> SubjectIdentity:
    subject = SubjectIdentity(email)
    seed = ('email', email_key(email))
    users = sorted(uid for uid in index.owner.get(seed, ())
                   if any(i.kind == 'email' and i.key == seed[1] for i in index.known_by(uid, cutoff)))
    if not users:
        subject.issues.append('IDENTITY_UNMATCHED: request email not found in identities valid by audit_at')
        subject.identifiers.append(Identifier('email', seed[1], email, None, None, 'request'))
        subject.identifiers.append(Identifier('hash', _sha256(email), _sha256(email), None, None, 'sha256(request)'))
        return subject
    if len(users) > 1:
        subject.issues.append(f'IDENTITY_AMBIGUOUS: request email maps to {users}')
        return subject
    uid = users[0]
    subject.user_ids = [uid]
    subject.identifiers = list(index.known_by(uid, cutoff))
    subject.issues += index.issues.get(uid, [])
    if corpus is not None:
        _close(subject, uid, cutoff, index, corpus, max_rounds)
    return subject


def _close(subject, uid, cutoff, index, corpus, max_rounds):
    """Add identifiers that eligible profile/tombstone rows or catalog links bind to the subject."""
    for _ in range(max_rounds):
        known = subject.tokens
        added = False
        for token in list(known):
            for obs in corpus.token_observations(token, cutoff):
                if obs.kind != LINK and obs.category not in CLOSURE_CATEGORIES:
                    continue
                for new in obs.tokens - known:
                    others = index.owner.get(new, set()) - {uid}
                    if others:
                        subject.issues.append(f'IDENTITY_CONFLICT: {obs.obs_id} ties {new[0]} owned by {sorted(others)}')
                        continue
                    subject.identifiers.append(Identifier(new[0], new[1], new[1], obs.observed_at, None, obs.source_id,
                                                         obs.available_at))
                    known = known | {new}
                    added = True
        if not added:
            return
    subject.issues.append('INVESTIGATION_BUDGET_EXHAUSTED: identity closure did not converge')

