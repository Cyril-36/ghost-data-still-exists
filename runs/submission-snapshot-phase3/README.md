# Submission snapshot: Phase 3 hardened output

A frozen copy of the root `output.csv` (518 rows) and the run that produced it (`runs/phase3-hardened`),
preserved before Phase 4 changes inference. Do not regenerate or edit these files.

- `output.csv`: SHA-256 `968454758d715785bfd94de664d7dfd522f1621eacaa1f2336ad3786cc89982f`, which equals
  `run/run_manifest.json` `output_sha256`. Statuses are 95 COMPLETE, 413 INCOMPLETE and
  10 NOT_APPLICABLE, with 61 disclosed fallbacks. The file is PROVISIONAL.
- `run/`: case plans, coverage, media extractions, the manifest (engine `phase3-hardened-1`, staged intent,
  533 cache hits, 0 calls) and the usage ledger.
- `VALIDATION.txt`: `main.py validate --run-dir run` on this copy (VALID).
- `SHA256SUMS`: check it with `sha256sum -c SHA256SUMS` from this directory.

The output is byte-identical through commit b69f2b0 (D063 and D064 checks).
