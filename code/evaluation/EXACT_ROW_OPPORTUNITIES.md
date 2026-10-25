# Exact-row opportunity report (32 public samples)

Commit analysed: `d000b5a` on local `main`. Every run here is offline or served entirely from the local
response cache (`--budget-paise 0`): **0 paid calls**. The ₹100 campaign budget stays closed. Labels
were read only by the evaluation tools. Root `output.csv` (SHA-256 `968454758d71…`) and every historical
run directory are unchanged.

## Reproduced starting point

| Run | Status | Correct steps (recall) | Precision | Exact rows |
|---|---|---|---|---|
| Offline | 27/32 | 136/193 | 136/137 | 1/32 |
| Cached Roar (Jev intent, Haiku reader) | 28/32 | 141/193 | 141/142 | 1/32 |
| Cached TokenBazaar staged (two target answers missing, HTTP 503) | 28/32 | 135/193 | 135/136 | 1/32 |

**Why recall rose but exact rows did not:** a row is exact only if every step matches. Each of the
31 non-exact rows still lacks at least one step that the evidence or the policy does not support (below).
The engine never adds a wrong step. Apart from one citation on a disclosed fallback row, every
difference is a missing step. Shared steps are always in the labels' order.

## Combining offline parsing with staged models

Offline gets **no** expected step right that cached Roar misses. Roar's only advantage is multilingual
intent: case 03 (French) and case 14 (Japanese). Its pipeline already falls back to the deterministic
classifier and native PDF evidence, so it is the combination. Nothing more is gained by merging.

## Per-case distance from an exact row (cached Roar run)

Distance counts missing, extra and wrong steps, wrong status and wrong citations.
- **POLICY:** the label relies on an interpretation the supplied rules do not settle.
- **UNAVAILABLE:** the participant files hold no admissible evidence for the step.

| Case | Distance | Expected → predicted | Remaining differences | Blocker |
|---|---|---|---|---|
| case_05 | 0 | INCOMPLETE → INCOMPLETE | exact | EXACT |
| case_06 | 1 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_006 — POLICY: item scope (D062) | POLICY |
| case_07 | 1 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_007 — UNAVAILABLE: account named only by identity-free claims or 404 lookups | UNAVAILABLE |
| case_08 | 1 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_008 — UNAVAILABLE: account named only by identity-free claims or 404 lookups | UNAVAILABLE |
| case_10 | 1 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_010 — UNAVAILABLE: account named only by identity-free claims or 404 lookups | UNAVAILABLE |
| case_11 | 1 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_PRESENT audit_record_9394 — POLICY: retention period not supplied | POLICY |
| case_13 | 1 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_PRESENT audit_record_4816 — POLICY: retention period not supplied | POLICY |
| case_14 | 1 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_PRESENT audit_record_987 — POLICY: retention period not supplied | POLICY |
| case_16 | 1 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_PRESENT audit_record_9528 — POLICY: retention period not supplied | POLICY |
| case_24 | 1 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_PRESENT audit_record_7095 — UNAVAILABLE: only in a file captured after audit_at | UNAVAILABLE |
| case_01 | 2 | COMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_001 (evidence_54) — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>status follows the missing steps | UNAVAILABLE |
| case_02 | 2 | COMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_002 (evidence_54) — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>status follows the missing steps | UNAVAILABLE |
| case_03 | 2 | COMPLETE → COMPLETE | VERIFY_RECORD_ABSENT account_7566 (evidence_94) — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_ANONYMIZED analytics_9406 (evidence_94) — UNAVAILABLE: anonymized record, identifiers null | UNAVAILABLE |
| case_09 | 2 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_3960 — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_PRESENT audit_record_4902 — UNAVAILABLE: only in a file captured after audit_at | UNAVAILABLE |
| case_12 | 2 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_9797 — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_PRESENT audit_record_2945 — POLICY: retention period not supplied | POLICY, UNAVAILABLE |
| case_15 | 2 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_4745 — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_PRESENT audit_record_1596 — POLICY: retention period not supplied | POLICY, UNAVAILABLE |
| case_17 | 2 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_4092 — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_PRESENT audit_record_1797 — POLICY: retention period not supplied | POLICY, UNAVAILABLE |
| case_18 | 2 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_5521 — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_PRESENT audit_record_6645 — UNAVAILABLE: record in no participant file | UNAVAILABLE |
| case_19 | 2 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_9102 — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_PRESENT audit_record_6793 — POLICY: retention period not supplied | POLICY, UNAVAILABLE |
| case_20 | 2 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_5037 — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_PRESENT audit_record_5569 — UNAVAILABLE: record in no participant file | UNAVAILABLE |
| case_21 | 2 | COMPLETE → COMPLETE | VERIFY_RECORD_ABSENT account_6440 (evidence_92) — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_ANONYMIZED analytics_5335 (evidence_92) — UNAVAILABLE: anonymized record, identifiers null | UNAVAILABLE |
| case_22 | 2 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_4990 — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_PRESENT audit_record_3482 — POLICY: retention period not supplied | POLICY, UNAVAILABLE |
| case_23 | 2 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_410 — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_PRESENT audit_record_4128 — UNAVAILABLE: only in a file captured after audit_at | UNAVAILABLE |
| case_25 | 2 | COMPLETE → COMPLETE | VERIFY_RECORD_ABSENT account_3136 (evidence_92) — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_ANONYMIZED analytics_1895 (evidence_92) — UNAVAILABLE: anonymized record, identifiers null | UNAVAILABLE |
| case_26 | 2 | COMPLETE → COMPLETE | VERIFY_RECORD_ABSENT account_5684 (evidence_92) — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_ANONYMIZED analytics_4566 (evidence_92) — UNAVAILABLE: anonymized record, identifiers null | UNAVAILABLE |
| case_27 | 2 | COMPLETE → COMPLETE | VERIFY_RECORD_ABSENT account_3866 (evidence_92) — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_ANONYMIZED analytics_4574 (evidence_92) — UNAVAILABLE: anonymized record, identifiers null | UNAVAILABLE |
| case_28 | 2 | COMPLETE → COMPLETE | VERIFY_RECORD_ABSENT account_5482 (evidence_92) — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_ANONYMIZED analytics_6077 (evidence_92) — UNAVAILABLE: anonymized record, identifiers null | UNAVAILABLE |
| case_29 | 2 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_7012 — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_PRESENT audit_record_3954 — UNAVAILABLE: only in a file captured after audit_at | UNAVAILABLE |
| case_30 | 2 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_4163 — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_PRESENT audit_record_1707 — UNAVAILABLE: only in a file captured after audit_at | UNAVAILABLE |
| case_32 | 2 | INCOMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_8858 — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_PRESENT audit_record_7068 — POLICY: retention period not supplied | POLICY, UNAVAILABLE |
| case_04 | 3 | COMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_004 (evidence_54) — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_ANONYMIZED analytics_901 (evidence_56) — UNAVAILABLE: anonymized record, identifiers null<br>status follows the missing steps | UNAVAILABLE |
| case_31 | 4 | COMPLETE → INCOMPLETE | VERIFY_RECORD_ABSENT account_4857 (evidence_51) — UNAVAILABLE: account named only by identity-free claims or 404 lookups<br>VERIFY_RECORD_PRESENT audit_record_6909 (evidence_51) — POLICY: retention period not supplied<br>status follows the missing steps<br>citation: fallback rows carry none | POLICY, UNAVAILABLE |


## Summary

| Group | Cases |
|---|---|
| Exact now | 05 |
| One difference, recoverable from admissible evidence | **none** |
| One difference, policy interpretation | 06 (item scope), 11, 13, 14, 16 (retention) |
| One difference, evidence unavailable | 07, 08, 10 (account), 24 (post-cutoff record) |
| Two or more differences | 26 cases; each includes an unavailable account or anonymized-analytics step |

## Traced blockers

1. **Account record (26 steps, every case except 05 and 06).** Each labelled account ID appears only in a
   `deletion_worker` DELETE_RECORD claim naming no person ("Target resource: account_007"), or in a 404
   profile lookup with every identifier null. Linking by timing is the only signal: the claim usually
   lands seconds to minutes before the request's `reported_at`. Measured: the claim nearest
   `reported_at` is the labelled account in 14/32 samples. Cases 18 and 20 were reported 179 s from the
   same claim, so the rule would give both requests the same wrong account. Rejected (D032). It is now
   guarded by `tests/test_exact_row_guards.py`.
2. **Anonymized analytics (7 steps: cases 03, 04, 21, 25–28).** Every identifier field is null; the only
   link is an ANONYMIZE_RECORD claim naming no person. Unavailable for the same reason.
3. **Security-record retention (11 steps).** No source supplies a retention period for these records,
   before or after the cutoff. The labels expect VERIFY_RECORD_PRESENT anyway.
4. **Post-cutoff records (5 steps: cases 09, 23, 24, 29, 30).** Known only from snapshots captured two days
   after `audit_at` (`evidence_02`, `evidence_06`). None of them carries a retention period either.
5. **Records absent from every participant file:** `audit_record_6645` and `audit_record_5569`.

## Policy-interpretation counterfactuals (measured, not adopted)

The earlier "presumed retention gives 4/32" no longer holds. With `presume_retention=True` the
independent verifier rejects 10 plans (UNPROVEN_CONDITION), as designed: offline 88/193, Roar 90/193,
still 1/32 exact. Relaxing the verifier's retention check as well, in a scratch harness that was never
committed, gives on the cached Roar run:

| | Strict (current) | Retention presumed end to end |
|---|---|---|
| Exact rows | 1/32 | **5/32** (adds 11, 13, 14, 16) |
| Status | 28/32 | 29/32 |
| Correct steps | 141/193 | 153/193 |
| Precision | 141/142 | **153/153** |

Every added retention step matches a label, with no false step: the labels consistently treat security
audit logs as retained without a supplied period. On the 518 graded requests the same change rewrites 202 rows:
- 52 go INCOMPLETE → COMPLETE;
- 150 gain a retention check;
- fallbacks drop from 61 to 9.

One reading that does not simply assume the rule's condition: no supplied rule authorizes removing a
SECURITY_AUDIT_LOG, so its only policy-consistent end state is "present". That is a decision for the
project owner (D052 keeps strict). It is reported here, not applied.

**Item scope (case 06).** "My account page is gone … Can you remove that search result too?" The account
record is grounded and absent. Commit e0aaef4 (account scope) produced this row exactly. D062 now
scopes the request to the search result, so the planned account-absence check is dropped. A narrower
variant would add only a *verification* (never remediation) of an account the person says was already
deleted, and only when it is grounded and absent. That would make case 06 exact again. This is also an
owner decision, because D062 ruled out label-led widening.

## Decisions and result

- **No evidence-supported defect was found.** No production behaviour changed, and the measured accuracy is
  unchanged: 1/32 exact (offline and cached).
- **Added:** `tests/test_exact_row_guards.py` keeps timing-based account attribution and claim-only
  analytics links out of the planner.
- **Ceiling with current evidence and rules:** 1/32. With both owner decisions it would be 6/32 on the samples: retention (+4),
  measured; case 06 scope (+1), measured at e0aaef4. Every other row needs evidence the files do not contain.
