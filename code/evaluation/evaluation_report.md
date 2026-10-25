# Evaluation report — INTERIM (Phase 3: correctness fixes, Jev decision layer, media)

This is an interim report, not the final submission report. The root `output.csv`
is **PROVISIONAL**. Every number below was measured on the commit that contains
this file. Run artifacts are in `runs/phase3-provisional/`, and benchmark artifacts
are in `runs/phase3-provisional/benchmarks/`.

## Correctness-hardening pass (audit of e0aaef4)

Fixes for the independent audit's findings are recorded in `decisions.md` D044–D057. Regression tests are in
`tests/test_hardening_regressions.py`: 17 test functions, all failing on e0aaef4 and passing now (an earlier
version of this report said 19, which was wrong). The follow-up patch (D058–D061) adds
`tests/test_followup_regressions.py`: 6 tests, 5 of which fail on 7b8c216; the sixth checks that absence
verification from NOT_FOUND lookups is preserved.

| Measurement | e0aaef4 baseline | Hardened |
|---|---|---|
| Full 518 rows | 95 / 413 / 10, 61 fallbacks | **byte-identical** (`output.csv` SHA-256 `968454758d71…`) |
| Sample, offline mode | 27/32 status, 134/135 precision, 134/193 recall, 2/32 exact | 26/32, 126/127, 126/193, 1/32 exact (D062: a mentioned account closure no longer authorizes account scope; cases 06 and 28 become fallbacks) |
| Sample, staged mode | 28/32, 142/143, 142/193, 2/32 exact (reproduced from cache) | **PENDING**: needs 2 live Jev target calls (cases 06, 08); gateway HTTP 503 at both commits |
| Intent benchmark (78 cases, scope-aware) | v2: 72/72 on decision labels only | **PENDING**: needs live calls for the 6 new keep-account cases and target questions |
| Unit tests | 130 | 153 (Python 3.11–3.14), ruff clean |

Why the full output does not change:
- No full-set request is item-scoped. Jev answers reconcile to 508 ACCOUNT and 10 NON_DELETION.
- No linked record has evidence naming another known person.
- No SQL snapshot has conflicting times.
- Each PDF names a single record.
- Media enrichment was established before every affected cutoff.
- Every profile listing row carries an identifier.

The fixes are guards against failure scenarios the data does not currently contain. Each is exercised by its regression test.

Sample cases 06 and 08 are now item-scoped. Jev's account-wide probability for them is 0.54 and 0.48, below the 0.60 needed for account scope. They need the target answer, and until then they fail closed: the disclosed fallback has no steps.

Gap analysis now rebuilds the audited run's own evidence, including media from cached responses. On the offline and cached staged sample runs it still finds 0 eligible ownership links missed by the engine.

## Data roles and leakage limits

- **`sample_output.csv` (32 labeled cases):** evaluation only. All 32 were inspected in earlier phases, so sample scores are development measurements. Rules discovered from samples are flagged `[sample-informed]` in `decisions.md`; in Phase 3 that is D038, family probability.
- **Intent benchmark:** 72 cases written separately by the team (`evaluation/intent_cases.py`), covering 12 scenarios × 6 languages (en, es, pt, fr, ja, ko). They are not paraphrases of dataset requests.
- **Synthetic audit fixtures:** 13, each in a different identifier vocabulary. Their answers are never readable during inference.
- **Vision ground truth:** a manual reading of the 11 screenshots (`evaluation/vision_ground_truth.py`), transcribed before scoring. It covers literal IDs and identifiers only.
- **Unlabeled requests:** the 518 are reported only through structural validation and status/fallback counts.

## Sample metrics (32 cases): before and after

| Metric | Phase 2 | Phase 3 default (strict) | + `presume_retention` (experiment) | all relaxations (experiment) |
|---|---|---|---|---|
| Status accuracy | 23/32 | **28/32** | 29/32 | 29/32 |
| Step precision | 131/145 | **142/143** | 154/154 | 156/156 |
| Step recall | 131/193 | **142/193** | 154/193 | 156/193 |
| Step F1 | 262/338 | **284/336** | — | — |
| Positional field accuracy | 682/1158 | 689/1158 | — | — |
| Sequence accuracy | 5/32 | 2/32 | — | — |
| Exact-row accuracy | 5/32 | 2/32 | 6/32 | 6/32 |
| False COMPLETE | 0 | 0 | 0 | 0 |

The Phase 3 default ran in staged intent with media, from cached validated
responses (0 network calls, 47 cache hits). Status confusion:

| Expected | Predicted | Cases |
|---|---|---|
| COMPLETE | COMPLETE | 6 |
| COMPLETE | INCOMPLETE | 4 |
| INCOMPLETE | INCOMPLETE | 22 |

### Changes against Phase 2, each with the rule that caused it

| Case(s) | Change | Cause |
|---|---|---|
| 03 | status corrected (French request now authorized) | Jev intent (D037, D039) |
| 14 | intent resolved (was unsupported language); status unchanged | Jev intent (D037, D039) |
| 21, 25, 26, 27, 28 | status corrected (profile absence was unproven) | explicit FULL_TABLE coverage: the subject is absent from a full-table snapshot of the profile table (D029); no profile resource ID, so no account step |
| 07 | +3 correct steps; unknown-category obligation resolved | PDF journal extract gives the record and its retention (D041) |
| 11, 13, 16 | **exact row lost** (status still right) | strict retention (D030): the retained security record has no supplied retention period, so the row becomes a fallback without citations |
| 31 | **status regressed** COMPLETE→INCOMPLETE | strict retention (D030) |
| — | precision 0.903 → 0.993 | search query identity no longer attached to hits (D028); disclosed fallbacks emit fewer uncitable steps |

The exact-row and sequence regressions all come from one rule. Policy rule
conditions for security and legal records require a retention period, and the
evidence does not supply one. Phase 2 presumed it. With `presume_retention`, the
lost rows come back (6/32 exact). The strict default stays until the project
owner decides otherwise; see the open decision in Limitations.

Other experiments on the samples:
- `post_cutoff_ownership_links` adds 2 correct steps, but would break the audit_at contract.
- `subject_scoped_omission` has no effect.

### Why steps are still missed (`evaluation/main.py gaps`, default config)

| Cause | Missed steps |
|---|---|
| OWNERSHIP_UNGROUNDED | 31 |
| RETENTION_CONDITION_UNPROVEN | 11 |
| POST_CUTOFF_ONLY | 5 |
| NOT_IN_STRUCTURED_DATA | 3 |
| CITATION_ONLY | 1 |
| INTENT_UNSUPPORTED | 0 (was 9) |
| STATE_OR_POLICY | **0** |

## Ownership-link investigation

Source: `benchmarks/ownership_analysis.json`. 36 missed sample resources are
attributed to ownership or the cutoff.

| Verdict | Resources |
|---|---|
| Eligible grounded link that the engine missed | **0** |
| Linked only in sources available after `audit_at` | 7 |
| No grounded link in any source | 29 |

The 29 are records whose sources are either operation claims that name no
person, or records with no identifier at all: 404 lookups and fully anonymized
rows. Apart from the 12 catalog-link rows, no activity row names a subject
identifier. The 32 repopulation edges are now traversed (D033: multi-hop,
visited set, cross-person guard) and add no sample steps. No unsupported
association was introduced to close the gap: there is no request-window timing
attribution and no suffix matching.

## Multilingual intent (staged decision layer)

Benchmark (`benchmarks/intent_benchmark_v2.json`). "Correct" includes leaving
the 12 ambiguous or contradictory cases unresolved. "Unsafe" means authorizing
deletion when it should not.

| Method | Synthetic 72 | by language (en/es/pt/fr/ja/ko, each /12) | Unsafe | Samples 32 |
|---|---|---|---|---|
| Deterministic (English classifier) | 16/72 | 6/2/2/2/2/2 | **1** (en hypothetical) | 29/32 |
| Jev only | 72/72 | 12 each | 0 | 32/32 |
| Generative only | 72/72 | 12 each | 0 | 32/32 |
| Staged (default) | 72/72 (60 by Jev, 12 correctly unresolved) | 12 each | 0 | 32/32 |

- **Label accuracy:** Jev's raw label matched 68/72.
- **Choice/flag disagreements:** there were 3. In each case the request was contradictory, Jev's choice was DELETE_ACCOUNT, and the `conflicting_instructions` / `preserve_account` flags kept it unresolved (D037).
- **Calibration:** every probability band was 100% correct, so there is no error signal and the probabilities are **not** treated as calibrated.
- **Cost:** Jev costs 1 paisa per request (header). Generative cost 561 paise for 25 calls (~22 paise per call, reasoning-heavy).
- **Mean latency:** Jev 4.09 s on synthetic and 1.37 s on samples; 1.54 s in the full run.
- **Benchmark v1 failures, fixed in v2:**
  - A generative batch returned empty content because reasoning consumed `max_tokens`, which invalidated 10 decisions. Fixed with a batch size of 5, 8,000 max tokens, per-item validation and one retry.
  - Jev split probability between the VERIFY and SCOPED sub-labels. Fixed with family probability (D038).

Full 518 run, Jev-decided intent by language:

| Language | DELETE | NON_DELETION |
|---|---|---|
| English | 446 | 10 |
| Spanish / Portuguese / French | 38 | — |
| Japanese | 12 | — |
| Korean | 12 | — |

0 requests reached the generative stage. `reported_status` is never used as a signal.

## PDF and screenshot coverage

All **15/15** media files were processed (`media_extractions.json`, `coverage.json`
status INTERPRETED).

- **PDFs (4):**
  - Native text was extracted from every page.
  - Jev classified the document kind correctly for all 4 (p = 1.0).
  - The legal retention register is order-keyed and matches no structured record, so nothing is attached (reported).
- **Screenshots (11):**
  - A blind reader saw only the image and a fixed prompt.
  - Against the manual reading, resource IDs scored 23/23 for both precision and recall, and identifiers 18/18 (`benchmarks/vision_scores.json`).
  - One malformed ID guard is tested.
  - `marketing_001`'s identifiers conflict with its catalog link, so the identity-conflict guard leaves it unattached.
- **Effect on samples:** +3 correct steps (case 07), 0 regressions.
- **No media output assigns status or steps.** Media only adds observations that pass validation (D041, D042).
- **Small set:** the vision set is 11 images, too few to estimate a field error rate.

## Custom evaluation cases (SYNTHETIC audit fixtures)

All 13 run end to end through `run_audit`. Every one is an exact-row match.

| Fixture | Scenario | Expected |
|---|---|---|
| fixture_a | profile still returned by readback | INCOMPLETE, 3 steps |
| fixture_b | absent account + retained security record + remaining search entry; DELETE success claim | INCOMPLETE, 5 steps |
| fixture_c | "Stop promotional emails only. Keep my account…" | NOT_APPLICABLE |
| fixture_d | profile absent from full-table primary and failover snapshots | COMPLETE |
| fixture_e | tombstone with name/phone/hash, absence proven by an exact NOT_FOUND lookup | REDACT, never DELETE |
| fixture_e_clean | tombstone keeps only its stable reference | COMPLETE |
| fixture_f | request at new email; data under old email and its SHA-256 | found and deleted |
| fixture_decoy | same name, similar emails; decoy security record read back | decoys excluded; COMPLETE |
| fixture_future | remaining record only in a file exported 1 s after audit_at | ignored; COMPLETE |
| fixture_replica | primary clean, failover replica holds the profile | REPORT_REMAINING_RECORD |
| fixture_no_absence | DELETE claim + listing that does not cover the subject | still PRESENT |
| fixture_no_retention (new) | retained security record with no supplied retention period | disclosed fallback, never COMPLETE |
| fixture_mixed_search (new) | query by subject returns own + another person's document | only own document reported |

The six Phase 3 regressions are in `tests/test_phase3_regressions.py`. All six
fail on commit f14f284 (checked in a separate worktree) and pass now.

## Full prediction set (518 requests, unlabeled)

The default strict configuration ran staged intent with media.

| Measure | Result |
|---|---|
| Rows | 518, VALID |
| COMPLETE | 95 |
| INCOMPLETE | 413 |
| NOT_APPLICABLE | 10 |
| Disclosed fallbacks | 61, all UNRESOLVED_OBLIGATIONS |
| Intent fallbacks | 0 (Phase 2 had 62) |

The 61 fallbacks break down as follows:

| Unresolved obligation | Cases |
|---|---|
| Retention condition unproven | 52 |
| Retention + absence | 2 |
| Absence unproven | 3 |
| Flow | 2 |
| Treatment | 1 |
| Category | 1 |

Against Phase 2, 301 rows changed:

| Change | Rows | Cause |
|---|---|---|
| COMPLETE→INCOMPLETE | 38 | all strict retention |
| INCOMPLETE→COMPLETE | 67 | 58 from full-table subject-level profile absence, 9 from non-English intent now resolved |

The 58 rows have no citable account resource ID, so they carry no account verification step (see Limitations).

Sources: 79 PARSED, 15 INTERPRETED. Outputs are byte-identical across hash seeds.

## Failures found and fixed in this phase

| Failure | Detected by | Fix |
|---|---|---|
| alias known before its evidence existed | review + regression | availability vs validity (D026) |
| one clean copy hid an exposing replica | review + regression | per-copy exposure (D027) |
| search query identity attached to every hit | review + mixed fixture | ownership from document fields (D028) |
| SQL scopes for collections the table never held | review + regression | table-held collections only (D029) |
| file-wide coverage heuristic | review + regression | FULL_TABLE / SUBJECT_SCOPED (D029) |
| retention presumed | review + fixture | UNPROVEN_CONDITION (D030) |
| generative batch truncated (empty content) | benchmark v1 | batch 5, per-item validation, retry |
| Jev sub-label probability split | benchmark v1 | family probability (D038) |
| deterministic classifier authorized a hypothetical | benchmark | Jev cross-check; deterministic is no longer authoritative |
| 8 Jev calls lost to HTTP 429 | full run 1 usage log | Retry-After honoured, 5 bounded retries; rerun identical |
| ASCII85 streams without `<~` framing | PDF extraction test | framing fix |
| flows/backup resolved to `search_service` from claims | media diff | systems from STATE/LINK only (D034) |
| user key read as a resource ID | media diff | catalog-label resolution (D042) |

## Limitations

- **Open decision: strict vs presumed retention.** Strict retention is correct by the policy text but costs 4 exact rows and 1 status on the samples, and causes 52 full-set fallbacks.
- **Sample scores are development measurements** on inspected cases.
- **Ownership:** 29 sample resources are ungrounded in every source. Recall is capped until an investigator finds grounded links.
- **Subject-level absence:** 58 full-set COMPLETE rows rely on it and lack the account resource step.
- **Small vision set:** 11 images. Synthetic image fixtures would need an imaging dependency, so offline tests use recorded responses.
- **Uncalibrated probabilities:** the 0.60 and 0.70/0.30 thresholds are policy choices, not calibrated cut-offs.
- **Structural validation only:** it does not prove that a citation semantically supports its step.
