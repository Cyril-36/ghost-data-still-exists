"""Bounded AI investigator (plan sections 14 and 21): the model chooses evidence tools and proposes
facts; code validates every call and fact; the deterministic engine re-plans from accepted facts.

Loop per routed case (sequential, one case at a time):
  brief -> [model turn: tool calls -> tool results]* -> submit_case_file -> verifier
        -> feedback (at most `max_feedback_rounds`) -> ... -> stop
Limits: `max_rounds` model turns that reach the tools, `max_schema_repairs` re-asks after a
malformed response, `max_tool_calls`, and per-case token and paise caps under the gateway's run
ceiling. An identical tool call is refused unless new evidence arrived since it was made.

The model never sets a status, writes steps, or executes anything: tool calls are validated by
`tool_call_problem` (in the gateway) and run by `InvestigationTools`, which are read-only.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .casefile import CaseFile, FactValidator, ToolCall, fact_observations
from .intent_models import PROMPTS
from .planner import UNRESOLVED, plan_case
from .providers import BudgetExceeded, ProviderError, strict_json
from .tools import EVIDENCE_TOOLS, TOOL_SCHEMAS, InvestigationTools, ToolError, Tools

PROMPT = 'investigator_v1.md'
ROUTE_FALLBACKS = ('UNRESOLVED_OBLIGATIONS', 'NO_REPRESENTABLE_OBLIGATION', 'SCOPE_TARGET_UNGROUNDED')


@dataclass(frozen=True)
class InvestigatorConfig:
    model: str
    max_rounds: int = 4
    max_feedback_rounds: int = 2
    max_schema_repairs: int = 1
    max_tool_calls: int = 16
    max_case_tokens: int = 60000
    max_case_paise: float = 400.0
    max_completion_tokens: int = 2000

    def describe(self) -> dict:
        return asdict(self)


def prompt_version() -> str:
    digest = hashlib.sha256((PROMPTS / PROMPT).read_bytes() + json.dumps(TOOL_SCHEMAS, sort_keys=True).encode())
    return f'investigator_v1:{digest.hexdigest()[:16]}'


def route_reasons(plan, findings=()) -> list[str]:
    """Why a case needs investigation; [] when it is settled and independently verified."""
    if plan.intent.decision != 'DELETE' or plan.subject is None or not plan.subject.resolved:
        return []
    reasons = []
    if plan.fallback and plan.fallback_reason.startswith(ROUTE_FALLBACKS):
        reasons.append(f'fallback: {plan.fallback_reason}')
    reasons += [f'unresolved: {o.system_id}/{o.resource_id}' for o in plan.obligations
                if o.result == UNRESOLVED and o.resource_id]
    reasons += [f'verifier: {f.code} {f.resource_id or f.source_id or ""}'.strip() for f in findings if f.routes]
    return reasons


def probe_tool_calling(gateway, model: str) -> dict:
    """One bounded call: does `model` return a schema-valid native tool call on this gateway?"""
    tools = [{'type': 'function', 'function': {'name': 'echo_handle', 'parameters': {
        'type': 'object', 'properties': {'handle': {'type': 'string'}}, 'required': ['handle'],
        'additionalProperties': False}}}]
    choice = {'type': 'function', 'function': {'name': 'echo_handle'}}
    messages = [{'role': 'user', 'content': 'Call echo_handle with handle "probe_ok_1".'}]
    try:
        payload = gateway.chat('tool_probe', model, messages, 'tool_probe_v1', lambda p: None, max_tokens=200,
                               tools=tools, tool_choice=choice)
        args = strict_json(payload['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
        ok = args == {'handle': 'probe_ok_1'}
        return {'model': model, 'ok': ok, 'returned_model': payload.get('model'),
                'detail': '' if ok else f'unexpected arguments {args}'}
    except (ProviderError, KeyError, IndexError, TypeError, ValueError) as exc:
        return {'model': model, 'ok': False, 'detail': str(exc)[:300]}


class Investigator:
    def __init__(self, gateway, config: InvestigatorConfig, corpus, index, policy, texts, verifier=None):
        self.gateway, self.config = gateway, config
        self.corpus, self.index, self.policy, self.texts = corpus, index, policy, texts
        self.tools = Tools(corpus, index, policy)
        self.validator = FactValidator(corpus, index, policy, texts)
        self.verifier = verifier             # callable(plan, case) -> [Finding]
        self.version = prompt_version()

    # -- brief -----------------------------------------------------------------------------------
    def _brief(self, case: CaseFile) -> str:
        status = {r.source_id: r.status for r in self.corpus.reports}
        inventory = [{'source_id': m.file_id, 'origin': m.origin, 'captured_at': m.captured_at.isoformat(),
                      'media': self.texts.kind(m.file_id), 'parse_status': status.get(m.file_id)}
                     for m in self.corpus.context.manifest if m.captured_at <= case.audit_at]
        return json.dumps({
            'request': {'text': case.request_text, 'audit_at': case.audit_at.isoformat()},
            'authorized_scope': {'scope': case.scope, 'target_category': case.target},
            'subject_identifiers': [{'kind': k, 'value': v, 'provenance': p} for k, v, p in case.identifiers],
            'candidate_resources': case.candidates, 'conflicts': case.conflicts, 'open_questions': case.questions,
            'policy_categories': sorted(self.policy.by_category) + ['DATA_FLOW'],
            'source_inventory': inventory,
            'limits': {'rounds': self.config.max_rounds, 'tool_calls': self.config.max_tool_calls},
        }, ensure_ascii=False)

    # -- accounting ------------------------------------------------------------------------------
    def _case_usage(self, start):
        records = self.gateway.usage[start:]
        tokens = sum((r.input_tokens or 0) + (r.output_tokens or 0) for r in records)
        reserve = self.gateway.config.chat_cost_reserve_paise
        paise = sum(r.cost_paise_header if r.cost_paise_header is not None else
                    (reserve if r.mode == 'live' and r.status != 'error' else 0.0) for r in records)
        return tokens, paise

    def _replan(self, plan, facts):
        if not facts:
            return plan
        corpus = self.corpus.extended(fact_observations(facts))
        return plan_case(plan.request, corpus, self.policy, self.index, plan.intent)

    # -- one tool call ---------------------------------------------------------------------------
    @staticmethod
    def _progress(case):
        return len(case.facts), len(case.handles), len(case.inspected)

    def _execute(self, case, itools, call, seen):
        fn = call['function']
        name, args = fn['name'], strict_json(fn['arguments'])
        key = (name, json.dumps(args, sort_keys=True))
        record = ToolCall(case.rounds, name, args, False)
        if len(case.tool_calls) >= self.config.max_tool_calls:
            result = {'error': f'TOOL_LIMIT: {self.config.max_tool_calls} tool calls per case'}
        elif name in EVIDENCE_TOOLS and seen.get(key) == self._progress(case):
            result = {'error': 'REPEATED_CALL: identical call with no new evidence since; inspect something else'}
        elif name in EVIDENCE_TOOLS:
            try:
                result = itools.call(name, args)
                record.ok = True
                seen[key] = self._progress(case)       # a failed call may be retried
            except ToolError as exc:
                result = {'error': f'TOOL_ERROR: {exc}'}
            except Exception as exc:                # a failing tool ends nothing but this call
                result = {'error': f'TOOL_FAILURE: {type(exc).__name__}'}
        elif name == 'propose_fact':
            fact, reason = self.validator.validate(args, case)
            if fact is None:
                case.rejected.append({'proposal': args, 'reason': reason})
                result = {'accepted': False, 'reason': reason}
            else:
                case.facts.append(fact)
                result = {'accepted': True}
                record.ok = True
        else:                                   # submit_case_file
            record.ok = True
            result = {'received': True}
        record.error = result.get('error') or result.get('reason') or ''
        record.returned = {k: result[k] for k in ('total', 'returned', 'truncated', 'total_matches', 'accepted')
                           if k in result}
        case.tool_calls.append(record)
        return result

    # -- the loop --------------------------------------------------------------------------------
    def run(self, plan, questions=()):
        """-> (final CasePlan, CaseFile). The final plan is the deterministic re-plan from accepted
        facts, or the input plan when nothing was accepted or verification failed."""
        case = CaseFile.from_plan(plan, questions)
        itools = InvestigationTools(self.tools, self.texts, case)
        messages = [{'role': 'system', 'content': (PROMPTS / PROMPT).read_text(encoding='utf-8')},
                    {'role': 'user', 'content': self._brief(case)}]
        start, seen, candidate = len(self.gateway.usage), {}, plan
        while not case.stop_reason:
            tokens, paise = self._case_usage(start)
            if case.rounds >= self.config.max_rounds:
                case.stop_reason = 'ROUND_LIMIT'
                break
            if tokens >= self.config.max_case_tokens or \
                    paise + self.gateway.config.chat_cost_reserve_paise > self.config.max_case_paise:
                case.stop_reason = 'CASE_BUDGET'
                break
            try:
                payload = self.gateway.chat('investigator', self.config.model, messages, self.version,
                                            lambda p: None, max_tokens=self.config.max_completion_tokens,
                                            tools=TOOL_SCHEMAS, tool_choice='required')
            except BudgetExceeded as exc:
                case.stop_reason = f'RUN_BUDGET: {exc}'
                break
            except ProviderError as exc:
                if str(exc).startswith('invalid') and case.schema_repairs < self.config.max_schema_repairs:
                    case.schema_repairs += 1
                    messages.append({'role': 'user', 'content': f'Your last response was rejected ({exc}). Reply '
                                     'only with tool calls whose names and arguments match the schemas.'})
                    continue
                case.stop_reason = f'MODEL_ERROR: {str(exc)[:200]}'
                break
            case.rounds += 1
            message = payload['choices'][0]['message']
            calls = message['tool_calls']
            messages.append({'role': 'assistant', 'content': message.get('content'), 'tool_calls': calls})
            submitted = None
            for call in calls:
                result = self._execute(case, itools, call, seen)
                if call['function']['name'] == 'submit_case_file':
                    submitted = strict_json(call['function']['arguments'])
                messages.append({'role': 'tool', 'tool_call_id': call['id'],
                                 'content': json.dumps(result, ensure_ascii=False, default=str)})
            if submitted is None:
                continue
            candidate = self._replan(plan, case.facts)
            findings = self.verifier(candidate, case) if self.verifier else []
            case.verifier_findings.append([f.record() for f in findings])
            blocking = [f for f in findings if f.blocking]
            if not blocking:
                case.stop_reason = f'SUBMITTED: {submitted.get("resolution")}'
            elif case.feedback_rounds < self.config.max_feedback_rounds:
                case.feedback_rounds += 1
                messages.append({'role': 'user', 'content': json.dumps(
                    {'verifier_feedback': [f.record() for f in blocking],
                     'instruction': 'Address these findings with evidence, or submit NO_SUPPORTED_ACTION.'})})
            else:
                case.stop_reason = 'VERIFIER_REJECTED'
        if case.stop_reason.startswith('SUBMITTED') and case.facts:
            case.outcome = 'ACCEPTED'
            return candidate, case
        if case.facts and self.verifier:
            final = self._replan(plan, case.facts)
            if not any(f.blocking for f in self.verifier(final, case)):
                case.outcome = 'ACCEPTED'
                return final, case
        case.outcome = 'PHASE3_KEPT'
        return plan, case

