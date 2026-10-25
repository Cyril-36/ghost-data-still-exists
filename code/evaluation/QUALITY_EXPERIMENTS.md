# Quality experiments — 2026-10-25

Starting point: existing local `main`, `5cd8b54b687fdecf2ba80d59b78c55d55f4f7a7d`.
No branch/worktree was created; no push. This report separates actual measured gains from failed
experiments and remaining evidence constraints. Public expected answers were used by evaluation
only. No production case-ID, subject-ID, source-ID or expected-step exceptions were added.

A separate workspace action committed `85f56f7627f30c96433feb6c5a1661e9bf14f626` during this run
(prior log, documentation typo/count and stray test-output cleanup). It was inspected and preserved;
the final verification includes that commit. These improvements are committed on top of it.

## Measured outcome

The retained changes improve the **offline** sample: ten more correct steps, one more correct
status and six more correctly cited expected steps. They reduce reliance on model availability.
**Public-sample exact rows did not improve. The strongest measured staged configuration also
remains unchanged.** Passing tests or tool probes is not a hidden HackerRank score.

| Metric, 32 public samples | Offline before | Offline after | Cached Roar before → after |
| --- | ---: | ---: | ---: |
| Correct status | 26/32 (81.25%) | 27/32 (84.375%) | 28/32 → 28/32 (87.5%) |
| Exact rows | 1/32 | 1/32 | 1/32 → 1/32 |
| Ordered step arrays exact | 1/32 | 1/32 | 1/32 → 1/32 |
| Step precision | 126/127 (99.21%) | 136/137 (99.27%) | 141/142 → 141/142 (99.30%) |
| Step recall | 126/193 (65.28%) | 136/193 (70.47%) | 141/193 → 141/193 (73.06%) |
| Step F1 | 78.75% | 82.42% | 84.18% → 84.18% |
| Positional field accuracy | 619/1158 (53.45%) | 659/1158 (56.91%) | 681/1158 → 681/1158 (58.81%) |
| Expected cited steps matched exactly | 48/75 | 54/75 | 56/75 → 56/75 |

The one incorrect step in each sample configuration remains the previously disclosed citation
difference; none of the retained improvements adds another incorrect step. Measurements are
development results on inspected public data, not estimates of generalization error.

Cached TokenBazaar gets 28/32 statuses, 135/193 correct steps (135/136 precision), 1/32 exact rows,
and 54/75 matching cited steps, before and after. This current cache is missing two typed target
decisions. Historical TokenBazaar figures used a different cache and must not be relabeled as this
baseline. Two new attempts to complete the missing entries both returned HTTP 503; there was no
accuracy gain and no further retry.

## Retained implementation changes

1. **Explicit English item scope and prior-deletion checks** (`ghostaudit/intent.py`,
   `intent_models.py`). Recognize literal search-result/export removal as ITEM, including when the
   account must be preserved. A direct request to verify that an account was deleted is ACCOUNT
   verification rather than an unrelated item request just because another sentence mentions a
   search result. Hypothetical, multiple-target and third-party item requests abstain; negated
   requests cannot authorize deletion. Models cannot widen a recognized item to the whole account
   or replace its explicit target category. The planner still needs eligible ownership and a unique
   target. Unknown languages continue to abstain offline.
2. **Narrow native PDF evidence in offline mode** (`ghostaudit/media.py`). Explicit security-journal
   structures require a security/compliance heading plus event and subject reference fields. Support
   attachment structures require a support reference and attachment/upload cues. Ambiguous,
   incomplete and example/template documents abstain. Existing literal harvesting, section ownership,
   cross-person controls, cutoff and system grounding still apply. Native recognition records its
   method and no invented model probability. Staged mode continues using typed document decisions.
3. **Source-diverse, paginated retrieval** (`ghostaudit/tools.py`). Interleave sources, putting media
   hits first, so a long structured dump cannot bury a relevant document. `start_match` and
   `next_match` expose later hits without repeating earlier pages. Pagination advances only over
   items actually returned after clipping. Shown-text registration remains exact; hidden matches
   do not grant resource handles or citation rights.

The engine version is `phase4-3`. Provider profiles, selected role models, deterministic policy and
independent verifier remain intact. The experimental investigator prompt was restored and neither
larger round limits nor a new default investigator model were committed.

## Experiments and rejected approaches

| Experiment | Conditions | Measured outcome / decision |
| --- | --- | --- |
| Fresh baseline replay | Offline; copied TokenBazaar cache; copied Roar cache | Establish the table above; zero paid calls |
| Native tool probes | `gpt-6-luna`, `deepseek-v4.1-flash`, `claude-haiku-5-5` | All pass; Haiku probe was already cached. DeepSeek received only a probe, not an accuracy evaluation. |
| Haiku investigator | Roar cached intent/media; `claude-haiku-5-5`; 10 routing rounds, 40 tools, 250k per-case token admission limit, 750-paise case limit | 101 live calls; 15 cases routed, 10 ran before the cumulative experiment margin stopped the rest. Better batching/validated facts, but unchanged sample predictions. Do not adopt larger limits/model by default. |
| Native PDF prototype | Offline, structurally recognized documents only | 129/193 correct steps versus 126/193; no new incorrect step. Keep after adversarial tests. |
| TokenBazaar cache completion | Two missing intent-target decisions | Both HTTP 503; unchanged predictions. Stop retries. |
| Luna focused investigator | `gpt-6-luna`; 6 rounds, 40 tools, 250k tokens, 750-paise case limit; source-diverse retrieval and focused submission prompt | 72 live investigator calls, plus its earlier probe; 10 cases ran, 5 stopped at the cumulative budget margin. More submissions but unchanged predictions. Restore the prompt; retain retrieval based on independent coverage tests. |
| Literal English authorization + native PDF | Offline, no labels passed to inference | Final 136/193 correct steps and 27/32 statuses. Keep with scope, negation, foreign-owner and category-disagreement regressions. |

These are bounded operational comparisons, not randomized model rankings: the two investigator runs
have different model/round/prompt conditions and incomplete coverage after budget stops. Accepted
facts that duplicate existing evidence do not count as an accuracy gain. Limits, unsubmitted facts
and verifier feedback continue to preserve the input plan; nothing was auto-published to improve a
score. More model calls cannot legitimately invent an absent ownership link or retention date.

## Cumulative budget and billing uncertainty

Authorized allowance: **10,000 paise (₹100)** across this entire effort. A single on-disk ledger
admits every inference attempt; retries are disabled for experiments. Read-only catalog GETs were
used to select priced models. Calls used locally configured credentials without printing them.

Before a call, reserve using every UTF-8 request byte as an input token, the full output allowance,
current catalog rates and a 2x margin, with a 50-paise floor. Once usage arrives, settle to the
provider charge if available, otherwise 2x the completed-call catalog estimate with that same
50-paise floor. Failed/unknown calls retain the full reservation. Initial worst-case reservations
were reconciled against completed usage before later experiments; **the ledger was never reset**.
Stop admitting calls at 9,000 paise, leaving at least ₹10 of the authorized allowance as margin.

| Model | HTTP inference attempts | Received responses | Catalog estimate (paise) | Conservative accounted (paise) |
| --- | ---: | ---: | ---: | ---: |
| `claude-haiku-5-5` | 101 | 101 | 1700.812080 | 5076.284160 |
| `gpt-6-luna` | 73 | 73 | 1050.631120 | 3721.701600 |
| `deepseek-v4.1-flash` | 1 | 1 | 0.955812 | 50.000000 |
| `typesafe/jev-latest` | 2 | 0 (503) | unknown | 100.000000 |
| **Total** | **177** | **175** | **2752.399012 known estimate** | **8947.985760** |

Thus known catalog-estimated usage is **₹27.52**, while the conservative ledger is **₹89.48**,
including reserves for the two failed TokenBazaar calls. **Actual provider-reported charges are
unavailable, not zero.** Roar returned no billing header; prices/currency conversion are estimates
(the gateway uses ₹88/USD). Local estimates do not guarantee an invoice, and no provider-side cap
was claimed to exist or bypassed. Paid experimentation is closed; no further calls were made after
the final ledger total. Fresh response caches and usage records are preserved for inspection.

## Tests and artifact verification

18 new test methods cover native document positives and abstention, ambiguity and missing system,
two-person pages, crowded-source retrieval and complete pagination after clipping, hidden handles,
invalid offsets, literal item scope, hypothetical/multiple/foreign/negated requests, model scope and
category disagreement, and two independent complete-row pipeline cases. The native pipeline case
includes an ablation showing the missing steps without native interpretation.

New feature tests failed before implementation. Two existing expectations were updated for the
intended capability changes: the item request now asserts DELETE and has explicit ITEM/category
tests; offline media coverage now expects two INTERPRETED PDFs and asserts their exact source set,
while all 79 structured sources still parse. No test was skipped or removed.

| Final check | Python 3.11.13 | Python 3.14.7 |
| --- | --- | --- |
| Full suite | **306/306**, 9.703 s | **306/306**, 10.879 s |
| Both provider compatibility suites | **31/31** | **31/31** |
| Existing synthetic fixtures | **13/13 exact** | **13/13 exact** |
| Fresh 518-case offline audit | 90 COMPLETE, 418 INCOMPLETE, 10 NOT_APPLICABLE; 124 fallbacks; 0 calls | same |
| Full copied-cache staged replay | 95 COMPLETE, 413 INCOMPLETE, 10 NOT_APPLICABLE; 61 fallbacks; 533 hits; 0 calls | same |

Ruff and diff checks pass. External socket connections were blocked during final verification;
loopback HTTP tests were allowed, API-key variables removed and `.env` not loaded. Offline/sample
and full copied-cache CSVs are byte-identical across Python versions and hash seeds 1 and 777.
The complete staged replay remains byte-identical to preserved root `output.csv`.

Relative to the starting SHA's full offline output, only `case_45` changes: explicit verification of
prior account deletion is resolved rather than mistaken for an item request. It becomes COMPLETE
under existing eligible evidence checks. This is a generic authorization interpretation, not a
case-specific exception. Full 518-case labels are unavailable, so no full-set accuracy gain is claimed.

All 1,371 protected files retain their original hashes: datasets, `.env`, original submission,
historical runs and old response caches remain untouched. Three recovery stashes are preserved.
Existing local log bytes are retained, with these turns appended and excluded from the commit.

Reproduction/trace artifacts are in ignored `.runs/quality-iz9j79br/`: baseline/final CSVs, manifests,
metrics, live traces, copied caches, pricing catalogs, cumulative `budget.json`, spending summary,
test logs, scripts and the rejected prompt snapshot. This work does not edit the original evaluation
reports; the separate cleanup commit described above is retained.
Historical baseline metrics use their frozen predictions; rebuilding an old offline evidence universe
with the new native interpreter is correctly refused by provenance checks rather than silently mixed.

## Limits and next opportunities

The main staged scoring ceiling remains grounded resource association and supplied retention
conditions. Some expected account/analytics IDs have no admissible owner link; some records appear
only after cutoff; some retained records lack a supplied period. None was guessed, linked by ID
similarity/timing or accepted by relaxing policy. These constraints still dominate exact rows.

The strongest current sample result is cached Roar without live investigation. Use the retained
offline path when no gateway is available; it now loses fewer supported steps. Future investigator
work should demonstrate new verified facts on an independent, grounded holdout before spending on
all 518 cases. Search coverage has improved, but no live accuracy gain from that change is claimed.
Model-independent processing of genuinely new literal evidence is more promising than repeatedly
asking a larger model to infer facts that are absent. A new, separately authorized budget and
provider-side cap would be needed before further paid experiments.
