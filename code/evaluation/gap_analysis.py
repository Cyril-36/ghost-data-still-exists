"""Attribute each missed labeled step to a cause. Evaluation-only (reads labels).

Causes, checked in order for every expected step the prediction lacks:
  INTENT_UNSUPPORTED      the case fell back because its language/intent is unsupported
  CITATION_ONLY           same step predicted, only evidence_id differs (fallback rows carry no citation)
  NOT_IN_STRUCTURED_DATA  resource never appears in any parsed structured source or activity row
                          (it can only be in a PDF/screenshot, or nowhere participant-visible)
  POST_CUTOFF_ONLY        it appears only in sources available after the case's audit_at
  OWNERSHIP_UNGROUNDED    it appears in eligible sources, but no eligible record ties it to the subject
  STATE_OR_POLICY         it is linked, but its state/treatment came out differently
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ghostaudit.corpus import build_corpus
from ghostaudit.identity import IdentityIndex, resolve_subject
from ghostaudit.inputs import load_context, load_requests
from ghostaudit.output import read_output
from ghostaudit.planner import discover

FIELDS = ('phase', 'system_id', 'resource_id', 'operation', 'state')


def analyze(dataset: Path, requests_path: Path, predictions: Path, expected: Path, run_dir: Path) -> dict:
    context = load_context(dataset)
    corpus = build_corpus(context)
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
