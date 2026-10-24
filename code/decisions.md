# Implementation decisions

Each entry: decision, why, evidence, and where it lives. D001-D013 are Phase 1
(contracts, validation, serialization, scoring). D014+ are Phase 2 (deterministic
audit engine). Entries marked **[sample-informed]** were discovered by inspecting
supplied sample evidence; they are stated as general rules about evidence
semantics and must be judged on whether they generalize, not on sample fit.
No inference code names a case, resource, or evidence ID (enforced by
`test_inference_code_contains_no_literal_dataset_ids`).

## D001 — Fresh Phase 1 implementation in this workspace

This workspace started with empty `code/main.py` and `code/evaluation/main.py`.
`plan.md` line 8 ("M0 foundation started … eight passing tests") describes a
different copy that the user told us not to use, so none of that code was reused
or consulted. Everything under `code/` was written in this session.

## D002 — Standard library only; uv lock pins the interpreter

User choice for Phase 1. `code/pyproject.toml` declares no dependencies and
`code/uv.lock` records that resolved state, so `uv run --project code …` is
reproducible. Plain `python3` (3.11+) works too. Tested on CPython 3.11.13,
3.12.11, 3.13.5, and 3.14.7. Pydantic is deferred until model/tool boundaries
exist (Phase 2+). Typed records are frozen dataclasses in `ghostaudit/contracts.py`.

## D003 — Row order: numeric `case_<n>` suffix, used only for ordering

`deletion_requests.csv` is in ascending numeric order (case_33 … case_550).
Lexical order would differ (case_100 before case_33). `output.case_sort_key`
sorts numerically for `case_<digits>` and literally for any other ID (fixtures).
The suffix is never used for identity, users, or resources.
Open risk: the contract only says "sorted by case_id". If the grader expects
lexical order, this one function changes.

## D004 — Step order: phase → official operation rank → system_id → resource_id

The ranks come from the official operation table (`OPERATION_TABLE`, tested
against a verbatim transcription). system_id/resource_id use literal string
comparison. All 32 sample answers are already in this order (test
`test_samples_pass_strict_validation_and_are_already_canonical`). The samples
cannot tell lexical from natural-numeric ID ordering (0 of their step groups
differ), so this is a documented choice, not something the samples prove.

## D005 — One step per (phase, system_id, resource_id, facet) [revised in Phase 2]

Phase 1 allowed one step per (phase, system, resource). The official operations
act on three independent facets: record state (ABSENT/PRESENT/REDACTED/
ANONYMIZED), data flow (ACTIVE->DISABLED) and restore path (ACTIVE->BLOCKED).
Two record-state steps for one resource contradict each other (DELETE vs
REDACT); a record step plus a restore step do not (a retained backup that must
also be restore-blocked). `contracts.OPERATION_FACET`, `output.canonical_steps`
and `_check_plan_chain` now key on the facet. Contradictory remedies are still
rejected; all 32 samples remain valid and canonical. Replica copies that share a
resource ID are still one output obligation (plan section 10.4).

## D006 — INCOMPLETE plans must be internally consistent

For each resource: a finding needs its mapped remediation
(REMAINING→DELETE, EXPOSED_PII→REDACT|ANONYMIZE, ACTIVE_FLOW→DISABLE,
RESTORE_RISK→BLOCK). A remediation needs a finding and the matching final
verification. Verification-only resources (already compliant, or retained) are
allowed. Sources: plan §16.3; AGENTS.md §6.5 "Verification must cover the
required final state for each remediated record". All 32 samples comply.
Implemented in `output._check_plan_chain`.

## D007 — INCOMPLETE needs a FINDING unless it is a disclosed fallback [revised in Phase 2]

Plan section 17 best-effort fallback is now implemented. A row may be INCOMPLETE
without findings only if its case ID is listed by the run that produced the CSV
(`unresolved_cases.jsonl`, cross-checked against `run_manifest.json`, whose
`output_sha256` must match the CSV). Such a row may contain only VERIFICATION
steps (checks for obligations that are already settled), never remediation.
Without the run directory, `validate` rejects these rows. Fallback rows are
predictions, not evidence that data remains.

## D008 — Citation eligibility: exists and available at or before audit_at

`EvidenceRef.available_at` is the manifest `captured_at` for files and
`event_time` for activity and identity-audit rows. The comparison is inclusive
(evidence captured exactly at audit_at is eligible). All sample citations
satisfy this (0 late). The validator checks existence and timing only. It does
**not** check that the cited source supports the asserted state; Phase 2+ must
do that.

## D009 — One evidence-ID namespace

file_id, activity_id, and audit_id do not collide in the supplied data (checked).
`load_context` rejects any collision, so an `evidence_id` always resolves to
exactly one source.

## D010 — Labels and expected answers are unreachable from inference

- `ghostaudit.inputs` opens only five fixed context filenames plus an explicitly
  passed request file. It never lists a directory.
- Fixtures write expected answers to `<fixture>/expected_output.csv`, beside
  `input/` rather than inside it.
- Scoring and fixtures live in `code/evaluation/`. `ghostaudit` and `main.py` never
  import them. Tests enforce this with an `open()` spy and a source scan.

## D011 — Strict input enums fail loudly

Unknown `reported_status`, `reported_result`, `required_action`, or audit
`field_name` values reject the whole load. This avoids silently mis-handling a
new value. `event_type` stays an open vocabulary because its semantics belong to
later parsers.

## D012 — Output encoding

UTF-8, LF line endings (matching the supplied CSVs), compact JSON with keys in
the problem statement's order and `ensure_ascii=False`. Samples mix two key
orders, and the grader ignores key order. Every write is atomic: validate, write a
temporary file, read it back, re-validate, then `os.replace`.

## D013 — Fixture details not fixed by the plan

Fixture A follows plan §19.2 exactly. Unspecified fields were chosen as follows:
reported_status `COMPLETE` (a claim contradicted by evidence), reported_at
2026-01-02T12:00:00Z, identity updated_at 2025-12-01T09:00:00Z. Fixture C uses
the plan §19.4 request text, a COMPLETE workflow claim, and evidence that the
account and marketing profile are still FOUND, so ignoring intent would produce
findings. Its rules are USER_PROFILE and MARKETING_PROFILE (both DELETE).
Both fixtures are SYNTHETIC and make no claim about the supplied corpus.

## D014 — Observation model with two timestamps and a location

Every extracted fact is an `Observation` (`observations.py`) carrying source ID,
`available_at` (manifest capture / activity time), `observed_at` (the embedded
record time), locator, excerpt, system, resource, physical location, category,
proof kind and identity tokens. Both timestamps must be at or before audit_at
(`corpus.eligible`). Operation claims (`kind=CLAIM`) never set state.

## D015 — Parser selection by content and structure

`parsers.sniff` uses byte signatures (PNG, PDF, JSON, SQL, log timestamp). JSON
families are chosen by shape (`service_exports`, `snapshots`, `collection`,
`records`). Categories come from collection/table/group names, explicit
`record_type`, or event semantics, never from resource-ID prefixes; e.g. a log
`contact_lookup_completed` on an `analytics_*` ID is a MARKETING_PROFILE (DELETE).

## D016 — Restricted SQLite

Snapshots run in a private `:memory:` database with an authorizer allowlist
(CREATE TABLE/INDEX, INSERT, transactions, `foreign_keys` pragma, `json_valid`),
then switch to read-only (READ/SELECT/`table_info`). ATTACH, DROP, DELETE,
writable_schema and later writes are denied (tested). A progress handler bounds
runaway statements. Queries use fixed templates over table names read from
`sqlite_master`.

## D017 — Snapshot listings are subject-scoped [sample-informed]

A collection listing (Family B/C JSON, SQL table) proves a known resource absent
only for a subject the same capture demonstrably covers (some record in the
file carries the subject's identifiers): `Corpus.covers`. Evidence: one bundle
lists only two people's security events, yet its empty `customer_profiles`
listing is cited as proof that those two people's profiles are gone. Treating
every listing as global would mark other people's retained security records
missing (it produced 8 false SPEC_GAP results before this rule).
Trade-off: a genuinely global listing that never mentions the subject cannot
prove absence for them; that is reported, not guessed.

## D018 — Query keys do not prove ownership of a not-found target [sample-informed]

Identity on a lookup's input (`lookup_key`, `match_key`, `query`) links the
target resource only if the lookup returned the record. A 404/zero-row lookup
of resource X "with key Y" does not show that X belongs to Y. Explicit per-row
attribution (`subject_reference` in lookup tables and handover rows) still links
even for NOT_FOUND rows. Evidence: a not-found lookup of another account with a
subject's email was the only link to a resource the subject does not own.

## D019 — Identity: intervals, original bytes, conservative closure

`IdentityIndex` rebuilds each user's identifier history from the snapshot plus
the audit log (newest change first), so each value has a validity interval; only
values valid at or before audit_at are known. Matching uses casefolded email
keys; hashes are SHA-256 of the original email bytes. Closure adds an identifier
only from USER_PROFILE / ACCOUNT_TOMBSTONE rows or catalog links that tie it to
an already-proven identifier, and never if another known person owns it.
Reference fields (`user_id`, `customer_reference`, `subject_reference`) are
recognized by field, not by the dataset's `user_<n>` format.

## D020 — Strict cutoff even for identity links

Ownership links found only in files that became available after audit_at are
not used, even when the embedded record was observed earlier. This follows the
contract ("evidence created later must not change the answer") and the user's
instruction that both timestamps be eligible. Measured cost on samples: 5 missed
verification steps (security records linked only by later exports). If this is
relaxed for identity facts (not state), it must be a deliberate, documented
choice.

## D021 — Ungrounded primary profile is unresolved, never inferred

Every deletion has a USER_PROFILE obligation. When no eligible record ties a
profile resource ID to the subject, the engine looks for subject-level proof
(the latest covering exhaustive profile listings contain no row with the
subject's identifiers). That can satisfy the obligation but cannot produce a
step, because the contract needs a resource ID. It never guesses the ID from
numeric suffixes (2 of 490 grounded account/user pairs share a suffix), request
timing (3-15 candidate deletions per request window), or deletion claims that
name no person. Measured: grounded for 79 of 446 deletion requests in the full
set; 22 of 62 missed sample steps are ungrounded account IDs.

## D022 — Obligation model drives status and steps

`planner.obligation` compares the required final state (from `policy.Policy`) to
the reconstructed state: SATISFIED / VIOLATED / UNRESOLVED. `_derive` alone sets
the status: any VIOLATED -> INCOMPLETE; all SATISFIED (>=1 representable) ->
COMPLETE with cited checks; otherwise a disclosed fallback. A confirmed INCOMPLETE
plan includes checks for satisfied obligations and the primary-profile check even
if its current state is unproven (it must end absent in every deletion). Other
unresolved obligations are disclosed in `case_plans.jsonl`, not emitted.

## D023 — Policy semantics

Rules are loaded from the CSV at runtime; each known category has an expected
action, and any mismatch, unknown category, or expired retention becomes a policy
issue (no default DELETE). Support conversations/tickets satisfy rule_05's
shared-context condition by their nature (customer + agent + order); REDACT keeps
the stable `customer_reference` and removes direct identifiers. ANONYMIZE also
requires removing references. Security/legal records without a supplied
retention end are presumed retained (recorded as a note). BACKUP_ARCHIVE is
modelled as a restore obligation only. A REDACT/ANONYMIZE record that is entirely
absent is SATISFIED with VERIFY_RECORD_ABSENT.

## D024 — Intent: deterministic English only, explicitly limited

`intent.classify` recognizes English deletion, verification-of-deletion and
explicit preservation/negation statements; negated clauses are removed before
looking for deletion cues. Non-English text, mixed scope ("delete X but not Y")
and unrecognized text return UNRESOLVED, which becomes a disclosed fallback with
no steps. It never reads `reported_status`. It is not multilingual: 62 of 518
requests are currently unresolved for language alone. Patterns were written from
reading request texts (inputs), never from labels; a model-based classifier
should replace it.

## D025 — Determinism

Outputs are byte-identical across `PYTHONHASHSEED` values (checked for 10 seeds
on all fixtures and 4 full runs). A reference-following loop that iterated a
list while appending to it hung for some hash seeds; it now iterates a frozen
snapshot (`planner.discover`).
