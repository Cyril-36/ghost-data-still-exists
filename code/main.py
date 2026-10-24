"""Ghost Data Still Exists - command-line entry point.

Phase 1 commands:
  inventory  strictly load every input and hash every manifest evidence file
  validate   check a predictions CSV against the output contract

The `audit` command (actual inference) is not implemented yet.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from ghostaudit.contracts import ContractError
from ghostaudit.inputs import load_context, load_requests
from ghostaudit.output import read_output, validate_output

DEFAULT_DATASET = Path(__file__).resolve().parent.parent / 'dataset'


def inventory(dataset: Path) -> dict:
    context = load_context(dataset)
    requests = {name: len(load_requests(dataset / name))
                for name in ('deletion_requests.csv', 'sample_deletion_requests.csv')
                if (dataset / name).exists()}
    files = []
    for entry in context.manifest:
        data = (context.root / entry.file_path).read_bytes()
        files.append({'file_id': entry.file_id, 'file_path': entry.file_path, 'bytes': len(data),
                      'sha256': hashlib.sha256(data).hexdigest(),
                      'captured_at': entry.captured_at.isoformat()})
    return {
        'requests': requests,
        'context_rows': {'user_identities': len(context.identities),
                         'user_audit_log': len(context.audit_log),
                         'system_activity': len(context.activities),
                         'deletion_rules': len(context.rules),
                         'evidence_files': len(context.manifest)},
        'evidence_by_extension': dict(sorted(Counter(Path(f['file_path']).suffix for f in files).items())),
        'evidence_bytes': sum(f['bytes'] for f in files),
        'citable_ids': dict(sorted(Counter(r.kind for r in context.evidence.values()).items())),
        'files': files,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    inv = sub.add_parser('inventory', help='load and validate all inputs')
    inv.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    inv.add_argument('--out', type=Path, help='write the full inventory JSON here')
    val = sub.add_parser('validate', help='validate a predictions CSV (structure and citations only)')
    val.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    val.add_argument('--requests', type=Path, required=True)
    val.add_argument('--predictions', type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == 'inventory':
            result = inventory(args.dataset)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
            print(json.dumps({k: v for k, v in result.items() if k != 'files'}, indent=2))
        else:
            context = load_context(args.dataset)
            requests = load_requests(args.requests)
            rows = read_output(args.predictions)
            validate_output(rows, requests, context.evidence)
            statuses = dict(sorted(Counter(r.deletion_status for r in rows).items()))
            print(f'VALID: {len(rows)} rows, statuses {statuses}. Checked: exact case set, order, '
                  f'step contract, citation existence and audit_at eligibility. '
                  f'NOT checked: whether citations semantically support each step.')
    except (ContractError, FileNotFoundError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
