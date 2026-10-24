"""Evidence observations: one extracted fact with full provenance.

An Observation never decides an obligation. It records what one source said
about one resource (or identity) at one time and location, so later stages can
apply cutoff, ownership and policy rules and cite the original source ID.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

# Kinds of observation.
STATE = 'STATE'      # direct readback / snapshot row / explicit lookup result
CLAIM = 'CLAIM'      # an operation was attempted or reported; never proves state
LINK = 'LINK'        # explicit identity/resource link with no state assertion

PRESENT, ABSENT = 'PRESENT', 'ABSENT'
LOGICAL = 'primary'  # service readbacks and primary tables describe the logical primary copy

# --- identity tokens -------------------------------------------------------------------

_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_USER = re.compile(r'^user_\d+$')
_HASH = re.compile(r'^[0-9a-fA-F]{64}$')
_PHONE = re.compile(r'^\+\d[\d\s().-]{6,}$')
EMAIL_IN_TEXT = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
PHONE_IN_TEXT = re.compile(r'\+\d[\d\s().-]{7,}\d')


def email_key(value: str) -> str:
    """Matching key only. Original bytes are kept elsewhere for hashing."""
    return value.strip().casefold()


def phone_key(value: str) -> str:
    return '+' + re.sub(r'\D', '', value)


def classify_token(value) -> tuple[str, str] | None:
    """Map a literal identifier to (kind, matching key); None if not an identifier.

    Names are deliberately never tokens: they cannot establish ownership.
    """
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v or v.lower() in ('null', 'none', '[redacted]'):
        return None
    if _USER.match(v):
        return ('user', v)
    if _EMAIL.match(v):
        return ('email', email_key(v))
    if _HASH.match(v):
        return ('hash', v.lower())
    if _PHONE.match(v):
        return ('phone', phone_key(v))
    return None


def tokens_from(*values) -> frozenset[tuple[str, str]]:
    return frozenset(t for t in map(classify_token, values) if t)


def reference_tokens(*values) -> frozenset[tuple[str, str]]:
    """Values of reference fields (user_id, customer_reference, subject_reference).

    By schema these hold a user reference unless the value is itself an email,
    hash or phone; any other single-token value is treated as a user reference.
    """
    found = set()
    for v in values:
        token = classify_token(v)
        if token is None and isinstance(v, str) and v.strip() and not re.search(r'\s', v.strip()) \
                and v.strip().lower() not in ('null', 'none', '[redacted]'):
            token = ('user', v.strip())
        if token:
            found.add(token)
    return frozenset(found)


def text_tokens(text) -> frozenset[tuple[str, str]]:
    """Identifier tokens embedded in free text (emails and phone numbers)."""
    if not isinstance(text, str):
        return frozenset()
    found = [('email', email_key(m)) for m in EMAIL_IN_TEXT.findall(text)]
    found += [('phone', phone_key(m)) for m in PHONE_IN_TEXT.findall(text)]
    return frozenset(found)


# --- observations -------------------------------------------------------------------------

@dataclass(frozen=True)
class Observation:
    source_id: str                 # original file_id or activity_id (the only citable IDs)
    available_at: datetime         # when the containing source became available
    observed_at: datetime          # when the described state held
    locator: str                   # json pointer / table#row / line number
    excerpt: str                   # bounded literal support
    kind: str                      # STATE | CLAIM | LINK
    system_id: str | None = None
    resource_id: str | None = None
    location: str = LOGICAL        # physical copy (primary or a named replica)
    category: str | None = None    # data category from explicit schema semantics
    proof: str = ''                # READBACK | SNAPSHOT_ROW | EXACT_LOOKUP | SCAN | CHECK | CLAIM
    presence: str | None = None    # PRESENT | ABSENT | None (not asserted)
    exposed: tuple[str, ...] | None = None   # prohibited identifier fields still populated; None = not assessed
    enabled: bool | None = None    # data flows
    restore_allowed: bool | None = None      # backups
    retention_until: datetime | None = None
    tokens: frozenset = frozenset()          # identity tokens tying this record to a person
    refs: tuple[str, ...] = ()               # explicitly referenced resource IDs
    attributes: tuple = ()                   # extra (key, value) pairs for diagnostics

    @property
    def obs_id(self) -> str:
        return f'{self.source_id}#{self.locator}'


@dataclass(frozen=True)
class Scope:
    """An exhaustive listing of one collection at one physical location and time.

    `coverage` says for whom the listing is exhaustive:
      FULL_TABLE      a table in a point-in-time database snapshot: complete for that
                      table, so omission proves absence there for anyone.
      SUBJECT_SCOPED  an export captured for a set of subjects; complete only for the
                      subjects that capture references (`subjects`, inferred from the
                      capture, not declared), so omission proves absence only for them.
    Exact lookups, readbacks and log lines are never Scopes.
    """
    source_id: str
    available_at: datetime
    observed_at: datetime
    system_id: str
    category: str
    location: str
    members: frozenset[str]
    member_tokens: frozenset = frozenset()   # identity tokens of every listed row
    locator: str = ''
    coverage: str = 'SUBJECT_SCOPED'
    subjects: frozenset = frozenset()        # SUBJECT_SCOPED: identity tokens of the whole capture

    def covers(self, subject_tokens) -> bool:
        if self.coverage == 'FULL_TABLE':
            return True
        return bool(subject_tokens) and bool(self.subjects & subject_tokens)


@dataclass
class SourceReport:
    """Parsing outcome for one manifest file. Failures are never silent."""
    source_id: str
    path: str
    parser: str
    status: str = 'PARSED'          # PARSED | PARTIAL | FAILED | UNSUPPORTED_MEDIA
    observations: int = 0
    scopes: int = 0
    issues: list[str] = field(default_factory=list)
