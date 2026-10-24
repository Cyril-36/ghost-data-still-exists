# Evaluation report — INTERIM (Phase 2 deterministic engine)

This is an interim report on the Phase 2 baseline. It is **not** the final
submission report: no model is used yet, screenshots/PDFs are not interpreted,
and intent is English-only. Every number below was measured on the commit that
contains this file, using the commands shown.

## Data roles and leakage limits

- `sample_output.csv` (32 labeled cases): evaluation only. All 32 sample answers
  were inspected while studying evidence semantics in this phase, so the sample
  score is a development measurement, not a held-out estimate. Rules that were
  discovered from sample evidence are flagged `[sample-informed]` in
  `decisions.md`.
- 518 prediction requests: unlabeled; only structural validation and coverage
  statistics are reported.
- 11 team-created SYNTHETIC fixtures: expected rows hand-written from each
  scenario, in a different identifier vocabulary from the supplied data.
- Inference never opens labels: loaders open fixed filenames only; fixture
  answers sit outside `input/` and are made unreadable during end-to-end tests.

## Sample metrics (32 cases)

```
python3 code/main.py audit --requests dataset/sample_deletion_requests.csv --output .runs/sample/predictions.csv --run-dir .runs/sample
python3 code/evaluation/main.py score ... --run-dir .runs/sample --expected dataset/sample_output.csv --out-dir .runs/sample/score
```

| Metric | Result |
|---|---|
| Status accuracy | 23/32 = 0.719 |
| Step precision | 131/145 = 0.903 |
| Step recall | 131/193 = 0.679 |
| Step F1 | 262/338 = 0.775 |
| Positional field accuracy | 682/1158 = 0.589 |
| Sequence accuracy | 5/32 = 0.156 |
| Exact-row accuracy | 5/32 = 0.156 |

Status confusion: COMPLETE→COMPLETE 1, COMPLETE→INCOMPLETE 9,
INCOMPLETE→INCOMPLETE 22. No false COMPLETE. No NOT_APPLICABLE in the labels.

### Why steps are missed (`evaluation/main.py gaps`)

| Cause | Missed steps |
|---|---|
| OWNERSHIP_UNGROUNDED — resource is in eligible evidence but nothing eligible ties it to the subject (22 primary accounts, 6 anonymized analytics) | 28 |
| CITATION_ONLY — right step, but the row is a fallback so it cannot cite | 14 |
| INTENT_UNSUPPORTED — Spanish, French, Japanese requests | 9 |
| NOT_IN_STRUCTURED_DATA — only in a PDF/screenshot, or nowhere | 6 |
| POST_CUTOFF_ONLY — linked only by files available after audit_at | 5 |
| STATE_OR_POLICY — linked resource with a wrong state or treatment | **0** |

The 14 extra predicted steps are exactly the 14 CITATION_ONLY steps (same step
without an evidence ID). Every other emitted step matches a labeled step.

## Custom evaluation cases (SYNTHETIC)

All run through `run_audit` from fixture inputs; result = exact-row match.

| Fixture | Scenario | Expected | Actual |
|---|---|---|---|
| fixture_a | profile still returned by readback | INCOMPLETE, 3 steps citing fx_file_A | match |
| fixture_b | absent account + retained security record next to a remaining search entry; DELETE success claim present | INCOMPLETE, 5 steps; compliant records still verified | match |
| fixture_c | "Stop promotional emails only. Keep my account…", data present, workflow says COMPLETE | NOT_APPLICABLE, [] | match |
| fixture_d | profile absent from exhaustive primary AND failover listings | COMPLETE citing the snapshot | match |
| fixture_e | tombstone still holding name/phone/hash | REPORT_EXPOSED_PII → REDACT (never DELETE) | match |
| fixture_e_clean | tombstone keeps only its stable reference | COMPLETE | match |
| fixture_f | request at new email; data only under old email and its SHA-256 | export + search found and deleted | match |
| fixture_decoy | same name, similar emails, second user | decoys excluded; COMPLETE | match |
| fixture_future | remaining record visible only in a file exported 1 s after audit_at | ignored; COMPLETE | match |
| fixture_replica | primary clean, failover replica holds the profile | REPORT_REMAINING_RECORD citing the snapshot | match |
| fixture_no_absence | DELETE success claim + listing that does not cover the subject | record still PRESENT | match |

## Full prediction set (518 requests, unlabeled)

`python3 code/main.py audit --requests dataset/deletion_requests.csv ...` — 0.45 s, 0 model calls.

- Rows: 518, valid; statuses COMPLETE 66, INCOMPLETE 442, NOT_APPLICABLE 10.
- Disclosed fallbacks: 143 (62 intent unresolved for language, 81 with
  unresolved obligations). 299 INCOMPLETE rows carry at least one confirmed
  violation.
- Primary-profile resource ID grounded: 79 of 446 deletion requests; another
  162 have subject-level profile absence proven but no resource ID to cite.
- Sources: 79/79 structured files parsed; 15 media files reported unsupported.

## Failures found and fixed in this phase

| Failure | Detected by | Fix |
|---|---|---|
| 8 retained security records reported missing | sample diff | listings prove absence only for subjects the capture covers (D017) |
| a not-found lookup made another account look like the subject's | sample diff | query keys link only returned records (D018) |
| pipe-wrapped logs mapped no events | parser coverage report | wrapper regex fixed; all 26 logs parse |
| handover rows with spaces in the subject were dropped | parser coverage report | row pattern fixed |
| short English requests classed as "unsupported language" | intent unit test | function-word comparison against other Latin-script languages |
| `user_<n>`-only reference matching | synthetic fixtures (other ID formats) | references recognized by field (D019) |
| hang for some hash seeds | fixture run | frozen iteration in `discover` (D025) |

## Limitations

- Sample score is a development measurement on inspected cases.
- No screenshot/PDF interpretation; no multilingual intent.
- Primary-profile ownership is ungrounded for most subjects under the strict
  rules; this caps exact-row accuracy until a grounded source is found.
- Structural validation does not prove that a citation semantically supports
  its step.
