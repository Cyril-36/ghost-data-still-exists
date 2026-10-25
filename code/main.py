"""Ghost Data Still Exists - command-line entry point.

Commands:
  inventory  strictly load every input, hash and parse every manifest evidence file
  audit      deterministic offline audit -> predictions CSV + run diagnostics
  validate   check a predictions CSV against the output contract
  probe-tools  one bounded live call: does a model return valid native tool calls?

`audit` is offline by default (no model calls). `--intent staged` uses Jev typed
decisions plus a generative fallback through the configured gateway, within
`--budget-paise`. Every plan is checked by the independent verifier.
`--investigator live|replay --investigator-model M` sends routed cases to the
Phase 4 investigator: live needs a passed `probe-tools` record for M; replay
serves only cached validated responses and never makes a network call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from ghostaudit.audit import load_disclosures, run_audit
from ghostaudit.contracts import ContractError
from ghostaudit.corpus import build_corpus
from ghostaudit.inputs import load_context, load_requests
from ghostaudit.output import read_output, validate_output

DEFAULT_DATASET = Path(__file__).resolve().parent.parent / 'dataset'
PROBES = Path('.cache/ghostaudit/tool_probes.json')     # gitignored; written only by probe-tools


def inventory(dataset: Path) -> dict:
    context = load_context(dataset)
    corpus = build_corpus(context)
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
        'parse_status': dict(sorted(Counter(r.status for r in corpus.reports).items())),
        'observations': len(corpus.observations),
        'scopes': len(corpus.scopes),
        'files': files,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    inv = sub.add_parser('inventory', help='load and validate all inputs')
    inv.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    inv.add_argument('--out', type=Path, help='write the full inventory JSON here')
    aud = sub.add_parser('audit', help='run the deterministic offline audit')
    aud.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    aud.add_argument('--requests', type=Path, required=True)
    aud.add_argument('--output', type=Path, required=True)
    aud.add_argument('--run-dir', type=Path, required=True, help='diagnostics directory bound to the output')
    aud.add_argument('--intent', choices=['offline', 'jev', 'staged'], default='offline',
                     help='offline: deterministic English only; jev: Jev + deterministic check; staged: + generative')
    aud.add_argument('--env-file', type=Path, help='explicitly load credentials from a local .env')
    aud.add_argument('--budget-paise', type=float, default=1500.0, help='hard ceiling on model spend for this run')
    aud.add_argument('--investigator', choices=['off', 'replay', 'live'], default='off',
                     help='Phase 4 investigator for routed cases (replay: cached responses only, 0 network calls)')
    aud.add_argument('--investigator-model', help='chat model with native tool calls (live needs probe-tools first)')
    aud.add_argument('--investigator-max-rounds', type=int, default=4)
    aud.add_argument('--investigator-max-tool-calls', type=int, default=16)
    aud.add_argument('--investigator-case-paise', type=float, default=400.0, help='per-case spend cap')
    aud.add_argument('--media-cache', type=Path,
                     help='media_extractions.json of an earlier run: validated screenshot readings for inspect_media')
    prb = sub.add_parser('probe-tools', help='one bounded live native tool-call probe (writes the probe record)')
    prb.add_argument('--model', required=True)
    prb.add_argument('--env-file', type=Path)
    prb.add_argument('--budget-paise', type=float, default=150.0)
    val = sub.add_parser('validate', help='validate a predictions CSV (structure and citations only)')
    val.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    val.add_argument('--requests', type=Path, required=True)
    val.add_argument('--predictions', type=Path, required=True)
    val.add_argument('--run-dir', type=Path, help='accept fallback rows disclosed by this run')
    args = parser.parse_args(argv)

    try:
        if args.command == 'inventory':
            result = inventory(args.dataset)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
            print(json.dumps({k: v for k, v in result.items() if k != 'files'}, indent=2))
        elif args.command == 'probe-tools':
            from ghostaudit.investigator import probe_tool_calling
            from ghostaudit.providers import Gateway, ProviderConfig, load_env_file
            if args.env_file:
                load_env_file(args.env_file)
            gateway = Gateway(ProviderConfig(max_cost_paise=args.budget_paise, max_retries=0, max_rate_limit_retries=1))
            result = probe_tool_calling(gateway, args.model)
            records = json.loads(PROBES.read_text(encoding='utf-8')) if PROBES.exists() else {}
            records[args.model] = {**result, 'usage': gateway.usage_summary()}
            PROBES.parent.mkdir(parents=True, exist_ok=True)
            PROBES.write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
            print(json.dumps(records[args.model], indent=2))
            return 0 if result['ok'] else 2
        elif args.command == 'audit':
            if args.output.resolve() == (args.dataset / 'sample_output.csv').resolve():
                raise ContractError('refusing to overwrite supplied labels')
            gateway = investigator_gateway = investigator = None
            if args.intent != 'offline' or args.investigator != 'off':
                from ghostaudit.providers import Gateway, ProviderConfig, load_env_file
                if args.env_file:
                    load_env_file(args.env_file)
            if args.intent != 'offline':
                gateway = Gateway(ProviderConfig(max_cost_paise=args.budget_paise))
            if args.investigator != 'off':
                from ghostaudit.investigator import InvestigatorConfig
                if not args.investigator_model:
                    raise ContractError('--investigator needs --investigator-model')
                probe = (json.loads(PROBES.read_text(encoding='utf-8')) if PROBES.exists() else {}).get(
                    args.investigator_model, {})
                if args.investigator == 'live' and not probe.get('ok'):
                    raise ContractError(f'no passed tool-call probe for {args.investigator_model}; run '
                                        f'`main.py probe-tools --model {args.investigator_model}` first')
                investigator = InvestigatorConfig(args.investigator_model, max_rounds=args.investigator_max_rounds,
                                                  max_tool_calls=args.investigator_max_tool_calls,
                                                  max_case_paise=args.investigator_case_paise)
                investigator_gateway = gateway if args.investigator == 'live' and gateway else Gateway(
                    ProviderConfig(max_cost_paise=0.0 if args.investigator == 'replay' else args.budget_paise))
                if args.investigator == 'replay':
                    investigator_gateway.mode = 'replay'
            media_cache = json.loads(args.media_cache.read_text(encoding='utf-8')) if args.media_cache else None
            summary = run_audit(args.dataset, args.requests, args.output, args.run_dir, gateway=gateway,
                                use_generative=args.intent == 'staged', investigator=investigator,
                                investigator_gateway=investigator_gateway, media_cache=media_cache)
            print(json.dumps({k: v for k, v in summary.items() if k not in ('fallback_ids', 'model_usage', 'code_sha256')},
                             indent=2))
        else:
            context = load_context(args.dataset)
            requests = load_requests(args.requests)
            rows = read_output(args.predictions)
            fallback_ids = load_disclosures(args.run_dir, args.predictions) if args.run_dir else frozenset()
            validate_output(rows, requests, context.evidence, fallback_ids)
            statuses = dict(sorted(Counter(r.deletion_status for r in rows).items()))
            print(f'VALID: {len(rows)} rows, statuses {statuses}, {len(fallback_ids)} disclosed fallbacks. '
                  f'Checked: exact case set, order, '
                  f'step contract, citation existence and audit_at eligibility. '
                  f'NOT checked: whether citations semantically support each step.')
    except (ContractError, ValueError, FileNotFoundError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
