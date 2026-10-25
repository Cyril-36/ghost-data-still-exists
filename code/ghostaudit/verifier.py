"""Independent verifier (plan section 18): checks a case plan and its accepted facts against the raw
evidence, the supplied rules and the output contract. It never calls the planner's discovery, state
or obligation code. It re-derives what it needs from eligible observations, so a planner bug is not
reproduced silently.

Checks, per case:
  integrity   the row passes the output contract (`check_row`)
  citations   every cited source is registered and available by audit_at; the cited source holds
              a non-claim observation of that resource showing the stated state, and no newer
              eligible observation at the same copy contradicts it
  ownership   eligible evidence names the subject on the record (or reaches it through explicit
              references); no record named in the steps carries another person's identifier;
              not-found lookups support absence checks only
  policy      the operations match the supplied rule for the record's category; a conditional
              retention needs a supplied period or hold date; ITEM scope stays in its category
  facts       every accepted investigator fact is re-checked against its source text
  coverage    subject-linked records with eligible state, active flows or open restores that touch
              them, and unparsed sources that mention the subject, must all be accounted for

Findings are BLOCKING (the plan cannot be accepted as is) or ADVISORY. `routes` marks findings an
investigation could address with more evidence.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .casefile import fact_observations
from .contracts import ContractError
from .corpus import eligible
from .observations import ABSENT, LINK, PRESENT, STATE, text_tokens
from .output import check_row

FAMILY = {
    'DELETE': {'REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'VERIFY_RECORD_ABSENT'},
    'REDACT': {'REPORT_EXPOSED_PII', 'REDACT_RECORD', 'VERIFY_RECORD_REDACTED', 'VERIFY_RECORD_ABSENT'},
    'ANONYMIZE': {'REPORT_EXPOSED_PII', 'ANONYMIZE_RECORD', 'VERIFY_RECORD_ANONYMIZED', 'VERIFY_RECORD_ABSENT'},
    'RETAIN': {'VERIFY_RECORD_PRESENT'},
    'RESTORE': {'REPORT_RESTORE_RISK', 'BLOCK_RESTORE', 'VERIFY_RESTORE_BLOCKED'},
    'FLOW': {'REPORT_ACTIVE_DATA_FLOW', 'DISABLE_DATA_FLOW', 'VERIFY_DATA_FLOW_DISABLED'},
}
# What a cited observation must show for each cited operation.
SUPPORTS = {
    'REPORT_REMAINING_RECORD': lambda o: o.presence == PRESENT,
    'REPORT_EXPOSED_PII': lambda o: bool(o.exposed) and o.presence != ABSENT,
    'REPORT_ACTIVE_DATA_FLOW': lambda o: o.enabled is True,
    'REPORT_RESTORE_RISK': lambda o: o.restore_allowed is True,
    'VERIFY_RECORD_ABSENT': lambda o: o.presence == ABSENT,
    'VERIFY_RECORD_PRESENT': lambda o: o.presence == PRESENT,
    'VERIFY_RECORD_REDACTED': lambda o: o.exposed == () and o.presence != ABSENT,
    'VERIFY_RECORD_ANONYMIZED': lambda o: o.exposed == () and o.presence != ABSENT,
    'VERIFY_DATA_FLOW_DISABLED': lambda o: o.enabled is False,
    'VERIFY_RESTORE_BLOCKED': lambda o: o.restore_allowed is False,
}
ATTRIBUTE = {'REPORT_REMAINING_RECORD': 'presence', 'VERIFY_RECORD_ABSENT': 'presence', 'VERIFY_RECORD_PRESENT':
             'presence', 'REPORT_ACTIVE_DATA_FLOW': 'enabled', 'VERIFY_DATA_FLOW_DISABLED': 'enabled',
             'REPORT_RESTORE_RISK': 'restore_allowed', 'VERIFY_RESTORE_BLOCKED': 'restore_allowed',
             'REPORT_EXPOSED_PII': 'exposed', 'VERIFY_RECORD_REDACTED': 'exposed', 'VERIFY_RECORD_ANONYMIZED': 'exposed'}
REMOVAL_CUE = re.compile(r'\b(deleted|removed|purged|erased|destroyed|404|not found)\b', re.IGNORECASE)
ROUTING = {'OMITTED_RESOURCE', 'RECREATION_PATH_IGNORED', 'UNINSPECTED_MENTION', 'PARTIAL_PLAN',
           'UNSUPPORTED_ASSOCIATION', 'UNPROVEN_CONDITION'}


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str                 # BLOCKING | ADVISORY
    message: str
    system_id: str = ''
    resource_id: str = ''
    source_id: str = ''

    @property
    def blocking(self) -> bool:
        return self.severity == 'BLOCKING'

    @property
    def routes(self) -> bool:
        return self.code in ROUTING

    def record(self) -> dict:
        return asdict(self)


class Verifier:
    def __init__(self, corpus, index, context, texts):
        self.corpus, self.index, self.context, self.texts = corpus, index, context, texts
        self.rules = {r.data_category: r for r in context.rules}
        self._lower = {}

    # -- evidence helpers ------------------------------------------------------------------------
    def _observations(self, rid, cutoff, extra):
        return [o for o in self.corpus.resource_observations(rid, cutoff)] + \
               [o for o in extra if o.resource_id == rid and eligible(o, cutoff)]

    def _text(self, source_id):
        if source_id not in self._lower:
            self._lower[source_id] = '\n'.join(line for _, line in self.texts.lines(source_id)).casefold()
        return self._lower[source_id]

    def _owned(self, subject, cutoff, extra):
        """{(system, resource): how} for records eligible evidence ties to the subject."""
        tokens, owned = subject.tokens, {}
        for obs in [o for t in tokens for o in self.corpus.token_observations(t, cutoff)] + \
                [o for o in extra if eligible(o, cutoff) and o.tokens & tokens]:
            if obs.resource_id and obs.system_id and obs.kind in (STATE, LINK):
                lookup = obs.kind == STATE and obs.presence == ABSENT
                prev = owned.get((obs.system_id, obs.resource_id))
                owned[(obs.system_id, obs.resource_id)] = 'lookup' if lookup and prev in (None, 'lookup') else 'direct'
        edges = {}
        for obs in self.corpus.observations:
            if obs.refs and obs.resource_id and eligible(obs, cutoff):
                for ref in obs.refs:
                    edges.setdefault(obs.resource_id, set()).add(ref)
                    edges.setdefault(ref, set()).add(obs.resource_id)
        frontier, seen = [(r, 0) for (_, r), how in owned.items() if how == 'direct'], {r for _, r in owned}
        while frontier:
            rid, depth = frontier.pop(0)
            for other in sorted(edges.get(rid, ())) if depth < 3 else ():
                if other in seen:
                    continue
                seen.add(other)
                systems = {o.system_id for o in self._observations(other, cutoff, extra)
                           if o.system_id and o.kind in (STATE, LINK)}
                if len(systems) == 1:
                    owned[(systems.pop(), other)] = 'reference'
                    frontier.append((other, depth + 1))
        return owned

    def _foreign(self, observations, subject):
        own = set(subject.user_ids)
        return sorted({u for o in observations if o.kind == STATE for t in o.tokens if t not in subject.tokens
                       for u in self.index.owner.get(t, ()) - own})

    def _categories(self, system, observations):
        cats = {o.category for o in observations if o.system_id == system and o.kind == STATE and o.category}
        held = self.corpus.system_categories.get(system, set())
        return cats or (set(held) if len(held) == 1 else set())

    def _allowed(self, category, cutoff, observations):
        """-> (allowed operations, problem or '')."""
        if category == 'DATA_FLOW':
            return FAMILY['FLOW'], ''
        rule = self.rules.get(category)
        if rule is None:
            return set(), f'no supplied rule for {category}'
        if rule.required_action == 'RETAIN':
            if category == 'BACKUP_ARCHIVE':
                return FAMILY['RESTORE'], ''
            dated = [o.retention_until for o in observations if o.retention_until is not None]
            if not dated:
                return FAMILY['RETAIN'], f'{rule.rule_id} "{rule.condition}" needs a supplied period; none is eligible'
            if max(dated) < cutoff:
                return set(), f'{rule.rule_id} retention ended {max(dated).isoformat()}'
        return FAMILY.get(rule.required_action, set()), ''

    # -- checks ----------------------------------------------------------------------------------
    def verify(self, plan, case=None) -> list[Finding]:
        out = []

        def add(code, severity, message, system='', rid='', source=''):
            out.append(Finding(code, severity, message, system, rid, source))
        try:
            check_row(plan.row, plan.request, self.context.evidence, disclosed_fallback=plan.fallback)
        except ContractError as exc:
            add('INTEGRITY', 'BLOCKING', str(exc))
        if plan.intent.decision != 'DELETE' or plan.subject is None or not plan.subject.resolved:
            return out
        cutoff, subject = plan.request.audit_at, plan.subject
        facts = list(case.facts) if case else []
        extra = fact_observations(facts)
        owned = self._owned(subject, cutoff, extra)
        out += self._check_facts(facts, case, cutoff)
        steps_by_resource = {}
        for s in plan.steps:
            steps_by_resource.setdefault((s.system_id, s.resource_id), []).append(s)
        for (system, rid), steps in sorted(steps_by_resource.items()):
            obs = self._observations(rid, cutoff, extra)
            for s in steps:
                if s.evidence_id:
                    self._check_citation(s, obs, cutoff, add)
                if plan.status == 'COMPLETE' and s.operation == 'VERIFY_RECORD_ABSENT':
                    self._check_copies(s, obs, cutoff, subject, add)
            how = owned.get((system, rid))
            ops = {s.operation for s in steps}
            if how is None:
                only_claims = obs and all(o.kind not in (STATE, LINK) for o in obs)
                add('UNSUPPORTED_ASSOCIATION', 'BLOCKING', f'no eligible evidence ties {rid} to the subject'
                    + (' (only workflow claims name it)' if only_claims else ''), system, rid)
            elif how == 'lookup' and ops - {'VERIFY_RECORD_ABSENT'}:
                add('UNSUPPORTED_ASSOCIATION', 'BLOCKING', f'{rid} is tied to the subject only by not-found lookups; '
                    'they support an absence check, not a finding or remediation', system, rid)
            others = self._foreign([o for o in obs if o.system_id == system], subject)
            if others:
                add('CROSS_PERSON', 'BLOCKING', f'evidence for {rid} identifies {others}', system, rid)
            categories = self._categories(system, obs)
            if plan.intent.scope == 'ITEM' and plan.intent.target not in categories:
                add('SCOPE_EXCEEDED', 'BLOCKING', f'{rid} is not in the authorized category {plan.intent.target}',
                    system, rid)
            for category in sorted(categories) or [None]:
                if category is None:
                    add('POLICY_UNDETERMINED', 'BLOCKING', f'no eligible evidence says what {rid} is', system, rid)
                    continue
                allowed, problem = self._allowed(category, cutoff, [o for o in obs if o.system_id == system])
                if problem and 'needs a supplied period' in problem and 'VERIFY_RECORD_PRESENT' in ops:
                    add('UNPROVEN_CONDITION', 'BLOCKING', problem, system, rid)
                elif problem and not allowed:
                    add('POLICY_MISMATCH', 'BLOCKING', problem, system, rid)
                if ops - allowed:
                    add('POLICY_MISMATCH', 'BLOCKING', f'{sorted(ops - allowed)} do not match the rule for {category}',
                        system, rid)
        self._check_coverage(plan, owned, steps_by_resource, extra, cutoff, case, add)
        return out

    def _check_citation(self, step, obs, cutoff, add):
        ref = self.context.evidence.get(step.evidence_id)
        if ref is None or ref.available_at > cutoff:
            add('INELIGIBLE_CITATION', 'BLOCKING', f'{step.evidence_id} is unregistered or after audit_at',
                step.system_id, step.resource_id, step.evidence_id)
            return
        test, attr = SUPPORTS[step.operation], ATTRIBUTE[step.operation]
        here = [o for o in obs if o.source_id == step.evidence_id and o.system_id == step.system_id and o.kind == STATE]
        support = [o for o in here if test(o)]
        if not support and step.operation == 'VERIFY_RECORD_ABSENT':
            support = [s for s in self.corpus.scopes if s.source_id == step.evidence_id and s.system_id == step.system_id
                       and eligible(s, cutoff) and step.resource_id not in s.members and self.corpus.covers(s, frozenset())]
        if not support:
            add('UNSUPPORTED_CITATION', 'BLOCKING', f'{step.evidence_id} does not show {step.operation} for '
                f'{step.resource_id}', step.system_id, step.resource_id, step.evidence_id)
            return
        for o in support:
            location = getattr(o, 'location', None)
            newer = [n for n in obs if n.kind == STATE and n.system_id == step.system_id and n.location == location
                     and n.observed_at > o.observed_at and getattr(n, attr) is not None and not test(n)]
            if newer and all(o is not s for s in support if s is not o):
                add('STALE_CITATION', 'BLOCKING', f'{newer[0].source_id} later shows a different {attr} for '
                    f'{step.resource_id}', step.system_id, step.resource_id, step.evidence_id)
                return

    def _record(self, fact, span):
        """Whole page for documents and screenshots, the cited lines otherwise."""
        if self.texts.kind(fact.source_id) in ('pdf', 'image') and ':' in fact.locator:
            return '\n'.join(self.texts.pages(fact.source_id).get(fact.locator.split(':')[0], []))
        return span

    def _check_copies(self, step, obs, cutoff, subject, add):
        """Absence is a claim about every copy: the latest eligible state of each location, from explicit
        observations or omission from an exhaustive listing that covers the subject, must agree."""
        events = [(o.observed_at, o.location, o.presence, o.source_id) for o in obs
                  if o.kind == STATE and o.system_id == step.system_id and o.presence]
        events += [(s.observed_at, s.location, ABSENT, s.source_id) for s in self.corpus.scopes
                   if s.system_id == step.system_id and eligible(s, cutoff) and step.resource_id not in s.members
                   and self.corpus.covers(s, subject.tokens)]
        latest = {}
        for event in sorted(events, key=lambda e: (e[0], e[2] == PRESENT)):   # ties: presence wins
            latest[event[1]] = event
        present = sorted(loc for loc, e in latest.items() if e[2] == PRESENT)
        if present:
            source = latest[present[0]][3]
            add('REPLICA_DISAGREEMENT', 'BLOCKING', f'{step.resource_id} is still present at {present} ({source})',
                step.system_id, step.resource_id, source)

    def _check_facts(self, facts, case, cutoff):
        out = []
        for f in facts:
            problem = ''
            ref = self.context.evidence.get(f.source_id)
            if ref is None or ref.available_at > cutoff:
                problem = 'source unregistered or after audit_at'
            else:
                try:
                    span = self.texts.span(f.source_id, f.locator)
                except ValueError:
                    span = ''
                if ' '.join(f.literal.split()).casefold() not in ' '.join(span.split()).casefold():
                    problem = 'literal not found at its locator'
                elif f.kind == 'OWNERSHIP' and case is not None and not (text_tokens(f.literal) | {
                        t for t in case.subject_tokens if t[1] in f.literal.casefold()}) & case.subject_tokens:
                    problem = 'ownership literal names no identifier of the subject'
                elif f.kind == 'STATE' and f.value == PRESENT and REMOVAL_CUE.search(self._record(f, span)):
                    problem = 'the cited record says it was removed; a presence cue elsewhere does not outweigh that'
            if problem:
                out.append(Finding('FACT_UNSUPPORTED', 'BLOCKING', f'{f.kind} fact: {problem}', f.system_id,
                                   f.resource_id, f.source_id))
        return out

    def _check_coverage(self, plan, owned, steps_by_resource, extra, cutoff, case, add):
        accounted = {(o.system_id, o.resource_id) for o in plan.obligations if o.resource_id} | set(steps_by_resource)
        target = plan.intent.target if plan.intent.scope == 'ITEM' else None
        for (system, rid), how in sorted(owned.items()):
            if (system, rid) in accounted or how == 'lookup':
                continue
            obs = self._observations(rid, cutoff, extra)
            if target and target not in self._categories(system, obs):
                continue
            if any(o.kind == STATE and o.system_id == system for o in obs):
                add('OMITTED_RESOURCE', 'BLOCKING', f'{rid} is tied to the subject by eligible evidence but is not '
                    'in the plan', system, rid)
        if target is None:
            mine = {rid for _, rid in owned}
            adjacent = {}
            for obs in self.corpus.observations:     # explicit flow/restore -> record references
                if obs.refs and obs.resource_id and eligible(obs, cutoff):
                    for ref in obs.refs:
                        if ref in mine:
                            adjacent.setdefault(obs.resource_id, ref)
                        elif obs.resource_id in mine:
                            adjacent.setdefault(ref, obs.resource_id)
            for rid, record in sorted(adjacent.items()):
                live = sorted((o for o in self._observations(rid, cutoff, extra) if o.kind == STATE and
                               (o.enabled is not None or o.restore_allowed is not None)), key=lambda o: o.observed_at)
                if live and (live[-1].enabled or live[-1].restore_allowed) and \
                        (live[-1].system_id, rid) not in accounted:
                    add('RECREATION_PATH_IGNORED', 'BLOCKING', f'{rid} can copy or restore {record} and is not in '
                        'the plan', live[-1].system_id or '', rid, live[-1].source_id)
        if not plan.fallback and any(o.result == 'UNRESOLVED' for o in plan.obligations if o.resource_id):
            add('PARTIAL_PLAN', 'ADVISORY', 'some linked records are unresolved and not in the steps')
        values = [v for v in {i.original for i in plan.subject.identifiers if i.kind in ('email', 'phone')} if v]
        inspected = case.inspected if case else set()
        for entry in self.context.manifest:
            if entry.captured_at > cutoff or entry.file_id in inspected:
                continue
            if any(t in plan.subject.tokens for o in self.corpus.by_source.get(entry.file_id, ()) for t in o.tokens):
                continue
            if any(v.casefold() in self._text(entry.file_id) for v in values):
                add('UNINSPECTED_MENTION', 'ADVISORY', f'{entry.file_id} mentions the subject but yields no '
                    'parsed evidence for them', source=entry.file_id)
