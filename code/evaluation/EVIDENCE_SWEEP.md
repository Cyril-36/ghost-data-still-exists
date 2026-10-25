# Evidence sweep of the remaining sample misses (after D085)

Investigation only. The labels were used to choose which resources to examine, never in a production rule.
No paid calls were made, and root `output.csv` (`71d7398e…7912c1`) is unchanged.

**Method.** For each of the 29 missed resources (22 account profiles, 7 analytics records) every occurrence in
every source was gathered: structured files, logs, SQL rows, PDFs, recorded screenshot readings and activity
rows. For each occurrence the sweep collected:
- identifiers and resource references in the same line, SQL row, JSON object (and its nested objects) and log
  trace;
- each identifier classified as the subject's (current or historical), another known person's, or unknown;
- the source's capture time against the case's `audit_at`.

## Findings per resource

| Resource(s) | Admissible pre-audit evidence | Conclusion |
|---|---|---|
| account_001, 002, 004, 007, 008, 010, 5521, 5037, 4990, 410, 7012, 4163, 4857, 8858 (14) | identity-free deletion-worker claims only (account_004: none at all) | no admissible link; the only connections are a numeric suffix or claim timing (forbidden, D032) |
| account_6440, 3136, 5482 (`evidence_92`) | 404 profile lookup with `profile: null` and no lookup key | no admissible link |
| account_5684, 3866 | 404 in `evidence_92`; identity only in `evidence_02` (captured 09-17, after the 09-15 cutoff) | inadmissible: post-cutoff only |
| account_3960 (case_09) | subject email in the lookup trace of `evidence_06`, captured 09-18 after the 09-16 cutoff | inadmissible: post-cutoff only |
| analytics_9406, 901, 5335, 1895, 4566, 4574, 6077 | every row has null `customer_reference`, email, phone and hash; claims name no person | no admissible link |
| account_006 (case_06) | `evidence_57` search row `search_001` holds the subject's email and `account_006` as a foreign key | linked by reference, but case_06 is item-scoped to the search entry (D062) and no evidence shows the account's state. Adding it means widening an item request's verification scope: a policy change, with no effect on the 518 |
| account_4092 (case_17) | `evidence_01` trace `tr-4708586`: `lookup_key` is the subject's email, 404 for account_4092 | admissible, but a second lookup-keyed profile (account_6523, `evidence_67`, March) exists for the same email. Only the request window or timing separates them, which is forbidden; the D085 uniqueness guard correctly holds both back. No effect on the 518 |

**Parser review.** The only discarded identifier was the lookup key of 404 log lookups, fixed in D085. JSON
`profile_lookups` rows in `evidence_92` carry no key at all, so nothing there is discarded. Missing information,
not unextracted information, explains every other miss.

**D085 decoy check on the 518.** 71 of the 72 D085-linked rows have no competing lookup-keyed profile at any
time, post-cutoff included. The exception, case_46 (same person as case_17), links account_6523 from a lookup
made inside its own request window (03-08 to 03-11). The competitor account_4092 appears only after its cutoff.
This mirrors case_17's label, which uses the lookup made during that request.

## Strongest opportunities

1. **Sample exact rows (8/32) are at the admissible-evidence ceiling.** case_06 and case_17 would each become
   exact only through a scope-widening or timing rule. Neither changes any graded row, so both would be
   sample-driven. Not implemented.
2. **Candidate D086 (graded set only, not applied).** `evidence_14` (screenshot captured 2026-08-07 08:00,
   eligible for case_39, cutoff 08-08 10:00) shows `data_flow_1121` with selector `jain.aditi@gmail.com`
   (user_14), "Status: Healthy", "Mode: Continuous", 1,284 events/hour and "200 OK". `activity_4002` (08-07
   08:07) also links the flow to the same email and user_14. Under a narrowed revision of D042 (running status
   plus independent flow evidence; a badge alone is never a flag), case_39 changes from a disclosed fallback to
   finding, disable and verify. No sample row changes, so labels cannot validate it.
