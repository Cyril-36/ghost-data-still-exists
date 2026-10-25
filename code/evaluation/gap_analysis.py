"""Attribute each missed labeled step to a cause. Evaluation-only (reads labels).

Causes, checked in order for every expected step the prediction lacks:
  INTENT_UNSUPPORTED      the case fell back because its language/intent is unsupported
  CITATION_ONLY           same step predicted, only evidence_id differs (fallback rows carry no citation)
  NOT_IN_STRUCTURED_DATA  resource never appears in any parsed structured source or activity row
                          (it can only be in a PDF/screenshot, or nowhere participant-visible)
  POST_CUTOFF_ONLY        it appears only in sources available after the case's audit_at
  OWNERSHIP_UNGROUNDED    it appears in eligible sources, but no eligible record ties it to the subject
  RETENTION_CONDITION_UNPROVEN  linked retained record, but no supplied retention period (strict policy)
  STATE_UNPROVEN          linked, but its current state could not be established
  STATE_OR_POLICY         linked with a decided state/treatment that differs from the label

The corpus is rebuilt exactly as the audited run built it (its engine configuration, and
media interpreted from the run's cached model responses only); the rebuilt evidence must
match the run's source status and media extractions, or the analysis refuses to run.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ghostaudit.config import EngineConfig
from ghostaudit.corpus import build_corpus
from ghostaudit.identity import IdentityIndex, resolve_subject
from ghostaudit.inputs import load_context, load_requests
from ghostaudit.output import read_output
from ghostaudit.planner import discover
from ghostaudit.providers import Gateway, ProviderConfig

FIELDS = ('phase', 'system_id', 'resource_id', 'operation', 'state')


def run_corpus(context, run_dir: Path):
    """The audited run's evidence universe: same config, media from cached responses only (no calls)."""
    manifest = json.loads((Path(run_dir) / 'run_manifest.json').read_text(encoding='utf-8'))
    used_gateway = manifest['inference_modes']['gateway_mode'] is not None
    gateway = Gateway(ProviderConfig(max_calls=0)) if used_gateway else None
    corpus = build_corpus(context, EngineConfig(**manifest['engine_config']), gateway)
    extractions = json.loads(json.dumps(getattr(corpus, 'media_extractions', {}), default=str))
    recorded = json.loads((Path(run_dir) / 'media_extractions.json').read_text(encoding='utf-8'))
    if dict(Counter(r.status for r in corpus.reports)) != manifest['source_status'] or extractions != recorded:
        raise ValueError('rebuilt evidence differs from the audited run (source status or media extractions)')
    return corpus


def analyze(dataset: Path, requests_path: Path, predictions: Path, expected: Path, run_dir: Path) -> dict:
    context = load_context(dataset)
    corpus = run_corpus(context, run_dir)
    index = IdentityIndex(context)
    requests = {r.case_id: r for r in load_requests(requests_path)}
    plans = {json.loads(line)['case_id']: json.loads(line)
             for line in open(Path(run_dir) / 'case_plans.jsonl', encoding='utf-8')}
    predicted = {r.case_id: r for r in read_output(predictions)}
    causes, rows = Counter(), []
    for gold in read_output(expected):
        req, plan, pred = requests[gold.case_id], plans[gold.case_id], predicted[gold.case_id]
        have = {tuple(getattr(s, f) for f in FIELDS): s.evidence_id for s in pred.steps}
        subject = resolve_subject(req.email, req.audit_at, index, corpus)
        linked, _ = discover(subject, corpus, index, req.audit_at) if subject.resolved else ({}, {})
        linked_ids = {rid for _, rid in linked}
        for step in gold.steps:
            key = tuple(getattr(step, f) for f in FIELDS)
            if key in have and have[key] == step.evidence_id:
                continue
            all_obs = corpus.by_resource.get(step.resource_id, [])
            eligible = [o for o in all_obs if o.available_at <= req.audit_at and o.observed_at <= req.audit_at]
            if plan['fallback'] and plan['fallback_reason'].startswith('INTENT_UNRESOLVED'):
                cause = 'INTENT_UNSUPPORTED'
            elif key in have:
                cause = 'CITATION_ONLY'
            elif not all_obs:
                cause = 'NOT_IN_STRUCTURED_DATA'
            elif not eligible:
                cause = 'POST_CUTOFF_ONLY'
            elif step.resource_id not in linked_ids:
                cause = 'OWNERSHIP_UNGROUNDED'
            else:
                issues = [i for o in plan['obligations'] if o['resource_id'] == step.resource_id
                          for i in o['issues']]
                if any(i.startswith('UNPROVEN_CONDITION') for i in issues):
                    cause = 'RETENTION_CONDITION_UNPROVEN'
                elif any(i.startswith(('UNPROVEN', 'UNSUPPORTED', 'SPEC_GAP')) for i in issues):
                    cause = 'STATE_UNPROVEN'
                else:
                    cause = 'STATE_OR_POLICY'
            causes[cause] += 1
            rows.append({'case_id': gold.case_id, 'cause': cause, 'step': dict(zip(FIELDS, key)),
                         'expected_evidence': step.evidence_id})
    return {'missed_steps': len(rows), 'causes': dict(causes.most_common()), 'details': rows}


def render(result: dict) -> str:
    lines = ['# Missed-step attribution', '', f'Missed expected steps: {result["missed_steps"]}', '',
             '| Cause | Steps |', '|---|---|']
    lines += [f'| {k} | {v} |' for k, v in result['causes'].items()]
    lines += ['', '| Case | Cause | Missed step | Expected evidence |', '|---|---|---|---|']
    lines += [f'| {r["case_id"]} | {r["cause"]} | {r["step"]["operation"]} {r["step"]["system_id"]}/'
              f'{r["step"]["resource_id"]} | {r["expected_evidence"] or "-"} |' for r in result['details']]
    return '\n'.join(lines) + '\n'


def ownership_details(dataset: Path, requests_path: Path, gaps: dict, run_dir: Path) -> list[dict]:
    """For each missed step whose resource is unlinked, list every source mentioning that
    resource and classify the relationship it offers (evaluation-only diagnosis)."""
    context = load_context(dataset)
    corpus = run_corpus(context, run_dir)
    index = IdentityIndex(context)
    requests = {r.case_id: r for r in load_requests(requests_path)}
    out, seen = [], set()
    for d in gaps['details']:
        if d['cause'] not in ('OWNERSHIP_UNGROUNDED', 'POST_CUTOFF_ONLY'):
            continue
        rid, req = d['step']['resource_id'], requests[d['case_id']]
        if (d['case_id'], rid) in seen:
            continue
        seen.add((d['case_id'], rid))
        subject = resolve_subject(req.email, req.audit_at, index, corpus)
        all_tokens = {(i.kind, i.key) for uid in subject.user_ids for i in index.history[uid]}
        rows = []
        for o in corpus.by_resource.get(rid, []):
            eligible = o.available_at <= req.audit_at and o.observed_at <= req.audit_at
            if o.tokens & subject.tokens:
                cls = 'CATALOG_LINK' if o.kind == 'LINK' else 'DIRECT_REFERENCE'
            elif o.tokens & all_tokens:
                cls = 'IDENTIFIER_NOT_KNOWN_AT_CUTOFF'
            elif o.refs:
                cls = 'RELATIONSHIP_EDGE_ONLY'
            elif o.kind == 'CLAIM':
                cls = 'OPERATION_CLAIM_NAMES_NO_PERSON'
            elif o.tokens:
                cls = 'NAMES_A_DIFFERENT_IDENTIFIER'
            else:
                cls = 'RECORD_WITHOUT_ANY_IDENTIFIER'
            rows.append({'source': o.source_id, 'eligible': eligible, 'kind': o.kind, 'proof': o.proof,
                         'class': cls})
        eligible_classes = sorted({r['class'] for r in rows if r['eligible']})
        late_classes = sorted({r['class'] for r in rows if not r['eligible']})
        if any(c in ('CATALOG_LINK', 'DIRECT_REFERENCE') for c in eligible_classes):
            verdict = 'LINK_AVAILABLE_BUT_MISSED (engine defect)'
        elif any(c in ('CATALOG_LINK', 'DIRECT_REFERENCE') for c in late_classes):
            verdict = 'LINK_ONLY_IN_POST_CUTOFF_SOURCE'
        elif not rows:
            verdict = 'RESOURCE_NOT_IN_ANY_PARSED_SOURCE'
        else:
            verdict = 'NO_GROUNDED_LINK_IN_ANY_SOURCE'
        out.append({'case_id': d['case_id'], 'resource_id': rid, 'verdict': verdict,
                    'eligible_relationships': eligible_classes, 'post_cutoff_relationships': late_classes,
                    'sources': rows})
    return out
