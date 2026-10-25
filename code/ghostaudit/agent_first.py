"""Agent-authored decisions, checked rather than rewritten by deterministic code.

No plan_case, deterministic obligations or investigator re-planning runs on this path.
CasePlan is only the existing output/diagnostic container. Execution primitives and
read-only evidence tools are shared with the investigator, not its semantic authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, replace
from collections import Counter

from .casefile import CaseFile, fact_observations
from .agent_protocol import normalize_proposal
from .contracts import ContractError, OPERATION_TABLE
from .corpus import eligible
from .observations import LINK, STATE
from .identity import resolve_subject
from .intent import ACCOUNT, DELETE, ITEM, NON_DELETION, UNRESOLVED, Intent, classify, hypothetical_deletion
from .investigator import Investigator
from .output import parse_steps_json, row_problems
from .planner import OPERATIONS, CasePlan, Obligation
from .policy import PolicyError
from .providers import ProviderError, ReplayGateway, strict_json
from .tools import EVIDENCE_TOOLS, TOOL_SCHEMAS, InvestigationTools, locator_lines
from .verifier import Finding

STR = {'type': 'string'}
PROPOSAL_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'authorization': {'type': 'object', 'additionalProperties': False, 'properties': {
        'decision': {'type': 'string', 'enum': [DELETE, NON_DELETION, UNRESOLVED],
                     'description': 'The action requested by the customer, not whether deletion has finished. '
                                    'Missing compliance evidence does not turn a deletion request into NON_DELETION.'},
        'scope': {'type': 'string', 'enum': [ACCOUNT, ITEM, 'NONE'],
                  'description': 'Your interpretation of permission: ACCOUNT for removing an account/profile '
                                 'and its governed copies; ITEM for a bounded artifact/category; NONE otherwise.'},
        'target': {'type': 'string', 'description': 'Must be the empty string for ACCOUNT or NONE. '
            'For ITEM, use exactly one supplied data category. Resource IDs belong in steps, not here.'},
        'whole_category': {'type': 'boolean', 'description': 'Must be false for ACCOUNT or NONE. '
            'For ITEM only, true means the customer requested every record of that one category.'},
        'request_quote': STR, 'reason': STR},
        'required': ['decision', 'scope', 'target', 'whole_category', 'request_quote', 'reason']},
    'deletion_status': {'type': 'string', 'enum': ['COMPLETE', 'INCOMPLETE', 'NOT_APPLICABLE']},
    'steps': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'properties': {**{k: STR for k in ('system_id', 'resource_id')},
                       'evidence_id': {'type': 'string', 'description': 'FINDING steps cite the source that '
                           'shows the violation. In an INCOMPLETE row every REMEDIATION and VERIFICATION step '
                           'is future work with empty evidence_id, even for a record already in its final '
                           'state. In a COMPLETE row every VERIFICATION step cites its proof.'},
                       'phase': {'type': 'string', 'enum': list(dict.fromkeys(p for p, _, _ in OPERATION_TABLE))},
                       'operation': {'type': 'string', 'enum': [op for _, op, _ in OPERATION_TABLE]},
                       'state': {'type': 'string', 'enum': sorted({s for _, _, s in OPERATION_TABLE})},
                       'step_no': {'type': 'integer'}},
        'required': ['step_no', 'phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id']}},
    'witnesses': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'properties': {'step_no': {'type': 'integer'}, 'source_id': STR, 'locator': STR, 'literal': STR},
        'required': ['step_no', 'source_id', 'locator']}},
    'policy_undecided': {'type': 'array', 'description': 'Optional. Owned records you deliberately leave out of the '
        'steps because the supplied policy cannot decide their treatment (for example a retention condition '
        'with no supplied period). Each is independently re-checked; the status must stay INCOMPLETE.',
        'items': {'type': 'object', 'additionalProperties': False,
                  'properties': {'system_id': STR, 'resource_id': STR, 'reason': STR},
                  'required': ['system_id', 'resource_id', 'reason']}}},
    'required': ['authorization', 'deletion_status', 'steps', 'witnesses']}
AGENT_TOOLS = [t for t in TOOL_SCHEMAS if t['function']['name'] in (*EVIDENCE_TOOLS, 'propose_fact')] + [{
    'type': 'function', 'function': {'name': 'submit_audit', 'description':
        'Propose the final authorization, status and complete ordered steps for independent verification.',
        'parameters': PROPOSAL_SCHEMA}}]
REQUEST_TOOL = {'type': 'function', 'function': {'name': 'interpret_request',
    'description': 'Interpret what the customer requests before investigating whether it has happened. '
                   'Record your own authorization interpretation; you may revise it in your final proposal.',
    'parameters': PROPOSAL_SCHEMA['properties']['authorization']}}
CATALOG_TOOL = {'type': 'function', 'function': {'name': 'list_sources',
    'description': 'Browse eligible source metadata, not subject membership or state proof. '
                   'Filter by content kind or file/origin text and paginate using next_offset.',
    'parameters': {'type': 'object', 'additionalProperties': False, 'properties': {
        'offset': {'type': 'integer'}, 'kind': {'type': 'string',
            'enum': ['', 'pdf', 'image', 'json', 'sql', 'text', 'log']}, 'query': STR}, 'required': []}}}
AGENT_TOOLS.insert(0, CATALOG_TOOL)
HALLUCINATION_CODES = {'INVENTED_RESOURCE', 'INELIGIBLE_CITATION', 'UNSHOWN_WITNESS', 'UNSUPPORTED_CITATION',
                       'UNSUPPORTED_ASSOCIATION', 'FACT_UNSUPPORTED', 'CROSS_PERSON'}


def treatment_chain(policy, category, cutoff):
    """The supplied policy's required operation chain for a category with no supplied period."""
    try:
        action = policy.treatment(category, retention_until=None, audit_at=cutoff).action
    except PolicyError as exc:
        return f'{category}: undecidable without supplied evidence ({exc})'
    ops = [op for op in OPERATIONS[action] if op]
    return f'{category} ({action}): ' + ' -> '.join(ops) + ('' if len(ops) > 1 else ' only, never a finding or remediation')


def policy_treatments(policy, rules, cutoff):
    """The supplied rules, each with the treatment chain the policy and verifier apply to it."""
    return {'rules': [{**asdict(r), 'verifier_treatment': treatment_chain(policy, r.data_category, cutoff)}
                      for r in rules],
            'flows': treatment_chain(policy, 'DATA_FLOW', cutoff),
            'note': 'A finding applies only where eligible evidence shows the violation. A record already in '
                    'its required final state gets only its VERIFICATION step: cited in a COMPLETE row, empty '
                    'evidence_id in an INCOMPLETE row. A supplied retention or hold period that has ended is a '
                    'policy conflict.'}


def offline_gateway(gateway):
    return gateway is not None and gateway.mode == 'replay' and (
        isinstance(gateway, ReplayGateway) or gateway.config.max_calls == 0)


def allowed_agent_gateway(gateway, allow_live=False):
    if offline_gateway(gateway):
        return True
    return bool(allow_live and gateway is not None and gateway.mode == 'live'
                and math.isfinite(gateway.config.max_cost_paise) and gateway.config.max_cost_paise > 0
                and gateway.config.max_calls > 0 and gateway.config.max_retries == 0
                and gateway.config.max_rate_limit_retries == 0)


class ProposalVerifier:
    """Validate agent semantics; only structural serialization and retrieved-span quoting are automatic."""

    def __init__(self, verifier, policy):
        self.verifier, self.policy = verifier, policy
        self.last_authorization_review = None
        self.last_serialization = None
        self.resolved_witnesses = []

    def review_authorization(self, text):
        return self.authorization_review(text) if getattr(self, 'authorization_review', None) else None

    def verify(self, request, subject, proposal, case):
        findings = []
        self.last_authorization_review = None
        self.last_serialization = None
        self.resolved_witnesses = []
        proposal, self.last_serialization = normalize_proposal(proposal)

        def reject(code, message):
            findings.append(Finding(code, 'BLOCKING', message))

        a = proposal['authorization']
        quote = a['request_quote']
        if not quote.strip() or quote not in request.request_text or not a['reason'].strip():
            reject('AUTHORIZATION_UNSUPPORTED', 'Quote the actual request and explain its authorization.')
        det = classify(request.request_text)
        decision, scope = a['decision'], a['scope']
        if decision == UNRESOLVED:
            reject('AUTHORIZATION_UNRESOLVED', 'UNRESOLVED requires genuine ambiguity in the request permission '
                'and will produce a disclosed fallback. Missing compliance evidence or an incorrectly encoded '
                'scope field is not request ambiguity. Re-read the request and choose your own interpretation.')
        if decision == DELETE:
            if hypothetical_deletion(request.request_text):
                reject('AUTHORIZATION_UNSUPPORTED', 'A hypothetical or capability question does not authorize deletion.')
            if not subject.resolved or scope not in (ACCOUNT, ITEM):
                reject('AUTHORIZATION_UNSUPPORTED', 'Deletion needs one resolved subject and an explicit scope.')
            if det.decision == NON_DELETION or (scope == ACCOUNT and (
                    det.scope == ITEM or det.reason == 'both deletion and preservation statements')):
                reject('SCOPE_EXCEEDED', 'The request preserves the account or authorizes only one item.')
            if scope == ITEM and (a['target'] not in self.policy.by_category or (
                    det.scope == ITEM and det.target and a['target'] != det.target)):
                reject('SCOPE_EXCEEDED', 'The proposed item category conflicts with the request or supplied policy.')
            if det.scope == ITEM and not det.whole_category and a['whole_category']:
                reject('SCOPE_EXCEEDED', 'An explicit single-item request cannot become a whole-category deletion.')
            if scope == ACCOUNT and (a['target'] or a['whole_category']):
                reject('SCOPE_EXCEEDED', 'For authorization.scope="ACCOUNT", authorization.target must be "" '
                    '(the empty string) and authorization.whole_category must be false. Put resource IDs only '
                    'in steps.resource_id. This is a field-encoding constraint, not a rejection of ACCOUNT '
                    'permission; do not change a justified request interpretation merely to fix these fields.')
            if proposal['deletion_status'] == 'NOT_APPLICABLE':
                reject('STATUS_MISMATCH', 'Authorized deletion cannot be NOT_APPLICABLE.')
        elif decision == NON_DELETION:
            if proposal['deletion_status'] != 'NOT_APPLICABLE' or scope != 'NONE' or a['target']:
                reject('STATUS_MISMATCH', 'Non-deletion requires NONE scope, no target and NOT_APPLICABLE.')
            review = self.review_authorization(request.request_text) if det.decision == UNRESOLVED else None
            self.last_authorization_review = review
            independent = isinstance(review, dict) and review.get('decision') == NON_DELETION \
                and type(review.get('confidence')) in (int, float) and 0.9 <= review['confidence'] <= 1 \
                and review.get('conflicting') is False and isinstance(review.get('quote'), str) \
                and bool(review['quote'].strip()) and review['quote'] in request.request_text
            if det.decision != NON_DELETION and not independent:
                reject('AUTHORIZATION_UNSUPPORTED', 'Non-deletion could not be independently confirmed; disclose uncertainty.')
        intent = Intent(decision, a['reason'], (quote,), 'agent-first-v1', scope=scope if decision == DELETE else None,
                        target=a['target'] or None, whole_category=a['whole_category'])
        plan = CasePlan(request, intent, subject, status=proposal['deletion_status'],
                        steps=parse_steps_json(json.dumps(proposal['steps'])))
        for problem in row_problems(plan.row, request, self.verifier.context.evidence):
            reject('INTEGRITY', problem)  # every contract problem at once, not one per round
        witnesses = {w['step_no']: w for w in proposal['witnesses']}
        extra = fact_observations(case.facts)
        owned = self.verifier._owned(subject, request.audit_at, extra)
        disclosed = self._disclosures(proposal, plan, subject, owned, extra, case, reject)
        grouped = {}
        for s in plan.steps:
            grouped.setdefault((s.system_id, s.resource_id), []).append(s)
        parents = {key for key, steps in grouped.items() if self.verifier.parent_absence_source(
            intent, *key, steps, self.verifier._observations(key[1], request.audit_at, extra),
            request.audit_at, subject, extra, owned=owned) is not None}
        if len(witnesses) != len(proposal['witnesses']):
            reject('UNSHOWN_WITNESS', 'Witness step numbers must be unique.')
        for number, step in enumerate(plan.steps, 1):
            if step.resource_id not in case.handles:
                reject('INVENTED_RESOURCE', f'{step.resource_id} was never retrieved.')
            systems = {o.system_id for o in self.verifier._observations(step.resource_id, request.audit_at, extra)
                       if o.system_id and o.kind in (STATE, LINK)}
            if systems and step.system_id not in systems:
                reject('SYSTEM_MISMATCH', f'{step.resource_id} is recorded under system_id {sorted(systems)}, '
                       f'not {step.system_id!r}. system_id is the owning service named in the evidence, not a '
                       'table, file or replica name.')
            parent = (step.system_id, step.resource_id) in parents
            if not step.evidence_id and not parent:
                continue
            w = witnesses.get(number)
            try:
                if not w or (step.evidence_id and w['source_id'] != step.evidence_id):
                    raise ValueError('Each cited step needs its own matching source witness.')
                proof_source = step.evidence_id or w['source_id']
                if parent:
                    check = replace(step, evidence_id=proof_source)
                    obs = self.verifier._observations(step.resource_id, request.audit_at, extra)
                    def add(code, severity, message, *rest):
                        findings.append(Finding(code, severity, message))
                    self.verifier._check_citation(check, obs, request.audit_at, add)
                    self.verifier._check_copies(check, obs, request.audit_at, subject, add)
                if not locator_lines(w['locator']) <= case.shown.get(w['source_id'], set()):
                    raise ValueError('The cited lines were never retrieved in full.')
                span = self.verifier.texts.span(w['source_id'], w['locator'])
                literal = w.get('literal', span)
                if not isinstance(literal, str) or not literal.strip():
                    raise ValueError('A supplied literal must be non-empty; omit it to quote the retrieved span.')
                omission = step.operation == 'VERIFY_RECORD_ABSENT' and any(
                    s.source_id == proof_source for s in self.verifier._absence_scopes(
                        step, self.verifier._observations(step.resource_id, request.audit_at,
                            fact_observations(case.facts)), request.audit_at, case.subject_tokens))
                named = re.search(rf'(?<![\w.@+-]){re.escape(step.resource_id)}(?![\w@+-])', literal)
                if ' '.join(literal.split()) not in ' '.join(span.split()) or not (named or omission):
                    raise ValueError('The literal must quote the retrieved resource at this locator.')
                if 'literal' not in w:
                    self.resolved_witnesses.append({**w, 'literal': literal, 'origin': 'already_retrieved_span'})
            except ValueError as exc:
                reject('UNSHOWN_WITNESS', str(exc))
        # The baseline verifier independently checks ownership, copies, coverage and cited states.
        # Retention restrictions are enforced even if the proposal omits a retained resource.
        if decision == DELETE and subject.resolved:
            for fact in case.facts:
                conflicted = any(r.source_id == fact.source_id and
                    any('TEMPORAL_CONFLICT' in issue for issue in r.issues) for r in self.verifier.corpus.reports)
                future = any(o.source_id == fact.source_id and not eligible(o, request.audit_at)
                             for o in self.verifier.corpus.by_resource.get(fact.resource_id, ()))
                if conflicted or future:
                    reject('FACT_UNSUPPORTED', 'Agent facts cannot re-date temporally conflicted or future observations.')
            extra = fact_observations(case.facts)
            owned = self.verifier._owned(subject, request.audit_at, extra)
            if scope == ITEM:
                selected = {(s.system_id, s.resource_id) for s in plan.steps} - parents
                if not a['whole_category'] and len(selected) > 1:
                    reject('SCOPE_EXCEEDED', 'A single-item request cannot select several resources.')
                candidates = {(s, r) for s, r in owned if a['target'] in self.verifier._categories(
                    s, self.verifier._observations(r, request.audit_at, extra), request.audit_at)}
                named = {(s, r) for s, r in candidates if re.search(
                    rf'(?<![\w.@+-]){re.escape(r)}(?![\w@+-])', request.request_text)}
                if not a['whole_category'] and ((named and selected != named) or (not named and len(candidates) != 1)):
                    reject('SCOPE_TARGET_UNGROUNDED', 'Several eligible items exist; the request must identify which one.')
            else:
                selected = set(owned)
            for system, rid in sorted(selected - set(disclosed)):
                for problem in self._policy_problems(system, rid, request.audit_at, extra)[1]:
                    reject('UNPROVEN_POLICY', f'{system}/{rid}: {problem}. Do not invent a treatment; if you '
                           'agree the supplied policy cannot decide it, leave it out of the steps and list it '
                           'under policy_undecided (the status then stays INCOMPLETE).')
            if plan.status == 'COMPLETE' and scope == ACCOUNT:
                profiles = [(s, r) for s, r in owned if 'USER_PROFILE' in self.verifier._categories(
                    s, self.verifier._observations(r, request.audit_at, extra), request.audit_at)]
                if not profiles:
                    reject('PROFILE_COVERAGE_UNPROVEN', 'No owned profile ID is grounded; cannot certify complete account deletion.')
        plan.obligations = [Obligation(system, rid, None, None, issues=[f'AGENT_DISCLOSED_UNRESOLVED: {why}'])
                            for (system, rid), why in sorted(disclosed.items())]
        findings += self.verifier.verify(plan, case)
        unique = {(f.code, f.severity, f.message, f.system_id, f.resource_id, f.source_id): f for f in findings}
        return plan, list(unique.values())


    def _policy_problems(self, system, rid, cutoff, extra):
        """(categories, problems): why the supplied policy cannot decide this record, if it cannot."""
        obs = self.verifier._observations(rid, cutoff, extra)
        categories = self.verifier._categories(system, obs, cutoff)
        until = max((o.retention_until for o in obs if o.system_id == system and o.retention_until), default=None)
        problems = []
        for category in sorted(categories):
            try:
                self.policy.treatment(category, audit_at=cutoff, retention_until=until)
            except PolicyError as exc:
                problems.append(str(exc))
        return categories, problems

    def _disclosures(self, proposal, plan, subject, owned, extra, case, reject):
        """Records the agent leaves unresolved; accepted only where the verifier cannot decide them either."""
        items = proposal.get('policy_undecided') or []
        if not items:
            return {}
        if plan.intent.decision != DELETE or plan.intent.scope != ACCOUNT or plan.status != 'INCOMPLETE':
            reject('DISCLOSURE_UNSUPPORTED', 'Unresolved records may be listed only by an INCOMPLETE '
                   'account-scope deletion; they never support COMPLETE.')
            return {}
        stepped = {(s.system_id, s.resource_id) for s in plan.steps}
        disclosed = {}
        for item in items:
            key, why = (item['system_id'], item['resource_id']), item['reason']
            if key in disclosed or not why.strip():
                reject('DISCLOSURE_UNSUPPORTED', 'List each unresolved record once, with a reason.')
                continue
            if key[1] not in case.handles:
                reject('INVENTED_RESOURCE', f'{key[1]} was never retrieved.')
                continue
            if key in stepped:
                reject('DISCLOSURE_UNSUPPORTED', f'{key[0]}/{key[1]} has steps; a record is either planned '
                       'or left unresolved, not both.')
                continue
            if not subject.resolved or owned.get(key) not in ('direct', 'reference'):
                reject('DISCLOSURE_UNSUPPORTED', f'No eligible evidence links {key[1]} to the subject in '
                       f'{key[0]} (not-found lookups are not a link); an unlinked record needs no disclosure.')
                continue
            categories, problems = self._policy_problems(*key, plan.request.audit_at, extra)
            if not categories:
                reject('DISCLOSURE_UNSUPPORTED', f'No eligible evidence says what {key[1]} is; that is missing '
                       'evidence to retrieve, not an undecidable policy.')
                continue
            if not problems:
                reject('DISCLOSURE_UNSUPPORTED', f'The supplied policy decides {key[0]}/{key[1]} '
                       f'({", ".join(sorted(categories))}); plan its steps instead: '
                       + '; '.join(treatment_chain(self.policy, c, plan.request.audit_at) for c in sorted(categories)))
                continue
            disclosed[key] = why.strip()
        return disclosed


class AgentFirst:
    def __init__(self, gateway, config, corpus, index, policy, texts, verifier, *, allow_live=False,
                 authorization_model=None, finalize_on_pressure=False, request_first=False):
        if not allowed_agent_gateway(gateway, allow_live):
            raise ContractError('agent-first needs replay or explicit bounded live opt-in with retries disabled')
        if authorization_model == config.model:
            raise ContractError('authorization review must use a distinct model from the primary agent')
        self.gateway, self.config, self.corpus, self.index = gateway, config, corpus, index
        self.policy, self.texts = policy, texts
        self.checker = ProposalVerifier(verifier, policy)
        self.authorization_model = authorization_model
        self.finalize_on_pressure = finalize_on_pressure
        self.request_first = request_first
        if authorization_model:
            from .authorization_review import AuthorizationReview
            self.checker.authorization_review = AuthorizationReview(gateway, authorization_model)
        self.executor = Investigator(gateway, config, corpus, index, policy, texts)
        from .intent_models import PROMPTS
        self.prompt = (PROMPTS / 'agent_first_v1.md').read_text(encoding='utf-8')
        self.version = 'agent-first-v1:' + hashlib.sha256(
            (self.prompt + json.dumps(AGENT_TOOLS, sort_keys=True)).encode()).hexdigest()[:16]

    @staticmethod
    def _request_bound(body, output_limit, anchor=None):
        """Upper bound on a request's tokens. Bytes bound tokens; with an anchor (messages sent,
        model and tools, provider-reported prompt_tokens) only messages appended since are byte-counted."""
        size = lambda value: len(json.dumps(value, ensure_ascii=False).encode())
        if anchor and anchor[0] <= len(body['messages']) and anchor[1] == (body['model'], size(body.get('tools'))):
            return anchor[2] + size(body['messages'][anchor[0]:]) + size(body.get('tool_choice')) + output_limit
        return size(body) + output_limit

    def _can_admit(self, start, model, body, output_limit, anchor=None):
        tokens, paise = self.executor._case_usage(start)
        bound = self._request_bound(body, output_limit, anchor)
        reserve = max(self.gateway.config.chat_cost_reserve_paise,
                      self.gateway._worst_case(model, body, output_limit) or 0)
        return tokens + bound <= self.config.max_case_tokens and paise + reserve <= self.config.max_case_paise

    def _catalog(self, cutoff, arguments):
        offset = arguments.get('offset', 0)
        if type(offset) is not int or offset < 0:
            raise ContractError('catalog offset must be a nonnegative integer')
        kind, query = arguments.get('kind', ''), arguments.get('query', '').casefold()
        rows = [{**asdict(m), 'kind': self.texts.kind(m.file_id)} for m in self.corpus.context.manifest
                if m.captured_at <= cutoff]
        rows = sorted((r for r in rows if (not kind or r['kind'] == kind)
                       and (not query or query in f"{r['file_id']} {r['file_path']} {r['origin']}".casefold())),
                      key=lambda r: r['file_id'])
        page = rows[offset:offset + 20]
        return {'sources': page, 'total': len(rows), 'offset': offset,
                'next_offset': offset + len(page) if offset + len(page) < len(rows) else None,
                'note': 'Metadata is not ownership, membership or state evidence; inspect content.'}

    def run(self, request):
        subject = resolve_subject(request.email, request.audit_at, self.index, self.corpus)
        unresolved = Intent(UNRESOLVED, 'No verified agent proposal', method='agent-first-v1')
        fallback = CasePlan(request, unresolved, subject, fallback=True, fallback_reason='AGENT_UNVERIFIED: no submission')
        case = CaseFile.from_plan(fallback)
        itools = InvestigationTools(self.executor.tools, self.texts, case)
        brief = {'request': {k: v for k, v in asdict(request).items() if k != 'case_id'},
                 'subject': {'user_ids': subject.user_ids,
                 'identifiers': case.identifiers, 'issues': subject.issues},
                 'policy': policy_treatments(self.policy, self.corpus.context.rules, request.audit_at),
                 'allowed_operations_in_order': OPERATION_TABLE,
                 'source_inventory': {'by_kind': dict(sorted(Counter(self.texts.kind(m.file_id)
                     for m in self.corpus.context.manifest if m.captured_at <= request.audit_at).items())),
                     'browse_tool': 'list_sources', 'note': 'Metadata does not establish case membership.'},
                 'limits': self.config.describe()}
        # No deterministic candidates, obligations, status or step list is supplied.
        opening = {'request': brief['request'], 'instruction':
            'First interpret the requested action and scope from the customer wording alone. '
            'Do not assess deletion completeness or source coverage yet.'} if self.request_first else brief
        messages = [{'role': 'system', 'content': self.prompt},
                    {'role': 'user', 'content': json.dumps(opening, default=str, ensure_ascii=False)}]
        start, seen, attempts = len(self.gateway.usage), {}, []
        anchor = None  # (messages sent, (model, tools size), reported prompt_tokens) of the last call
        processed_tokens = 0  # Payload usage survives cache replay; billed gateway usage does not.
        request_interpreted = not self.request_first
        recorded = None  # the agent's own validated interpret_request reading, echoed back, never applied
        evidence_repair_used = False
        if getattr(self.checker, 'authorization_review', None):
            self.checker.authorization_review.admit = lambda model, messages, limit: self._can_admit(
                start, model, {'model': model, 'messages': messages, 'max_completion_tokens': limit}, limit)
        for turn in range(self.config.max_rounds):
            interpreting = not request_interpreted
            active_tools = [REQUEST_TOOL] if interpreting else AGENT_TOOLS
            choice = {'type': 'function', 'function': {'name': 'interpret_request'}} if interpreting else 'required'
            body = {'model': self.config.model, 'messages': messages, 'tools': active_tools,
                    'tool_choice': choice, 'max_completion_tokens': self.config.max_completion_tokens}
            request_bound = self._request_bound(body, self.config.max_completion_tokens, anchor)
            remaining_rounds = self.config.max_rounds - turn
            remaining_tools = max(0, self.config.max_tool_calls - len(case.tool_calls))
            finalize = not interpreting and self.finalize_on_pressure and (remaining_rounds == 1 or remaining_tools <= 1
                or processed_tokens + 2 * request_bound > self.config.max_case_tokens)
            evidence_codes = {'OMITTED_RESOURCE', 'UNSHOWN_WITNESS', 'INVENTED_RESOURCE', 'UNPROVEN_POLICY',
                              'SYSTEM_MISMATCH',
                              'POLICY_UNDETERMINED', 'UNSUPPORTED_CITATION', 'UNSUPPORTED_ASSOCIATION',
                              'UNPROVEN_CONDITION', 'FACT_UNSUPPORTED'}
            repair = bool(not interpreting and not evidence_repair_used and attempts and not attempts[-1]['accepted']
                and remaining_rounds > 1 and remaining_tools > 1
                and any(f['code'] in evidence_codes for f in attempts[-1]['findings']))
            if repair:
                finalize = False  # One chance to retrieve missing proof; all admission bounds still apply.
            if finalize:
                body['tool_choice'] = {'type': 'function', 'function': {'name': 'submit_audit'}}
            messages.append({'role': 'user', 'content': json.dumps({'execution_budget': {
                'remaining_rounds': remaining_rounds, 'remaining_tool_calls': remaining_tools,
                'submission_required': finalize,
                'evidence_repair_window': repair,
                'tokens_processed': processed_tokens,
                'remaining_case_tokens': max(0, self.config.max_case_tokens - processed_tokens),
                'case_cost_limit_paise': self.config.max_case_paise,
                'next_request_token_bound_before_notice': request_bound,
                'finalization_urgent': remaining_rounds <= 2 or remaining_tools <= 3
                    or processed_tokens + request_bound >= 0.65 * self.config.max_case_tokens}})})
            if not self._can_admit(start, self.config.model, body, self.config.max_completion_tokens, anchor):
                case.stop_reason = 'CASE_BUDGET'
                break
            if len(case.tool_calls) >= self.config.max_tool_calls:
                case.stop_reason = 'TOOL_LIMIT'
                break
            sent = len(messages)
            try:
                if repair:
                    evidence_repair_used = True
                payload = self.gateway.chat('primary_agent', self.config.model, messages, self.version,
                    lambda p: None, max_tokens=self.config.max_completion_tokens, tools=active_tools,
                    tool_choice=body['tool_choice'])
            except ProviderError as exc:
                if str(exc).startswith('invalid'):
                    if case.schema_repairs < self.config.max_schema_repairs:
                        case.schema_repairs += 1
                        messages.append({'role': 'user', 'content': 'Your tool response failed schema validation: '
                                         + str(exc)[:300] + '. Correct it using the declared tool schemas.'})
                        continue
                    case.stop_reason = 'SCHEMA_REPAIR_LIMIT'
                    break
                case.stop_reason = f'MODEL_UNAVAILABLE: {type(exc).__name__}'
                break
            usage = payload.get('usage') or {}
            processed_tokens += sum(v for v in (usage.get('prompt_tokens'), usage.get('completion_tokens'))
                                    if type(v) is int and v >= 0)
            prompt = usage.get('prompt_tokens')
            anchor = (sent, (body['model'], len(json.dumps(active_tools, ensure_ascii=False).encode())), prompt) \
                if type(prompt) is int and prompt > 0 else None
            message = payload['choices'][0]['message']
            if attempts and not attempts[-1]['accepted']:
                case.feedback_turns += 1
            messages.append({'role': 'assistant', 'content': message.get('content'), 'tool_calls': message['tool_calls']})
            case.rounds = turn + 1
            accepted = None
            for call in message['tool_calls']:
                if len(case.tool_calls) >= self.config.max_tool_calls:
                    result = {'error': 'TOOL_LIMIT'}
                elif call['function']['name'] == 'list_sources':
                    from .casefile import ToolCall
                    arguments = strict_json(call['function']['arguments'])
                    try:
                        result = self._catalog(request.audit_at, arguments)
                        case.tool_calls.append(ToolCall(case.rounds, 'list_sources', arguments, True,
                                                       {'returned': len(result['sources'])}))
                    except ContractError as exc:
                        result = {'error': str(exc)}
                        case.tool_calls.append(ToolCall(case.rounds, 'list_sources', arguments, False, error=str(exc)))
                elif interpreting and call['function']['name'] == 'interpret_request':
                    from .casefile import ToolCall
                    interpretation = strict_json(call['function']['arguments'])
                    quoted = interpretation['request_quote']
                    valid = bool(quoted.strip()) and quoted in request.request_text
                    case.tool_calls.append(ToolCall(case.rounds, 'interpret_request', interpretation, valid))
                    result = {'recorded': valid, 'note': 'This is your interpretation, not permission granted '
                        'by code. Re-evaluate ambiguous wording if necessary; final authorization and all '
                        'evidence still require independent verification.'}
                    request_interpreted = valid
                    if valid:
                        recorded = interpretation
                elif call['function']['name'] == 'submit_audit':
                    proposal = strict_json(call['function']['arguments'])
                    self.checker.last_authorization_review = None
                    self.checker.last_serialization = None
                    self.checker.resolved_witnesses = []
                    try:
                        if call is not message['tool_calls'][-1]:
                            raise ContractError('submit_audit must be the final tool call of a turn')
                        candidate, findings = self.checker.verify(request, subject, proposal, case)
                    except (ContractError, ValueError) as exc:
                        findings = [Finding('INTEGRITY', 'BLOCKING', str(exc))]
                    blocking = [f for f in findings if f.blocking]
                    case.lookup_handles.update(f.resource_id for f in blocking
                                               if f.code == 'OMITTED_RESOURCE' and f.resource_id)
                    if blocking:
                        case.feedback_rounds += 1
                    attempts.append({'proposal': proposal, 'accepted': not blocking,
                                     'findings': [f.record() for f in findings],
                                     'authorization_model': self.authorization_model,
                                     'authorization_review': self.checker.last_authorization_review,
                                     'serialization': self.checker.last_serialization,
                                     'resolved_witnesses': self.checker.resolved_witnesses})
                    case.verifier_findings.append([f.record() for f in findings])
                    from .casefile import ToolCall
                    case.tool_calls.append(ToolCall(case.rounds, 'submit_audit', proposal, not blocking))
                    result = {'accepted': not blocking, 'verifier_feedback': [f.record() for f in findings]}
                    if recorded and recorded.get('decision') != UNRESOLVED and any(
                            f.code == 'AUTHORIZATION_UNRESOLVED' for f in blocking):
                        result['your_recorded_interpretation'] = recorded
                        result['interpretation_note'] = ('You recorded this reading of the request before '
                            'investigating. Missing, partial or contradictory evidence is an audit outcome, '
                            'not ambiguity in what the customer asked. Keep or revise it from the wording alone.')
                    if not blocking:
                        accepted = candidate
                else:
                    result = self.executor._execute(case, itools, call, seen)
                messages.append({'role': 'tool', 'tool_call_id': call['id'],
                                 'content': json.dumps(result, ensure_ascii=False, default=str)})
            if interpreting and request_interpreted:
                messages.append({'role': 'user', 'content': json.dumps(brief, default=str, ensure_ascii=False)})
            # Every batched call is processed; a later rejection cannot be hidden by an earlier acceptance.
            if accepted is not None and attempts[-1]['accepted']:
                case.scope, case.target = accepted.intent.scope, accepted.intent.target
                case.outcome, case.stop_reason = 'AGENT_ACCEPTED', 'VERIFIED_PROPOSAL'
                return accepted, case, attempts
        case.stop_reason = case.stop_reason or 'ROUND_LIMIT'
        case.outcome = 'AGENT_FALLBACK'
        fallback.fallback_reason = 'AGENT_UNVERIFIED: ' + case.stop_reason
        return fallback, case, attempts


def proposal_metrics(records):
    attempts = [a for record in records for a in record.get('agent_proposals', [])]
    rejected = sum(not a['accepted'] for a in attempts)
    hallucinated = sum(any(f['code'] in HALLUCINATION_CODES for f in a['findings']) for a in attempts)
    return {'proposals': len(attempts), 'rejected': rejected,
            'rejection_rate': rejected / len(attempts) if attempts else None,
            'grounding_rejected': hallucinated,
            'grounding_rejection_rate': hallucinated / len(attempts) if attempts else None,
            'hallucination_rate_proxy': hallucinated / len(attempts) if attempts else None}
