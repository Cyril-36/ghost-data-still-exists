"""Label-based metrics. Evaluation-only: inference code must never import this."""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from ghostaudit.contracts import OutputRow, Step

SCORED_FIELDS = ('phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id')


def _rate(numerator: int, denominator: int) -> dict:
    return {'numerator': numerator, 'denominator': denominator,
            'rate': numerator / denominator if denominator else None}


def _key(step: Step) -> tuple[str, ...]:
    return tuple(getattr(step, f) for f in SCORED_FIELDS)


def score(predictions: Sequence[OutputRow], expected: Sequence[OutputRow]) -> dict:
    """Compare predictions with labels over the same case set.

    - status accuracy: exact deletion_status match per case
    - step precision/recall/F1: multiset match on the six fields (step_no excluded)
    - field accuracy (positional): step i vs step i; unmatched steps count as six errors
    - sequence accuracy: identical ordered step arrays
    - exact-row accuracy: status and sequence both correct
    """
    gold = {r.case_id: r for r in expected}
    pred = {r.case_id: r for r in predictions}
    if len(gold) != len(expected) or len(pred) != len(predictions):
        raise ValueError('duplicate case IDs in predictions or labels')
    if set(gold) != set(pred):
        raise ValueError(f'case sets differ: missing={sorted(set(gold) - set(pred))[:5]} '
                         f'extra={sorted(set(pred) - set(gold))[:5]}')

    status_ok = sequence_ok = row_ok = 0
    true_pos = n_pred = n_gold = 0
    field_ok = field_total = 0
    field_errors = Counter()
    confusion = Counter()
    diffs = []
    for case_id, g in gold.items():
        p = pred[case_id]
        s_match = p.deletion_status == g.deletion_status
        q_match = p.steps == g.steps
        status_ok += s_match
        sequence_ok += q_match
        row_ok += s_match and q_match
        confusion[(g.deletion_status, p.deletion_status)] += 1

        pc, gc = Counter(map(_key, p.steps)), Counter(map(_key, g.steps))
        true_pos += sum((pc & gc).values())
        n_pred += len(p.steps)
        n_gold += len(g.steps)

        first_divergence = None
        for i in range(max(len(p.steps), len(g.steps))):
            field_total += len(SCORED_FIELDS)
            if i >= len(p.steps) or i >= len(g.steps):
                field_errors['unmatched_step'] += len(SCORED_FIELDS)
                first_divergence = i + 1 if first_divergence is None else first_divergence
                continue
            for f in SCORED_FIELDS:
                if getattr(p.steps[i], f) == getattr(g.steps[i], f):
                    field_ok += 1
                else:
                    field_errors[f] += 1
                    first_divergence = i + 1 if first_divergence is None else first_divergence

        if not (s_match and q_match):
            diffs.append({
                'case_id': case_id,
                'expected_status': g.deletion_status, 'actual_status': p.deletion_status,
                'missing_steps': [dict(zip(SCORED_FIELDS, k)) for k in (gc - pc).elements()],
                'extra_steps': [dict(zip(SCORED_FIELDS, k)) for k in (pc - gc).elements()],
                'first_divergent_step_no': first_divergence,
            })

    cases = len(gold)
    return {
        'cases': cases,
        'status_accuracy': _rate(status_ok, cases),
        'step_precision': _rate(true_pos, n_pred),
        'step_recall': _rate(true_pos, n_gold),
        'step_f1': _rate(2 * true_pos, n_pred + n_gold),
        'field_accuracy_positional': _rate(field_ok, field_total),
        'field_errors': dict(sorted(field_errors.items())),
        'sequence_accuracy': _rate(sequence_ok, cases),
        'exact_row_accuracy': _rate(row_ok, cases),
        'confusion': [{'expected': e, 'predicted': a, 'count': n}
                      for (e, a), n in sorted(confusion.items())],
        'diffs': sorted(diffs, key=lambda d: d['case_id']),
    }


def render_markdown(result: dict, title: str) -> str:
    def fmt(m):
        rate = 'n/a' if m['rate'] is None else f'{m["rate"]:.4f}'
        return f'{m["numerator"]}/{m["denominator"]} = {rate}'

    lines = [f'# {title}', '',
             'Measured by comparing predictions with labels for the same case set. '
             'This says nothing about cases without labels.', '',
             '| Metric | Result |', '|---|---|']
    for name in ('status_accuracy', 'step_precision', 'step_recall', 'step_f1',
                 'field_accuracy_positional', 'sequence_accuracy', 'exact_row_accuracy'):
        lines.append(f'| {name} | {fmt(result[name])} |')
    lines += ['', 'Step F1 is shown as 2·TP / (predicted + expected steps).',
              'Positional field accuracy counts each field of unmatched steps as an error.', '',
              '## Status confusion', '', '| Expected | Predicted | Count |', '|---|---|---|']
    lines += [f'| {c["expected"]} | {c["predicted"]} | {c["count"]} |' for c in result['confusion']]
    lines += ['', '## Field errors', '']
    lines += [f'- {k}: {v}' for k, v in result['field_errors'].items()] or ['- none']
    lines += ['', f'## Mismatched cases ({len(result["diffs"])})', '']
    for d in result['diffs']:
        lines.append(f'- {d["case_id"]}: status {d["expected_status"]} -> {d["actual_status"]}; '
                     f'{len(d["missing_steps"])} missing, {len(d["extra_steps"])} extra; '
                     f'first divergent step {d["first_divergent_step_no"]}')
    return '\n'.join(lines) + '\n'
