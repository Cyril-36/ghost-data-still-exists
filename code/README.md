# Ghost Data Still Exists — deletion audit

Status: **Phase 2 — deterministic, offline audit engine.** It parses the
structured evidence, resolves current and historical identities, reconstructs
state at each case's `audit_at`, applies the supplied policy, and writes a
validated `output.csv`-format file. **It calls no model.** Screenshots, PDFs and
non-English request text are reported as unresolved coverage gaps, never guessed.
The root `output.csv` is not produced yet; this is a baseline, not a submission.

## Setup

Python 3.11+ (tested 3.11.13, 3.12.11, 3.13.5, 3.14.7). Standard library only;
nothing to install. Optional: `uv run --project code python code/main.py ...`.

## Commands (from the repository root; any cwd works)

```sh
# Load every input strictly, hash and parse all evidence, report parser coverage
python3 code/main.py inventory [--out .runs/inventory.json]

# Audit requests -> predictions CSV + run diagnostics (never writes dataset/)
python3 code/main.py audit --requests dataset/sample_deletion_requests.csv \
    --output .runs/sample/predictions.csv --run-dir .runs/sample
python3 code/main.py audit --requests dataset/deletion_requests.csv \
    --output .runs/full/predictions.csv --run-dir .runs/full

# Validate (structure, case set, order, citation existence/eligibility).
# --run-dir accepts the best-effort fallback rows that run disclosed.
python3 code/main.py validate --requests dataset/deletion_requests.csv \
    --predictions .runs/full/predictions.csv --run-dir .runs/full

# Evaluation (labels are read here only)
python3 code/evaluation/main.py score --requests dataset/sample_deletion_requests.csv --dataset dataset \
    --predictions .runs/sample/predictions.csv --run-dir .runs/sample \
    --expected dataset/sample_output.csv --out-dir .runs/sample/score
python3 code/evaluation/main.py gaps --dataset dataset --requests dataset/sample_deletion_requests.csv \
    --predictions .runs/sample/predictions.csv --expected dataset/sample_output.csv \
    --run-dir .runs/sample --out-dir .runs/sample/gaps
python3 code/evaluation/main.py fixtures --out .runs/fixtures

# Tests
python3 -m unittest discover -s code/tests -v
```

Run directory: `case_plans.jsonl` (intent, identity, obligations, issues per
case), `unresolved_cases.jsonl` (every fallback and why), `coverage.json`
(per-source parse status), `run_manifest.json` (input hashes, counts, output
SHA-256).

## Pipeline

```
requests ─► intent (English-only, deterministic) ─► NOT_APPLICABLE | UNRESOLVED fallback | DELETE
                                                                                  │
evidence ─► parsers ─► Observations + Scopes ─► corpus indexes                    ▼
                                    identity (intervals, closure) ─► discover linked resources
                                    state at audit_at (per location) ─► policy ─► obligations
                                                                                  │
                         status + steps derived from obligations ─► validated CSV + diagnostics
```

| Module | Role |
|---|---|
| `ghostaudit/contracts.py` | Typed records, official operation table, facets, UTC parsing |
| `ghostaudit/inputs.py` | Strict CSV loaders, manifest path safety, evidence-ID registry |
| `ghostaudit/observations.py` | Observation/Scope model, identifier tokens and matching keys |
| `ghostaudit/parsers/` | JSON (4 families), SQL (restricted SQLite), logs (6 wrappers), handover text, activity rows; media = explicit gap |
| `ghostaudit/corpus.py` | Parse once, exact indexes, cutoff-aware queries, capture coverage |
| `ghostaudit/identity.py` | Identifier histories with validity intervals, conservative closure |
| `ghostaudit/intent.py` | Deterministic English intent (temporary, limited) |
| `ghostaudit/state.py` | State at audit_at per physical location; scoped omission; conflict reporting |
| `ghostaudit/policy.py` | Supplied rules -> treatment, retention checks, policy issues |
| `ghostaudit/planner.py` | Discovery, obligations, status and steps |
| `ghostaudit/audit.py` | Run orchestration, diagnostics, disclosure binding |
| `ghostaudit/tools.py` | Read-only, bounded, cutoff-aware discovery operations for a future investigator |
| `ghostaudit/output.py` | Canonical order, validation, atomic CSV write and read-back |
| `evaluation/` | Scoring, missed-step attribution, 11 synthetic fixtures (labels never reach inference) |

## What works, what is partial, what is planned

Working (tested): structured parsing of all 79 non-media sources; identity
resolution across email changes, case variants and hashes; per-location state
with replica drift; policy obligations for every supplied category; status and
steps from obligations; disclosed best-effort fallbacks; 11 synthetic fixtures
pass end to end; outputs are byte-identical across hash seeds.

Partial: intent (English only; 62/518 requests unresolved); resource discovery
(a subject's primary profile ID is grounded for 79/446 deletion requests, see
`decisions.md` D021); omission-based absence only for subject-covering captures.

Planned (not implemented): screenshot/PDF extraction (15 files), model-based
multilingual intent, the investigator loop, the final `output.csv`, and the
required `usage_report.md`. See `evaluation/evaluation_report.md` for measured
results and limitations, and `decisions.md` for every design choice.
