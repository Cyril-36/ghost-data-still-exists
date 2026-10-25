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
from dataclasses import asdict, replace
from pathlib import Path

from .config import STRICT, EngineConfig
from .corpus import build_corpus
from .identity import IdentityIndex
from .intent_models import decide_intents
from .inputs import load_context, load_requests
from .output import write_output
from .investigator import Investigator, InvestigatorConfig, route_reasons
from .planner import VIOLATED, CasePlan, plan_case, withhold
from .policy import Policy
from .providers import Gateway
from .tools import SourceText
from .verifier import Verifier

ENGINE_VERSION = 'phase4-1'


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
              investigator_gateway: Gateway | None = None, media_cache: dict | None = None) -> dict:
    """gateway=None: offline intent (deterministic only). With a gateway: staged intent (Jev decisions,
    generative fallback for escalations). `investigator` + `investigator_gateway`: routed cases are
    investigated (sequentially). `media_cache`: earlier validated screenshot extractions that
    `inspect_media` may show when this run has no reader of its own."""
    dataset, run_dir = Path(dataset), Path(run_dir)
    context = load_context(dataset)
    requests = load_requests(requests_path)
    corpus = build_corpus(context, config, gateway)
    policy = Policy(context.rules, presume_retention=config.presume_retention)
    index = IdentityIndex(context)
    # Case IDs are only in-process keys here: Jev sees the request text alone and the
    # generative fallback sees opaque handles.
    intents = decide_intents({r.case_id: r.request_text for r in requests}, gateway, use_generative)
    plans = [plan_case(r, corpus, policy, index, intents[r.case_id].intent) for r in requests]

    if investigator_gateway is not None and gateway is not None and investigator_gateway is not gateway:
        # One advertised ceiling for the run: a separate investigator gateway gets only what the intent
        # and media calls (all finished by now) left of it.
        left = max(0.0, gateway.config.max_cost_paise - gateway.spent_paise)
        investigator_gateway.config = replace(investigator_gateway.config,
                                              max_cost_paise=min(investigator_gateway.config.max_cost_paise, left))
    texts = SourceText(context, {**(media_cache or {}), **getattr(corpus, 'media_extractions', {})})
    verifier = Verifier(corpus, index, context, texts)
    agent = Investigator(investigator_gateway, investigator, corpus, index, policy, texts, verifier.verify) \
        if investigator and investigator_gateway else None
    phase4, case_files = {}, []
    for i, p in enumerate(plans):
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
            f.write(json.dumps(_plan_record(p, intents[p.request.case_id].stage, phase4[p.request.case_id]),
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

    gateways = [g for g in (gateway, investigator_gateway) if g is not None]
    gateways = [g for n, g in enumerate(gateways) if g not in gateways[:n]]
    usage = [u for g in gateways for u in g.usage]
    if gateways:
        with open(run_dir / 'usage.jsonl', 'w', encoding='utf-8') as f:
            for g in gateways:
                for r in g.usage:
                    f.write(json.dumps(asdict(r)) + '\n')
    fallback_ids = frozenset(p.request.case_id for p in fallbacks)
    write_output(output, [p.row for p in plans], requests, context.evidence, fallback_ids)

    code_dir = Path(__file__).resolve().parent
    summary = {
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
        'output_file': str(output), 'output_sha256': _sha256(output),
        'cases': len(plans),
        'statuses': dict(Counter(p.status for p in plans)),
        'fallback_count': len(fallbacks),
        'fallback_ids': sorted(fallback_ids),
        'fallback_reasons': dict(Counter(p.fallback_reason.split(':')[0] for p in fallbacks)),
        'inference_modes': {
            'intent': 'staged (deterministic check -> Jev decisions -> generative fallback)' if gateway and
                      use_generative else 'jev + deterministic check' if gateway else 'deterministic offline',
            'gateway_mode': getattr(gateway, 'mode', None),
            'providers': {role: {k: getattr(g.config, k) for k in ('provider', 'jev_model', 'intent_fallback_model',
                                                                    'vision_model', 'investigator_model')}
                          for role, g in (('intent', gateway), ('investigator', investigator_gateway)) if g},
            'media': ('PDF native text + Jev document kind; screenshots via blind vision reader'
                      if gateway and config.use_media else 'PDF text only (no state); screenshots not read')},
        'source_status': dict(Counter(r.status for r in corpus.reports)),
        'intent_stages': dict(Counter(o.stage for o in intents.values())),
        'model_calls': sum(1 for u in usage if u.mode != 'cache'),
        'model_cache_hits': sum(1 for u in usage if u.mode == 'cache'),
        'model_usage': {k: v for g in gateways for k, v in g.usage_summary().items()},
        'model_spent_paise_header': round(sum(g.spent_paise for g in gateways), 2),
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
