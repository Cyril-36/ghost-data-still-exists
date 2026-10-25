# Agent-first review follow-up — 2026-10-25

Starting revision: `8779251`, existing `main`. This supersedes the offline-only integration limits
in `AGENT_FIRST_ARCHITECTURE.md`; its original measurements remain historical evidence.
No paid calls, pricing requests, new branches, worktrees or push. The prior paid campaign stays closed.

## Confirmed findings and changes

1. **Live integration was absent**, intentionally disabled in the original prototype. An in-memory
   reproduction of the starting `audit.py` rejects `agent_live=True` before executing inference.
   The CLI now separately selects `--agent-mode live`; replay remains the default. Live requires
   `--allow-agent-live`, an explicit model, finite positive total budget and per-call reservation,
   positive call limit, and an already passed provider/model tool-call probe. There is no automatic
   paid probe, catalog fetch, or transport/rate-limit retry. Both TokenBazaar and Roar use the existing
   Gateway, response validation, cache and accounting. Mock regressions cover admission refusal,
   exhaustion, transport failure, cache reuse, both profiles and CLI preflight. Failure produces a
   disclosed empty INCOMPLETE answer, never a baseline planner replacement.

2. **Item scope rejected legitimate parent verification.** A synthetic search-result request with
   independently owned, already-absent parent profile was rejected by both category and single-item
   checks. The verifier now permits only an `account_service` USER_PROFILE
   VERIFICATION/VERIFY_RECORD_ABSENT/ABSENT check alongside SEARCH_INDEX_ENTRY item authorization.
   Ownership must be independently established by direct evidence or a grounded reference, not a
   not-found query input. Eligible category-matching absence evidence and all-copy checks must pass.
   The agent must retrieve a matching literal witness even when the INCOMPLETE CSV contract requires
   an empty evidence_id. CSV fields are preserved unchanged; the witness remains in diagnostics.
   Parent deletion, present parents, unrelated categories, lookup-only ownership and missing witnesses
   remain rejected. This exception grants no parent modification authority.

3. **Whole-request question regex and English-only non-deletion gate were over-restrictive.**
   Incidental questions and existence conditions rejected actual account/item removal instructions;
   the baseline English classifier also incorrectly authorized a capability-only question. The new
   check examines deletion-bearing clauses, preserves operative instructions and requested inspection
   of prior removal, and rejects capability questions and embedded/quoted counterfactuals. Replay caught
   an intermediate regression in prior-removal inspection; a further synthetic failing/passing test
   fixed it before completion. Explicit preservation, item limits and contradiction guards remain.

   An uncertain English classifier no longer vetoes every multilingual non-deletion proposal. Optional
   `--authorization-model` uses a distinct model and request-only context, with no primary rationale,
   steps or labels. Confirmation requires NON_DELETION, a full verbatim request quote, non-conflict and
   confidence >=0.9. The deterministic classifier's explicit deletion decision cannot be overridden
   through this confirmation path. Unavailable, conflicting, low-confidence or malformed confirmation
   fails closed. The reviewer never authors status or steps; the primary proposal remains authoritative
   after successful independent validation. Responses, model identity and review findings are logged.

The initial ten focused authorization tests reproduced six failures before fixes. The final focused
file has 15 tests; the new mock-live file has 15. Existing tests still enforce planner independence,
retention, ownership, category-matching absence, source cutoffs and retrieved witnesses.

## Live controls and readiness

The CLI requires all of these separately from baseline paid-run flags:

- `--decision-mode agent-first --agent-mode live --allow-agent-live`
- `--agent-model MODEL` with a previously passed provider/model entry in the existing probe registry
- `--agent-budget-paise BUDGET --agent-call-reserve-paise RESERVE --agent-max-calls LIMIT`
- fresh output/run paths; credentials loaded only through explicit `--env-file` after live preflight

Do not run this mode until a new paid experiment is separately authorized. No such authorization is
in force for this review. Jev remains optional; general-purpose native tool-calling models can be
selected through either existing provider profile. Actual Gemini/Claude model IDs and availability
have not been queried or newly probed.

Primary reasoning, optional media and independent authorization review share one run ledger. Primary
and review also respect the remaining per-case token/cost allowance. Repeated confirmation of an
unchanged request is memoized. The CLI copies the input cache; validated used responses persist in
`RUN/model_cache/`, which can be passed as a later `--agent-cache`. Original caches are unchanged.

Reservations bound **admitted** spending; a provider may bill more than an estimate/reservation after
completion. This is not a hard invoice guarantee. Future paid testing needs a conservative explicit
reservation and a provider-side cap. Request-only model separation is useful independence, not a
proof of multilingual correctness; the confidence threshold is not calibrated. Real-model tool use,
reasoning quality, multilingual agreement, invoice reconciliation, latency and end-to-end live
accuracy remain unverified. No accuracy improvement is claimed from mocked responses.

## Verification results

All runs stripped API-key environment variables and blocked external socket connections. Provider
HTTP tests used loopback servers; live integration replaced the transport with mocks. Synthetic
charges in those tests are accounting inputs, not money spent. **Actual new spending: zero.**

| Check | Python 3.11.13 | Python 3.14.7 |
|---|---:|---:|
| Complete unittest discovery | 358 passed | 358 passed |
| Dedicated TokenBazaar/Roar compatibility suites | 40 passed | 40 passed |
| Synthetic fixtures, exact rows | 13/13 | 13/13 |
| Fresh offline full dataset | 518 rows, 0 calls | 518 rows, 0 calls |
| Cached staged full dataset | 518 rows, 533 hits, 0 calls | 518 rows, 533 hits, 0 calls |
| Agent-first public replay | 32 disclosed fallbacks, 0 calls | 32 disclosed fallbacks, 0 calls |

Ruff and `git diff --check` pass. Both versions produce byte-identical fresh baseline outputs and
agent replay outputs, including with a different hash seed. Fresh full/sample baseline CSVs match
starting `8779251` exactly. Cached staged output remains byte-identical to preserved root output.
The 32-row fresh offline baseline remains 27/32 status accuracy, 136/137 step precision, 136/193
recall and 1/32 exact rows. Historical public caches still contain no compatible primary proposals;
agent replay is therefore an availability/fallback check, not a measurement of live agent accuracy.
No new public citation, hallucination or rejection-rate improvement is inferred from zero proposals.

All 1,371 protected file hashes (datasets, .env, root output, existing caches and historical artifacts)
match the preservation inventory. Three recovery stashes are retained. Original log bytes are
preserved and the required turn entry appended; log.txt remains uncommitted.

Reproduction logs and fresh run artifacts are preserved under ignored
`.runs/review-agent-live-vh2hzbrv/`. Full-dataset replay work products are under the task's temporary
`ghost-complete-audit.joqLwT/agentreviewfinal311` and `agentreviewfinal314` folders. This change is
committed locally for review; nothing is pushed.
