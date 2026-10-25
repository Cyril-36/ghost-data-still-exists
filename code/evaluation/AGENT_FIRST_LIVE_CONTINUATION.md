# Agent-first continuation: two verified live outputs

2026-10-25. Continue existing main from `c8e12c9`. The user reopened paid testing for the two
pilot cases and authorized another ₹10: **the cumulative pilot cap became ₹20 (2,000 paise)**.
No prior exposure was reset. The earlier 8,947.98576-paise campaign remains closed and unchanged.

**Result:** Haiku now investigates and authors verifier-accepted outputs for both pilot cases.
Case_05 matches the public expected row exactly. Case_07 matches three of four expected steps.
Both final statuses are correct; neither selected output is a fallback. This repairs the earlier
failure to submit valid answers, but does not establish superiority over the deterministic baseline.

## Changes that produced the successful configuration

1. Optional `--agent-request-first`: the **same primary agent** first calls `interpret_request`
   with its own interpretation of permission. This turn receives the request before the large
   evidence inventory. The full evidence context then arrives and the agent chooses evidence tools.
   Its initial interpretation is recorded, not approved by code or copied into the final answer.
   The agent may revise it; tests prove that an initial DELETE never overwrites a final UNRESOLVED.
   No deterministic planner or multilingual classifier authors this interpretation.
2. Optional `--agent-finalize-on-pressure`: request `submit_audit` before the final turn or when
   the remaining conservative context allowance threatens to leave no submission/repair turn.
   This controls the execution boundary, **not authorization, status, resources, operations or steps**.
   A final proposal with unsupported evidence or unresolved permission still fails. Tool, round,
   token and case-cost limits were not increased to obtain the successful outputs.
3. Describe scope fields directly in the native tool schema. ACCOUNT requires `target=""` and
   `whole_category=false`; resource IDs belong in steps. Previously Haiku chose DELETE correctly
   but put an account description in target, then misread terse feedback as denial of permission.
   Field-specific feedback now distinguishes encoding errors from permission decisions.
4. Clarify that missing compliance evidence is not uncertain customer permission. DELETE-rule
   violations use REPORT_REMAINING_RECORD; exposing PII does not justify a duplicate finding for
   the same deletable record. These are general contract rules, not public case labels or IDs.

Default behavior stays unchanged unless the two flags are selected. All semantic decisions remain
agent-authored. The verifier still enforces ownership, scope, sources, cutoff, copies and retention;
it accepts or rejects proposals without silently repairing them. No public answer is supplied to
inference, and expected rows are read only afterward for scoring.

## Selected real-model results

Provider/model: Roar `claude-haiku-5-5`, using the existing successful native tool-call probe.
Live CLI flags included explicit opt-in, remaining run budget, 50-paise reserve, eight-call cap,
eight rounds, 40 tools, 120,000 case tokens and 4,000 completion tokens per turn. Jev and paid
screenshot reading remained off. Planner/replanner functions were guarded to raise if called.

| Metric | case_05 | case_07 |
|---|---:|---:|
| Independently verified final proposal | Yes | Yes |
| Final fallback | No | No |
| Exact row | 1/1 | 0/1 |
| Status correct | 1/1 | 1/1 |
| Correct steps / expected | 3/3 | 3/4 |
| Step precision | 3/3 | 3/3 |
| Expected finding citations matched | 1/1 | 1/1 |
| API calls / tool calls including interpretation and submissions | 5 / 6 | 6 / 7 |
| Proposals accepted / rejected | 1 / 0 | 1 / 1 |
| Feedback turns | 0 | 1 |
| Input / output tokens | 59,767 / 1,365 | 74,974 / 1,990 |
| Transport latency | 10.051 s | 12.930 s |

Case_05 retrieved the surviving failover profile, proved its subject association and submitted the
correct finding, deletion and future verification. In case_07 Haiku inspected the support PDF,
retrieved its resource timeline, then corrected rejected authorization and an incomplete step group
in response to verifier feedback. It ultimately authored the supported support-record deletion plan.
The verifier did not generate those steps for it.

Combined selected outputs: **1/2 exact rows, 2/2 statuses, 6/6 step precision, 6/7 recall, 2/2 finding
citations**. This equals the fresh deterministic baseline on these two cases. The measurable gain
is over the earlier failed agent configuration: two verified decisions now exist, rather than empty
fallbacks. These are selected pilot runs after iterative development, not held-out accuracy or a
reliability estimate. Only these two case IDs were run; no automatic 32/518-case model evaluation.

The missing case_07 expected step is future absence verification for `account_007`. Eligible
structured evidence names it in an isolated DELETE_RECORD SUCCESS claim without a subject link.
The investigation did not establish ownership from independent eligible evidence. A numerical
similarity to `user_07` is not a legitimate association. The step was not fabricated to match a
public label; another source might establish the link, but none was demonstrated in this pilot.

## Experiments, failures and rollback

The scope-prompt-only run still searched without submitting. A submission-window run submitted but
mixed authorization and completeness. Request-first then obtained DELETE, but exposed the target
encoding error and duplicate PII finding. The final schema/feedback configuration verified both cases.
All unsuccessful traces, proposals and charges are preserved.

A subsequent activity-detail search experiment exposed an ownership-only catalog link for a storage
object. Haiku promoted that link to current presence without state/category proof, omitted the PDF
resource and failed verification. It was **reverted**, along with its experimental tests, which are
archived in the ignored pilot directory. The selected configuration and both successful output caches
are restored and replay consistently. This is not permission to ignore activity evidence: direct
source/timeline tools remain available, while broader typed retrieval needs a separately validated
approach that keeps identity links distinct from state. No safety gate was weakened for the scores.

## Cumulative spending: closed below the expanded ceiling

| Item | Paise |
|---|---:|
| Authorized cumulative pilot ceiling after explicit increase | 2,000 |
| Admission stop, retaining a margin | 1,900 |
| Total conservative exposure, all pilot attempts | **1,438.012 (₹14.38012)** |
| Roar token/catalog estimate at assumed ₹100/USD, all pilot attempts | **671.725 (₹6.71725)** |
| Failed TokenBazaar probe included in conservative exposure | 25 |
| Actual billing | **Unavailable** |

Totals include **all** pilot attempts, including the first ₹10 phase: 46 successful Roar calls and
one failed TokenBazaar probe, 605,395 reported input and 13,266 output tokens. Unknown failed-call
exposure was retained. Previous claims that the catalog was reachable were not treated as inference
availability. Roar did not report charge headers; missing billing is not a zero charge.

Before every POST the independent persistent ledger reserves twice a conservative byte-as-token
catalog bound, full output allowance and any long-context multiplier, with a 25-paise minimum.
Successful estimates are settled with a 2x margin and floor; failures retain the full reservation.
Catalog conversion and estimates are assumptions, not guaranteed invoices. Actual account billing
and provider-side spending caps remain unverified. The ledger is closed; no further paid runs.

## Verification, saved outputs and next limit

Eight new tests cover request-first semantic independence, finalization without evidence relaxation,
agent repair of field encoding, and default dynamic tools. **370 tests pass on Python 3.11 and 3.14**,
including provider suites. Ruff, diff checks and all 13 fixtures pass. Selected real responses replay
with **zero network calls**, five/six cache hits and byte-identical accepted outputs. Historical caches,
`.env`, datasets, root output and all 1,371 protected hashes remain unchanged.

All traces, budgets, unsuccessful experiments and successful caches remain in
`.runs/agent-live-pilot-jb8dtr63/`. The selected live runs are `case_05-contract-agent` and
`case_07-contract-agent`; their individual CSVs are combined without semantic edits in
`selected-predictions.csv`, with a source-hash/provenance companion. Do not replace root output with
this two-row pilot file. The earlier report remains an accurate record of the initial stopped pilot.

The two tested options and clear tool-field contract are ready for controlled further evaluation.
They do not prove broader superiority over the baseline. Further paid expansion needs a new explicit
instruction; the current pilot stops here. Verified changes are committed locally, without pushing.
