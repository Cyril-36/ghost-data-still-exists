"""Validated case file for one investigated request (plan sections 14 and 21.2).

The investigator proposes facts; only `FactValidator` admits them. An accepted fact cites an
eligible, already-inspected source with a locator and a verbatim literal, names a resource handle
the tools returned, and carries the literal support its kind needs:

  OWNERSHIP  the literal names the resource and an identifier of this subject, and no other person
  STATE      ABSENT: a readback cue in a machine readback line; PRESENT: a presence cue, or a captured
             document page naming the record (D041); activity claims never qualify
  CATEGORY   a category the supplied policy knows, with a cue word, consistent with the system
  RETENTION  an ISO date that appears in the literal next to a retention or hold cue

Accepted facts become ordinary `Observation`s (`fact_observations`), so the existing state, policy
and obligation code decides what they mean. The case file never holds a status or output steps.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .observations import ABSENT, LINK, PRESENT, STATE, Observation, classify_token, text_tokens
from .planner import id_tokens
from .tools import FACT_KINDS, SourceText, ToolError

ABSENT_CUE = re.compile(r'\b(404|not[ _]found|no such|absent|does not exist|no rows?|purged)\b', re.IGNORECASE)
PRESENT_CUE = re.compile(r'\b(200|found|present|active|exists|stored|retained|available|captured|uploaded|'
                         r'indexed|enabled|attached|listed)\b', re.IGNORECASE)
CLAIM_CUE = re.compile(r'\b(reported|requested|scheduled|completed|success(?:ful)?|queued|job|worker)\b', re.IGNORECASE)
INSTRUCTION_CUE = re.compile(r'\b(ignore (?:all|any|the|previous|prior|your)\b|disregard (?:all|any|the|previous|'
                             r'prior|your)\b|you must\b|mark (?:this|the) (?:case|request)|system prompt|as an ai\b)',
                             re.IGNORECASE)
READBACK_MEDIA = ('json', 'sql', 'log', 'text', 'csv')      # machine output, as `sniff` names it
RETENTION_CUE = re.compile(r'\b(retain|retained|retention|hold|until|keep)\b', re.IGNORECASE)
CATEGORY_CUES = {
    'USER_PROFILE': ('profile', 'account'), 'SECONDARY_IDENTIFIER': ('secondary', 'alias', 'identifier'),
    'SEARCH_INDEX_ENTRY': ('search', 'index'), 'CACHE_ENTRY': ('cache',),
    'SUPPORT_MESSAGE': ('message', 'conversation', 'chat'), 'SUPPORT_ATTACHMENT': ('attachment',),
    'ANALYTICS_PROFILE': ('analytics', 'cohort', 'audience'), 'PRODUCT_TELEMETRY': ('telemetry',),
    'USER_EXPORT': ('export',), 'MARKETING_PROFILE': ('marketing', 'newsletter'),
    'EMAIL_DELIVERY_LOG': ('delivery', 'bounce'), 'SECURITY_AUDIT_LOG': ('security', 'audit'),
    'LEGAL_HOLD_RECORD': ('legal', 'hold'), 'BACKUP_ARCHIVE': ('backup', 'archive'),
    'ACCOUNT_TOMBSTONE': ('tombstone',), 'DATA_FLOW': ('flow', 'connector', 'sync'),
}
_MONTHS = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()


@dataclass(frozen=True)
class Fact:
    kind: str
    system_id: str
    resource_id: str
    source_id: str
    locator: str
    literal: str
    value: str
    available_at: datetime
    observed_at: datetime
    subject_token: tuple = ()          # OWNERSHIP: the (kind, key) of the subject identifier it names


@dataclass
class ToolCall:
    round: int
    name: str
    arguments: dict
    ok: bool
    returned: dict = field(default_factory=dict)     # bounded summary: handles, counts, truncation
    error: str = ''


@dataclass
class CaseFile:
    case_id: str
    audit_at: datetime
    request_text: str
    scope: str                         # ACCOUNT | ITEM
    target: str | None                 # ITEM: the data category the request authorizes
    user_ids: list
    identifiers: list                  # [(kind, literal value, provenance)] supported by identity resolution
    subject_tokens: frozenset
    candidates: list = field(default_factory=list)   # [{system_id, resource_id, category, result, issues}]
    conflicts: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    handles: set = field(default_factory=set)        # resource IDs the model may name
    inspected: set = field(default_factory=set)      # source IDs shown to the model (citable)
    facts: list = field(default_factory=list)        # accepted Fact
    rejected: list = field(default_factory=list)     # [{proposal, reason}]
    tool_calls: list = field(default_factory=list)
    verifier_findings: list = field(default_factory=list)   # per verification pass: [finding dicts]
    rounds: int = 0
    feedback_rounds: int = 0
    schema_repairs: int = 0
    stop_reason: str = ''
    outcome: str = ''                  # ACCEPTED | PHASE3_KEPT | FALLBACK

    @property
    def identifier_values(self) -> set:
        return {value for _, value, _ in self.identifiers}

    @classmethod
    def from_plan(cls, plan, questions=()) -> CaseFile:
        subject = plan.subject
        cf = cls(plan.request.case_id, plan.request.audit_at, plan.request.request_text, plan.intent.scope,
                 plan.intent.target, list(subject.user_ids),
                 sorted({(i.kind, i.original, i.provenance) for i in subject.identifiers}), subject.tokens)
        for o in plan.obligations:
            if o.resource_id:
                cf.candidates.append({'system_id': o.system_id, 'resource_id': o.resource_id, 'category': o.category,
                                      'result': o.result, 'issues': o.issues[:4]})
                cf.handles.add(o.resource_id)
            cf.conflicts += [i for i in o.issues if i.startswith(('IDENTITY_CONFLICT', 'CONTRADICTORY_STATE'))]
            if o.result == 'UNRESOLVED':
                cf.questions.append(f'{o.system_id}/{o.resource_id}: ' + '; '.join(o.issues[:3]))
        if plan.fallback:
            cf.questions.append(f'case fallback: {plan.fallback_reason}')
        cf.questions += list(questions)
        cf.handles |= {u for u in subject.user_ids}
        return cf

    def record(self) -> dict:
        out = {k: v for k, v in asdict(self).items() if k not in ('facts', 'subject_token', 'subject_tokens')}
        out['audit_at'] = self.audit_at.isoformat()
        out['handles'], out['inspected'] = sorted(self.handles), sorted(self.inspected)
        out['facts'] = [{**{k: v for k, v in asdict(f).items() if k not in ('available_at', 'observed_at',
                                                                             'subject_token')},
                         'available_at': f.available_at.isoformat()} for f in self.facts]
        return out


def _norm(text: str) -> str:
    return ' '.join(text.split()).casefold()


def _date_forms(value: datetime) -> tuple[str, ...]:
    return (value.date().isoformat(), f'{value.day} {_MONTHS[value.month - 1]} {value.year}',
            f'{value.day:02d} {_MONTHS[value.month - 1]} {value.year}')


class FactValidator:
    """Admits a proposal only with the literal support its kind needs (see module docstring)."""

    def __init__(self, corpus, index, policy, texts: SourceText):
        self.corpus, self.index, self.policy, self.texts = corpus, index, policy, texts
        self.systems = set(corpus.system_categories) | {m.origin for m in corpus.context.manifest} | {
            s for a in corpus.context.activities for s in (a.target_system, a.source_system) if s}

    def _other_people(self, tokens, case: CaseFile):
        own = set(case.user_ids)
        return sorted({u for t in tokens if t not in case.subject_tokens for u in self.index.owner.get(t, ()) - own})

    def identity_tokens(self, text: str) -> set:
        found = set(text_tokens(text))
        found |= {t for t in map(classify_token, re.findall(r'\b(?:user_\d+|[0-9a-fA-F]{64})\b', text)) if t}
        return found

    def record_context(self, source_id, locator, span) -> str:
        """The text a cited record belongs to: the whole page of a document or screenshot (D061: a
        section naming two people binds neither), otherwise the cited lines."""
        if self.texts.kind(source_id) in ('pdf', 'image'):
            page = locator.split(':')[0] if ':' in locator else ''
            return '\n'.join(self.texts.pages(source_id).get(page, []))
        return span

    def validate(self, proposal: dict, case: CaseFile) -> tuple[Fact | None, str]:
        kind, system, rid = proposal.get('kind'), proposal.get('system_id'), proposal.get('resource_id')
        source, locator, literal = proposal.get('source_id'), proposal.get('locator'), proposal.get('literal')
        value = proposal.get('value') or ''
        if kind not in FACT_KINDS or not all(isinstance(x, str) and x for x in (system, rid, source, locator, literal)):
            return None, 'MALFORMED: kind, system_id, resource_id, source_id, locator and literal are required'
        if source not in self.texts.refs:
            return None, f'INVENTED_CITATION: {source!r} is not a registered source'
        ref = self.texts.refs[source]
        if ref.available_at > case.audit_at:
            return None, f'POST_CUTOFF_SOURCE: {source} available {ref.available_at.isoformat()} after audit_at'
        if source not in case.inspected:
            return None, f'UNINSPECTED_SOURCE: {source} was never returned by a tool in this investigation'
        if rid not in case.handles:
            return None, f'INVENTED_RESOURCE: {rid!r} was never returned by a tool or listed as a candidate'
        try:
            span = self.texts.span(source, locator)
        except ToolError as exc:
            return None, f'BAD_LOCATOR: {exc}'
        if len(literal) < 8 or _norm(literal) not in _norm(span):
            return None, f'LITERAL_NOT_IN_SOURCE: the literal does not occur verbatim at {source} {locator}'
        if INSTRUCTION_CUE.search(span):
            return None, 'UNTRUSTED_TEXT: the cited lines contain instructions; evidence text is data, not a source of facts'
        if rid not in id_tokens(literal) and kind != 'CATEGORY':
            return None, f'RESOURCE_NOT_IN_LITERAL: {rid} is not an exact token of the literal'
        problem = self._system_problem(system, rid, source, span)
        if problem:
            return None, problem
        observed = self.texts.manifest[source].captured_at if source in self.texts.manifest else ref.available_at
        fact = dict(kind=kind, system_id=system, resource_id=rid, source_id=source, locator=locator, literal=literal,
                    value=value, available_at=ref.available_at, observed_at=observed)
        check = getattr(self, f'_check_{kind.lower()}')(fact, case, span)
        if isinstance(check, str):
            return None, check
        return Fact(**fact, **check), ''

    def _system_problem(self, system, rid, source, span):
        if system not in self.systems:
            return f'UNKNOWN_SYSTEM: {system!r}'
        known = {o.system_id for o in self.corpus.by_resource.get(rid, ()) if o.kind in (STATE, LINK) and o.system_id}
        if known:
            return None if system in known else f'SYSTEM_CONFLICT: evidence places {rid} in {sorted(known)}'
        entry = self.texts.manifest.get(source)
        claimed = {a.target_system for a in self.corpus.context.activities if a.resource_id == rid}
        if (entry and entry.origin == system) or system in span or system in claimed:
            return None
        return f'SYSTEM_UNGROUNDED: nothing in {source} or the activity stream places {rid} in {system}'

    def _check_ownership(self, fact, case, span):
        tokens = self.identity_tokens(fact['literal'])
        mine = sorted(tokens & case.subject_tokens)
        if not mine:
            return 'NO_SUBJECT_IDENTIFIER: the literal names no identifier of this subject'
        others = self._other_people(self.identity_tokens(self.record_context(fact['source_id'], fact['locator'], span)),
                                    case)
        if others:
            return f'CROSS_PERSON: the cited record also identifies {others}'
        return {'subject_token': mine[0]}

    def _check_state(self, fact, case, span):
        literal, value = fact['literal'], fact['value']
        if fact['source_id'] in self.texts.activities:
            return 'CLAIM_NOT_STATE: an activity row reports work; it never proves the resulting state'
        if value not in (PRESENT, ABSENT):
            return 'BAD_VALUE: STATE value must be PRESENT or ABSENT'
        if value == ABSENT and self.texts.kind(fact['source_id']) not in READBACK_MEDIA:
            return 'UNSUPPORTED_STATE: absence needs a machine readback (SQL, JSON or log), not a document or screenshot'
        if value == ABSENT and (not ABSENT_CUE.search(literal) or CLAIM_CUE.search(literal)):
            return 'UNSUPPORTED_STATE: ABSENT needs a readback cue (404, not found, ...) and no operation claim'
        document = self.texts.kind(fact['source_id']) in ('pdf', 'image')
        if value == PRESENT and ABSENT_CUE.search(literal):
            return 'UNSUPPORTED_STATE: PRESENT contradicts an absence cue in the literal'
        if value == PRESENT and not document and not PRESENT_CUE.search(literal):
            # A captured document page that names the record shows it existed at capture (D041);
            # machine output must say so explicitly.
            return 'UNSUPPORTED_STATE: PRESENT needs a presence cue (found, active, stored, ...)'
        return {}

    def _check_category(self, fact, case, span):
        value = fact['value']
        if value != 'DATA_FLOW' and value not in self.policy.by_category:
            return f'UNKNOWN_CATEGORY: {value!r} has no supplied rule'
        if not any(cue in fact['literal'].casefold() for cue in CATEGORY_CUES.get(value, ())):
            return f'UNSUPPORTED_CATEGORY: the literal has no cue for {value}'
        held = self.corpus.system_categories.get(fact['system_id'], set())
        if held and value not in held:
            return f'CATEGORY_CONFLICT: {fact["system_id"]} holds {sorted(held)}, not {value}'
        known = {o.category for o in self.corpus.by_resource.get(fact['resource_id'], ()) if o.kind == STATE and o.category}
        if known and value not in known:
            return f'CATEGORY_CONFLICT: evidence already classifies {fact["resource_id"]} as {sorted(known)}'
        return {}

    def _check_retention(self, fact, case, span):
        try:
            until = datetime.fromisoformat(fact['value']).replace(tzinfo=timezone.utc)
        except ValueError:
            return 'BAD_VALUE: RETENTION value must be an ISO date'
        literal = fact['literal']
        if not RETENTION_CUE.search(literal) or not any(f.casefold() in literal.casefold() for f in _date_forms(until)):
            return 'UNSUPPORTED_RETENTION: the literal needs a retention or hold cue and this exact date'
        return {}


def fact_observations(facts) -> list[Observation]:
    """Accepted facts -> observations for the existing state and obligation model."""
    by_resource, out = {}, []
    for f in facts:
        by_resource.setdefault((f.system_id, f.resource_id), []).append(f)
    for (system, rid), group in sorted(by_resource.items()):
        category = next((f.value for f in group if f.kind == 'CATEGORY'), None)
        for f in group:
            base = dict(source_id=f.source_id, available_at=f.available_at, observed_at=f.observed_at,
                        locator=f.locator, excerpt=f.literal[:300], system_id=system, resource_id=rid,
                        proof='INVESTIGATOR_FACT', attributes=(('fact', f.kind),))
            if f.kind == 'OWNERSHIP':
                out.append(Observation(kind=LINK, tokens=frozenset({f.subject_token}), **base))
            elif f.kind == 'STATE':
                out.append(Observation(kind=STATE, presence=f.value, category=category, **base))
            elif f.kind == 'RETENTION':
                until = datetime.fromisoformat(f.value).replace(tzinfo=timezone.utc)
                out.append(Observation(kind=STATE, category=category, retention_until=until, **base))
            elif not any(g.kind in ('STATE', 'RETENTION') for g in group):
                out.append(Observation(kind=STATE, category=category, **base))
    return out
