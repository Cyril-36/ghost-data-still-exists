# Complete repository audit — 2026-10-25

## Outcome and scope

The implementation keeps model authority bounded and final decisions deterministic. The audit
reproduced and repaired 27 groups of execution/correctness defects, plus stale documentation (F28).
It adds 37 regression tests. All 282 tests passed on Python 3.11.13 and 3.14.7 at the time of this audit (288 after the destination-scope follow-up, 5cd8b54); both provider suites,
all 13 fixtures, fresh offline and cache-only 518-case runs also pass. No tests were skipped or
deleted to obtain these results. Ruff passes. This is an engineering audit, not proof that every
possible bug has been found or that the submission matches the hidden answers.

The most consequential repairs protect inputs and completed runs, preserve contradictory-request
authorization, prevent nonfinite spending ledgers, enforce exactly shown evidence, and independently
check missing output resources and surviving untreated copies. Full offline sample exact-row
accuracy remains 1/32. There is no measured live investigator improvement from these fixes.

No paid API calls, model experiments, push, new branch, clone or worktree were performed for this
audit. TokenBazaar and Roar profiles and role assignments remain intact: Roar `jev` for typed
decisions, `claude-haiku-5-5` for screenshots and `gpt-oss-20b` for investigation. The prior live
Roar baseline of 28/32 status, 1/32 exact rows and 141/193 step recall remains historical.

## Git state and preservation

Repository: `Cyril-36/ghost-data-still-exists`, remote
`https://github.com/Cyril-36/ghost-data-still-exists.git`.

Primary checkout:
`/Users/cyril/Documents/HackerRank Orchestrate Oct/hackerrank-orchestrate-october26-main`.

| Checkpoint | Exact state |
| --- | --- |
| Starting local main | `46f22b2465275a06ff9d93fb20e2157e6573e81c`; 17 commits ahead of origin/main; only tracked modification was append-only `log.txt` (+189 lines) |
| Ending verified implementation | `417482d17d7848a6f21dd50804388b4c08fd05a5`, existing local `main`; all implementation changes and regression tests committed |
| Remote main, freshly checked | `fedf680ace24bd6e6c25c38fc3233237effe7a99` |
| Remote Claude branch, freshly checked | `claude/gracious-archimedes-1rba87` at `46f22b2465275a06ff9d93fb20e2157e6573e81c`; no newer Cloud commit |
| Report delivery | This report is committed in a documentation-only child of the implementation commit. Its exact SHA is reported in the final chat and available with `git rev-parse HEAD`; a document cannot embed its own Git commit hash. |
| Ending working changes | Only preserved/appended `log.txt`; no uncommitted implementation changes. Audit evidence is gitignored under `.runs/code-audit-20261025/`. |

All original Claude commits remain ancestors. No history was discarded. The local Claude and
`hardening/phase3-correctness` branches remain available. The earlier workspace cleanup removed
the registered `ghost-data-hardening` and `ghost-data-review` worktrees only after preservation;
this audit did not remove more folders. The unrelated detached `/private/tmp/base_wt` was left alone.

All 1,371 originally inventoried protected files still match SHA-256, including `.env`, datasets,
root and dataset `output.csv`, response caches and historical runs. Historical evaluation reports
were preserved. Existing recovery archives and these stashes remain intact:

- `5866b9d0288e6d15881de062abf5077616fead9a`: primary work before Cloud integration.
- `9228314d36e08732dc2e04efb49a5558b5b58502`: unique unfinished hardening drafts.
- `29158ba9e33cbb4b6fbdd9df56438b055dfa48bd`: review log before worktree removal.

Older overlapping drafts were not reapplied over the integrated Cloud code. Recovery instructions
and byte-verified archives remain in `.runs/workspace-cleanup-20261025T061227Z/`.

## Files and subsystems reviewed

Read the repository `AGENTS.md`, `problem_statement.md`, root and code READMEs, decisions including
the latest review/provider choices, `phase4_plan.md`, prompts and the latest commit diff. Review
covered every production Python module and the evaluation boundary; tests were read alongside
their implementations and extended with synthetic adversarial cases.

| Subsystem | Files inspected under `code/` | Checks and result |
| --- | --- | --- |
| Input, contracts, CLI, publication | `main.py`; `ghostaudit/inputs.py`, `contracts.py`, `output.py`, `audit.py`, `config.py` | CSV schemas, duplicates, timestamps, manifest containment, canonical sorting/tuples, atomic output and diagnostics; F01, F17 |
| Identity and authorization | `identity.py`, `intent.py`, `intent_models.py`, intent prompts | Historical/reused identifiers, graph links/cycles, system-scoped IDs, item/account scope, contradictory requests; F02, F16 |
| Evidence and time | `corpus.py`, `observations.py`, `state.py`; all `parsers/{activity,jsonsrc,logsrc,pdfsrc,records,sqlsrc,textsrc}.py`; `media.py` | Claims versus state, per-copy exposure, omissions, SQL authorizers/coverage, cutoff eligibility, malformed media; F08, F10–F12, F24–F27 |
| Policy and planning | `policy.py`, `planner.py` | Supplied treatments, retention uncertainty, tombstones, dependency ordering, fallbacks; F13; existing regression families retained |
| Investigator and grounding | `casefile.py`, `tools.py`, `investigator.py`, investigator prompt | Tool schemas/IDs/order, bounded results, actual visibility, proposal/submission protocol, feedback, budgets; F04–F09, F16, F23 |
| Independent verifier | `verifier.py` | Ownership traversal, cutoff, stale citations, omitted resources, policy and surviving copies; F08, F13–F16. New copy checks do not call planner state reconstruction. |
| Providers | `providers.py`, CLI integration | Both endpoint profiles, Jev adaptation, model IDs, cache isolation, native tools, usage, retries, redirects/auth, reservations/concurrency; F03, F18–F23 |
| Evaluation | `evaluation/{main,scoring,error_analysis,gap_analysis,fixtures,intent_benchmark,intent_cases,vision_benchmark,vision_ground_truth,scripted_transcript_demo}.py`; historical reports | Label isolation, exact/field/sequence scoring, citation accounting, reproduction provenance; F17; historical results not relabeled |
| Packaging/config/docs | READMEs, `decisions.md`, `pyproject.toml`, lockfile and prompts | Standard-library execution, explicit run modes, preserved model roles, stale instructions; F28 |

No organizer-only truth was used. Public expected labels were loaded only by evaluation/test code,
after inference. No production case-ID exceptions or numerical-ID ownership heuristics were added.

## Confirmed findings and before/after regressions

Paths below are relative to `code/`; line numbers refer to the verified implementation commit.
Every F01–F27 group has a failing-before regression or directly observed failing run. The named
new tests now pass on both Python versions. Multiple closely related symptoms are grouped together;
the grouping is not a count of independently exploitable vulnerabilities.

### P0 — preservation, authorization and unbounded spending

**F01 — Inputs and completed diagnostics could be overwritten.** `ghostaudit/audit.py:88`.
Passing the request CSV, a policy CSV or evidence path as output replaced it with predictions.
Reusing a completed run directory could replace diagnostics before a failed rerun completed; output
could also target `coverage.json`. Three `PublicationSafety` tests in
`tests/test_complete_audit_io.py:14` failed before. Resolve paths and reject input/diagnostic
collisions and nonempty run directories before inference. After: all tested original bytes stay
unchanged and unsafe calls raise `ContractError`. Fresh run directories are now required.

**F02 — A model could waive an explicit account preservation instruction.**
`ghostaudit/intent_models.py:267`. “Please delete my account, but keep my account active.” was
deterministically unresolved, but a model returning account deletion without conflict flags could
authorize it. `AuthorizationBoundary` (`tests/test_complete_audit_intent.py:10`) failed before.
Reconciliation now refuses account-wide authorization for this explicit contradiction; narrower
grounded item scope remains possible. After: the tested contradictory request remains unresolved.

**F03 — NaN/invalid accounting could disable the spending ceiling.**
`ghostaudit/providers.py:418`, `:529`, `:584`. NaN configuration, billed cost or catalog estimates
could enter the ledger, making comparisons against the ceiling false. Before: synthetic NaN
billing/catalog responses allowed a poisoned balance. `AccountingAndCache` tests at
`tests/test_complete_audit_providers.py:50`, `:73`, `:78` failed before. Validate finite nonnegative
configuration/costs/prices; reject invalid billing and charge its reserved amount; treat invalid
catalog pricing as unpriced and reserve accordingly. After: finite accounting remains enforced
and the next call is refused at the cap. Token-count validation is additional defensive hardening.
This does not guarantee an eventual invoice when actual prices are unknown (see remaining risks).

### P1 — evidence, policy, output and reproducible execution

**F04 — Clipped repeated text authorized invisible locators.** `ghostaudit/tools.py:223`, `:291`.
Registration used substring survival in a JSON preview. Forty identical lines let visible text
register an unseen `L40`. `ExactVisibility.test_repeated_clipped_lines_do_not_register_unseen_locators`
(`tests/test_complete_audit_grounding.py:24`) failed before. Clip complete structured items and
register exact `(source, locator, text)` identities only; previews register nothing. After: visible
`L1` is citable, hidden `L40` is rejected.

**F05 — Decoded SQL rows granted visibility to unseen raw statements.**
`ghostaudit/tools.py:406`. A 21-statement raw line with only 20 decoded rows returned let a fact
cite the hidden 21st statement. The SQL hidden-statement test (`test_complete_audit_grounding.py:38`)
failed before. Decoded rows now grant handles, never raw-line citation rights; inspect the raw source
explicitly. After: the same fact is refused before inspection and accepted after its full line is
actually returned. Ambiguous multi-INSERT lines have no inferred locator.

**F06 — SQL locator matching accepted ID prefixes.** `ghostaudit/tools.py:428`.
Searching `fx_row_1` selected the line for `fx_row_10`. The prefix regression
(`test_complete_audit_grounding.py:60`) failed before. Require token boundaries and one unambiguous
single-statement match. After: the correct `L4` is selected and valid explicitly inspected evidence
is accepted.

**F07 — Valid source lines were unnecessarily uncitable.** `ghostaudit/tools.py:338`.
Lines above 300 characters were always previews, even when the full line fit the tool budget.
The positive long-line test (`test_complete_audit_grounding.py:12`) failed before. Return complete
lines that fit the bounded result budget and register them only if the entire item survives.
After: a valid 400-character-plus line can support a fact; longer/clipped lines remain uncitable.

**F08 — Future categories leaked into earlier state classification.**
`ghostaudit/corpus.py:52`, `state.py:75`, `verifier.py:142`. System-wide categories were computed
over all time. A category introduced later could classify an earlier record or prevent an otherwise
unique earlier category. `RecreationExposure.test_future_system_category_does_not_classify_an_earlier_record`
(`test_complete_audit_evidence.py:24`) failed before. Resolve fallback categories with both availability
and observation cutoffs; use the same temporal boundary in the independent verifier. After: future
categories cannot alter the earlier state. One full offline row changes as detailed below.

**F09 — Future or cross-system metadata rejected valid investigator facts.**
`ghostaudit/casefile.py:207`, `:254`. Fact metadata checks used all-time observations/activities and
categories from the same resource ID in other systems. The two-subcase cutoff regression
(`test_complete_audit_verifier.py:40`) failed before. Restrict system/activity/category evidence to
the cutoff and resource category checks to the proposed system. After: an earlier grounded PDF
category remains accepted despite future conflicting metadata.

**F10 — Recreated copies inherited stale clean scans.** `ghostaudit/state.py:113`.
An old clean scan, followed by absence and recreation without a new scan, made a surviving copy
look treated. The recreation regression (`test_complete_audit_evidence.py:32`) failed before.
Exposure history now starts after the latest absence at that location. After: recreated unscanned
data has unknown exposure; cleanliness requires fresh proof.

**F11 — Missing scan counts were mistaken for successful anonymization.**
`ghostaudit/parsers/logsrc.py:116`. Missing/invalid `direct_identifiers` became zero; even a visible
email could be overridden by a reported zero. Both `IncompletePiiScans` tests
(`test_complete_audit_evidence.py:45`) failed before. Missing, invalid and negative counts now mean
unknown exposure, while explicit identifiers take precedence and a valid zero stays clean.
After: the tested incomplete scans cannot prove anonymization.

**F12 — Partially skipped SQL tables still proved exhaustive absence.**
`ghostaudit/parsers/sqlsrc.py:146`. A JSON-in-SQL row with inconsistent resource IDs or an unknown
collection was skipped, yet the remaining table generated a `FULL_TABLE` scope. The inconsistent-row
test (`test_complete_audit_evidence.py:63`) failed before. Keep valid positive observations but
withhold exhaustive scopes for a table with rejected rows. After: malformed rows cannot silently
remove a resource through omission inference.

**F13 — Conflicting policy rows silently selected the last action.**
`ghostaudit/policy.py:54`, `verifier.py:149`. A warning did not stop duplicate category rules from
selecting the last row. The duplicate-rule regression (`test_complete_audit_evidence.py:88`) failed
before. Track conflicts and refuse treatment; the independent verifier rejects the category too.
After: deletion cannot be selected by CSV ordering when supplied rules conflict.

**F14 — Retained obligations hid missing output steps.** `ghostaudit/verifier.py:315`.
Removing every output step for an owned resource while keeping its internal obligation was accepted
as covered. `MissingOutputCoverage` (`test_complete_audit_verifier.py:18`) failed before. Coverage
now requires emitted steps or an explicit unresolved obligation. After: the mutated plan is blocked
with `OMITTED_RESOURCE`.

**F15 — A clean primary could verify an untreated replica.** `ghostaudit/verifier.py:274`.
Independent cross-copy checks existed for absence but not redaction/anonymization. A synthetic
COMPLETE tombstone plan with a clean primary and exposing replica was accepted. `TreatmentCopies`
(`test_complete_audit_verifier.py:57`) failed before. Independently inspect surviving copies,
eligible omissions and scans after recreation. After: the mutated plan is blocked with
`REPLICA_DISAGREEMENT`; no planner reconstruction function is reused.

**F16 — Unindexed identity tokens crashed set arithmetic.**
`ghostaudit/casefile.py:151`, `verifier.py:137`. The default `()` was subtracted from a set when a
token had no owner entry. A full copied-cache run reproduced the crash, and the explicit unindexed
token test (`test_complete_audit_verifier.py:29`) failed before. Use an empty set default. After:
the synthetic checks and all 518 cached staged cases complete; unknown identifiers are not assigned
to another person.

**F17 — Gap analysis could attribute changes to the wrong evidence/output.**
`ghostaudit/audit.py:196`, `evaluation/gap_analysis.py:37`, `:63`.
Changing evidence bytes without changing parse statuses was accepted; changed predictions could
be scored as the recorded run. Both `EvaluationBinding` tests (`test_complete_audit_io.py:43`)
failed before. New manifests hash evidence bodies; reconstruction checks recorded context/evidence
hashes, allowed paths, requests/output binding and output contracts. After: both mutations are
refused. Historical manifests without body hashes remain readable but cannot prove original bytes.

### P2 — transport, concurrency, resource and budget reliability

**F18 — The loopback HTTP fixture reset unread POST bodies.**
`tests/test_provider_compat.py:191`. Replies neither consumed the request body nor declared response
length. On macOS, closing with unread POST bytes caused intermittent `ConnectionResetError`
(`errno 54`) while reading HTTP errors. A 24-test diagnostic run reproduced 8 failures and 1 error:
503 explanations vanished and 429 waits/retries changed when bodies were lost. This was transport
fixture flakiness affecting both Python versions, not a Python 3.11 language incompatibility.
Drain the declared request body and set response `Content-Length`. The large-POST framing test
(`test_complete_audit_providers.py:31`) failed before and passes after. No assertion was weakened.
The original 429 and 503 tests also passed 12 repetitions each on each interpreter (24 per version).

**F19 — HTTP error bodies were unbounded and unclosed.** `ghostaudit/providers.py:652`, `:546`.
Error handling read the entire body and left streams open; unreadable bodies also leaked. The bounded
and unreadable-body tests (`test_complete_audit_providers.py:18`, `:42`) failed before. Read at most
64 KiB and close in `finally`; close catalog HTTP failures too. After: bounded consumption and
closure hold, including on read failure; explanation sanitization and retry tests still pass.

**F20 — Concurrent same-key cache writes shared one temporary path.**
`ghostaudit/providers.py:489`. Two writers racing at `os.replace` could produce `FileNotFoundError`.
The synchronized writer test (`test_complete_audit_providers.py:87`) failed before. Use a unique
temporary file per writer, atomic replacement and final cleanup. After: both writes complete and
the final entry is valid. Response cache keys and provider isolation remain unchanged.

**F21 — Pre-send provider failures leaked reservations.** `ghostaudit/providers.py:644`.
Missing credentials raised `ProviderError` outside the settlement paths, leaving 120 paise reserved.
The missing-credentials test (`test_complete_audit_providers.py:67`) failed before. Record and settle
local provider errors before re-raising. After: outstanding reservation is zero; no request is sent.

**F22 — Multibyte prompts were undercounted in priced reservations.**
`ghostaudit/providers.py:559`. A character count was described as request bytes; equal-length Japanese
and ASCII prompts reserved the same amount despite different UTF-8 sizes. The multibyte test
(`test_complete_audit_providers.py:61`) failed before. Count encoded bytes. After: the larger wire
payload reserves more. Token estimates remain heuristics, not invoice guarantees.

**F23 — Investigator admission ignored the next turn's cost/context.**
`ghostaudit/investigator.py:193`. Checking only tokens already consumed and flat chat reserves could
admit a huge initial context or priced turn beyond the per-case limit. Both `CaseAdmission` tests
(`test_complete_audit_investigator.py:11`) failed before. Include messages, tools, output allowance
and the larger applicable reservation before sending. After: oversized cases stop with `CASE_BUDGET`
and zero model calls; existing investigator feedback/submission regressions pass.

**F24 — Failed/quarantined SQL loads leaked connections.**
`ghostaudit/parsers/sqlsrc.py:57`, `:108`. `executescript` errors and early temporal returns bypassed
the final close. The failed-load test (`test_complete_audit_evidence.py:73`) failed before. Close
on load failure and use `finally` around parsing. After: failed synthetic load is closed. An existing
test also now registers its own connection cleanup.

**F25 — Malformed PDF metadata/streams escaped as pipeline crashes.**
`ghostaudit/parsers/pdfsrc.py:225`, `:62`, `ghostaudit/media.py:130`.
A missing `/Pages` dereferenced a missing regex match; corrupt Flate raised a raw zlib error; an
invalid harvested retention date raised outside the extraction handler. The malformed catalog,
date and compressed-stream tests (`test_complete_audit_evidence.py:82`, `:102`, `:108`) failed before.
Raise domain errors and include harvesting inside the controlled unreadable-media path. After:
malformed inputs produce an explicit unsupported/unreadable result rather than invented evidence
or a run crash.

**F26 — Compressed PDF streams expanded without a bound.**
`ghostaudit/parsers/pdfsrc.py:62`. A tiny compressed body could allocate arbitrarily large decoded
data. The 9 MiB expansion test (`test_complete_audit_evidence.py:115`) failed before. Limit each
decoded stream to 8 MiB and reject excess/truncated streams. After: the crafted expansion is refused.
This is a per-stream bound, not a whole-document resource guarantee.

**F27 — PDF framing stripped valid compressed bytes.**
`ghostaudit/parsers/pdfsrc.py:38`. Unrestricted newline stripping removed checksum bytes that happened
to equal a newline. A valid compressed tab (`zlib.compress(b'\t')`) failed extraction. The binary
boundary test (`test_complete_audit_evidence.py:96`) failed before. Honor a direct stream `/Length`
where supplied; otherwise remove only the framing newline. After: the valid binary payload decodes.

### P3 — documentation

**F28 — README commands and architecture status were stale.** Root `README.md:20` and
`code/README.md:3`. The root cloned the starter and ran the now-required CLI without a subcommand;
the code README described existing investigator/verifier work as planned and reused an old run
directory in its example. Update repository/run commands, document fresh-directory requirements,
current Phase 4 status, evidence hashes and realistic reservation limits. Verified against CLI,
code and the successful offline command shapes; no artificial documentation test was added.

## Verification matrix

All tests use synthetic fixtures, replay doubles, copied caches or loopback HTTP servers. External
socket connections were blocked by a startup audit hook, both API-key variables were removed,
and `.env` was not loaded. Loopback tests required lifting the sandbox's local-bind restriction;
an initial sandbox permission error was environmental, not classified as a code defect.

| Check | Python 3.11.13 | Python 3.14.7 |
| --- | --- | --- |
| Full unittest discovery | **282/282**, 10.666 s | **282/282**, 11.081 s |
| TokenBazaar + Roar dedicated suites | **31/31**, 5.085 s | **31/31**, 5.095 s |
| Repeated original 429 + 503 tests | **24/24**, 12 iterations of each | **24/24**, 12 iterations of each |
| New audit regression tests | **37/37**, included in full suite | **37/37**, included in full suite |
| Synthetic fixture exact outputs | **13/13** | **13/13** |
| Fresh full offline inference + validation | **518 rows**, 0 calls | **518 rows**, 0 calls |
| Copied-cache staged replay + validation | **518 rows**, 533 hits, 0 calls | **518 rows**, 533 hits, 0 calls |
| Fresh sample scoring/gap analysis | **32 rows**, 0 calls | **32 rows**, 0 calls |
| Cross-version/hash-seed determinism | Seed 1 | Seed 777; all three prediction CSVs byte-identical |

Ruff (`ruff check code`) and `git diff --check`: **pass**. Existing phase 3, hardening, follow-up,
second-review, investigator, verifier, replay, SQL security, identity and output regressions are all
included in the full suite. This reports test execution, not a measured line/branch coverage percentage.

Preserved root output validates **518 rows**, 95 COMPLETE / 413 INCOMPLETE / 10 NOT_APPLICABLE,
with 61 hash-bound disclosed fallbacks. Structural validation checks case set, order, step tuples,
citations and cutoff eligibility; it does not independently prove semantic correctness of every row.

Fixture names: `fixture_a`, `fixture_b`, `fixture_c`, `fixture_d`, `fixture_decoy`, `fixture_e`,
`fixture_e_clean`, `fixture_f`, `fixture_future`, `fixture_mixed_search`, `fixture_no_absence`,
`fixture_no_retention`, `fixture_replica`.

Reproduction commands from the primary root (use a fresh fixture output path each time):

```sh
env -u TOKENBAZAAR_API_KEY -u ROAR_API_KEY code/.venv/bin/python -m unittest discover -s code/tests -v
env -u TOKENBAZAAR_API_KEY -u ROAR_API_KEY python3 -m unittest discover -s code/tests -v
env -u TOKENBAZAAR_API_KEY -u ROAR_API_KEY PYTHONPATH=code/tests python3 -m unittest test_provider_compat test_providers_dual -v
ruff check code
python3 code/evaluation/main.py fixtures --out /tmp/ghost-audit-fixtures-fresh
python3 code/main.py validate --requests dataset/deletion_requests.csv --predictions output.csv --run-dir runs/phase3-hardened
```

The actual test runs additionally set `PYTHONPATH` to the external-connection blocking hook.
Retained local evidence is under `.runs/code-audit-20261025/`: full and provider logs for both
versions, original HTTP failure log, both final offline result JSONs, determinism hashes and
diagnostic/reproduction scripts. Fresh generated outputs are under
`/private/tmp/ghost-complete-audit.joqLwT/final311/` and `final314/`; these temporary paths are not
submission artifacts. The saved replay script copies the response cache before use and sets a
zero call/cost allowance. No historical cache or run file was rewritten.

## Current predictions, metrics and changes

| Run | COMPLETE | INCOMPLETE | NOT_APPLICABLE | Disclosed fallbacks |
| --- | ---: | ---: | ---: | ---: |
| Before fixes, full offline | 88 | 420 | 10 | 126 |
| After fixes, full offline | 89 | 419 | 10 | 125 |
| After fixes, copied-cache staged | 95 | 413 | 10 | 61 |

The copied-cache staged CSV matches the preserved root output for every case. It reproduces the
existing staged responses, not a new live experiment. Full offline changes only `case_50`: earlier
all-time category aggregation made `analytics_7438` unresolved because future observations added
another category to `analytics_platform`. At this case's cutoff the sole established category is
`ANALYTICS_PROFILE`; an eligible exhaustive snapshot omission in
`evidence_20#customer_data_read_replica` settles that resource. The row becomes COMPLETE instead of
a disclosed INCOMPLETE fallback. The source/category/time rule is generic and covered synthetically;
no production case exception was introduced. Hidden labels are unavailable, so this is a traced
behavior correction, not a measured full-dataset accuracy gain. Existing subject-level absence
without a grounded resource ID still does not invent an output step.

Offline sample predictions are unchanged (4 COMPLETE, 28 INCOMPLETE, 9 fallbacks):

| Metric | Current offline measurement |
| --- | ---: |
| Status accuracy | 26/32 = 81.25% |
| Exact rows | 1/32 = 3.125% |
| Sequence accuracy | 1/32 = 3.125% |
| Step precision | 126/127 = 99.21% |
| Step recall | 126/193 = 65.28% |
| Step F1 | 78.75% |
| Positional field accuracy | 619/1158 = 53.45% |

The gap attribution covers the 67 missing expected steps: 26 `OWNERSHIP_UNGROUNDED`,
19 `INTENT_UNSUPPORTED`, 10 `RETENTION_CONDITION_UNPROVEN`, 6 `NOT_IN_STRUCTURED_DATA`,
5 `POST_CUTOFF_ONLY`, 1 `CITATION_ONLY`. These are diagnostics under the run's evidence universe,
not proof that every expected step is safely derivable. One predicted cited step differs from the
public expected citation. No missing link or retention period was invented to improve the score.

Cross-version/hash-seed CSV SHA-256 values:

- Full offline: `f6d265f166e611d6c5c24a5d650a2d0b08baee8268d4c901a6dc44467754d958`.
- Sample offline: `f031fe8d0e3be2ea44879eb24e5e3a3858cbc019ba3f0c575e4dd30020aa9266`.
- Copied-cache staged: `968454758d715785bfd94de664d7dfd522f1621eacaa1f2336ad3786cc89982f`.

## Investigated concerns without a new reproduced defect

- Reused identifiers, cross-person graph traversal, cycles and resource IDs reused across systems:
  existing adversarial regressions pass; no numerical-similarity ownership shortcut was introduced.
- Native tool schemas, required tool choices, malformed responses, tool ordering, submission feedback
  and accepted-fact application: existing controls pass. Historical one-tool-per-turn behavior is
  model-dependent; no offline test proves that a live model will submit before its round limit.
- Provider profile isolation, TokenBazaar keys, Roar boolean adaptation, auth/proxy selection,
  redirects, returned model IDs and bounded retries: compatibility tests pass; no new live endpoint
  claim is made. Both versions support the same request protocol after transport fixture repair.
- SQL arbitrary filesystem/extension access and tool path traversal: inspected restrictions and
  existing adversarial tests pass. No new exploit was reproduced within the audited surfaces.
- Scoring exact rows, positional fields, order and cited-step identity: existing tests pass;
  new work addresses provenance rather than changing score definitions to improve results.
- Root output is byte-preserved. Validation success does not establish that every retained
  historical prediction is semantically right.

## Remaining risks and next development priorities

Required before push: independent review of these commits, especially input publication guards,
authorization reconciliation, temporal category fallback, copy treatment checks and accounting.
Do not regenerate the submission or spend on models merely because static/replay checks pass.

Recommended correctness/reliability work:

1. **Keep honest uncertainty.** Full staged replay still has 61 fallbacks and sample exact accuracy
   remains low. Missing ownership and retention conditions cannot be repaired by relaxing evidence
   requirements. Grounded additional links may improve recall; unavailable evidence must stay disclosed.
2. **Evaluate investigator usefulness with bounded, independent cases.** First use replay/scripted
   cases that require several inspections, explicit proposals and final submission. The new structured
   results and full-line inspection remove specific barriers, but do not claim a fix for the historical
   `gpt-oss-20b` round-exhaustion behavior. A separately authorized, capped live evaluation is still needed.
3. **Treat unknown pricing as unknown.** UTF-8/cost reservations are estimates. Unpriced or incomplete
   catalogs and actual charges above estimates cannot ensure the eventual provider invoice stays under
   a local limit. Use a provider-side cap before any future paid experiment. Live API availability and
   model behavior were not tested here.
4. **PDF support is deliberately narrow.** The native parser is not a complete PDF implementation;
   exotic font/CMap encodings, deeply nested page trees and aggregate document expansion remain limits.
   The new 8 MiB cap is per stream. Unsupported content must remain visible as unreadable evidence.
5. **Run publication is not a multi-process transaction.** Fresh-directory checks protect ordinary
   reruns. Simultaneous independent processes claiming exactly the same previously empty directory
   were not tested/locked. Use distinct run directories. Atomic CSV/cache replacement is preserved.
6. **Historical provenance has limits.** New runs bind evidence bodies. Older manifests cannot prove
   bytes they never hashed; code hashes are provenance records, not a promise of identical behavior
   under arbitrary future code. Keep old reports and responses for comparison.

The largest exact-row opportunities are better bounded multilingual intent coverage (19 attributed
steps in offline mode), legitimately grounded ownership discovery (26), supported media facts (6)
and canonical citation selection (1). Establish whether each additional fact is actually available
before attempting it. The 10 retention-condition and 5 future-only gaps are evidence constraints,
not invitations to presume retention or use later evidence. Do not optimize against sample case IDs.

No new features or AI experiments were started after this audit. Stop for independent review;
the remote remains unchanged.
