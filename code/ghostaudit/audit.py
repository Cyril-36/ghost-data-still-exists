"""Offline audit run: requests in, validated output.csv plus run-linked diagnostics out.

Run directory contents (written before the CSV is published):
  case_plans.jsonl        every case: intent, identity, obligations, issues, steps
  unresolved_cases.jsonl  every best-effort fallback row and why (plan section 17)
  coverage.json           per-source parsing outcome, including unsupported media
  case_files.jsonl        every investigated case: tool calls, facts, rejections, verifier findings
  run_manifest.json       input hashes, counts, fallback IDs, and the output CSV sha256

Phase 4: the independent verifier checks every plan. Routed cases (fallbacks, unresolved records,
verifier routing findings) go to the investigator when one is configured, and every final plan is
verified again. A plan that still has blocking findings is withheld as a disclosed fallback.

A fallback row is only publishable when it is disclosed in this run directory;
`validate --run-dir` re-checks that binding.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .config import STRICT, EngineConfig
from .contracts import ContractError
from .corpus import build_corpus
from .identity import IdentityIndex
from .intent_models import decide_intents
from .inputs import load_context, load_requests
from .output import read_output, write_output
from .investigator import Investigator, InvestigatorConfig, route_reasons
from .planner import VIOLATED, CasePlan, plan_case, withhold
from .policy import Policy
from .providers import Gateway
from .tools import SourceText
from .verifier import Verifier

ENGINE_VERSION = 'phase4-3'


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _investigation(case) -> dict | None:
    if case is None:
        return None
    return {'outcome': case.outcome, 'stop_reason': case.stop_reason, 'rounds': case.rounds,
            'feedback_rounds': case.feedback_rounds, 'schema_repairs': case.schema_repairs,
            'tool_calls': len(case.tool_calls), 'facts_accepted': len(case.facts), 'facts_rejected': len(case.rejected),
            'verifier_passes': len(case.verifier_findings),
            'verifier_blocking': sum(1 for v in case.verifier_findings for f in v if f['severity'] == 'BLOCKING')}


def _plan_record(p: CasePlan, stage: str = '', phase4: dict | None = None) -> dict:
    return {
        'case_id': p.request.case_id,
        'status': p.status,
        'fallback': p.fallback,
        'fallback_reason': p.fallback_reason,
        'intent': {'decision': p.intent.decision, 'reason': p.intent.reason, 'spans': list(p.intent.spans),
                   'method': p.intent.method, 'stage': stage, 'scope': p.intent.scope, 'target': p.intent.target,
                   'signals': [list(x) for x in p.intent.signals]},
        'subject': None if p.subject is None else {
            'user_ids': p.subject.user_ids,
            'identifiers': sorted({f'{i.kind}:{i.original}<-{i.provenance}' for i in p.subject.identifiers}),
            'issues': p.subject.issues},
        'obligations': [{
            'system_id': o.system_id, 'resource_id': o.resource_id, 'category': o.category, 'action': o.action,
            'rule_id': o.rule_id, 'result': o.result, 'proof': o.proof.obs_id if o.proof else None,
            'proof_kind': o.proof.proof if o.proof else None,
            'links': sorted({x.obs_id for x in o.links})[:10], 'issues': o.issues} for o in p.obligations],
        'issues': p.issues,
        'steps': len(p.steps),
        **(phase4 or {}),
    }


def run_audit(dataset: Path, requests_path: Path, output: Path, run_dir: Path,
              config: EngineConfig = STRICT, gateway: Gateway | None = None,
              use_generative: bool = True, investigator: InvestigatorConfig | None = None,
              investigator_gateway: Gateway | None = None, media_cache: dict | None = None,
              decision_mode: str = 'baseline', agent_gateway: Gateway | None = None,
              agent_config: InvestigatorConfig | None = None, agent_live: bool = False,
              authorization_model: str | None = None, agent_finalize_on_pressure: bool = False,
              agent_request_first: bool = False, recorded=None) -> dict:
    """gateway=None: offline intent (deterministic only). With a gateway: staged intent (Jev decisions,
    generative fallback for escalations). `investigator` + `investigator_gateway`: routed cases are
    investigated (sequentially). `media_cache`: earlier validated screenshot extractions that
    `inspect_media` may show when this run has no reader of its own."""
    dataset, run_dir, output = Path(dataset).resolve(), Path(run_dir).resolve(), Path(output).resolve()
    requests_path = Path(requests_path).resolve()
    if output.is_relative_to(dataset) or output == requests_path or run_dir.is_relative_to(dataset):
        raise ContractError('output and run directory must not overwrite or live inside dataset inputs')
    if requests_path.is_relative_to(run_dir) or output == run_dir:
        raise ContractError('run directory conflicts with requests or output')
    reserved = {'case_plans.jsonl', 'case_files.jsonl', 'unresolved_cases.jsonl', 'media_extractions.json',
                'coverage.json', 'usage.jsonl', 'run_manifest.json', 'model_cache'}
    if output.is_relative_to(run_dir) and output.relative_to(run_dir).parts[0] in reserved:
        raise ContractError('output conflicts with a run diagnostic')
    if output.suffix.lower() != '.csv':
        raise ContractError('output destination must be a CSV prediction file')
    if output.exists():
        if not output.is_file():
            raise ContractError('output destination must be a regular prediction file')
        try:
            read_output(output)
        except (ContractError, UnicodeError, OSError) as exc:
            raise ContractError('refusing to replace an existing file that is not a prediction CSV') from exc
    if run_dir.exists() and (not run_dir.is_dir() or any(run_dir.iterdir())):
        raise ContractError('run directory is not empty; use a fresh path to preserve previous run artifacts')
    if decision_mode not in ('baseline', 'agent-first'):
        raise ContractError('decision_mode must be baseline or agent-first')
    if decision_mode == 'agent-first' and (not config.is_default or investigator is not None):
        raise ContractError('agent-first requires strict configuration and no baseline investigator')
    if decision_mode == 'agent-first':
        from .agent_first import allowed_agent_gateway
        if agent_config is None or not allowed_agent_gateway(agent_gateway, agent_live) or (
                gateway is not None and not allowed_agent_gateway(gateway, agent_live)):
            raise ContractError('agent-first requires replay or explicit bounded live opt-in before loading evidence')
        if authorization_model == agent_config.model:
            raise ContractError('authorization review must use a distinct model')
        if gateway is not None and gateway is not agent_gateway and gateway.mode == 'live':
            gateway.share_budget(agent_gateway)
    context = load_context(dataset)
    requests = load_requests(requests_path)
    if recorded is not None and (gateway is not None or decision_mode != 'baseline'):
        raise ContractError('recorded decisions replace the intent/media gateway in baseline mode only')
    corpus = build_corpus(context, config, gateway, recorded.media_for(context) if recorded else None)
    policy = Policy(context.rules, presume_retention=config.presume_retention)
    index = IdentityIndex(context)
    # Case IDs are only in-process keys here: Jev sees the request text alone and the
    # generative fallback sees opaque handles.
    intents = (recorded.intent_outcomes(requests) if recorded else
               decide_intents({r.case_id: r.request_text for r in requests}, gateway, use_generative)) \
        if decision_mode == 'baseline' else {}
    plans = [plan_case(r, corpus, policy, index, intents[r.case_id].intent) for r in requests] \
        if decision_mode == 'baseline' else []

    if investigator_gateway is not None and gateway is not None and investigator_gateway is not gateway \
            and investigator_gateway.mode == 'live':
        investigator_gateway.share_budget(gateway)      # one ceiling for every paid call (replay keeps its zero)
    texts = SourceText(context, {**(media_cache or {}), **getattr(corpus, 'media_extractions', {})})
    verifier = Verifier(corpus, index, context, texts)
    agent = Investigator(investigator_gateway, investigator, corpus, index, policy, texts, verifier.verify) \
        if investigator and investigator_gateway else None
    phase4, case_files = {}, []
    if decision_mode == 'agent-first':
        from .agent_first import AgentFirst
        if agent_config is None:
            raise ContractError('agent-first needs an explicit agent model/configuration')
        primary = AgentFirst(agent_gateway, agent_config, corpus, index, policy, texts, verifier,
                             allow_live=agent_live, authorization_model=authorization_model,
                             finalize_on_pressure=agent_finalize_on_pressure, request_first=agent_request_first)
        for request in requests:
            p, case, attempts = primary.run(request)
            plans.append(p)
            case_files.append({**case.record(), 'agent_proposals': attempts})
            phase4[p.request.case_id] = {'routing': ['agent-first'], 'verifier': attempts[-1]['findings'] if attempts else [],
                                       'withheld_by_verifier': p.fallback, 'investigation': _investigation(case)}
    for i, p in enumerate(plans if decision_mode == 'baseline' else []):
        findings = verifier.verify(p)
        routed = route_reasons(p, findings)
        case = None
        if agent and routed:
            p, case = agent.run(p, routed)
            case_files.append(case.record())
            findings = verifier.verify(p, case)
        blocking = [f for f in findings if f.blocking]
        if blocking:
            p = withhold(p, 'VERIFIER_REJECTED: ' + ', '.join(sorted({f.code for f in blocking})),
                         {f.resource_id for f in blocking})
            findings = verifier.verify(p, case)
        plans[i] = p
        phase4[p.request.case_id] = {'routing': routed, 'verifier': [f.record() for f in findings],
                                     'withheld_by_verifier': bool(blocking), 'investigation': _investigation(case)}

    run_dir.mkdir(parents=True, exist_ok=True)
    fallbacks = [p for p in plans if p.fallback]
    with open(run_dir / 'case_plans.jsonl', 'w', encoding='utf-8') as f:
        for p in plans:
            f.write(json.dumps(_plan_record(p, intents[p.request.case_id].stage if decision_mode == 'baseline'
                                           else 'primary-agent', phase4[p.request.case_id]),
                               ensure_ascii=False) + '\n')
    with open(run_dir / 'case_files.jsonl', 'w', encoding='utf-8') as f:
        for record in case_files:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    with open(run_dir / 'unresolved_cases.jsonl', 'w', encoding='utf-8') as f:
        for p in fallbacks:
            f.write(json.dumps({
                'case_id': p.request.case_id, 'reason': p.fallback_reason,
                'confirmed_violation': any(o.result == VIOLATED for o in p.obligations),
                'missing_obligations': [f'{o.system_id}/{o.resource_id}: {"; ".join(o.issues)}'
                                        for o in p.obligations if o.result == 'UNRESOLVED'],
                'note': 'best-effort INCOMPLETE prediction; not evidence that data remains',
            }, ensure_ascii=False) + '\n')
    (run_dir / 'media_extractions.json').write_text(json.dumps(
        getattr(corpus, 'media_extractions', {}), indent=2, ensure_ascii=False, default=str) + '\n', encoding='utf-8')
    (run_dir / 'coverage.json').write_text(json.dumps({
        'sources': {r.source_id: {'path': r.path, 'parser': r.parser, 'status': r.status,
                                  'observations': r.observations, 'scopes': r.scopes, 'issues': r.issues}
                    for r in corpus.reports},
        'status_counts': dict(Counter(r.status for r in corpus.reports)),
        'policy_issues': policy.issues}, indent=2) + '\n', encoding='utf-8')

    gateways = [g for g in (gateway, investigator_gateway, agent_gateway) if g is not None]
    gateways = [g for n, g in enumerate(gateways) if g not in gateways[:n]]
    usage = [u for g in gateways for u in g.usage]
    for g in gateways:          # the run's own validated responses: offline replay and analysis need no paid call
        for u in g.usage:
            source = g._cache_path(u.fingerprint) if u.status == 'ok' and u.fingerprint else None
            if source is not None and source.exists():
                target = run_dir / 'model_cache' / source.parent.name / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
    if gateways:
        with open(run_dir / 'usage.jsonl', 'w', encoding='utf-8') as f:
            for g in gateways:
                for r in g.usage:
                    f.write(json.dumps(asdict(r)) + '\n')
    fallback_ids = frozenset(p.request.case_id for p in fallbacks)
    write_output(output, [p.row for p in plans], requests, context.evidence, fallback_ids)

    code_dir = Path(__file__).resolve().parent
    summary = {
        'decision_mode': decision_mode,
        'artifact_status': ('Phase 4 engine: every plan independently verified; '
                            + (f'investigator {getattr(investigator_gateway, "mode", "?")} ({investigator.model})'
                               if agent else 'investigator not run (no live or replayed model)')
                            + '. Fallback rows are predictions, not evidence that data remains.'),
        'engine_version': ENGINE_VERSION,
        'code_sha256': {p.relative_to(code_dir).as_posix(): _sha256(p) for p in sorted(code_dir.rglob('*.py'))},
        'prompt_sha256': {p.name: _sha256(p) for p in sorted((code_dir.parent / 'prompts').glob('*'))
                          if p.is_file() and not p.name.startswith('.')},
        'engine_config': config.describe(),
        'experimental': not config.is_default,
        'requests_file': str(requests_path), 'requests_sha256': _sha256(requests_path),
        'dataset_files_sha256': {name: _sha256(dataset / name) for name in sorted(
            ['user_identities.csv', 'user_audit_log.csv', 'system_activity.csv', 'deletion_rules.csv',
             'evidence_files.csv'])},
        'evidence_files_sha256': {m.file_path: _sha256(dataset / m.file_path) for m in context.manifest},
        'output_file': str(output), 'output_sha256': _sha256(output),
        'cases': len(plans),
        'statuses': dict(Counter(p.status for p in plans)),
        'fallback_count': len(fallbacks),
        'fallback_ids': sorted(fallback_ids),
        'fallback_reasons': dict(Counter(p.fallback_reason.split(':')[0] for p in fallbacks)),
        'inference_modes': {
            'intent': 'recorded Jev decisions, re-reconciled with the deterministic check' if recorded else
                      'staged (deterministic check -> Jev decisions -> generative fallback)' if gateway and
                      use_generative else 'jev + deterministic check' if gateway else 'deterministic offline',
            'gateway_mode': getattr(gateway, 'mode', None),
            'providers': {role: {k: getattr(g.config, k) for k in ('provider', 'jev_model', 'intent_fallback_model',
                                                                    'vision_model', 'investigator_model')}
                          for role, g in (('intent', gateway), ('investigator', investigator_gateway)) if g},
            'media': ('PDF native text + recorded Jev document kinds; recorded screenshot readings' if recorded
                      else 'PDF native text + Jev document kind; screenshots via blind vision reader'
                      if gateway and config.use_media else 'PDF text only (no state); screenshots not read')},
        'recorded_decisions': recorded.describe() if recorded else None,
        'source_status': dict(Counter(r.status for r in corpus.reports)),
        'intent_stages': dict(Counter(o.stage for o in intents.values())),
        'model_calls': sum(1 for u in usage if u.mode != 'cache'),
        'model_cache_hits': sum(1 for u in usage if u.mode == 'cache'),
        'model_usage': {k: v for g in gateways for k, v in g.usage_summary().items()},
        'model_spent_paise_header': round(sum(u.cost_paise_header or 0 for u in usage), 6),   # provider-reported only
        'budget_charged_paise': round(sum({id(g._ledger): g.spent_paise for g in gateways}.values()), 6),
        'model_cost_paise': {'actual_reported': round(sum(u.cost_paise_header or 0 for u in usage), 6),
                             'estimated': round(sum(u.cost_estimate_paise or 0 for u in usage), 6),
                             'unpriced_live_calls': sum(1 for u in usage if u.mode == 'live' and u.status != 'error'
                                                        and u.cost_paise_header is None and u.cost_estimate_paise is None),
                             'run_ceiling': gateway.config.max_cost_paise if gateway else
                             (investigator_gateway.config.max_cost_paise if investigator_gateway else 0.0)},
        'phase4': _phase4_summary(phase4, investigator if agent else None,
                                  getattr(investigator_gateway, 'mode', None) if agent else None),
        'run_quality': 'best_effort_with_unresolved' if fallbacks else 'strict',
    }
    if decision_mode == 'agent-first':
        from .agent_first import proposal_metrics
        summary['artifact_status'] = 'Agent-first: verified agent-authored answers or explicitly disclosed fallbacks; no planner overwrite.'
        summary['agent_first'] = {**proposal_metrics(case_files), 'config': agent_config.describe(),
                                  'execution_mode': agent_gateway.mode, 'live_opt_in': agent_live,
                                  'authorization_model': authorization_model,
                                  'finalize_on_pressure': agent_finalize_on_pressure,
                                  'request_first': agent_request_first,
                                  'accepted_cases': len(plans) - len(fallbacks), 'fallback_cases': len(fallbacks),
                                  'network_calls': sum(u.mode == 'live' for u in usage),
                                  'rounds': sum(r['rounds'] for r in case_files),
                                  'tool_calls': sum(len(r['tool_calls']) for r in case_files),
                                  'feedback_turns': sum(r['feedback_turns'] for r in case_files)}
        summary['phase4'] = None  # baseline investigator metrics do not describe the primary agent
        summary['inference_modes']['intent'] = 'primary agent interpretation with deterministic authorization guards'
        summary['inference_modes']['gateway_mode'] = agent_gateway.mode
        summary['inference_modes']['providers']['primary_agent'] = {
            'provider': agent_gateway.config.provider, 'model': agent_config.model}
    (run_dir / 'run_manifest.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return summary


def _phase4_summary(phase4: dict, investigator, mode) -> dict:
    inv = [v['investigation'] for v in phase4.values() if v['investigation']]
    final = Counter(f['code'] for v in phase4.values() for f in v['verifier'])
    return {
        'investigator': investigator.describe() if investigator else None, 'investigator_mode': mode,
        'routed': sum(1 for v in phase4.values() if v['routing']),
        'investigated': len(inv),
        'accepted': sum(1 for x in inv if x['outcome'] == 'ACCEPTED'),
        'phase3_kept': sum(1 for x in inv if x['outcome'] == 'PHASE3_KEPT'),
        'stop_reasons': dict(Counter(x['stop_reason'].split(':')[0] for x in inv)),
        'rounds': sum(x['rounds'] for x in inv), 'tool_calls': sum(x['tool_calls'] for x in inv),
        'feedback_rounds': sum(x['feedback_rounds'] for x in inv),
        'facts_accepted': sum(x['facts_accepted'] for x in inv), 'facts_rejected': sum(x['facts_rejected'] for x in inv),
        'cases_with_verifier_rejections': sum(1 for x in inv if x['verifier_blocking']),
        'withheld_by_verifier': sum(1 for v in phase4.values() if v['withheld_by_verifier']),
        'final_verifier_findings': dict(sorted(final.items())),
    }


def load_disclosures(run_dir: Path, predictions: Path) -> frozenset[str]:
    """Fallback IDs disclosed by the run that produced `predictions` (hash-checked)."""
    manifest = json.loads((Path(run_dir) / 'run_manifest.json').read_text(encoding='utf-8'))
    if manifest['output_sha256'] != _sha256(predictions):
        raise ValueError('predictions file does not match the run manifest output_sha256')
    disclosed = set()
    with open(Path(run_dir) / 'unresolved_cases.jsonl', encoding='utf-8') as f:
        for line in f:
            disclosed.add(json.loads(line)['case_id'])
    if disclosed != set(manifest['fallback_ids']):
        raise ValueError('unresolved_cases.jsonl does not match run_manifest fallback_ids')
    return frozenset(disclosed)
