"""Offline audit run: requests in, validated output.csv plus run-linked diagnostics out.

Run directory contents (written before the CSV is published):
  case_plans.jsonl        every case: intent, identity, obligations, issues, steps
  unresolved_cases.jsonl  every best-effort fallback row and why (plan section 17)
  coverage.json           per-source parsing outcome, including unsupported media
  run_manifest.json       input hashes, counts, fallback IDs, and the output CSV sha256

A fallback row is only publishable when it is disclosed in this run directory;
`validate --run-dir` re-checks that binding.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from .config import STRICT, EngineConfig
from .corpus import build_corpus
from .identity import IdentityIndex
from .intent_models import decide_intents
from .inputs import load_context, load_requests
from .output import write_output
from .planner import VIOLATED, CasePlan, plan_case
from .policy import Policy
from .providers import Gateway

ENGINE_VERSION = 'phase3-hardened-1'


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _plan_record(p: CasePlan, stage: str = '') -> dict:
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
    }


def run_audit(dataset: Path, requests_path: Path, output: Path, run_dir: Path,
              config: EngineConfig = STRICT, gateway: Gateway | None = None,
              use_generative: bool = True) -> dict:
    """gateway=None: offline (deterministic intent only, no model calls).
    With a gateway: staged intent (Jev decisions, generative fallback for escalations)."""
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

    run_dir.mkdir(parents=True, exist_ok=True)
    fallbacks = [p for p in plans if p.fallback]
    with open(run_dir / 'case_plans.jsonl', 'w', encoding='utf-8') as f:
        for p in plans:
            f.write(json.dumps(_plan_record(p, intents[p.request.case_id].stage), ensure_ascii=False) + '\n')
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

    if gateway is not None:
        gateway.write_usage(run_dir / 'usage.jsonl')
    fallback_ids = frozenset(p.request.case_id for p in fallbacks)
    write_output(output, [p.row for p in plans], requests, context.evidence, fallback_ids)

    code_dir = Path(__file__).resolve().parent
    summary = {
        'artifact_status': ('PROVISIONAL: produced by the Phase 3 engine; not the final verified submission '
                            '(no investigator/independent verifier yet; strict evidence rules leave disclosed '
                            'fallbacks). Fallback rows are predictions, not evidence that data remains.'),
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
            'media': ('PDF native text + Jev document kind; screenshots via blind vision reader'
                      if gateway and config.use_media else 'PDF text only (no state); screenshots not read')},
        'source_status': dict(Counter(r.status for r in corpus.reports)),
        'intent_stages': dict(Counter(o.stage for o in intents.values())),
        'model_calls': sum(1 for u in getattr(gateway, 'usage', []) if u.mode != 'cache'),
        'model_cache_hits': sum(1 for u in getattr(gateway, 'usage', []) if u.mode == 'cache'),
        'model_usage': gateway.usage_summary() if gateway else {},
        'model_spent_paise_header': round(gateway.spent_paise, 2) if gateway else 0.0,
        'run_quality': 'best_effort_with_unresolved' if fallbacks else 'strict',
    }
    (run_dir / 'run_manifest.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return summary


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
