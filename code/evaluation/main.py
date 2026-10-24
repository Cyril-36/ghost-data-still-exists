"""Evaluation entry point. Never imported by inference.

  fixtures  write synthetic fixture inputs and their expected answers
  score     compare a predictions CSV with a labels CSV for the same requests
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # code/ for ghostaudit

from ghostaudit.contracts import ContractError  # noqa: E402
from ghostaudit.inputs import load_context, load_requests  # noqa: E402
from ghostaudit.output import read_output, validate_output  # noqa: E402

from evaluation.fixtures import FIXTURES, write_fixture  # noqa: E402
from evaluation.scoring import render_markdown, score  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    fx = sub.add_parser('fixtures')
    fx.add_argument('--out', type=Path, required=True)
    fx.add_argument('--only', choices=sorted(FIXTURES), action='append')
    sc = sub.add_parser('score')
    sc.add_argument('--requests', type=Path, required=True)
    sc.add_argument('--predictions', type=Path, required=True)
    sc.add_argument('--expected', type=Path, required=True)
    sc.add_argument('--dataset', type=Path, help='also check citations against this dataset')
    sc.add_argument('--out-dir', type=Path, required=True, help='writes metrics.json and metrics.md')
    args = parser.parse_args(argv)

    try:
        if args.command == 'fixtures':
            for name in args.only or sorted(FIXTURES):
                base = write_fixture(FIXTURES[name], args.out)
                print(f'{name}: inputs {base / "input"}  expected {base / "expected_output.csv"}')
            return 0
        requests = load_requests(args.requests)
        evidence = load_context(args.dataset).evidence if args.dataset else None
        predictions, expected = read_output(args.predictions), read_output(args.expected)
        validate_output(predictions, requests, evidence)
        validate_output(expected, requests, evidence)
        result = score(predictions, expected)
    except (ContractError, ValueError, FileNotFoundError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / 'metrics.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    (args.out_dir / 'metrics.md').write_text(
        render_markdown(result, f'Evaluation: {args.predictions.name} vs {args.expected.name}'), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'diffs'}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
