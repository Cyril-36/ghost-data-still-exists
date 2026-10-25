# Agent-first live pilot — 2026-10-25

Historical initial pilot. The user later reopened testing and expanded the cumulative cap;
[`AGENT_FIRST_LIVE_CONTINUATION.md`](AGENT_FIRST_LIVE_CONTINUATION.md) records the two verified
outputs and final cumulative spending. The figures below describe the earlier stopped phase.

Starting revision: `b6a9198`, existing `main`. Additional authorized exposure: **1,000 paise (₹10)**,
independent of the earlier closed campaign. Initial instruction selected TokenBazaar; subsequent user
steering selected Roar and Haiku 5.5, then authorized a further attempt with autonomous tools.
Only `case_05` was tested. **No independently verified final agent answer and no accuracy gain.**
`case_07` was not run because the required successful first-case gate was never met.

## Provider checks and spending controls

Local `.env` credentials were available and read without printing them. Free catalogs confirmed
TokenBazaar `google/gemini-3.8-flash` and Roar `claude-haiku-5-5`. No TokenBazaar native probe existed
for Gemini, so one small, budgeted native probe was attempted. It failed with HTTP 503 in 3.05 seconds:
“TokenBazaar service is temporarily unavailable.” No retries or other TokenBazaar inference calls.
Catalog availability did not mean inference recovery. The full **25-paise failed-call reservation**
is retained because actual billing is unknown.

Roar's existing successful native Haiku tool-call probe was reused without another paid probe.
The catalog advertises tool calls and USD prices of $0.10/M input and $0.50/M output tokens, with
5x multipliers beyond 100,000 input tokens. The pilot uses an explicit conservative ₹100/USD
conversion assumption, not a claimed exchange rate. Long-context multipliers are included in
preflight bounds when the conservative input bound crosses the threshold.

Every actual POST is reserved in a new persistent cumulative ledger before transmission. Reservation:
max(25 paise, twice the catalog charge counting **every UTF-8 request byte as an input token**, plus
the full requested output allowance). Unknown/failed calls retain that amount. Successful calls
settle conservatively to max(25 paise, twice the token-based estimate, any reported charge).
Admission stops at **950 paise**, below the 1,000-paise authorization. No automatic retries.

These controls bound admitted estimated exposure; they cannot guarantee a provider's final invoice.
Provider-side caps and actual account billing were not independently verified. Roar supplied no
actual charge headers, and the failed TokenBazaar probe supplied no billing evidence. A zero total
in an absent-header accounting field must not be interpreted as a free run.

| Exposure | Result |
|---|---:|
| Prior closed campaign, unchanged | 8,947.98576 paise conservative, 177 events |
| New failed TokenBazaar probe reserve | 25 paise |
| New Roar catalog estimate, ₹100/USD assumption | 165.344 paise (₹1.65344) |
| New total conservative exposure, including failed probe | **360.384 paise (₹3.60384)** |
| Actual new billing | **Unavailable** |

The new ledger is closed at that exposure. No old budget was reset, reused or overwritten.
Production manifests use the existing ₹88/USD estimator (145.50272 paise across the Roar calls);
the independent pilot ledger deliberately uses the larger conversion and safety margin above.

## Runs and observed agent behavior

Both Roar runs used the real CLI argument path with `--decision-mode agent-first --agent-mode live
--allow-agent-live --provider roar --agent-model claude-haiku-5-5`, explicit remaining budget,
50-paise call reserve and eight-call limit. Limits were unchanged between attempts: eight rounds,
40 tool calls, 120,000 charged case tokens, 4,000 completion tokens per turn and 400-paise case cap.
The independent outer ledger enforced the cumulative budget across both runs and the failed probe.
Jev and paid media readers were off. Existing planner and investigator re-planner functions were
patched to raise if called; neither was called. The primary model chose all evidence calls and
proposal fields; no deterministic proposal was supplied or silently substituted.

| Measurement | Initial Haiku run | User-authorized retry |
|---|---:|---:|
| Successful API calls | 5 | 6 |
| Evidence/submit tool calls | 15 / 0 | 6 / 2 |
| Input / output tokens | 69,637 / 824 | 82,402 / 1,837 |
| Sum of transport latency | 8.25 s | 11.84 s |
| Catalog estimate, paise at ₹100/USD | 73.757 | 91.587 |
| Conservative settled exposure, paise | 150.49 | 184.894 |
| Proposals / accepted / rejected | 0 / 0 / 0 | 2 / 0 / 2 |
| Stop reason | CASE_BUDGET | CASE_BUDGET |

The first run found the subject's surviving profile in a failover SQL replica, then inspected broad,
often unrelated and truncated sources. One activity query using an email as a resource handle was
correctly refused. A screenshot inspection returned no evidence because no cached reading was
supplied; this did not trigger a hidden paid reader. Repeated growing contexts consumed the charged
case-token allowance before the nominal round limit. No proposal was submitted.

After inspection and offline-tested budget feedback, the authorized retry focused on identifier
search, relevant raw SQL lines, activity, a SQL query and one screenshot inspection. It submitted:

1. A correctly cited remaining-record finding, but authorization NON_DELETION combined with an
   INCOMPLETE finding, and no remediation or verification. The verifier rejected the contradictory
   authorization/status and incomplete step group.
2. In response to feedback, the agent supplied the finding, DELETE_RECORD remediation and future
   VERIFY_RECORD_ABSENT check. These **three raw proposed steps match all three public expected
   steps**, with the expected finding citation. However, authorization remained UNRESOLVED, with an
   incorrect rationale that the customer had to supply an internal category/scope name. It was
   rejected. The agent repaired step completeness, not the authorization failure.

Public expected rows were read only after inference for evaluation. No labels or baseline plans
were sent to the agent. No resource links, temporal exceptions or retention assumptions were added.

## Final-output comparison

| Case_05 metric | Fresh deterministic baseline | Initial agent output | Retry agent output |
|---|---:|---:|---:|
| Verified agent proposal | N/A | No | No |
| Exact row | 1/1 | 0/1 | 0/1 |
| Status match | 1/1 | 1/1 | 1/1 |
| Correct steps / expected | 3/3 | 0/3 | 0/3 |
| Step precision | 3/3 | Undefined, empty | Undefined, empty |
| Expected finding citations matched | 1/1 | 0/1 | 0/1 |

Both published agent outputs are explicit empty INCOMPLETE fallbacks. Their status match is not
proof of a successful agent decision. The retry's verifier rejection rate is 2/2 (100%); one feedback
turn repaired the step group but neither proposal verified. Detected grounding rejection is 0/2,
which is a limited proxy, not proof of zero hallucination or semantic error. Rejected proposal
content is preserved for diagnosis and never promoted into the final CSV.

## Retained offline improvements and verification

- Supply execution-budget progress on every agent turn: remaining rounds/tools, processed tokens,
  case-token headroom and an urgency flag as context grows. Keep admission limits unchanged. Notices
  contain no resource, authorization, status or step recommendation. Payload token usage keeps the
  notices reproducible for validated cached responses; live billing counters would break replay keys.
- Explain relevant-source traversal, pagination and timely submission in the general prompt. Clarify
  that ACCOUNT/ITEM are the agent's interpretation of customer language, not enum words the customer
  must use, and that missing evidence does not mean NON_DELETION. This is generic guidance, not a
  sample-specific label or resource rule.
- Stop describing UNRESOLVED as NON_DELETION in verifier feedback. It still blocks publication and
  requires a disclosed fallback. No authorization, ownership, evidence or policy constraint is relaxed.

Four new offline regressions cover budget-responsive submission, rejection under pressure, unchanged
admission refusal and accurate unresolved-authorization feedback. New tests failed before the fixes.
The existing live-to-cache replay test now includes nonzero token usage. Complete suites: **362 tests
pass on Python 3.11 and 3.14**, including both provider suites. Ruff and diff checks pass; all 13
fixtures match exactly. Replaying the six frozen real responses makes zero network calls and yields
an identical fallback with both proposals still rejected. This response replay is not a fresh model
evaluation of the final prompt. Prompt/scope-feedback changes after the paid retry remain untested
live; no accuracy gain is claimed for them.

The original root output, datasets, `.env`, historical caches/runs and 1,371 protected hashes remain
unchanged. All new requests, raw responses, validated used-response caches, traces, scoring results,
ledger and runtime wrappers are in ignored `.runs/agent-live-pilot-jb8dtr63/`. `log.txt` is append-only
and remains uncommitted. Verified changes are committed on main. After reviewing the pilot status, the user explicitly
authorized pushing them.

## Pilot conclusion

Roar/Haiku executed genuine autonomous evidence retrieval and proposed relevant steps, including a
partial feedback repair. It did **not** produce a valid final audit decision. The deterministic
baseline is stronger on this tested case. Do not expand to more cases based on this pilot. A future
separately authorized experiment should first demonstrate valid authorization and final submission
on this same small control, and measure the final scope/budget-feedback prompt before broader use.
