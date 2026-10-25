# Ghost Data Still Exists — deletion audit

Status: **Phase 3 — evidence engine + measured model decision layer. PROVISIONAL output.**
The engine parses all structured evidence, resolves current and historical
identities, reconstructs state at each case's `audit_at`, applies the supplied
policy, and derives status and steps from validated obligations. Models are used
only for bounded decisions: request intent (Jev typed answers, generative
fallback), PDF document kind (Jev), and blind literal screenshot reading (vision).
Final status, evidence validation and CSV construction are deterministic.

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
# Offline (no model calls): English-only deterministic intent, PDF text without state, screenshots unread
python3 code/main.py audit --requests dataset/sample_deletion_requests.csv \
    --output .runs/sample/predictions.csv --run-dir .runs/sample

# Staged (Jev typed intent -> generative fallback; PDF kind; blind screenshot reader), hard spend ceiling
python3 code/main.py audit --requests dataset/deletion_requests.csv --output output.csv \
    --run-dir runs/phase3-hardened --intent staged --env-file .env --budget-paise 1500

# Validate structure, case set, order, citation existence and eligibility, and disclosed fallbacks
python3 code/main.py validate --requests dataset/deletion_requests.csv \
    --predictions output.csv --run-dir runs/phase3-hardened

# Evaluation (labels are read here only)
python3 code/evaluation/main.py score --requests dataset/sample_deletion_requests.csv --dataset dataset \
    --predictions .runs/sample/predictions.csv --run-dir .runs/sample \
    --expected dataset/sample_output.csv --out-dir .runs/sample/score
python3 code/evaluation/main.py gaps ...            # missed-step attribution
python3 code/evaluation/main.py fixtures --out .runs/fixtures
python3 code/evaluation/main.py intent-benchmark --env-file .env --out .runs/intent   # live, bounded

python3 -m unittest discover -s code/tests      # 153 tests, offline (replay doubles, loopback servers)
```

`--intent offline|jev|staged` selects the intent mode. `--budget-paise` is a hard
spend ceiling: every call reserves a worst-case cost before it is sent (25 paise per
decision, 120 per chat call), so concurrent calls cannot overrun it. Gateway calls never
follow redirects, so the bearer token cannot reach another origin. Validated model
responses are cached under `.cache/ghostaudit/` (gitignored), keyed by endpoint,
requested model, prompt hash and input, and re-validated on every read. A rerun with a warm cache makes 0 network calls, and the manifest
records `model_calls` and `model_cache_hits`.

Each run directory contains:
- `case_plans.jsonl`: intent stage and signals, identity, obligations and issues.
- `unresolved_cases.jsonl`: every disclosed fallback and its reason.
- `coverage.json`: per-source parse and interpretation status.
- `media_extractions.json`: what was read from each PDF or screenshot.
- `usage.jsonl`: one line per model attempt, recording requested and returned model, tokens, latency, cost header, retries and cache use.
- `run_manifest.json`: input hashes, code and prompt hashes, engine config, inference modes, and the output SHA-256.

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

**Planned:**
- An investigator using bounded tool calls and `choose_candidate`.
- An independent verifier.
- The final submission run.

See `evaluation/evaluation_report.md`, `evaluation/usage_report.md` and `decisions.md`.
