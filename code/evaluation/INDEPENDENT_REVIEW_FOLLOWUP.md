# Focused independent-review follow-up — 2026-10-25

Starting commit: `be3e323b2e0cc9327b7bc6e6336a03184a93441a`, existing local `main`.
Scope: only the two reported findings. No new feature, repository-wide audit, branch, worktree,
paid call or push. The final local commit SHA is provided in the delivery message.

## 1. Output destination safety — confirmed and fixed

Before the fix, `run_audit()` successfully replaced temporary synthetic `.env`, `AGENTS.md`,
`code/main.py` and unrelated `notes.csv` files with prediction CSVs. A CSV-named symlink also
allowed its resolved protected target to be replaced. No destructive test used actual project files.

`ghostaudit/audit.py` now checks the resolved destination before inference or diagnostic publication:

- The destination must have a `.csv` suffix.
- An existing destination must be a regular file parseable by `read_output()`, including its exact
  prediction header and step structure. Unrelated CSVs are refused too.
- Existing dataset/request/diagnostic guards remain in force.

Fresh CSV paths still work. A valid final `output.csv` can deliberately be republished using a fresh
run directory. Invalid existing CSVs now require a fresh destination instead of being overwritten.
This guard protects the audit entry point; the generic atomic CSV writer is unchanged.

## 2. Exhaustive absence scopes across categories — confirmed and fixed

Constructed a synthetic system with a subject's resource in one category and another resource in a
different category. A later empty exhaustive snapshot for the other category had the same system
and replica location. Before the fix, the complete independent verifier returned **no blocking
findings** for three incorrect COMPLETE plans: absent, redacted and anonymized. For absence, the
wrong scope also served as the citation fallback. For treatment, it incorrectly removed an exposing
replica from consideration while the clean primary supported the citation.

`ghostaudit/verifier.py` now uses one local `_absence_scopes()` filter for `_check_copies()`,
`_check_treatment_copies()` and the `VERIFY_RECORD_ABSENT` citation fallback. The scope must match
the system and the resource's eligible category evidence, be available and observed by the cutoff,
omit the resource, and satisfy existing coverage rules. No planner state reconstruction is called.

After the fix, wrong-category absence is blocked by `UNSUPPORTED_CITATION` and
`REPLICA_DISAGREEMENT`; wrong-category redaction/anonymization is blocked by
`REPLICA_DISAGREEMENT`. Matching-category omissions remain accepted. Neither a future observation
nor future availability is accepted even when the category matches. Policy rules are unchanged.

## Regressions and exact verification results

`tests/test_review_destination_scopes.py` adds six test methods with destination, symlink, publication,
three-operation category, positive matching-category and dual-cutoff subcases. Before production
changes: **8 failures across 3 test methods**, with the positive/cutoff controls already passing.
After changes: **6/6 methods pass**, including every subcase; no existing test was weakened.

| Check | Python 3.11.13 | Python 3.14.7 |
| --- | --- | --- |
| Complete unittest suite | 288/288, 10.058 s | 288/288, 10.558 s |
| TokenBazaar + Roar suites | 31/31 | 31/31 |
| All synthetic fixtures, exact outputs | 13/13 | 13/13 |
| Fresh full offline validation | 518 rows; 0 calls | 518 rows; 0 calls |
| Copied-cache staged replay | 518 rows; 533 cache hits; 0 calls | 518 rows; 533 cache hits; 0 calls |

Ruff and diff checks pass. API-key variables were removed, `.env` was not loaded, and an audit hook
blocked external socket connections. Loopback provider test servers were permitted. Replay used
copies of the original cache with zero call/cost allowances. No live model behavior was tested.

Fresh full offline, sample offline and staged replay CSVs are byte-identical across both interpreters
(hash seeds 1 and 777), and to their corresponding pre-follow-up outputs. The staged replay also
matches preserved root `output.csv`: 95 COMPLETE, 413 INCOMPLETE, 10 NOT_APPLICABLE and 61 fallbacks.
The earlier audit's `case_50` offline change predates this follow-up; these fixes add no prediction change.

All 1,371 protected files retain their recorded SHA-256 values: datasets, caches, `.env`, root output
and historical runs are untouched. Existing uncommitted `log.txt` bytes are preserved, with this
turn appended separately and excluded from the commit. Recovery stashes remain intact.

Before/after logs and final replay summaries are retained locally under the ignored directory
`.runs/review-destination-scopes-20261025/`. Fresh generated outputs are under
`/private/tmp/ghost-complete-audit.joqLwT/review311/` and `review314/`.
