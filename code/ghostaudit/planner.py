"""Obligations -> deletion status -> ordered steps, all from one model.

discover()   resources tied to the subject by eligible evidence
obligation() required final state vs reconstructed state: SATISFIED | VIOLATED | UNRESOLVED
plan_case()  status and steps derived only from those obligations

Status rules (plan sections 4.3, 16.2, 17):
- NON_DELETION intent                                  -> NOT_APPLICABLE, []
- any VIOLATED obligation                              -> INCOMPLETE (confirmed)
- nothing violated, nothing unresolved, >=1 obligation -> COMPLETE (verification only, cited)
- anything else                                        -> INCOMPLETE best-effort fallback,
  disclosed; it lists only checks for satisfied obligations and never invents a finding.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import OutputRow, Request, Step
from .corpus import Corpus
from .identity import IdentityIndex, SubjectIdentity, resolve_subject
from .intent import DELETE, NON_DELETION, Intent, classify
from .observations import ABSENT, LINK, PRESENT, STATE, Observation
from .output import canonical_steps
from .policy import Policy, PolicyError
from .state import ResourceState, reconstruct

SATISFIED, VIOLATED, UNRESOLVED = 'SATISFIED', 'VIOLATED', 'UNRESOLVED'

# action -> (finding, remediation, verification) operations
OPERATIONS = {
    'DELETE': ('REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'VERIFY_RECORD_ABSENT'),
    'REDACT': ('REPORT_EXPOSED_PII', 'REDACT_RECORD', 'VERIFY_RECORD_REDACTED'),
    'ANONYMIZE': ('REPORT_EXPOSED_PII', 'ANONYMIZE_RECORD', 'VERIFY_RECORD_ANONYMIZED'),
    'RETAIN': (None, None, 'VERIFY_RECORD_PRESENT'),
    'DISABLE_FLOW': ('REPORT_ACTIVE_DATA_FLOW', 'DISABLE_DATA_FLOW', 'VERIFY_DATA_FLOW_DISABLED'),
    'BLOCK_RESTORE': ('REPORT_RESTORE_RISK', 'BLOCK_RESTORE', 'VERIFY_RESTORE_BLOCKED'),
}
STATE_OF = {'REPORT_REMAINING_RECORD': 'PRESENT', 'REPORT_EXPOSED_PII': 'PRESENT', 'REPORT_ACTIVE_DATA_FLOW': 'ACTIVE',
            'REPORT_RESTORE_RISK': 'ACTIVE', 'DELETE_RECORD': 'ABSENT', 'REDACT_RECORD': 'REDACTED',
            'ANONYMIZE_RECORD': 'ANONYMIZED', 'DISABLE_DATA_FLOW': 'DISABLED', 'BLOCK_RESTORE': 'BLOCKED',
            'VERIFY_RECORD_ABSENT': 'ABSENT', 'VERIFY_RECORD_PRESENT': 'PRESENT', 'VERIFY_RECORD_REDACTED': 'REDACTED',
            'VERIFY_RECORD_ANONYMIZED': 'ANONYMIZED', 'VERIFY_DATA_FLOW_DISABLED': 'DISABLED',
            'VERIFY_RESTORE_BLOCKED': 'BLOCKED'}


@dataclass
class Obligation:
    system_id: str
    resource_id: str | None          # None: subject-level obligation with no grounded resource ID
    category: str | None
    action: str | None
    rule_id: str = ''
    result: str = UNRESOLVED
    verify_op: str | None = None
    finding_op: str | None = None
    remedy_op: str | None = None
    proof: Observation | None = None
    links: list = field(default_factory=list)
    issues: list = field(default_factory=list)

    @property
    def representable(self) -> bool:
        return self.resource_id is not None and self.verify_op is not None


@dataclass
class CasePlan:
    request: Request
    intent: Intent
    subject: SubjectIdentity | None = None
    obligations: list[Obligation] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    status: str = 'INCOMPLETE'
    steps: tuple[Step, ...] = ()
    fallback: bool = False
    fallback_reason: str = ''

    @property
    def row(self) -> OutputRow:
        return OutputRow(self.request.case_id, self.status, self.steps)


# --- discovery -----------------------------------------------------------------------------

def discover(subject: SubjectIdentity, corpus: Corpus, index: IdentityIndex, cutoff):
    """Resources that eligible evidence ties to the subject, with the linking observations.

    Links come from exact identity tokens on the record (or its log trace), explicit
    catalog links, and explicit resource references (e.g. source_resource_id).
    """
    linked: dict[tuple[str, str], list[Observation]] = {}
    conflicts: dict[tuple[str, str], list[str]] = {}
    own = set(subject.user_ids)
    seen = set()
    for token in subject.tokens:
        for obs in corpus.token_observations(token, cutoff):
            if not (obs.resource_id and obs.system_id) or obs.kind not in (STATE, LINK) or obs.obs_id in seen:
                continue
            seen.add(obs.obs_id)
            key = (obs.system_id, obs.resource_id)
            linked.setdefault(key, []).append(obs)
            for other in obs.tokens:
                others = index.owner.get(other, set()) - own
                if others and other not in subject.tokens:
                    conflicts.setdefault(key, []).append(
                        f'IDENTITY_CONFLICT: {obs.obs_id} also names {other[0]} of {sorted(others)}')
    # One hop over explicit references, iterating a frozen snapshot so newly added
    # links cannot be revisited (that would loop when two records reference each other).
    direct = [obs for observations in linked.values() for obs in observations]
    for obs in direct:
        for ref in obs.refs:
            systems = {o.system_id for o in corpus.resource_observations(ref, cutoff) if o.system_id}
            if len(systems) == 1:
                bucket = linked.setdefault((systems.pop(), ref), [])
                if obs not in bucket:
                    bucket.append(obs)
    return linked, conflicts


# --- obligations -----------------------------------------------------------------------------

def obligation(state: ResourceState, policy: Policy, audit_at) -> Obligation:
    ob = Obligation(state.system_id, state.resource_id, None, None, issues=list(state.issues))
    if not state.categories:
        ob.issues.append('UNSUPPORTED_CATEGORY: no state evidence establishes what this record is')
        return ob
    try:
        treatments = {c: policy.treatment(c, retention_until=state.retention_until, audit_at=audit_at)
                      for c in sorted(state.categories)}
    except PolicyError as exc:
        ob.issues.append(str(exc))
        return ob
    if len({t.action for t in treatments.values()}) > 1:
        ob.issues.append(f'POLICY_CONFLICT: categories {sorted(treatments)} require different treatments')
        return ob
    ob.category, treatment = next(iter(treatments.items()))
    ob.action, ob.rule_id = treatment.action, treatment.rule_id
    finding, remedy, verify = OPERATIONS[ob.action]

    def settle(result, proof, issue=None):
        ob.result, ob.proof = result, proof
        if result == VIOLATED:
            ob.finding_op, ob.remedy_op, ob.verify_op = finding, remedy, verify
        elif result == SATISFIED:
            ob.verify_op = verify
        if issue:
            ob.issues.append(issue)
        return ob

    if ob.action == 'DELETE':
        if state.presence == PRESENT:
            return settle(VIOLATED, state.presence_proof)
        if state.presence == ABSENT:
            return settle(SATISFIED, state.presence_proof)
        return settle(UNRESOLVED, None, 'UNPROVEN_ABSENCE: no eligible lookup or exhaustive snapshot settles presence')
    if ob.action in ('REDACT', 'ANONYMIZE'):
        if state.presence == ABSENT:
            # Removing the whole record exceeds the required treatment; verify its absence.
            ob.result, ob.proof, ob.verify_op = SATISFIED, state.presence_proof, 'VERIFY_RECORD_ABSENT'
            return ob
        if state.exposed is None:
            return settle(UNRESOLVED, None, 'UNPROVEN_TREATMENT: no eligible observation shows which identifiers remain')
        if state.exposed:
            return settle(VIOLATED, state.exposure_proof)
        return settle(SATISFIED, state.exposure_proof)
    if ob.action == 'RETAIN':
        if state.presence == PRESENT:
            return settle(SATISFIED, state.presence_proof, f'NOTE: {treatment.note}')
        if state.presence == ABSENT:
            return settle(UNRESOLVED, None, 'SPEC_GAP_MISSING_REQUIRED_RECORD: required retained record is absent')
        return settle(UNRESOLVED, None, 'UNPROVEN_PRESENCE: retained record state unknown')
    if ob.action == 'BLOCK_RESTORE':
        if state.restore_allowed is True:
            return settle(VIOLATED, state.restore_proof)
        if state.restore_allowed is False:
            return settle(SATISFIED, state.restore_proof)
        return settle(UNRESOLVED, None, 'UNPROVEN_FLOW_LINK: restore access not established')
    if ob.action == 'DISABLE_FLOW':
        if state.enabled is True:
            return settle(VIOLATED, state.enabled_proof)
        if state.enabled is False:
            return settle(SATISFIED, state.enabled_proof)
        return settle(UNRESOLVED, None, 'UNPROVEN_FLOW_LINK: connector enabled state not established')
    return settle(UNRESOLVED, None, f'UNSUPPORTED_CATEGORY: action {ob.action}')


def profile_obligation(subject, corpus, cutoff) -> Obligation:
    """Primary-profile obligation when no profile resource ID is grounded for the subject.

    Satisfied only by exhaustive profile listings (latest per location) that contain no
    row carrying the subject's identifiers. It can never produce an output step,
    because the contract needs a resource_id that the evidence does not supply.
    """
    ob = Obligation('account_service', None, 'USER_PROFILE', 'DELETE', 'rule_01')
    latest = {}
    for (system, category), scopes in corpus.scopes_by_key.items():
        if category != 'USER_PROFILE':
            continue
        for s in scopes:
            if s.available_at <= cutoff and s.observed_at <= cutoff and corpus.covers(s, subject.tokens):
                key = (system, s.location)
                if key not in latest or s.observed_at > latest[key].observed_at:
                    latest[key] = s
    if not latest:
        ob.issues.append('UNPROVEN_ABSENCE: no profile resource linked and no eligible exhaustive profile listing')
        return ob
    if any(s.member_tokens & subject.tokens for s in latest.values()):
        ob.issues.append('CONTRADICTORY_STATE: a profile listing carries the subject but no resource was linked')
        return ob
    ob.result = SATISFIED
    ob.issues.append('NOTE: subject absent from the latest exhaustive profile listings '
                     f'{sorted(s.source_id for s in latest.values())}; profile resource ID not grounded, so no step')
    return ob


# --- case plan ---------------------------------------------------------------------------------

def plan_case(request: Request, corpus: Corpus, policy: Policy, index: IdentityIndex) -> CasePlan:
    plan = CasePlan(request, classify(request.request_text))
    if plan.intent.decision == NON_DELETION:
        plan.status, plan.steps = 'NOT_APPLICABLE', ()
        return plan
    if plan.intent.decision != DELETE:
        return _fallback(plan, f'INTENT_UNRESOLVED: {plan.intent.reason}')

    cutoff = request.audit_at
    plan.subject = resolve_subject(request.email, cutoff, index, corpus)
    plan.issues += plan.subject.issues
    if not plan.subject.resolved:
        return _fallback(plan, 'IDENTITY_UNRESOLVED: ' + '; '.join(plan.subject.issues))

    linked, conflicts = discover(plan.subject, corpus, index, cutoff)
    for (system_id, resource_id), links in sorted(linked.items()):
        state = reconstruct(corpus, system_id, resource_id, cutoff, plan.subject.tokens)
        ob = obligation(state, policy, cutoff)
        ob.links = links
        if conflicts.get((system_id, resource_id)):
            ob.issues += conflicts[(system_id, resource_id)]
            ob.result, ob.verify_op, ob.finding_op, ob.remedy_op = UNRESOLVED, None, None, None
        plan.obligations.append(ob)
    if not any(o.category == 'USER_PROFILE' for o in plan.obligations):
        plan.obligations.append(profile_obligation(plan.subject, corpus, cutoff))
    return _derive(plan)


def _derive(plan: CasePlan) -> CasePlan:
    violated = [o for o in plan.obligations if o.result == VIOLATED]
    unresolved = [o for o in plan.obligations if o.result == UNRESOLVED]
    satisfied = [o for o in plan.obligations if o.result == SATISFIED and o.representable]

    if violated:
        steps = []
        for o in violated:
            steps += [Step('FINDING', o.system_id, o.resource_id, o.finding_op, STATE_OF[o.finding_op], o.proof.source_id),
                      Step('REMEDIATION', o.system_id, o.resource_id, o.remedy_op, STATE_OF[o.remedy_op], '')]
        # The primary profile must end absent in every deletion, so its final check is
        # planned even when its current state could not be proven.
        profiles = [o for o in unresolved if o.category == 'USER_PROFILE' and o.action == 'DELETE'
                    and o.resource_id and not any(i.startswith('IDENTITY_CONFLICT') for i in o.issues)]
        for o in profiles:
            o.verify_op = 'VERIFY_RECORD_ABSENT'
        steps += [Step('VERIFICATION', o.system_id, o.resource_id, o.verify_op, STATE_OF[o.verify_op], '')
                  for o in violated + satisfied + profiles]
        plan.status, plan.steps = 'INCOMPLETE', canonical_steps(steps)
        if unresolved:
            plan.issues.append(f'PARTIAL_PLAN: {len(unresolved)} obligation(s) unresolved and not in the steps')
        return plan
    if unresolved or not satisfied:
        reason = (f'UNRESOLVED_OBLIGATIONS: {len(unresolved)} obligation(s) could not be settled' if unresolved
                  else 'NO_REPRESENTABLE_OBLIGATION: no obligation with a grounded resource ID was established')
        return _fallback(plan, reason, satisfied)
    plan.status = 'COMPLETE'
    plan.steps = canonical_steps(Step('VERIFICATION', o.system_id, o.resource_id, o.verify_op,
                                      STATE_OF[o.verify_op], o.proof.source_id) for o in satisfied)
    return plan


def _fallback(plan: CasePlan, reason: str, satisfied=()) -> CasePlan:
    """Best-effort INCOMPLETE: a disclosed prediction, not evidence that data remains."""
    plan.status, plan.fallback, plan.fallback_reason = 'INCOMPLETE', True, reason
    authorized = plan.intent.decision == DELETE
    plan.steps = canonical_steps(Step('VERIFICATION', o.system_id, o.resource_id, o.verify_op,
                                      STATE_OF[o.verify_op], '') for o in satisfied) if authorized else ()
    return plan
