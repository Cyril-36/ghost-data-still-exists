"""Case-by-case error analysis of a labelled run. Evaluation-only: inference never imports this.

Every labelled difference is classified per case:
  STATUS_MISMATCH     deletion_status differs
  WRONG_CITATION      same phase/system/resource/operation/state, different evidence_id
  WRONG_OPERATION     same phase/system/resource, different operation or state
  MISSING_STEP        an expected step with no counterpart (root cause joined from `gaps.json`)
  UNEXPECTED_STEP     a predicted step with no counterpart
  ORDER_ONLY          the same steps in a different order
Missing steps are then grouped by the general root cause the gap attribution assigned, not by case.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT_CAUSES = {
    'OWNERSHIP_UNGROUNDED': 'Resource appears in eligible evidence, but nothing ties it to the subject',
    'INTENT_UNSUPPORTED': 'Request intent unresolved, so the case fell back (model-dependent)',
    'RETENTION_CONDITION_UNPROVEN': 'Retained record lacks a supplied retention condition (strict default)',
    'NOT_IN_STRUCTURED_DATA': 'Resource appears only in media or other unparsed content',
    'POST_CUTOFF_ONLY': 'Resource is known only from sources available after audit_at',
    'CITATION_ONLY': 'Same step predicted, but a fallback row carries no citation',
}


def _core(step: dict) -> tuple:
    return tuple(step[k] for k in ('phase', 'system_id', 'resource_id', 'operation', 'state'))


def diff_case(predicted: list[dict], expected: list[dict]) -> dict:
    """Classify the step differences of one case. Steps are dicts carrying the six scored fields."""
    full = lambda s: _core(s) + (s['evidence_id'],)              # noqa: E731
    pred, gold = Counter(map(full, predicted)), Counter(map(full, expected))
    missing, extra = list((gold - pred).elements()), list((pred - gold).elements())
    out = defaultdict(list)
    for kind, match in (('WRONG_CITATION', lambda s: s[:5]), ('WRONG_OPERATION', lambda s: s[:3])):
        for g in list(missing):
            p = next((p for p in extra if match(p) == match(g)), None)
            if p is not None:
                missing.remove(g)
                extra.remove(p)
                out[kind].append({'expected': g, 'predicted': p})
    out['MISSING_STEP'] = missing
    out['UNEXPECTED_STEP'] = extra
    order_only = not missing and not extra and not out['WRONG_CITATION'] and not out['WRONG_OPERATION'] \
        and [full(s) for s in predicted] != [full(s) for s in expected]
    return {k: v for k, v in out.items() if v} | ({'ORDER_ONLY': True} if order_only else {})


def analyze_errors(predictions, expected, gaps: dict, plans: dict[str, dict]) -> dict:
    """predictions/expected: OutputRow sequences; gaps: gap_analysis output; plans: case_id -> plan line."""
    cause_of = defaultdict(list)
    for m in gaps.get('details', []):
        cause_of[(m['case_id'], _core(m['step']) + (m.get('expected_evidence') or '',))].append(m['cause'])
    pred = {r.case_id: r for r in predictions}
    cases, by_kind, by_cause, cause_cases = [], Counter(), Counter(), defaultdict(set)
    for gold in sorted(expected, key=lambda r: r.case_id):
        row = pred[gold.case_id]
        steps = lambda r: [{f: getattr(s, f) for f in ('phase', 'system_id', 'resource_id', 'operation',  # noqa: E731
                                                       'state', 'evidence_id')} for s in r.steps]
        d = diff_case(steps(row), steps(gold))
        if row.deletion_status != gold.deletion_status:
            d['STATUS_MISMATCH'] = {'expected': gold.deletion_status, 'predicted': row.deletion_status}
        causes = []
        for g in d.get('MISSING_STEP', []):
            found = cause_of.get((gold.case_id, g), [])
            cause = found.pop(0) if found else 'UNATTRIBUTED'
            causes.append(cause)
            by_cause[cause] += 1
            cause_cases[cause].add(gold.case_id)
        for kind, v in d.items():
            by_kind[kind] += len(v) if isinstance(v, list) else 1
        plan = plans.get(gold.case_id, {})
        cases.append({'case_id': gold.case_id, 'expected_status': gold.deletion_status,
                      'predicted_status': row.deletion_status, 'exact': not d,
                      'fallback_reason': plan.get('fallback_reason') if plan.get('fallback') else None,
                      'expected_steps': len(gold.steps), 'predicted_steps': len(row.steps),
                      'missing_causes': dict(Counter(causes)), 'differences': d})
    return {'cases': cases, 'by_kind': dict(by_kind), 'by_cause': dict(by_cause),
            'cause_cases': {k: sorted(v) for k, v in cause_cases.items()},
            'exact_rows': sum(c['exact'] for c in cases), 'total': len(cases)}


def load_plans(run_dir: Path) -> dict[str, dict]:
    path = Path(run_dir) / 'case_plans.jsonl'
    return {p['case_id']: p for p in map(json.loads, path.read_text(encoding='utf-8').splitlines()) if p}


def render_errors(result: dict, title: str) -> str:
    lines = [f'# {title}', '', f'Exact rows: {result["exact_rows"]}/{result["total"]}.', '',
             '| Difference | Count |', '|---|---|']
    lines += [f'| {k} | {v} |' for k, v in sorted(result['by_kind'].items())]
    lines += ['', '## Missing steps by root cause', '', '| Root cause | Steps | Cases |', '|---|---|---|']
    for cause, n in sorted(result['by_cause'].items(), key=lambda kv: -kv[1]):
        lines.append(f'| {cause}: {ROOT_CAUSES.get(cause, "")} | {n} | {", ".join(result["cause_cases"][cause])} |')
    lines += ['', '## Cases', '', '| Case | Status (expected → predicted) | Steps (exp/pred) | Fallback | '
              'Missing (by cause) | Other differences |', '|---|---|---|---|---|---|']
    for c in result['cases']:
        other = ', '.join(f'{k} {len(v) if isinstance(v, list) else ""}'.strip()
                          for k, v in c['differences'].items() if k not in ('MISSING_STEP', 'STATUS_MISMATCH'))
        status = c['expected_status'] + ('' if c['expected_status'] == c['predicted_status']
                                         else f' → **{c["predicted_status"]}**')
        missing = ', '.join(f'{k} {v}' for k, v in c['missing_causes'].items()) or ('exact' if c['exact'] else '-')
        fallback = (c['fallback_reason'] or '').split(':')[0] or '-'
        lines.append(f'| {c["case_id"]} | {status} | {c["expected_steps"]}/{c["predicted_steps"]} | {fallback} | '
                     f'{missing} | {other or "-"} |')
    return '\n'.join(lines) + '\n'
