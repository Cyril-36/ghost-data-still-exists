# Frozen agent-first benchmark: all 32 public samples

2026-10-25. Source revision `3a8583bb9bf7fbf67bacfedf3c69430b1dd16971`, existing main.
The user authorized all 32 samples and increased the cumulative pilot cap to ₹100. All **32/32**
cases were processed. There were **30 fresh live-case attempts and two previously successful cached
pilot cases**; those two cases were used in development and are not held-out validation.

**The configuration does not generalize well enough for a 518-case run.** Only 4/32 final proposals
verified; 28/32 became explicitly disclosed empty fallbacks. The baseline recovers far more steps.
Neither the verifier nor the policy was relaxed to improve a score. Root output remains unchanged.

## Frozen configuration and comparison

Roar `claude-haiku-5-5`, native tool calls, primary-agent request interpretation and final semantic
authority. Flags: agent-first/live/allow-agent-live, request-first, finalize-on-pressure, eight rounds,
40 tool calls, 120,000 charged case tokens, 4,000 completion tokens, 50-paise call reserve and a
256-call run cap. No paid Jev or screenshot reader. Transport retries were zero. The existing
successful Haiku probe and a fresh free catalog check were used. A persistent independent ledger
reserved each POST before sending and included all prior pilot exposure. Planner/replanner calls
were guarded to raise if used; neither authored or repaired an agent answer.

The configuration was not changed during the benchmark. Public expected answers were read only
after inference finished. A fresh deterministic baseline was generated separately without model
calls. Both arms used the same strict evidence configuration and media settings. A historical cached
Roar staged baseline is reported separately as context, not an identical fresh control.

| Metric | Fresh deterministic baseline | Agent-first | Historical cached Roar baseline |
|---|---:|---:|---:|
| Exact rows | 1/32 (3.13%) | 1/32 (3.13%) | 1/32 (3.13%) |
| Status correct | 27/32 (84.38%) | 22/32 (68.75%) | 28/32 (87.50%) |
| Step precision | 136/137 (99.27%) | 12/12 (100%) | 141/142 (99.30%) |
| Step recall | 136/193 (70.47%) | 12/193 (6.22%) | 141/193 (73.06%) |
| Expected cited steps matched | 54/75 (72%) | 4/75 (5.33%) | 56/75 (74.67%) |

All four emitted agent finding citations match their public expected cited steps (4/4); the baseline
matches all 54 citations it emits (54/54). High emitted precision does not compensate for missing
coverage. The agent emits INCOMPLETE for all 32 rows, so its 22 correct statuses must not be confused
with 22 independently verified decisions: **only four decisions verified**.

Accepted cases: case_05 (3/3 steps, exact), case_06 (3/4), case_07 (3/4), case_08 (3/4). The earlier
two-case pilot demonstrated feasibility, not broad reliability. On the 30 newly tested cases, only
two more answers verified and no additional exact row was obtained.

## Verification and failure evidence

- 60 full audit proposals: **4 accepted, 56 rejected (93.33%)**.
- 24/60 proposals triggered grounding-related rejection codes (40%). This is a detection proxy,
  not a claim that the actual semantic hallucination rate is known or every error was detected.
- 25 case stops were CASE_BUDGET, four VERIFIED_PROPOSAL and three MODEL_UNAVAILABLE. The eight-round
  setting does not guarantee eight calls: growing repeated context often exhausts the charged
  case-token admission bound earlier.
- 165 live API attempts: 159 validated native responses, four invalid native/schema responses and
  two transport timeouts. Timeouts occurred on case_20 and case_23; no request was automatically
  retried. Unknown-charge reservations were retained. There were 11 initial cache hits.

Across rejected proposals, deduplicating each code within a proposal, the principal problems were:

| Rejection family | Proposals affected |
|---|---:|
| Integrity: ordering, missing steps/citations, conflicting contract fields | 41 |
| Authorization left unresolved | 20 |
| Omitted independently known resources | 16 |
| Unproven policy conditions | 16 |
| Missing/unshown source witnesses | 14 |
| Policy/action mismatch | 14 |
| Unsupported ownership/association | 12 |
| Unknown category/policy | 10 |

Among integrity failures, 15 proposals omitted required grouped remediation/verification, 12 had
noncanonical/duplicate ordering and four lacked a finding citation; categories can overlap. This is
not solely a round-limit issue. Additional turns would not automatically repair unsupported ownership,
policy assumptions or malformed complete plans. Disabling the verifier would hide these failures.

The rejected proposals, model responses, budget notices, tool results, raw requests and per-case
progress are retained. No failed row was replaced with the deterministic baseline to improve scores.

## Cost and runtime

| Measurement | Result |
|---|---:|
| Batch wall time | 542.36 seconds (about nine minutes) |
| Reported live input / output tokens | 2,167,497 / 60,259 |
| New batch catalog estimate, conservative ₹100/USD assumption | **₹24.68792** |
| New batch conservative exposure, including unknown failures | **₹56.48036** |
| All pilot exposure before this batch | ₹14.38012 |
| Cumulative pilot conservative exposure after this batch | **₹70.86048 of ₹100** |
| Actual billing | **Unavailable** |

Token totals include invalid responses when usage was reported; timeout usage is unknown. Production
Gateway telemetry uses its existing ₹88/USD estimate; the independent experimental ledger uses
₹100/USD, 2x margins, byte-as-token preflight bounds, long-context multipliers and reservation floors.
This is estimated admission control, not a guarantee about an eventual provider invoice. No actual
charge headers were supplied. The earlier separate 177-event campaign remains closed and unchanged
at 8,947.98576 paise conservative exposure; it was not reset or hidden in these figures.

The expanded pilot ledger is closed after all 32 cases. No remaining allowance is automatically
available for another experiment, model, prompt change or the 518-case dataset.

## Artifacts and offline reproduction

Local artifacts are under `.runs/agent-sample32-cckl3ldg/`:

- `agent.csv`, `agent/`: all 32 predictions, hash-bound manifest, fallbacks and complete case files.
- `baseline.csv`, `baseline/`: fresh deterministic comparison.
- `results.json`, `case-results.csv`: aggregate and per-case post-inference scores.
- `progress.jsonl`, `case-progress.jsonl`, `transport-*-request/response.json`, `agent/model_cache/`:
  full trace, raw transport captures and validated used responses.
- `budget-before.json`, `budget-after.json`, `argv.json`, `runtime.py`: budget and execution provenance.

A compact durable evaluation summary with per-case counts and artifact hashes is committed as
`agent_first_sample32_metrics.json`. It is evaluation data, not imported by production inference.

All 32 rows pass the existing schema/case/citation eligibility validation with hash-bound fallback
disclosures. Copied-cache replay on Python 3.11.13 and 3.14.7 (different hash seeds) makes **zero
network calls**, uses 168 cache hits and produces byte-identical CSVs with the same four accepted
answers. This reproduces final output; invalid and failed transport responses are intentionally not
trusted cached model responses, so replay need not reproduce their exact error traces. All 1,371
protected hashes match; datasets, `.env`, root output, original caches and historical artifacts remain
unchanged. Only documentation/evaluation evidence was changed; the frozen engine was not modified.

## Decision

Do **not** spend on a full 518-case run with this configuration. The next useful work is a small,
controlled improvement to complete-plan submission and targeted evidence retrieval, followed by a
model/configuration comparison on unseen cases. This benchmark supports keeping agent-first as an
experimental selectable architecture; it does not support replacing the stronger baseline output.
