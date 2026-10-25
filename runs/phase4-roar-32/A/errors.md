# Errors: predictions.csv vs sample_output.csv

Exact rows: 1/32.

| Difference | Count |
|---|---|
| MISSING_STEP | 51 |
| STATUS_MISMATCH | 4 |
| WRONG_CITATION | 1 |

Citations: 56/75 expected cited steps matched with the same evidence_id (1 wrong evidence_id). Resource obligations: 51 of 111 expected (system, resource) pairs missing, 0 additional.

## Missing steps by root cause

| Root cause | Steps | Cases |
|---|---|---|
| OWNERSHIP_UNGROUNDED: Resource appears in eligible evidence, but nothing ties it to the subject | 31 | case_01, case_02, case_03, case_04, case_07, case_08, case_09, case_10, case_12, case_15, case_17, case_18, case_19, case_20, case_21, case_22, case_23, case_25, case_26, case_27, case_28, case_29, case_30, case_31, case_32 |
| RETENTION_CONDITION_UNPROVEN: Retained record lacks a supplied retention condition (strict default) | 11 | case_11, case_12, case_13, case_14, case_15, case_16, case_17, case_19, case_22, case_31, case_32 |
| POST_CUTOFF_ONLY: Resource is known only from sources available after audit_at | 5 | case_09, case_23, case_24, case_29, case_30 |
| NOT_IN_STRUCTURED_DATA: Resource appears only in media or other unparsed content | 3 | case_04, case_18, case_20 |
| STATE_OR_POLICY:  | 1 | case_06 |

## Cases

| Case | Status (expected → predicted) | Steps (exp/pred) | Fallback | Missing (by cause) | Other differences |
|---|---|---|---|---|---|
| case_01 | COMPLETE → **INCOMPLETE** | 1/0 | NO_REPRESENTABLE_OBLIGATION | OWNERSHIP_UNGROUNDED 1 | - |
| case_02 | COMPLETE → **INCOMPLETE** | 1/0 | NO_REPRESENTABLE_OBLIGATION | OWNERSHIP_UNGROUNDED 1 | - |
| case_03 | COMPLETE | 3/1 | - | OWNERSHIP_UNGROUNDED 2 | - |
| case_04 | COMPLETE → **INCOMPLETE** | 2/0 | NO_REPRESENTABLE_OBLIGATION | NOT_IN_STRUCTURED_DATA 1, OWNERSHIP_UNGROUNDED 1 | - |
| case_05 | INCOMPLETE | 3/3 | - | exact | - |
| case_06 | INCOMPLETE | 4/3 | - | STATE_OR_POLICY 1 | - |
| case_07 | INCOMPLETE | 4/3 | - | OWNERSHIP_UNGROUNDED 1 | - |
| case_08 | INCOMPLETE | 4/3 | - | OWNERSHIP_UNGROUNDED 1 | - |
| case_09 | INCOMPLETE | 5/3 | - | OWNERSHIP_UNGROUNDED 1, POST_CUTOFF_ONLY 1 | - |
| case_10 | INCOMPLETE | 4/3 | - | OWNERSHIP_UNGROUNDED 1 | - |
| case_11 | INCOMPLETE | 14/13 | - | RETENTION_CONDITION_UNPROVEN 1 | - |
| case_12 | INCOMPLETE | 5/3 | - | OWNERSHIP_UNGROUNDED 1, RETENTION_CONDITION_UNPROVEN 1 | - |
| case_13 | INCOMPLETE | 8/7 | - | RETENTION_CONDITION_UNPROVEN 1 | - |
| case_14 | INCOMPLETE | 5/4 | - | RETENTION_CONDITION_UNPROVEN 1 | - |
| case_15 | INCOMPLETE | 5/3 | - | OWNERSHIP_UNGROUNDED 1, RETENTION_CONDITION_UNPROVEN 1 | - |
| case_16 | INCOMPLETE | 7/6 | - | RETENTION_CONDITION_UNPROVEN 1 | - |
| case_17 | INCOMPLETE | 5/3 | - | OWNERSHIP_UNGROUNDED 1, RETENTION_CONDITION_UNPROVEN 1 | - |
| case_18 | INCOMPLETE | 14/12 | - | OWNERSHIP_UNGROUNDED 1, NOT_IN_STRUCTURED_DATA 1 | - |
| case_19 | INCOMPLETE | 8/6 | - | OWNERSHIP_UNGROUNDED 1, RETENTION_CONDITION_UNPROVEN 1 | - |
| case_20 | INCOMPLETE | 8/6 | - | OWNERSHIP_UNGROUNDED 1, NOT_IN_STRUCTURED_DATA 1 | - |
| case_21 | COMPLETE | 3/1 | - | OWNERSHIP_UNGROUNDED 2 | - |
| case_22 | INCOMPLETE | 8/6 | - | OWNERSHIP_UNGROUNDED 1, RETENTION_CONDITION_UNPROVEN 1 | - |
| case_23 | INCOMPLETE | 8/6 | - | OWNERSHIP_UNGROUNDED 1, POST_CUTOFF_ONLY 1 | - |
| case_24 | INCOMPLETE | 7/6 | - | POST_CUTOFF_ONLY 1 | - |
| case_25 | COMPLETE | 3/1 | - | OWNERSHIP_UNGROUNDED 2 | - |
| case_26 | COMPLETE | 6/4 | - | OWNERSHIP_UNGROUNDED 2 | - |
| case_27 | COMPLETE | 6/4 | - | OWNERSHIP_UNGROUNDED 2 | - |
| case_28 | COMPLETE | 6/4 | - | OWNERSHIP_UNGROUNDED 2 | - |
| case_29 | INCOMPLETE | 14/12 | - | OWNERSHIP_UNGROUNDED 1, POST_CUTOFF_ONLY 1 | - |
| case_30 | INCOMPLETE | 14/12 | - | OWNERSHIP_UNGROUNDED 1, POST_CUTOFF_ONLY 1 | - |
| case_31 | COMPLETE → **INCOMPLETE** | 3/1 | UNRESOLVED_OBLIGATIONS | OWNERSHIP_UNGROUNDED 1, RETENTION_CONDITION_UNPROVEN 1 | WRONG_CITATION 1 |
| case_32 | INCOMPLETE | 5/3 | - | OWNERSHIP_UNGROUNDED 1, RETENTION_CONDITION_UNPROVEN 1 | - |
