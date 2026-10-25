# Cloud handoff: agent-first root causes and feedback-repair fixes

**Repository:** `Cyril-36/ghost-data-still-exists`
**Branch:** existing `main` only
**Handoff base:** `68132bea4161da211c230313bc43237d5f421756`
**No new branch/worktree or paid API call was used for this handoff.**

## Read these files first

1. [`AGENTS.md`](../../AGENTS.md) — repository contract, append-only `log.txt` requirements, safety boundaries.
2. [`agent_first_diagnostics/README.md`](agent_first_diagnostics/README.md) and [`sample32_summary.json`](agent_first_diagnostics/sample32_summary.json) — sanitized, per-case diagnostics for all 32 cases and every proposal.
3. [`AGENT_FIRST_SAMPLE32.md`](AGENT_FIRST_SAMPLE32.md) and [`agent_first_sample32_metrics.json`](agent_first_sample32_metrics.json) — frozen benchmark protocol, aggregate metrics, call and cost accounting.
4. [`AGENT_FIRST_ARCHITECTURE.md`](AGENT_FIRST_ARCHITECTURE.md) — model/verifier authority boundary and architecture status.
5. [`../ghostaudit/agent_first.py`](../ghostaudit/agent_first.py), [`../ghostaudit/agent_protocol.py`](../ghostaudit/agent_protocol.py), [`../ghostaudit/casefile.py`](../ghostaudit/casefile.py), [`../ghostaudit/tools.py`](../ghostaudit/tools.py) — implementation changes in this handoff.
6. [`../tests/test_agent_root_cause.py`](../tests/test_agent_root_cause.py) — synthetic regressions, especially the missing-resource feedback repair test.

The preserved full trace is local and ignored at `.runs/agent-sample32-cckl3ldg/`; it is intentionally not pushed. The compact package above was extracted from it using a field whitelist. It contains no request text, literal evidence, provider payloads or raw model cache. The package's post-inference expected-step counts are diagnostics only and are not read by inference.

## Architecture and status

The selectable agent-first mode remains separate from the existing deterministic baseline. The model interprets the request, searches and inspects evidence, selects resources and authors the full proposed status and ordered step list. Deterministic code remains responsible for validating evidence provenance and cutoff, ownership, scope, category, policy, retention, citations, output contracts and CSV serialization. The verifier can reject proposals and provide findings for another model round. A rejected or unavailable proposal produces an explicit safe fallback; the deterministic planner does not rewrite the model's semantic decision.

The implementation has a real tool-calling live path behind explicit opt-in, cost/call budgets and response caching, but it was not exercised live after these changes. The deterministic baseline remains available and unchanged in its authority. No success claim about new live-model accuracy is supported by the offline work below.

## Original 32-case result

The frozen paid benchmark is documented in `AGENT_FIRST_SAMPLE32.md`. It processed all 32 public samples using Roar `claude-haiku-5-5` native tool calls; 30 cases were fresh attempts and two reused prior successful cached pilot cases. Public labels were scored only after inference.

| Metric | Fresh deterministic baseline | Agent-first |
|---|---:|---:|
| Exact rows | 1/32 (3.13%) | 1/32 (3.13%) |
| Status accuracy | 27/32 (84.38%) | 22/32 (68.75%) |
| Step precision | 136/137 (99.27%) | 12/12 (100%) |
| Step recall | 136/193 (70.47%) | 12/193 (6.22%) |
| Expected cited steps matched | 54/75 (72.00%) | 4/75 (5.33%) |
| Verified agent decisions | — | 4/32 |
| Disclosed safe fallbacks | — | 28/32 |

The agent proposals that did verify were case_05 (3/3 steps, exact), case_06 (3/4), case_07 (3/4), and case_08 (3/4). Emitted-step precision is not comparable to completeness: the agent emitted only 12 steps total. Although its final status was INCOMPLETE for every row, only four rows were independently verified. The full per-proposal data, pre-verification matches and rejection codes are in the sanitized JSON.

## Root cause across all 28 fallbacks

The main result is not a single verifier defect. The trace shows a combination of model decision errors, weak discovery/retry affordances, context and serialization overhead, provider failures, and legitimate evidence/policy blockers. Sixty proposals were submitted: four accepted and 56 rejected. The 28 fallbacks had 25 `CASE_BUDGET` stops and three `MODEL_UNAVAILABLE` stops; only four cases completed with verified proposals. Eight configured rounds did not mean eight affordable model calls: repeated request, source inventory and tool-result context consumed the per-case token ceiling early. Across rejected proposals, the trace reported integrity/order/coverage issues in 41 proposals, unresolved authorization in 20, omitted independently known resources in 16, unproven policy conditions in 16, unshown witnesses in 14, policy/action mismatches in 14, unsupported associations in 12 and unknown category/policy in 10. These groups overlap.

| Case | What the trace established | Primary cause / safety interpretation |
|---|---|---|
| case_01 | Three proposals; identifier searches returned no usable match; no source inspected. | Model emitted placeholder resources and unsupported ownership/policy claims; `CASE_BUDGET`. Genuine absence of evidence must remain unresolved. |
| case_02 | Repeated search/submit loop; no proposal matched expected steps. | Request interpretation and incomplete/invalid step sets; unsupported resource/policy assertions. `CASE_BUDGET`. |
| case_03 | Some evidence was searched and one source inspected; proposed citation/association did not verify. | Model did not turn inspected evidence into supported ownership/citation and omitted a resource. `CASE_BUDGET`. |
| case_04 | Identifier searches but no inspected source; repeated incomplete proposals. | Model proposed a decision without source-backed steps, then hit token/call pressure. |
| case_09 | Some expected steps appeared, but rejected proposals had order/integrity issues and unshown witnesses; restore treatment was wrong/incomplete. | Structural proposal defects and policy mismatch; missing complete remediation/verification grouping. Not fixed by bypassing policy. |
| case_10 | Two expected steps were present in proposal, but request authorization remained unresolved. | Model request-classification failure blocked valid evidence work; verifier correctly refuses to treat unresolved authorization as permission. |
| case_11 | Most steps matched (12/14) but an omitted resource and retention condition blocked verification; one attempt mismatched policy. | Mixed: proposal completeness issue plus missing retention policy proof. The retention rule is a legitimate block, not an overstrict verifier defect. |
| case_12 | Search/inspection occurred, but proposals omitted relevant resources and included unsupported association/policy. | Model failed to ground account/profile coverage; missing evidence cannot be replaced by numeric-ID inference. |
| case_13 | Six to seven expected steps matched, but remaining resource/condition and retention requirements were not established. | Near-complete semantic proposal still not safely verifiable; retention remains a genuine blocker absent evidence. |
| case_14 | Evidence search and inspection ran, but malformed tool response/schema repair ended as `MODEL_UNAVAILABLE`. | Confirmed protocol/repair exhaustion defect; not evidence that the proposed answer was wrong or right. |
| case_15 | Three expected steps matched; required resource omitted and a resource was invented; retention remained unproven. | Mixed omission/model grounding errors and a genuine policy evidence gap. |
| case_16 | Multiple evidence tools ran; proposals had missing resource coverage, unshown/unsupported citations and association errors. | Tool result/citation workflow and proposal coverage errors; possible retention constraints remain independently applicable. |
| case_17 | Two expected steps appeared in one attempt, but other attempts omitted resources and included unsupported resources/policy or witnesses. | Model did not convert retrieval into a complete ownership-grounded plan; order/coverage errors compound the misses. |
| case_18 | Agent alternated unresolved authorization and a deletion proposal with policy mismatch and unshown evidence. | Model misunderstood request/authorization; the verifier's scope and evidence checks are required. |
| case_19 | Three expected steps matched in proposals, but other required resources were omitted and retention was unproven. | Proposal coverage plus genuine policy evidence gap; some semantic work was correct before verification. |
| case_20 | Only request interpretation and identifier search completed; provider timed out. | Transport failure, not a verifier defect. No retry was made under the frozen budget. |
| case_21 | Multiple proposals had unresolved authorization or incorrect policy operations; no expected steps matched. | Model decision/policy reasoning failure and incomplete plan; no evidence to support relaxing policy. |
| case_22 | Several expected steps were present, but cited SQL evidence was not fully shown and policy/coverage gaps remained. | Confirmed tool UX issue: clipped long source lines were not clearly marked as previews; omission and retention gaps also matter. |
| case_23 | Request interpretation and search ran; provider timed out. | Transport failure, not a verifier defect. No retry was made. |
| case_24 | Correctly identified account/search records in some proposals, but witness was unshown, resource omitted, or fabricated in alternatives. | The central feedback deadlock: verifier identified a missing resource, but evidence tools allowed searching only already retrieved handles. Also clipped/unshown citation handling. |
| case_25 | Unresolved authorization and a policy-mismatched proposal after limited evidence work. | Model's request interpretation and policy choice were weak; do not authorize a non-deletion request based on the agent's claim. |
| case_26 | Many SQL/timeline tool calls, no matching steps, missing resources and unresolved authorization. | Inefficient repeated/incorrect tool queries consumed budget; missing ownership/category cannot be inferred. |
| case_27 | Agent retrieved some evidence, but omitted active flow/resource coverage and lacked a supported citation. | Tool discovery and plan completeness; the verifier correctly requires evidence for a flow finding. |
| case_28 | Repeated unresolved/unsupported non-deletion decisions and status mismatch; no matching steps. | Model misclassified request semantics. Authorization cannot be loosened to accept guessed deletion intent. |
| case_29 | Partial expected step match, but several resources were omitted and citations/ownership were unsupported or unshown. | Historical identity/resource coverage and evidence retrieval gaps; every claimed resource still needs independent linkage. |
| case_30 | Model proposed account-wide scope despite an item-targeted request; verifier emitted `SCOPE_EXCEEDED` and ungrounded target findings. | Correct safety rejection: the evidence/request did not authorize broad deletion. Do not broaden item authorization. |
| case_31 | One of three expected steps matched; policy mismatches and unproven retention condition blocked the rest. | Mixed incompleteness and legitimate security/audit retention uncertainty; no evidence authorizes removing retained audit records. |
| case_32 | Partial expected steps but scope target was ungrounded and policy treatment mismatched. | Model misread a constrained tombstone/anonymization request. Preserve stable subject reference and apply only evidence-backed direct-identifier treatment. |

The diagnostic JSON records each attempt's exact rejection-code counts, the proposal's pre-verifier expected-step overlap, tool names/counts, source/line retrieval counts, calls/tokens and stop reason. It omits raw reasoning, literal evidence and request text by design. Case-specific notes above summarize the retained raw trace without treating expected answers as production logic.

## Confirmed implementation defects and changes

These are code/interface defects confirmed by the captured traces and exercised with synthetic scripted regressions. They do **not** establish a new model's live improvement.

1. **Verifier-feedback deadlock.** A verifier could name an omitted resource, but identifier search and activity lookup rejected it unless it had already been retrieved. Finalization pressure could also force `submit_audit` before an evidence repair turn. A verifier hint now enters a separate `lookup_handles` discovery-only set; a single bounded repair window is allowed after evidence-related rejection when round/tool capacity remains. Search results do not create ownership evidence. The independent verifier still checks ownership, category, source eligibility, timestamps, cutoff, policy and citation. The synthetic test patches planner/replanner to fail if called and verifies the agent can retrieve, correct and resubmit.
2. **No efficient source catalog.** The opening message serialized the whole eligible-source manifest repeatedly, adding roughly 15–17 KB to every request while not establishing case membership. It now gives compact counts by content kind and exposes paginated `list_sources` metadata-only browsing. Metadata cannot create resource handles, facts or ownership.
3. **Ambiguous truncation.** Long search hits were clipped to previews, and long inspected lines were clipped without line-level completeness information. Search matches already carry `text_complete`; inspected lines now carry it too. A clipped line is not registered as citable. This makes the limitation visible to the agent without weakening source/citation validation.
4. **Witnesses required redundant literal copying.** When an agent cited a source and locator already retrieved in full, it had to copy the exact text again; otherwise it could fail despite the evidence being visible. A missing literal may now be resolved only from that already retrieved full span. Supplied literals still have to quote the cited span and name the resource (or satisfy the absence rule); unseen/clipped text remains uncitable. Trace records the resolved literal and origin.
5. **Step number/order brittleness.** Valid steps could be rejected because the model numbered/order-serialized them incorrectly or attached a witness to its original step number. A narrow protocol normalizer sorts/renumbers unchanged steps and remaps witnesses through unique original IDs. It rejects duplicate/conflicting steps, duplicate IDs and orphan witnesses. It never changes decision, status, resource, operation, state, evidence or adds a step.
6. **Malformed initial request tool call advanced the phase.** An invalid first `interpret_request` response could accidentally consume the request-first slot. The phase now advances only after valid interpretation. Exhausted schema correction is recorded as `SCHEMA_REPAIR_LIMIT`, not misreported as provider unavailability.
7. **Tool schema constraints and case evidence fields.** Proposal phase/operation/state are enumerated from the contract, while the verifier remains authoritative. Timeline evidence now returns category, exposure, enabled/restore state, retention and references already available in eligible observations, making the agent's evidence view more complete without bypassing source ownership or cutoff checks. This prevents avoidable invalid tool output and gives the agent structured retrieval details without selecting an answer for it.

The fixes are covered by `test_agent_root_cause.py`; the full offline test result below is against the new code. The pre-fix live case traces are forensic evidence only; old cached responses are not a valid measurement of new prompting/tool behavior.

## Legitimate blocks that must remain

- An absent/unproven retention period for security/audit logs is a policy gap, not permission to delete. Several cases correctly remain unverified until eligible evidence establishes the retention treatment.
- Resource mention, matching identifiers in a manifest, or verifier feedback does not establish ownership. No numeric/user-suffix mapping may be invented.
- Item-scoped requests do not authorize account-wide deletion. A resource found under a different category still needs category-matching evidence.
- Evidence captured after `audit_at`, unshown lines, clipped previews, claims in activity logs, unknown categories, unsupported states and policy mismatches remain invalid.
- A model's `DELETE` interpretation does not itself establish customer authorization, and ambiguous/non-deletion requests must not be coerced into deletion.

## Offline validation and exact reproduction commands

Freshly verified in this handoff:

| Check | Result |
|---|---|
| Full unit suite, Python 3.11.14 | **382 tests passed** (loopback provider tests require localhost bind permission) |
| Full unit suite, Python 3.14.7 | **382 tests passed** (same loopback requirement) |
| TokenBazaar + Roar provider compatibility | **31 tests passed** |
| Ruff and `git diff --check` | **passed** |
| Synthetic fixtures | **13/13 generated**; targeted fixture/pipeline tests **20/20 passed**, including every fixture end-to-end |
| Sample-32 captured-cache replay, Python 3.11.14 and 3.14.7 | **0 network calls**, but **0 cache hits / 32 fallbacks / output differs** under changed cache keys/tool protocol |

Reproduce the local-only replay diagnostic (requires the preserved ignored run) with `/opt/homebrew/bin/python3.11 code/evaluation/agent_first_diagnostics/replay_local_sample32_no_network.py` and `/opt/homebrew/bin/python3.14 code/evaluation/agent_first_diagnostics/replay_local_sample32_no_network.py`. The original sample-32 cache was copied into a temporary directory for replay; the preserved `.runs` cache and original results were not edited. New source inventory/tool schemas changed request cache keys, so no captured response matched. The gateway's network methods were patched to raise, confirming no network fallback. This is a cache compatibility observation, not a successful replay and not a test of live behavior. Do not report the old run's previous 168-hit replay as validation of these fixes.

Commands:

```sh
# Full offline suite (on this host, localhost binding needs permission for loopback tests)
env -u TOKENBAZAAR_API_KEY -u ROAR_API_KEY python3.11 -m unittest discover -s code/tests
env -u TOKENBAZAAR_API_KEY -u ROAR_API_KEY python3.14 -m unittest discover -s code/tests

# Provider compatibility suites (same loopback permission requirement)
env -u TOKENBAZAAR_API_KEY -u ROAR_API_KEY PYTHONPATH=code/tests python3 -m unittest test_provider_compat test_providers_dual -v

# Synthetic fixture generation (no API calls)
env -u TOKENBAZAAR_API_KEY -u ROAR_API_KEY python3 code/evaluation/main.py fixtures --out /tmp/ghost-agent-handoff-fixtures

# Targeted new regressions and style
python3 -m unittest discover -s code/tests -p 'test_agent_root_cause.py' -v
python3 -m unittest discover -s code/tests -p 'test_fixtures.py' -v
/opt/homebrew/bin/python3.11 code/evaluation/agent_first_diagnostics/replay_local_sample32_no_network.py
/opt/homebrew/bin/python3.14 code/evaluation/agent_first_diagnostics/replay_local_sample32_no_network.py
ruff check code
git diff --check
```

The protected workspace inventory (`.env`, caches, datasets, root `output.csv`, historical runs and prior recovery data) was hashed before implementation. Validate it again after the commit; no tool used here writes to those paths. The raw request/response traces, caches and `.runs/` remain local.

## API spending and limitations

The 32-case campaign used a conservative cumulative ceiling of **₹100**. It ended at **₹70.86048** estimated conservative exposure: prior pilot ₹14.38012 plus this batch ₹56.48036; batch catalog estimate was ₹24.68792. Provider-reported actual billing was unavailable; estimates are not invoices or guarantees. This leaves ₹29.13952 below the campaign ceiling mathematically, but that campaign is closed and **no further spending is authorized by this handoff**. A separate earlier 177-event campaign remains recorded at 8,947.98576 paise conservative exposure; it was not reset, hidden, or merged into this 32-case campaign. No live call was made for these fixes.

## Next engineering steps, by expected impact

1. **Test live behavior only after separate budget authorization.** Freeze the code and protocol, make one bounded experiment on a handful of cases that previously failed for discoverability/serialization (e.g. cases 16, 22, 24, 29), with strict call/token ceilings and no auto-retry after transport failure. This is a targeted regression cohort, not a held-out generalization result; report it as such. Do not use the remaining campaign headroom without new approval.
2. **Reduce conversation growth across rounds.** Measure repeated message bytes by role and implement safe bounded summaries of already returned tool data, preserving source IDs, locators, cutoff metadata and verifier feedback verbatim enough for correction. Add exact replay tests and prove cache key/version separation.
3. **Add verifier feedback to tool affordances systematically.** Convert supported resource hints to discovery-only handles, but add tests for each finding family and prove a hint cannot establish association, state or policy. Keep one bounded repair turn/window and disclose budget exhaustion.
4. **Improve coverage planning without taking semantic authority from the model.** Give the agent concise policy/category reference tables and verifier gap lists; the model still chooses status/steps. Expand adversarial tests around retention, tombstones, cross-category copies, item/account scope and later-source cutoffs.
5. **Measure on a clean evaluation design.** Keep public sample labels outside inference; reserve a cohort not used for prompt/tool iteration or use a synthetic, independently authored benchmark. Compare exact rows, status, step precision/recall, citations, hallucination proxy, verifier rejection and fallbacks to the deterministic baseline. Do not infer live quality from scripted tests or stale cache replay.

## Handoff boundary

Claude Cloud should continue on existing `main`, inspect this report and the sanitized diagnostics first, then review the focused implementation/test diff. Do not restart paid experiments from local traces. All findings above are based either on the recorded old run or the new offline scripted tests; **actual post-fix model accuracy remains unverified**.
