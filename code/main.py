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
from ghostaudit.providers import ProviderError

DEFAULT_DATASET = Path(__file__).resolve().parent.parent / 'dataset'
PROBES = Path('.cache/ghostaudit/tool_probes.json')     # gitignored; written only by probe-tools


def load_pricing(gateway):
    from ghostaudit.providers import ProviderError
    try:
        gateway.load_pricing()
    except ProviderError as exc:              # no estimates: live costs are then reported as unpriced
        print(f'WARNING: price catalog unavailable ({exc})', file=sys.stderr)


def vision_capable(gateway, model) -> bool | None:
    """Catalog image-input flag for `model`: True only for an explicit "yes" (unknown is not enough)."""
    from ghostaudit.providers import ProviderError
    try:
        payload = gateway._get('models')
    except (ProviderError, OSError, ValueError):
        return None
    for entry in payload.get('data', []) if isinstance(payload, dict) else []:
        if isinstance(entry, dict) and entry.get('id') == model:
            return {'yes': True, 'no': False}.get(entry.get('roar_image_input'))
    return False


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


def run_agent_first(args):
    """Replay by default; live execution requires explicit opt-in, budget, reserve and a prior probe."""
    import math
    import shutil
    import tempfile
    from ghostaudit.investigator import InvestigatorConfig
    from ghostaudit.providers import Gateway, ProviderConfig, load_env_file
    if args.investigator != 'off':
        raise ContractError('agent-first is the primary agent; disable the baseline investigator')
    if args.agent_max_rounds < 1 or args.agent_max_tool_calls < 1 or args.agent_max_completion_tokens < 1:
        raise ContractError('agent round and tool limits must be positive')
    live = args.agent_mode == 'live'
    if live:
        if not args.allow_agent_live or not args.agent_model:
            raise ContractError('live agent requires --allow-agent-live and an explicit --agent-model')
        for name in ('agent_budget_paise', 'agent_call_reserve_paise'):
            value = getattr(args, name)
            if value is None or not math.isfinite(value) or value <= 0:
                raise ContractError(f'live agent requires an explicit positive --{name.replace("_", "-")}')
        if args.agent_call_reserve_paise > args.agent_budget_paise or args.agent_max_calls < 1:
            raise ContractError('live reserve must fit the budget and agent call limit must be positive')
        if args.authorization_model == args.agent_model:
            raise ContractError('authorization review needs a distinct model')
        probes = json.loads(PROBES.read_text()) if PROBES.exists() else {}
        if probes.get(f'{args.provider}:{args.agent_model}', {}).get('ok') is not True:
            raise ContractError('live agent needs a previously passed tool-call probe; no automatic paid probe is run')
        if args.env_file:
            load_env_file(args.env_file)
    elif args.allow_agent_live:
        raise ContractError('--allow-agent-live requires --agent-mode live')
    with tempfile.TemporaryDirectory(prefix='ghost-agent-replay-') as temporary:
        cache = Path(temporary) / 'cache'
        if args.agent_cache.exists():
            shutil.copytree(args.agent_cache, cache)
        else:
            cache.mkdir()
        g = Gateway(ProviderConfig(provider=args.provider, max_calls=args.agent_max_calls if live else 0,
                                   max_cost_paise=args.agent_budget_paise if live else 0,
                                   chat_cost_reserve_paise=args.agent_call_reserve_paise if live else None,
                                   max_retries=0, max_rate_limit_retries=0, cache_dir=str(cache),
                                   jev_model=args.jev_model, vision_model=args.vision_model))
        g.mode = 'live' if live else 'replay'
        model = args.agent_model or g.config.investigator_model
        if not model:
            raise ContractError('agent-first needs --agent-model for this provider')
        config = InvestigatorConfig(model, max_rounds=args.agent_max_rounds,
                                    max_tool_calls=args.agent_max_tool_calls, max_case_tokens=args.agent_max_case_tokens,
                                    max_case_paise=args.agent_max_case_paise,
                                    max_completion_tokens=args.agent_max_completion_tokens)
        media = json.loads(args.media_cache.read_text(encoding='utf-8')) if args.media_cache else None
        return run_audit(args.dataset, args.requests, args.output, args.run_dir,
                         gateway=g if args.intent != 'offline' else None, media_cache=media,
                         decision_mode='agent-first', agent_gateway=g, agent_config=config,
                         agent_live=live, authorization_model=args.authorization_model,
                         agent_finalize_on_pressure=args.agent_finalize_on_pressure,
                         agent_request_first=args.agent_request_first)


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
    aud.add_argument('--decision-mode', choices=['baseline', 'agent-first'], default='baseline')
    aud.add_argument('--agent-model', help='general-purpose tool-calling model ID for agent-first replay or explicitly opted-in live mode')
    aud.add_argument('--agent-mode', choices=['replay', 'live'], default='replay')
    aud.add_argument('--allow-agent-live', action='store_true', help='explicit live execution opt-in')
    aud.add_argument('--agent-budget-paise', type=float, help='explicit total live ceiling across agent/media/review')
    aud.add_argument('--agent-call-reserve-paise', type=float, help='conservative reservation for each live chat attempt')
    aud.add_argument('--agent-max-calls', type=int, default=20)
    aud.add_argument('--agent-finalize-on-pressure', action='store_true',
                     help='request an agent-authored final proposal before the execution allowance expires')
    aud.add_argument('--agent-request-first', action='store_true',
                     help='have the primary agent interpret request permission before receiving the evidence inventory')
    aud.add_argument('--authorization-model', help='distinct independent model for uncertain non-deletion confirmation')
    aud.add_argument('--agent-cache', type=Path, default=Path('.cache/ghostaudit'))
    aud.add_argument('--agent-max-rounds', type=int, default=8)
    aud.add_argument('--agent-max-case-tokens', type=int, default=120000,
                     help='per-case ceiling on processed agent tokens')
    aud.add_argument('--agent-max-case-paise', type=float, default=400.0,
                     help='per-case ceiling on charged agent exposure (unpriced calls count their full reserve)')
    aud.add_argument('--agent-max-tool-calls', type=int, default=40)
    aud.add_argument('--agent-max-completion-tokens', type=int, default=6000)
    aud.add_argument('--intent', choices=['offline', 'jev', 'staged'], default='offline',
                     help='offline: deterministic English only; jev: Jev + deterministic check; staged: + generative')
    aud.add_argument('--env-file', type=Path, help='explicitly load credentials from a local .env')
    aud.add_argument('--budget-paise', type=float, default=1500.0, help='hard ceiling on model spend for this run')
    aud.add_argument('--investigator', choices=['off', 'replay', 'live'], default='off',
                     help='Phase 4 investigator for routed cases (replay: cached responses only, 0 network calls)')
    aud.add_argument('--provider', choices=['tokenbazaar', 'roar'], default='tokenbazaar',
                     help='API provider for every model role (default tokenbazaar)')
    aud.add_argument('--intent-provider', choices=['tokenbazaar', 'roar'], help='provider for intent and media roles')
    aud.add_argument('--investigator-provider', choices=['tokenbazaar', 'roar'], help='provider for the investigator')
    aud.add_argument('--proxy-auth', action='store_true',
                     help='send no Authorization header when the key variable is unset (egress proxy injects it)')
    aud.add_argument('--jev-model', help='override the provider profile typed-decision model')
    aud.add_argument('--intent-fallback-model', help='override the provider profile generative intent fallback model')
    aud.add_argument('--vision-model', help='override the screenshot reader model (must accept image input)')
    aud.add_argument('--investigator-model', help='chat model with native tool calls (live needs probe-tools first)')
    aud.add_argument('--investigator-max-rounds', type=int, default=4)
    aud.add_argument('--investigator-max-tool-calls', type=int, default=16)
    aud.add_argument('--investigator-case-paise', type=float, default=400.0, help='per-case spend cap')
    aud.add_argument('--media-cache', type=Path,
                     help='media_extractions.json of an earlier run: validated screenshot readings for inspect_media')
    prb = sub.add_parser('probe-tools', help='one bounded live native tool-call probe (writes the probe record)')
    prb.add_argument('--model', required=True)
    prb.add_argument('--provider', choices=['tokenbazaar', 'roar'], default='tokenbazaar')
    prb.add_argument('--proxy-auth', action='store_true')
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
            gateway = Gateway(ProviderConfig(provider=args.provider, max_cost_paise=args.budget_paise, max_retries=0,
                                             max_rate_limit_retries=1, proxy_auth=args.proxy_auth or None))
            result = probe_tool_calling(gateway, args.model)
            records = json.loads(PROBES.read_text(encoding='utf-8')) if PROBES.exists() else {}
            records[f'{args.provider}:{args.model}'] = {**result, 'usage': gateway.usage_summary()}
            PROBES.parent.mkdir(parents=True, exist_ok=True)
            PROBES.write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
            print(json.dumps(records[f'{args.provider}:{args.model}'], indent=2))
            return 0 if result['ok'] else 2
        elif args.command == 'audit':
            if args.decision_mode == 'agent-first':
                summary = run_agent_first(args)
                print(json.dumps({k: v for k, v in summary.items() if k not in
                                  ('fallback_ids', 'model_usage', 'code_sha256')}, indent=2))
                return 0
            if args.output.resolve() == (args.dataset / 'sample_output.csv').resolve():
                raise ContractError('refusing to overwrite supplied labels')
            gateway = investigator_gateway = investigator = None
            intent_provider = args.intent_provider or args.provider
            investigator_provider = args.investigator_provider or args.provider
            if args.intent != 'offline' or args.investigator != 'off':
                from ghostaudit.providers import Gateway, ProviderConfig, load_env_file
                if args.env_file:
                    load_env_file(args.env_file)

                def config(provider, ceiling, **models):
                    return ProviderConfig(provider=provider, max_cost_paise=ceiling, proxy_auth=args.proxy_auth or None,
                                          **{k: v for k, v in models.items() if v})
            if args.intent != 'offline':
                gateway = Gateway(config(intent_provider, args.budget_paise, jev_model=args.jev_model,
                                         intent_fallback_model=args.intent_fallback_model,
                                         vision_model=args.vision_model))
                if gateway.profile.cost_header is None:
                    load_pricing(gateway)                      # catalog prices for cost estimates (free GET)
                    if gateway.config.vision_model and vision_capable(gateway, gateway.config.vision_model) is not True:
                        raise ContractError(f'{gateway.config.vision_model} is not catalogued with image input on '
                                            f'{intent_provider}; screenshots need an image-capable --vision-model')
            if args.investigator != 'off':
                from ghostaudit.investigator import InvestigatorConfig
                shared = gateway if gateway and investigator_provider == intent_provider and args.investigator == 'live' \
                    else None
                investigator_gateway = shared or Gateway(config(
                    investigator_provider, 0.0 if args.investigator == 'replay' else args.budget_paise,
                    investigator_model=args.investigator_model))
                model = args.investigator_model or investigator_gateway.config.investigator_model
                if not model:
                    raise ContractError(f'no investigator model configured for {investigator_provider}; '
                                        'pass --investigator-model')
                probe = (json.loads(PROBES.read_text(encoding='utf-8')) if PROBES.exists() else {}).get(
                    f'{investigator_provider}:{model}', {})
                if args.investigator == 'live' and not probe.get('ok'):
                    raise ContractError(f'no passed tool-call probe for {investigator_provider}:{model}; run '
                                        f'`main.py probe-tools --provider {investigator_provider} --model {model}` first')
                investigator = InvestigatorConfig(model, max_rounds=args.investigator_max_rounds,
                                                  max_tool_calls=args.investigator_max_tool_calls,
                                                  max_case_paise=args.investigator_case_paise)
                if gateway is not None and investigator_gateway is not gateway and args.investigator == 'live':
                    investigator_gateway.share_budget(gateway)    # one --budget-paise ceiling for the whole run
                if args.investigator == 'replay':
                    investigator_gateway.mode = 'replay'
                elif investigator_gateway is not shared and investigator_gateway.profile.cost_header is None:
                    load_pricing(investigator_gateway)
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
    except (ContractError, ProviderError, ValueError, FileNotFoundError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
