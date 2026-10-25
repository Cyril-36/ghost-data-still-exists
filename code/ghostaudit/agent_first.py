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

from .casefile import CaseFile, fact_observations
from .contracts import ContractError, OPERATION_TABLE
from .corpus import eligible
from .identity import resolve_subject
from .intent import ACCOUNT, DELETE, ITEM, NON_DELETION, UNRESOLVED, Intent, classify, hypothetical_deletion
from .investigator import Investigator
from .output import check_row, parse_steps_json
from .planner import CasePlan
from .policy import PolicyError
from .providers import ProviderError, ReplayGateway, strict_json
from .tools import EVIDENCE_TOOLS, TOOL_SCHEMAS, InvestigationTools, locator_lines
from .verifier import Finding

STR = {'type': 'string'}
PROPOSAL_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'authorization': {'type': 'object', 'additionalProperties': False, 'properties': {
        'decision': {'type': 'string', 'enum': [DELETE, NON_DELETION, UNRESOLVED]},
        'scope': {'type': 'string', 'enum': [ACCOUNT, ITEM, 'NONE']}, 'target': STR,
        'whole_category': {'type': 'boolean'}, 'request_quote': STR, 'reason': STR},
        'required': ['decision', 'scope', 'target', 'whole_category', 'request_quote', 'reason']},
    'deletion_status': {'type': 'string', 'enum': ['COMPLETE', 'INCOMPLETE', 'NOT_APPLICABLE']},
    'steps': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'properties': {**{k: STR for k in ('phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id')},
                       'step_no': {'type': 'integer'}},
        'required': ['step_no', 'phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id']}},
    'witnesses': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'properties': {'step_no': {'type': 'integer'}, 'source_id': STR, 'locator': STR, 'literal': STR},
        'required': ['step_no', 'source_id', 'locator', 'literal']}}},
    'required': ['authorization', 'deletion_status', 'steps', 'witnesses']}
AGENT_TOOLS = [t for t in TOOL_SCHEMAS if t['function']['name'] in (*EVIDENCE_TOOLS, 'propose_fact')] + [{
    'type': 'function', 'function': {'name': 'submit_audit', 'description':
        'Propose the final authorization, status and complete ordered steps for independent verification.',
        'parameters': PROPOSAL_SCHEMA}}]
HALLUCINATION_CODES = {'INVENTED_RESOURCE', 'INELIGIBLE_CITATION', 'UNSHOWN_WITNESS', 'UNSUPPORTED_CITATION',
                       'UNSUPPORTED_ASSOCIATION', 'FACT_UNSUPPORTED', 'CROSS_PERSON'}


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
    """Validate an authored answer. Never generate, fill in, reorder or replace its steps."""

    def __init__(self, verifier, policy):
        self.verifier, self.policy = verifier, policy
        self.last_authorization_review = None

    def review_authorization(self, text):
        return self.authorization_review(text) if getattr(self, 'authorization_review', None) else None

    def verify(self, request, subject, proposal, case):
        findings = []
        self.last_authorization_review = None

        def reject(code, message):
            findings.append(Finding(code, 'BLOCKING', message))

        a = proposal['authorization']
        quote = a['request_quote']
        if not quote.strip() or quote not in request.request_text or not a['reason'].strip():
            reject('AUTHORIZATION_UNSUPPORTED', 'Quote the actual request and explain its authorization.')
        det = classify(request.request_text)
        decision, scope = a['decision'], a['scope']
        if decision == UNRESOLVED:
            reject('AUTHORIZATION_UNRESOLVED', 'Unresolved authorization requires a disclosed fallback.')
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
                reject('SCOPE_EXCEEDED', 'ACCOUNT has no item target or whole-category flag.')
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
        try:
            check_row(plan.row, request, self.verifier.context.evidence)
        except ContractError as exc:
            reject('INTEGRITY', str(exc))
        witnesses = {w['step_no']: w for w in proposal['witnesses']}
        extra = fact_observations(case.facts)
        owned = self.verifier._owned(subject, request.audit_at, extra)
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
            parent = (step.system_id, step.resource_id) in parents
            if not step.evidence_id and not parent:
                continue
            w = witnesses.get(number)
            try:
                if not w or (step.evidence_id and w['source_id'] != step.evidence_id) or not w['literal'].strip():
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
                omission = step.operation == 'VERIFY_RECORD_ABSENT' and any(
                    s.source_id == proof_source for s in self.verifier._absence_scopes(
                        step, self.verifier._observations(step.resource_id, request.audit_at,
                            fact_observations(case.facts)), request.audit_at, case.subject_tokens))
                named = re.search(rf'(?<![\w.@+-]){re.escape(step.resource_id)}(?![\w@+-])', w['literal'])
                if ' '.join(w['literal'].split()) not in ' '.join(span.split()) or not (named or omission):
                    raise ValueError('The literal must quote the retrieved resource at this locator.')
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
            for system, rid in selected:
                obs = self.verifier._observations(rid, request.audit_at, extra)
                for category in self.verifier._categories(system, obs, request.audit_at):
                    try:
                        self.policy.treatment(category, audit_at=request.audit_at,
                                              retention_until=max((o.retention_until for o in obs
                                                  if o.system_id == system and o.retention_until), default=None))
                    except PolicyError as exc:
                        reject('UNPROVEN_POLICY', f'{system}/{rid}: {exc}')
            if plan.status == 'COMPLETE' and scope == ACCOUNT:
                profiles = [(s, r) for s, r in owned if 'USER_PROFILE' in self.verifier._categories(
                    s, self.verifier._observations(r, request.audit_at, extra), request.audit_at)]
                if not profiles:
                    reject('PROFILE_COVERAGE_UNPROVEN', 'No owned profile ID is grounded; cannot certify complete account deletion.')
        findings += self.verifier.verify(plan, case)
        return plan, findings


class AgentFirst:
    def __init__(self, gateway, config, corpus, index, policy, texts, verifier, *, allow_live=False,
                 authorization_model=None):
        if not allowed_agent_gateway(gateway, allow_live):
            raise ContractError('agent-first needs replay or explicit bounded live opt-in with retries disabled')
        if authorization_model == config.model:
            raise ContractError('authorization review must use a distinct model from the primary agent')
        self.gateway, self.config, self.corpus, self.index = gateway, config, corpus, index
        self.policy, self.texts = policy, texts
        self.checker = ProposalVerifier(verifier, policy)
        self.authorization_model = authorization_model
        if authorization_model:
            from .authorization_review import AuthorizationReview
            self.checker.authorization_review = AuthorizationReview(gateway, authorization_model)
        self.executor = Investigator(gateway, config, corpus, index, policy, texts)
        from .intent_models import PROMPTS
        self.prompt = (PROMPTS / 'agent_first_v1.md').read_text(encoding='utf-8')
        self.version = 'agent-first-v1:' + hashlib.sha256(
            (self.prompt + json.dumps(AGENT_TOOLS, sort_keys=True)).encode()).hexdigest()[:16]

    def _can_admit(self, start, model, body, output_limit):
        tokens, paise = self.executor._case_usage(start)
        bound = len(json.dumps(body, ensure_ascii=False).encode()) + output_limit
        reserve = max(self.gateway.config.chat_cost_reserve_paise,
                      self.gateway._worst_case(model, body, output_limit) or 0)
        return tokens + bound <= self.config.max_case_tokens and paise + reserve <= self.config.max_case_paise

    def run(self, request):
        subject = resolve_subject(request.email, request.audit_at, self.index, self.corpus)
        unresolved = Intent(UNRESOLVED, 'No verified agent proposal', method='agent-first-v1')
        fallback = CasePlan(request, unresolved, subject, fallback=True, fallback_reason='AGENT_UNVERIFIED: no submission')
        case = CaseFile.from_plan(fallback)
        itools = InvestigationTools(self.executor.tools, self.texts, case)
        brief = {'request': {k: v for k, v in asdict(request).items() if k != 'case_id'},
                 'subject': {'user_ids': subject.user_ids,
                 'identifiers': case.identifiers, 'issues': subject.issues},
                 'policy': [asdict(r) for r in self.corpus.context.rules],
                 'allowed_operations_in_order': OPERATION_TABLE,
                 'source_inventory': [asdict(m) for m in self.corpus.context.manifest
                                      if m.captured_at <= request.audit_at],
                 'limits': self.config.describe()}
        # No deterministic candidates, obligations, status or step list is supplied.
        messages = [{'role': 'system', 'content': self.prompt},
                    {'role': 'user', 'content': json.dumps(brief, default=str, ensure_ascii=False)}]
        start, seen, attempts = len(self.gateway.usage), {}, []
        processed_tokens = 0  # Payload usage survives cache replay; billed gateway usage does not.
        if getattr(self.checker, 'authorization_review', None):
            self.checker.authorization_review.admit = lambda model, messages, limit: self._can_admit(
                start, model, {'model': model, 'messages': messages, 'max_completion_tokens': limit}, limit)
        for turn in range(self.config.max_rounds):
            body = {'model': self.config.model, 'messages': messages, 'tools': AGENT_TOOLS,
                    'tool_choice': 'required', 'max_completion_tokens': self.config.max_completion_tokens}
            request_bound = len(json.dumps(body, ensure_ascii=False).encode()) + self.config.max_completion_tokens
            remaining_rounds = self.config.max_rounds - turn
            remaining_tools = max(0, self.config.max_tool_calls - len(case.tool_calls))
            messages.append({'role': 'user', 'content': json.dumps({'execution_budget': {
                'remaining_rounds': remaining_rounds, 'remaining_tool_calls': remaining_tools,
                'tokens_processed': processed_tokens,
                'remaining_case_tokens': max(0, self.config.max_case_tokens - processed_tokens),
                'case_cost_limit_paise': self.config.max_case_paise,
                'next_request_token_bound_before_notice': request_bound,
                'finalization_urgent': remaining_rounds <= 2 or remaining_tools <= 3
                    or processed_tokens + request_bound >= 0.65 * self.config.max_case_tokens}})})
            if not self._can_admit(start, self.config.model, body, self.config.max_completion_tokens):
                case.stop_reason = 'CASE_BUDGET'
                break
            if len(case.tool_calls) >= self.config.max_tool_calls:
                case.stop_reason = 'TOOL_LIMIT'
                break
            try:
                payload = self.gateway.chat('primary_agent', self.config.model, messages, self.version,
                    lambda p: None, max_tokens=self.config.max_completion_tokens, tools=AGENT_TOOLS, tool_choice='required')
            except ProviderError as exc:
                if str(exc).startswith('invalid') and case.schema_repairs < self.config.max_schema_repairs:
                    case.schema_repairs += 1
                    messages.append({'role': 'user', 'content': 'Your tool response failed schema validation: '
                                     + str(exc)[:300] + '. Correct it using the declared tool schemas.'})
                    continue
                case.stop_reason = f'MODEL_UNAVAILABLE: {type(exc).__name__}'
                break
            usage = payload.get('usage') or {}
            processed_tokens += sum(v for v in (usage.get('prompt_tokens'), usage.get('completion_tokens'))
                                    if type(v) is int and v >= 0)
            message = payload['choices'][0]['message']
            if attempts and not attempts[-1]['accepted']:
                case.feedback_turns += 1
            messages.append({'role': 'assistant', 'content': message.get('content'), 'tool_calls': message['tool_calls']})
            case.rounds = turn + 1
            accepted = None
            for call in message['tool_calls']:
                if len(case.tool_calls) >= self.config.max_tool_calls:
                    result = {'error': 'TOOL_LIMIT'}
                elif call['function']['name'] == 'submit_audit':
                    proposal = strict_json(call['function']['arguments'])
                    self.checker.last_authorization_review = None
                    try:
                        if call is not message['tool_calls'][-1]:
                            raise ContractError('submit_audit must be the final tool call of a turn')
                        candidate, findings = self.checker.verify(request, subject, proposal, case)
                    except (ContractError, ValueError) as exc:
                        findings = [Finding('INTEGRITY', 'BLOCKING', str(exc))]
                    blocking = [f for f in findings if f.blocking]
                    if blocking:
                        case.feedback_rounds += 1
                    attempts.append({'proposal': proposal, 'accepted': not blocking,
                                     'findings': [f.record() for f in findings],
                                     'authorization_model': self.authorization_model,
                                     'authorization_review': self.checker.last_authorization_review})
                    case.verifier_findings.append([f.record() for f in findings])
                    from .casefile import ToolCall
                    case.tool_calls.append(ToolCall(case.rounds, 'submit_audit', proposal, not blocking))
                    result = {'accepted': not blocking, 'verifier_feedback': [f.record() for f in findings]}
                    if not blocking:
                        accepted = candidate
                else:
                    result = self.executor._execute(case, itools, call, seen)
                messages.append({'role': 'tool', 'tool_call_id': call['id'],
                                 'content': json.dumps(result, ensure_ascii=False, default=str)})
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
