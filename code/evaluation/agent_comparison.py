"""Evaluation only: compare frozen outputs, with agent proposal rejection/grounding metrics.

No inference code imports labels or this module. Grounding rejection is a detected-hallucination
proxy over proposals, not a claim that the verifier detects every possible semantic hallucination.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghostaudit.agent_first import proposal_metrics
from ghostaudit.audit import load_disclosures
from ghostaudit.output import read_output
from evaluation.scoring import score


def measure(predictions, expected):
    metrics = score(predictions, expected)
    metrics.pop('diffs')
    cited = Counter((r.case_id, s) for r in expected for s in r.steps if s.evidence_id)
    predicted = Counter((r.case_id, s) for r in predictions for s in r.steps)
    total, correct = sum(cited.values()), sum((cited & predicted).values())
    metrics['citation_correctness'] = {'correct': correct, 'expected_cited': total,
                                       'rate': correct / total if total else None}
    return metrics


def compare(baseline, agent, expected, agent_run):
    load_disclosures(agent_run, agent)  # refuse measurements attributed to a different output
    records = [json.loads(line) for line in (Path(agent_run) / 'case_files.jsonl').read_text().splitlines()]
    gold = read_output(expected)
    return {'baseline': measure(read_output(baseline), gold),
            'agent_first': {**measure(read_output(agent), gold), **proposal_metrics(records)},
            'limits': 'Scripted agents and cache misses are not real-model accuracy measurements. '
                      'Hallucination rate is a grounding-rejection proxy; absent proposals yield null rates.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--agent', type=Path, required=True)
    p.add_argument('--expected', type=Path, required=True)
    p.add_argument('--agent-run', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    result = compare(args.baseline, args.agent, args.expected, args.agent_run)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
