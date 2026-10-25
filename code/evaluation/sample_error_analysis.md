# Sample error analysis (32 labelled cases)

Produced by `python3 code/evaluation/main.py errors` (see the command below). The labels are read only
here. Inference never sees them.

**Which predictions.** The last completed *staged* sample predictions came from the response cache
on the development Mac (`.runs/`, `.cache/`, both gitignored). They are not in this checkout, and
regenerating them needs that cache or the live gateway, which returns HTTP 503. This analysis
therefore uses the **offline** sample run of commit b69f2b0. That run is reproducible with 0 model
calls, and its scores match the report (status 26/32, steps 126/127 precision and 126/193 recall,
exact rows 1/32). The last recorded staged scores were 28/32, 142/193 and 2/32 at e0aaef4 (from cache).
Staged mode changes only the INTENT_UNSUPPORTED rows below and the media-dependent steps. To redo
this analysis on staged predictions:

```sh
python3 code/evaluation/main.py errors --dataset dataset --requests dataset/sample_deletion_requests.csv \
    --predictions .runs/sample-staged/predictions.csv --expected dataset/sample_output.csv \
    --run-dir .runs/sample-staged --out-dir .runs/sample-staged/errors
```

## Findings

1. **The engine under-reports. It does not mis-report.** Across 32 cases there are 0 unexpected
   steps, 0 wrong operations or states, and 1 wrong citation (a fallback row that carries no
   evidence). All 66 other errors are missing steps. Every non-exact row is a strict subset of its
   label. Status mismatches (6) all go COMPLETE → INCOMPLETE, and each is a disclosed fallback.
2. **One step type dominates exact-row loss:** VERIFY_RECORD_ABSENT on the subject's
   `account_service` record (21 OWNERSHIP_UNGROUNDED steps plus 5 under unresolved intent). The
   ownership sweep finds no grounded link in any source for 19 of these account IDs, and 2 more are
   linked only in post-cutoff sources. Their only mentions are workflow claims that name no person
   (DELETE_RECORD … SUCCESS) or 404 rows with no identifier. Two shortcuts were measured and rejected:
   - attributing the account through the request's workflow time window identifies the labelled
     account in 1/32 cases;
   - matching the account number to the user number holds in 7 of 31 visible account/user rows and
     8 of 32 labels.

   D032 stands: no general evidence-grounded method recovers these IDs from the participant files.
3. **Retention is a policy question, not an evidence gap.** There are 10 missing
   VERIFY_RECORD_PRESENT steps on `security_service` audit records whose rule needs a retention
   period that no source supplies (D052). An offline experiment with `presume_retention=True` gives
   status 27/32, recall 137/193, precision 137/137 and **exact rows 4/32** (cases 11, 13 and 16 become
   exact), with no false steps. The default stays strict (owner decision).
   `post_cutoff_ownership_links` adds 2 steps and no exact rows. `subject_scoped_omission` changes
   nothing.
4. **Unresolved intent costs 19 steps and 5 cases** (02, 03, 06, 14, 28) in offline mode. This is
   model work (multilingual), not investigation. Cached staged answers resolved most of them before.
   Case 06 still needs 2 live Jev target calls (PENDING).
5. **Evidence only in media or unparsed content costs 6 steps** (cases 04, 07, 18, 20): a support
   attachment in a PDF, and security records visible only in documents or screenshots. These are the
   cases where a bounded investigator with `inspect_source` / `inspect_media` could add grounded
   facts.
6. **Post-cutoff-only security records cost 5 steps** (cases 09, 23, 24, 29, 30). The labels expect
   VERIFY_RECORD_PRESENT for records whose only eligible sources are dated after audit_at. These
   cannot be cited, and inferring them from later evidence would break the cutoff rule.

## Exact rows reachable per root-cause group (offline run; a case counts if every difference falls in the group(s))

| Fixed group(s) | Cases that become exact | Reachable by |
|---|---|---|
| INTENT_UNSUPPORTED | 5 (02, 03, 06, 14, 28) | staged intent (model), live PENDING |
| OWNERSHIP_UNGROUNDED | 7 (01, 08, 10, 21, 25, 26, 27) | no grounded method found (see 2) |
| RETENTION_CONDITION_UNPROVEN | 3 (11, 13, 16) | a policy decision only |
| OWNERSHIP + RETENTION | 16 | both of the above |
| NOT_IN_STRUCTURED_DATA | 0 alone; 11 with OWNERSHIP | investigator media/source inspection, plus ownership |
| POST_CUTOFF_ONLY | 1 (24) | not reachable under the cutoff rule |

Conclusion for Phase 4: an investigator cannot raise exact-row accuracy much on these samples. Most
lost rows need either the account-record link, which no grounded source provides, or a retention
policy change. Its measurable value lies in finding 5 (media and unparsed evidence). It can also
settle finding 2 case by case with recorded tool traces, instead of guessing an owner.

## Per-case comparison
Exact rows: 1/32.

| Difference | Count |
|---|---|
| MISSING_STEP | 66 |
| STATUS_MISMATCH | 6 |
| WRONG_CITATION | 1 |

## Missing steps by root cause

| Root cause | Steps | Cases |
|---|---|---|
| OWNERSHIP_UNGROUNDED: Resource appears in eligible evidence, but nothing ties it to the subject | 26 | case_01, case_04, case_07, case_08, case_09, case_10, case_12, case_15, case_17, case_18, case_19, case_20, case_21, case_22, case_23, case_25, case_26, case_27, case_29, case_30, case_31, case_32 |
| INTENT_UNSUPPORTED: Request intent unresolved, so the case fell back (model-dependent) | 19 | case_02, case_03, case_06, case_14, case_28 |
| RETENTION_CONDITION_UNPROVEN: Retained record lacks a supplied retention condition (strict default) | 10 | case_11, case_12, case_13, case_15, case_16, case_17, case_19, case_22, case_31, case_32 |
| NOT_IN_STRUCTURED_DATA: Resource appears only in media or other unparsed content | 6 | case_04, case_07, case_18, case_20 |
| POST_CUTOFF_ONLY: Resource is known only from sources available after audit_at | 5 | case_09, case_23, case_24, case_29, case_30 |

## Cases

| Case | Status (expected → predicted) | Steps (exp/pred) | Fallback | Missing (by cause) | Other differences |
|---|---|---|---|---|---|
| case_01 | COMPLETE → **INCOMPLETE** | 1/0 | NO_REPRESENTABLE_OBLIGATION | OWNERSHIP_UNGROUNDED 1 | - |
| case_02 | COMPLETE → **INCOMPLETE** | 1/0 | INTENT_UNRESOLVED | INTENT_UNSUPPORTED 1 | - |
| case_03 | COMPLETE → **INCOMPLETE** | 3/0 | INTENT_UNRESOLVED | INTENT_UNSUPPORTED 3 | - |
| case_04 | COMPLETE → **INCOMPLETE** | 2/0 | NO_REPRESENTABLE_OBLIGATION | NOT_IN_STRUCTURED_DATA 1, OWNERSHIP_UNGROUNDED 1 | - |
| case_05 | INCOMPLETE | 3/3 | - | exact | - |
| case_06 | INCOMPLETE | 4/0 | INTENT_UNRESOLVED | INTENT_UNSUPPORTED 4 | - |
| case_07 | INCOMPLETE | 4/0 | UNRESOLVED_OBLIGATIONS | NOT_IN_STRUCTURED_DATA 3, OWNERSHIP_UNGROUNDED 1 | - |
| case_08 | INCOMPLETE | 4/3 | - | OWNERSHIP_UNGROUNDED 1 | - |
| case_09 | INCOMPLETE | 5/3 | - | OWNERSHIP_UNGROUNDED 1, POST_CUTOFF_ONLY 1 | - |
| case_10 | INCOMPLETE | 4/3 | - | OWNERSHIP_UNGROUNDED 1 | - |
| case_11 | INCOMPLETE | 14/13 | - | RETENTION_CONDITION_UNPROVEN 1 | - |
| case_12 | INCOMPLETE | 5/3 | - | OWNERSHIP_UNGROUNDED 1, RETENTION_CONDITION_UNPROVEN 1 | - |
| case_13 | INCOMPLETE | 8/7 | - | RETENTION_CONDITION_UNPROVEN 1 | - |
| case_14 | INCOMPLETE | 5/0 | INTENT_UNRESOLVED | INTENT_UNSUPPORTED 5 | - |
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
| case_28 | COMPLETE → **INCOMPLETE** | 6/0 | INTENT_UNRESOLVED | INTENT_UNSUPPORTED 6 | - |
| case_29 | INCOMPLETE | 14/12 | - | OWNERSHIP_UNGROUNDED 1, POST_CUTOFF_ONLY 1 | - |
| case_30 | INCOMPLETE | 14/12 | - | OWNERSHIP_UNGROUNDED 1, POST_CUTOFF_ONLY 1 | - |
| case_31 | COMPLETE → **INCOMPLETE** | 3/1 | UNRESOLVED_OBLIGATIONS | OWNERSHIP_UNGROUNDED 1, RETENTION_CONDITION_UNPROVEN 1 | WRONG_CITATION 1 |
| case_32 | INCOMPLETE | 5/3 | - | OWNERSHIP_UNGROUNDED 1, RETENTION_CONDITION_UNPROVEN 1 | - |
