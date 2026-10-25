"""Intent benchmark: deterministic vs Jev-only vs generative-only vs staged, on the same cases.

Datasets: the SYNTHETIC multilingual set (`intent_cases.py`) and the 32 supplied sample
requests (all deletion requests according to their labels). Labels are read here only.

An outcome is the decision with its scope ('DELETE/ACCOUNT', 'DELETE/ITEM/<category>',
'NON_DELETION', 'UNRESOLVED'); synthetic cases are correct only with the right scope and
target. Sample labels do not state a scope, so for samples any DELETE/* counts as DELETE.
Every method gets the same target-grounding step for ITEM outcomes.
"""
from __future__ import annotations

import csv
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

from ghostaudit.intent import DELETE, ITEM, NON_DELETION, UNRESOLVED, classify
from ghostaudit.intent_models import Signals, decide_intents, generative_signals, ground_target, jev_signals, reconcile
from ghostaudit.providers import Gateway, ProviderConfig, ProviderError

from .intent_cases import cases as synthetic_cases


def sample_cases(dataset: Path):
    labels = {r['case_id']: r['deletion_status'] for r in csv.DictReader(open(dataset / 'sample_output.csv',
                                                                               encoding='utf-8'))}
    out = []
    for r in csv.DictReader(open(dataset / 'sample_deletion_requests.csv', encoding='utf-8')):
        expected = NON_DELETION if labels[r['case_id']] == 'NOT_APPLICABLE' else 'DELETE/*'
        out.append((f'sample:{r["case_id"]}', 'sample', 'sample_request', r['request_text'], expected, None))
    return out


def outcome(intent) -> str:
    if intent is None:
        return UNRESOLVED
    if intent.decision != DELETE:
        return intent.decision
    return '/'.join(x for x in (DELETE, intent.scope, intent.target) if x)


def _correct(expected, got):
    return got in expected.split('|') or (expected == 'DELETE/*' and got.startswith('DELETE/'))


def _unsafe(expected, got):
    """Authorizes more than was asked: deletion when none was, the account instead of one
    item, or a different item; or refuses a clear deletion as NON_DELETION."""
    deleting, asked = got.startswith(DELETE), expected.startswith(DELETE)
    if deleting and not asked:
        return True
    if deleting and 'DELETE/ITEM' in expected and not _correct(expected, got):
        return True
    return got == NON_DELETION and asked


def summarize(name, rows):
    """rows: [(key, lang, scenario, expected, decision)]"""
    n = len(rows)
    correct = sum(1 for r in rows if _correct(r[3], r[4]))
    unresolved = sum(1 for r in rows if r[4] == UNRESOLVED)
    unsafe = [r[0] for r in rows if _unsafe(r[3], r[4])]
    by_lang = defaultdict(lambda: [0, 0])
    by_scn = defaultdict(lambda: [0, 0])
    for _key, lang, scn, exp, got in rows:
        by_lang[lang][0] += _correct(exp, got)
        by_lang[lang][1] += 1
        by_scn[scn][0] += _correct(exp, got)
        by_scn[scn][1] += 1
    return {'method': name, 'cases': n, 'correct': correct, 'unresolved': unresolved, 'unsafe': len(unsafe),
            'unsafe_cases': unsafe, 'by_language': {k: f'{a}/{b}' for k, (a, b) in sorted(by_lang.items())},
            'by_scenario': {k: f'{a}/{b}' for k, (a, b) in sorted(by_scn.items())}}


def run(dataset: Path, out_dir: Path, budget_paise: float = 1500.0) -> dict:
    gateway = Gateway(ProviderConfig(max_cost_paise=budget_paise))
    sets = {'synthetic': synthetic_cases(), 'samples': sample_cases(dataset)}
    results, details = {}, {}
    for set_name, items in sets.items():
        texts = {k: t for k, _, _, t, _, _ in items}
        meta = {k: (lang, scn, exp, label) for k, lang, scn, _, exp, label in items}
        # 1. deterministic
        det = {k: classify(t) for k, t in texts.items()}
        # 2. Jev only (one call per request, isolated state)
        jev_sig, jev_lat = {}, {}
        for k, t in texts.items():
            start = time.time()
            try:
                jev_sig[k] = jev_signals(gateway, t)
            except ProviderError as exc:
                jev_sig[k] = str(exc)
            jev_lat[k] = time.time() - start
        jev = {k: reconcile(s) if isinstance(s, Signals) else None for k, s in jev_sig.items()}
        jev = {k: ground_target(gateway, texts[k], i) if i and i.scope == ITEM else i for k, i in jev.items()}
        # 3. generative only (batched, opaque handles)
        handles = {f'q{i + 1}': k for i, k in enumerate(texts)}
        gen_sig = generative_signals(gateway, [(h, texts[k]) for h, k in handles.items()])
        gen = {handles[h]: (reconcile(s) if isinstance(s, Signals) else None) for h, s in gen_sig.items()}
        gen = {k: ground_target(gateway, texts[k], i) if i and i.scope == ITEM else i for k, i in gen.items()}
        # 4. staged (deterministic cross-check -> Jev -> generative only for escalations)
        staged = decide_intents(texts, gateway, use_generative=True)

        def rows(decisions, meta=meta, texts=texts):
            return [(k, *meta[k][:3], outcome(decisions.get(k))) for k in texts]
        results[set_name] = [summarize('deterministic', rows(det)), summarize('jev_only', rows(jev)),
                             summarize('generative_only', rows(gen)),
                             summarize('staged', rows({k: o.intent for k, o in staged.items()}))]
        stage_counts = Counter(o.stage for o in staged.values())
        results[set_name][-1]['stage_counts'] = dict(stage_counts)

        # Jev diagnostics: label accuracy, choice/noul disagreement, probability vs correctness
        label_ok = disagree = 0
        prob_bins = defaultdict(lambda: [0, 0])
        for k, s in jev_sig.items():
            if not isinstance(s, Signals):
                continue
            exp_label = meta[k][3]
            if exp_label:
                label_ok += s.label == exp_label
            deletion = s.label in ('DELETE_ACCOUNT', 'VERIFY_PRIOR_DELETION', 'SCOPED_DELETION')
            if (deletion and (s.flag('preserve_account') == 'yes' or s.flag('marketing_only') == 'yes')) or \
                    (s.label == 'NON_DELETION' and s.flag('conflicting_instructions') == 'yes'):
                disagree += 1
            b = 'p>=0.95' if s.probability >= 0.95 else 'p 0.80-0.95' if s.probability >= 0.8 else \
                'p 0.60-0.80' if s.probability >= 0.6 else 'p<0.60'
            prob_bins[b][0] += _correct(meta[k][2], outcome(jev[k]))
            prob_bins[b][1] += 1
        results[set_name].append({'jev_diagnostics': {
            'label_accuracy': f'{label_ok}/{sum(1 for k in jev_sig if meta[k][3])}' if any(
                m[3] for m in meta.values()) else 'n/a',
            'choice_noul_disagreements': disagree,
            'decision_accuracy_by_choice_probability': {k: f'{a}/{b}' for k, (a, b) in sorted(prob_bins.items())},
            'mean_latency_s': round(sum(jev_lat.values()) / max(1, len(jev_lat)), 2)}})
        details[set_name] = {k: {
            'expected': meta[k][2], 'deterministic': outcome(det[k]),
            'jev': outcome(jev[k]) if jev[k] else 'ERROR', 'jev_reason': jev[k].reason if jev[k] else jev_sig[k],
            'jev_signals': dict(jev_sig[k].flags) | {'label': jev_sig[k].label, 'p': jev_sig[k].probability,
                                                     'account_p': jev_sig[k].account_probability}
            if isinstance(jev_sig[k], Signals) else None,
            'generative': outcome(gen[k]) if gen.get(k) else 'ERROR',
            'generative_spans': list(gen[k].spans) if gen.get(k) else None,
            'staged': outcome(staged[k].intent), 'staged_stage': staged[k].stage} for k in texts}

    usage = gateway.usage_summary()
    report = {'results': results, 'usage': usage, 'spent_paise_header_total': round(gateway.spent_paise, 2),
              'returned_models': sorted({f'{r.requested_model} -> {r.returned_model}' for r in gateway.usage
                                         if r.returned_model})}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'intent_benchmark.json').write_text(json.dumps(report | {'details': details}, indent=2,
                                                              ensure_ascii=False) + '\n', encoding='utf-8')
    gateway.write_usage(out_dir / 'usage.jsonl')
    return report

