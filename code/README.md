# Ghost Data Still Exists — deletion audit

Status: **Phase 1 (foundation) only.** The system validates inputs and outputs,
serializes answers canonically, ships two synthetic fixtures, and scores
predictions against labels. It does **not** audit cases yet. No `output.csv`
predictions exist, and no accuracy on real cases is claimed.

## Setup

Requires Python 3.11+. Uses the standard library only (no install step).

```sh
# optional, reproducible interpreter + lock
uv run --project code python code/main.py --help
```

## Commands (run from the repository root; any cwd works)

```sh
# Strictly load every input, hash all 94 evidence files
python3 code/main.py inventory [--dataset dataset] [--out .runs/inventory.json]

# Validate a predictions CSV: exact case set, row order, step contract,
# citation existence and audit_at eligibility (NOT semantic support)
python3 code/main.py validate --requests dataset/sample_deletion_requests.csv \
    --predictions dataset/sample_output.csv

# Write synthetic fixtures A and C (inputs + expected answers)
python3 code/evaluation/main.py fixtures --out .runs/fixtures

# Score predictions against labels
python3 code/evaluation/main.py score --requests R.csv --predictions P.csv \
    --expected E.csv [--dataset D] --out-dir .runs/score

# Tests
python3 -m unittest discover -s code/tests -v
```

## Layout

| Path | Role |
|---|---|
| `ghostaudit/contracts.py` | Typed records, official operation table and ranks, UTC timestamp parsing |
| `ghostaudit/inputs.py` | Strict CSV loaders, manifest path safety, evidence-ID registry |
| `ghostaudit/output.py` | Canonical step order, row/status/citation validation, atomic CSV write and read-back |
| `main.py` | Inference-side CLI (`inventory`, `validate`) |
| `evaluation/scoring.py` | Status, step P/R/F1, positional field, sequence, and exact-row metrics |
| `evaluation/fixtures.py` | SYNTHETIC fixtures A (residual profile) and C (marketing-only, NOT_APPLICABLE) |
| `evaluation/main.py` | Evaluation CLI (`fixtures`, `score`) |
| `tests/` | unittest suite |
| `decisions.md` | Implementation decisions with evidence |

`ghostaudit/` never imports `evaluation/`, and its loaders open only fixed input
filenames, so labels and expected answers cannot reach inference.

## Metrics (evaluation/scoring.py)

- Status accuracy (and a confusion matrix)
- Step precision/recall/F1 over the multiset of
  `(phase, system_id, resource_id, operation, state, evidence_id)`; `step_no` is excluded
- Positional field accuracy: step *i* vs step *i*; each field of an unmatched step is an error
- Sequence accuracy: identical ordered arrays
- Exact-row accuracy: status and sequence both correct

Every rate is reported as numerator/denominator.

## Not implemented yet

Evidence parsing (JSON/SQL/logs/PDF/images), identity resolution, temporal state
reconstruction, policy obligations, intent classification, investigator/model
providers, the `audit` command, best-effort fallback publication, and the
required `evaluation/evaluation_report.md` and `evaluation/usage_report.md`.
