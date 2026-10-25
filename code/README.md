# Ghost Data Still Exists — deletion audit

Status: **Phase 4: investigator and independent verifier implemented. Historical live experiments
are recorded in `decisions.md`; the current audit uses offline, loopback and replay checks only.
The root `output.csv` remains the preserved Phase 3 provisional output.**
The engine parses all structured evidence, resolves current and historical
identities, reconstructs state at each case's `audit_at`, applies the supplied
policy, and derives status and steps from validated obligations. Models are used
only for bounded decisions: request intent (Jev typed answers, generative
fallback), PDF document kind (Jev), and blind literal screenshot reading (vision).
In the default baseline, final status and steps are deterministic. The selectable agent-first mode
instead lets the primary agent author authorization, status and all steps; code accepts or rejects
the proposal without replacing it with a planner answer. See
[`AGENT_FIRST_ARCHITECTURE.md`](evaluation/AGENT_FIRST_ARCHITECTURE.md).

The root `output.csv` (518 rows) is **provisional**. Its run diagnostics are in
`runs/phase3-hardened/` (produced by the hardened code; byte-identical to the earlier
provisional output kept with its run in `runs/phase3-provisional/` as the comparison
baseline), and `validate --run-dir` re-checks the hash binding.

## Setup

Python 3.11+ (tested on 3.11.13, 3.12.11, 3.13.5 and 3.14.7). Standard library only,
with nothing to install. Optionally: `uv run --project code python code/main.py ...`.

Model calls need `TOKENBAZAAR_API_KEY` in the environment, read through the
TokenBazaar gateway `https://tokenbazaar.in/api/public/v1`. A local, uncommitted
`.env` is read only when you pass `--env-file .env`. Without a key, every command
runs offline.

## Commands (from the repository root)

```sh
# Agent-first default: cached responses only, no keys, pricing GETs or paid calls.
# Historical investigator caches use a different protocol; missing responses yield disclosed fallbacks.
python3 code/main.py audit --decision-mode agent-first --provider roar --agent-model claude-haiku-5-5 \
    --requests dataset/sample_deletion_requests.csv --output .runs/agent-first/predictions.csv \
    --run-dir .runs/agent-first --agent-max-rounds 8 --agent-max-completion-tokens 4000 \
    --agent-request-first --agent-finalize-on-pressure
```

Jev is optional here: `--intent jev` enables cached typed media interpretation, never precomputed
request intent. `--media-cache` supplies earlier screenshot readings for agent tools. The primary
model is chosen with `--agent-model` (Gemini, Claude or another native tool-calling model); its new
proposal protocol has a two-case live pilot; broader model accuracy remains unverified. `--agent-cache` is copied
before use so invalid/missing entries cannot alter the original cache. `--agent-mode replay` remains
the default. A separate live path now requires `--agent-mode live --allow-agent-live`, explicit
`--agent-model`, positive `--agent-budget-paise` and `--agent-call-reserve-paise`, a bounded
`--agent-max-calls`, and an already passed tool-call probe. There is no automatic probe, pricing
request or transport retry. Media and optional `--authorization-model` share the same spending
ledger. The authorization reviewer must be a distinct model; it sees only the request and can
confirm an otherwise unresolved non-deletion proposal, never author the primary agent's answer.
Used responses persist under the fresh run's `model_cache/` for replay. See
[`AGENT_FIRST_REVIEW_FIXES.md`](evaluation/AGENT_FIRST_REVIEW_FIXES.md) for controls and limitations.
The first real pilot initially failed; the subsequent controlled continuation produced verified Haiku
outputs on both pilot cases. Optional `--agent-request-first --agent-finalize-on-pressure` separates
permission interpretation from evidence investigation and reserves a submission window. The primary
agent still authors every semantic field. See
[`AGENT_FIRST_LIVE_CONTINUATION.md`](evaluation/AGENT_FIRST_LIVE_CONTINUATION.md) for exact results,
limits and closed cumulative spending; no broader accuracy gain or further paid run is authorized.

```sh
# Offline: explicit English intent, narrowly recognized native PDF structures, screenshots unread
python3 code/main.py audit --requests dataset/sample_deletion_requests.csv \
    --output .runs/sample/predictions.csv --run-dir .runs/sample

# Staged (paid): use fresh paths; reservations bound admitted estimated spending
python3 code/main.py audit --requests dataset/deletion_requests.csv --output .runs/staged/predictions.csv \
    --run-dir .runs/staged --intent staged --env-file .env --budget-paise 1500

# Validate structure, case set, order, citation existence and eligibility, and disclosed fallbacks
python3 code/main.py validate --requests dataset/deletion_requests.csv \
    --predictions output.csv --run-dir runs/phase3-hardened

# Evaluation (labels are read here only)
python3 code/evaluation/main.py score --requests dataset/sample_deletion_requests.csv --dataset dataset \
    --predictions .runs/sample/predictions.csv --run-dir .runs/sample \
    --expected dataset/sample_output.csv --out-dir .runs/sample/score
python3 code/evaluation/main.py gaps ...            # missed-step attribution
python3 code/evaluation/main.py errors ...          # case-by-case differences grouped by root cause
python3 code/evaluation/main.py fixtures --out .runs/fixtures
python3 code/evaluation/main.py intent-benchmark --env-file .env --out .runs/intent   # live, bounded

python3 -m unittest discover -s code/tests      # offline: replay doubles and loopback servers
```

Providers (D070): `--provider tokenbazaar` (default, unchanged) or `--provider roar`. Split them with
`--intent-provider tokenbazaar --investigator-provider roar`. Roar needs `ROAR_API_KEY`, or Claude
Cloud's proxy-injected secret for `api.roar-ai.com`. Role models come from the provider profile
and can be overridden with `--jev-model`, `--intent-fallback-model`, `--vision-model` and
`--investigator-model`.

```sh
python3 code/main.py audit --requests dataset/sample_deletion_requests.csv --output .runs/r/p.csv --run-dir .runs/r \
    --provider roar --intent staged --investigator live --budget-paise 200     # investigator: gpt-oss-20b
```

Phase 4 (decisions D065-D068):

```sh
# Every audit runs the independent verifier; routed cases go to the investigator when one is configured.
python3 code/main.py probe-tools --model <chat-model> --env-file .env          # one bounded live probe
python3 code/main.py audit --requests dataset/sample_deletion_requests.csv \
    --output .runs/p4/predictions.csv --run-dir .runs/p4 --intent staged --env-file .env \
    --investigator live --investigator-model <chat-model> --budget-paise 1500 \
    --media-cache runs/phase3-hardened/media_extractions.json
# replay: cached validated responses only, never a network call
python3 code/main.py audit ... --investigator replay --investigator-model <chat-model>
python3 code/evaluation/main.py errors ...   # adds citation and resource-obligation accuracy
```

The run directory also has `case_files.jsonl`, which records every investigated case: tool calls with
arguments and returned handles, accepted and rejected facts with reasons, verifier findings per pass,
rounds, and the stop reason. `case_plans.jsonl` gains `routing`, `verifier` and `investigation`, and
`run_manifest.json` gains a `phase4` summary.

Use a fresh `--run-dir` for every audit. Nonempty directories and paths that could overwrite
dataset files, requests or diagnostics are rejected. Existing results remain available for validation.
The output destination must end in `.csv`. An existing destination must already parse as a
prediction CSV; unrelated files are refused before inference. Fresh CSV paths and deliberate
replacement of the final `output.csv` remain supported.

`--intent offline|jev|staged` selects the intent mode. `--budget-paise` bounds admitted
spending against one shared reservation ledger. Reservations use catalog estimates when available
and defaults (25 paise per decision, 120 per chat call) otherwise. Unknown prices, incomplete
catalogs or larger actual charges prevent a guarantee about the eventual provider invoice; configure
a provider-side spending limit before paid runs. Gateway calls never
follow redirects, so the bearer token cannot reach another origin. Validated model
responses are cached under `.cache/ghostaudit/` (gitignored), keyed by endpoint,
requested model, prompt hash and input, and re-validated on every read. A rerun with a warm cache makes 0 network calls, and the manifest
records `model_calls` and `model_cache_hits`.

Each run directory contains:
- `case_plans.jsonl`: intent stage and signals, identity, obligations and issues.
- `unresolved_cases.jsonl`: every disclosed fallback and its reason.
- `coverage.json`: per-source parse and interpretation status.
- `media_extractions.json`: what was read from each PDF or screenshot.
- `usage.jsonl`: one line per model attempt, recording requested and returned model, tokens (including cached input tokens), latency, cost header, retries and cache use.
- `run_manifest.json`: input and evidence-body hashes, code and prompt hashes, engine config, inference modes, and the output SHA-256. Historical manifests without evidence-body hashes cannot prove those original bytes.

## Pipeline

```
request ─► deterministic check ─► Jev (choice + 3 yes/no) ─► reconcile ─► DELETE(ACCOUNT | ITEM) | NOT_APPLICABLE | UNRESOLVED
                                           ├─ conflict flagged by Jev ─► UNRESOLVED (never escalated)
                                           └─ uncertain/disagreeing ─► generative (verbatim spans) ─┘
                                   ITEM ─► Jev target choice (one data category) ─► only that category is planned
evidence ─► parsers (JSON, SQL, logs, text, activity, PDF text) ─► observations + explicit scopes
         └► media: PDF kind (Jev) / screenshot blind reader ─► validated literal facts ─► observations
identity (validity + availability) ─► discovery (catalog links, multi-hop relationship edges)
state at audit_at per copy ─► policy (supplied conditions only) ─► obligations ─► status + steps ─► validated CSV
```

| Layer | Decides | Never decides |
|---|---|---|
| Deterministic | parsing, identity, cutoff, state, policy, obligations, status, steps, citations, CSV | — |
| Jev (`typesafe/jev-latest`) | intent category and preserve/marketing/conflict flags; the one data category of an item-scoped deletion; PDF document kind | IDs, dates, facts, status |
| Generative (`google/gemini-2.5-flash`) | intent only when Jev is uncertain or disagrees (spans must be verbatim) | status, evidence |
| Vision (same model ID) | literal text on screenshots, read blind | flags, states, status, steps |

The gateway currently serves `google/gemini-2.5-flash` as `google/gemini-3.8-flash`.
Every call records both IDs (see `decisions.md` D036).

## Engine configuration

The defaults are strict. Relaxations exist only as recorded experiments
(`ghostaudit/config.py`, written to `run_manifest.json`):
- `subject_scoped_omission`
- `presume_retention`
- `post_cutoff_ownership_links`
- `use_media`

## What works, what is partial, what is planned

**Working (tested):**
- All 79 structured sources parse, and all 15 media files are interpreted.
- Six Phase 2 correctness fixes and the independent audit's hardening fixes have regression
  tests that fail on the code they fixed (`test_phase3_regressions.py`,
  `test_hardening_regressions.py`).
- A deletion request carries its authorization scope: a request to delete one item never
  authorizes the account or other records.
- Multilingual intent scored 72/72 on a separately written six-language set and 32/32 on the samples, with no unsafe authorizations.
- Disclosed fallbacks are bound to the output hash.
- Outputs are byte-identical across hash seeds.

**Partial:**
- **Ownership:** 29 sample resources have no grounded link in any source.
- **Retention:** 52 of the 61 fallbacks are retained records with no supplied retention period. They stay UNRESOLVED under the strict policy.
- **Subject-level absence:** 58 COMPLETE rows prove profile absence at subject level, without a citable resource ID.

**Implemented Phase 4:** bounded investigation with shown-text grounding, validated fact proposals,
next-turn token/cost admission and an independent verifier. Historical model-dependent tool efficiency
and accuracy limits remain; passing replay checks does not establish live improvement.

**Pending:** independent review and the final submission run.

See `evaluation/CODE_AUDIT_REPORT.md`, `evaluation/evaluation_report.md`,
`evaluation/usage_report.md`, `evaluation/QUALITY_EXPERIMENTS.md` and `decisions.md`.

The quality experiments improve the offline sample from 26/32 statuses and 126/193 correct steps
to 27/32 and 136/193. Exact rows remain 1/32; the best measured staged sample remains 28/32 statuses,
141/193 correct steps and 1/32 exact rows. Longer Haiku and Luna investigations did not improve
these outputs, so the provider profiles and default round limits stay unchanged. Search now
diversifies sources and supports `start_match`/`next_match` pagination. Offline native PDF handling
abstains unless the document has an explicit security-journal or support-attachment structure;
it does not invent ownership, dates or a system. Typed document decisions remain the staged path.
