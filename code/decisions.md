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

---

# Phase 3 decisions (correctness fixes, Jev decision layer, media)

## D026 — Identifier availability is separate from identifier validity (fix)

Each `Identifier` now carries `available_at`: the earliest availability of a source
establishing it (snapshot `updated_at`; each audit entry's `event_time` for both its old
and new value). `known_by(cutoff)` requires validity AND availability by the cutoff, so an
earlier alias evidenced only by a later audit entry is not known. The Phase 2 test that
permitted this was wrong and was replaced. Regression: `IdentityTimingRegression`.

## D027 — Exposure is assessed per surviving copy (fix)

`state.reconstruct` reads exposure from each present location's own latest exposure
observation. Any exposing copy keeps the record non-compliant; a copy whose exposure was
never assessed leaves the outcome unknown (never borrowed from another copy).
Regression: `ReplicaExposureRegression` (exposing replica observed earlier than a clean primary).

## D028 — Lookup context is not record identity (fix)

Family D `index_queries`: the query identifier is never attached to returned documents;
ownership comes only from each document's own fields. Logs: a trace's `*_started` lookup
keys link a returned resource only if the trace touched exactly one resource (all 994
traces in the corpus are single-target; all 58 search documents carry their own identity
fields, so the fix costs no real links). Regression: `SearchOwnershipRegression`, fixture
`fixture_mixed_search`.

## D029 — Explicit scope coverage (fix; replaces D017's file-wide rule)

`Scope.coverage` is `FULL_TABLE` (a table in a point-in-time database snapshot, only for
collections that table actually holds, and for inventory files only systems declared in
`snapshot_scope`) or `SUBJECT_SCOPED` (an export captured for an inferred subject set: the
identities in the same file and capture time). Exact lookups, readbacks and logs are never
listings. SUBJECT_SCOPED omission is OFF by default (`EngineConfig.subject_scoped_omission`)
because its subject set is inferred; measured effect of enabling it on samples: none.
Regressions: `SqlScopeRegression`, `ScopeCoverageRegression`.
Rejected: keeping the file-wide token heuristic (any subject token in a file covered every
collection) — that is the defect reviewed.

## D030 — Retention conditions must be supplied, not presumed (fix)

SECURITY_AUDIT_LOG / LEGAL_HOLD_RECORD without a supplied retention or hold end raise
`UNPROVEN_CONDITION`; the obligation is UNRESOLVED (never deleted, never verified as
retained). Retention evidence is taken from any eligible observation of the record
(readbacks, logs, inventory tables, the PDF journal extract). Measured cost on samples:
exact rows 6/32 -> 2/32 versus the `presume_retention` experiment; 38 full-set rows move
COMPLETE -> INCOMPLETE fallback. 558 of 662 security observations carry no retention end.
The experiment switch exists only for comparison. Regression: `RetentionRegression`,
fixture `fixture_no_retention`. **Open decision for the project owner** (see report).

## D031 — Relaxations are isolated experiments (`config.EngineConfig`)

`subject_scoped_omission`, `presume_retention`, `post_cutoff_ownership_links` default to
False and are recorded in every `run_manifest.json` (`engine_config`, `experimental`).
Sample effect of each is reported in `evaluation/evaluation_report.md`.

## D032 — Ownership-link investigation result

Of 36 missed sample resources attributed to ownership/cutoff: 0 had an eligible grounded
link the engine missed; 7 are linked only in sources available after audit_at; 29 have no
grounded link in any source (only operation claims naming no person, or records with no
identifier at all, e.g. 404 lookups and fully anonymized rows). No activity row except the
12 catalog links names a subject identifier. Rejected: request-window timing attribution and
suffix matching (unsupported associations). Tool: `evaluation/gap_analysis.ownership_details`.

## D033 — Relationship edges and multi-hop discovery

`SYNC_RECORD`/`RESTORE_BACKUP` rows naming a downstream resource ("repopulated downstream
resource X") become source->destination edges (the source lives in `source_system`); the
success flag still sets no state. Discovery traverses these edges and `source_resource_id`
references in both directions from directly linked records: depth <= 3, visited set,
eligible evidence only, and it stops at any record whose own evidence names another known
person. Measured sample effect: none (no regressions).

## D034 — System resolution ignores operation claims

A claim's `target_system` is where work was attempted, not which system owns the record
(two flows and a backup carried `search_service` claims). System resolution for media and
references uses STATE/LINK evidence first; claims only when nothing else exists.

## D035 — Provider architecture (plan section 21)

`providers.Gateway` exposes two distinct shapes: `decide()` -> `POST {base}/decisions`
(Jev typed answers) and `chat()` -> `POST {base}/chat/completions`. Credentials only from
`TOKENBAZAAR_API_KEY` (a local `.env` is loaded only with `--env-file`). Every call is checked
against a cost and call ceiling before sending; every attempt is recorded (requested and
returned model, tokens, reasoning tokens, latency, gateway cost header, retries, cache, mode).
Only validated responses are cached, keyed by endpoint + requested model + prompt version
hash + input. 429 responses honour Retry-After with up to 5 bounded retries (the first full
run lost 8 Jev calls to 429 after 2 retries; they escalated safely to the generative fallback).
`ReplayGateway` serves recorded responses for offline tests and is always labelled `replay`.

## D036 — Measured provider facts (bounded live probes, 25 Oct 2026)

- `typesafe/jev-latest` returns `typesafe/jev-1.13-20260917`; response: `answers.<q>` with
  `choice` + `probabilities` + `confidence` (choice) or `noul` (yes/no probability); usage
  `input_tokens`, `output_tokens`, `cost` (USD). Gateway header `x-tokenbaazar-cost-paise` is
  1 per call. Several questions about one request fit in one call.
- `google/gemini-2.5-flash` is served as `google/gemini-3.8-flash` (consistently; billed at the
  2.5 Flash rate by the header). `gemini-2.5-flash-lite` is served as `gemini-3.1-flash-lite`.
  The configuration keeps the user's requested ID and records the returned one on every call;
  this drift is disclosed, not hidden. Reasoning tokens dominate output (e.g. 577 of 594).
- `response_format` is ignored by chat; JSON is requested in the prompt and parsed strictly.

## D037 — Jev intent question design (prompts/intent_jev_v1.json)

One `choice` question (`DELETE_ACCOUNT`, `VERIFY_PRIOR_DELETION`, `SCOPED_DELETION`,
`NON_DELETION`, `AMBIGUOUS`) plus three `noul` questions about the same request:
`preserve_account`, `marketing_only`, `conflicting_instructions`. Deterministic
reconciliation (`intent_models.reconcile`): AMBIGUOUS, any conflicting flag not clearly "no",
a deletion choice with preserve/marketing not clearly "no", or family probability < 0.60 ->
UNRESOLVED; noul bands yes >= 0.70, no <= 0.30. Evidence for the extra flags: on 3 synthetic
contradictory requests Jev's choice was DELETE_ACCOUNT and only the flags prevented an
unsafe authorization.
Rejected: reconstructing intent from separate binary questions only (loses the five-way
category); mixing several requests into one Jev state (cross-contamination risk; one call
per request is cheap at ~1 paise).

## D038 — Family probability for authorization [sample-informed]

The 0.60 threshold applies to the probability of the chosen label's family (all deletion
labels together), not the sub-label: two sample requests split ~0.50/0.47 between
VERIFY_PRIOR and SCOPED (certain deletion, uncertain sub-type). Discovered on samples;
re-validated on the separately written synthetic set (72/72 unchanged).

## D039 — Staged intent and what each layer decides

- Deterministic: request parsing, language support check, English classifier as an
  independent cross-check, reconciliation of typed answers, all evidence/state/policy/status/CSV.
- Jev: the intent category and the three yes/no flags for every request; the document kind of
  each PDF; `choose_candidate` (validated bounded choice among grounded handles, for the
  investigator phase; not yet wired in).
- Generative (gateway `google/gemini-2.5-flash`): only requests Jev leaves unresolved or that
  disagree with a definite deterministic decision; batched (5) with opaque handles; every
  supporting span must be a verbatim substring; validated per item; never overrides a definite
  deterministic decision.
- Vision (same model ID): blind screenshot reading only.
Measured: Jev-only 72/72 synthetic and 32/32 samples, 0 unsafe; deterministic 16/72 with one
unsafe authorization (an English hypothetical "If I were to close my account..."); generative
72/72 and 32/32 at ~5x Jev's cost per decision. Full run: 518/518 resolved by Jev.
Probabilities are not treated as calibrated: accuracy was 100% in every probability band, so
there is no error signal to calibrate against.
Rejected: generative-first (cost, reasoning-token-driven truncation: one empty batch
invalidated 10 decisions in benchmark v1), deterministic-only (language gap and the unsafe bug).

## D040 — No Jev call for rule_05 (considered, rejected)

Rule_05's condition (shared customer/agent/order context) is definitional for support
conversations/tickets; 99 of 279 support observations carry no text; the 180 with text use
6 templates; the sample answers treat all support messages as REDACT. A Jev "no" could only
turn REDACT into an unsupported policy gap. No other unresolved decision point in the full run
is a choice among grounded alternatives (178 retention, 16 profile absence, 4 no category,
3 unknown state — all need evidence, not judgement).

## D041 — PDFs: native extraction + Jev document kind

`parsers/pdfsrc.py` (standard library): Catalog -> Pages -> content streams (Flate, ASCII85),
text operators, ToUnicode CMaps; pages/lines preserved. Literal harvest (IDs, identifiers,
labels, retain-until dates) is deterministic. One Jev choice classifies the document kind;
mapping is deterministic: SUPPORT_ATTACHMENT / SECURITY_AUDIT_RECORD -> records named in
the text (system from structured evidence or the manifest origin); CUSTOMER_STATEMENT_OR_EXPORT
-> the resource whose catalog link label appears verbatim; LEGAL_RETENTION_REGISTER rows are
order-keyed and match no structured record, so nothing is attached (reported).
Rejected: adding PyMuPDF/pypdf (dependency not needed for these files; native extraction
measured complete on all 4 PDFs, all pages); signature rules per document (would be tuned to 4 files).

## D042 — Screenshots: blind reader, conservative mapping

Prompt `vision_extract_v1.md` contains no case, subject or expected answer. Output must
parse strictly; IDs must match the record-ID pattern (malformed IDs such as `data_flow1121` are
rejected and reported); a user key shown as a title resolves to a record only through an exact
catalog label. A row yields PRESENT state with its own identity tokens and visibly exposed
identifier fields only if its system/category agree with the page kind and with structured
evidence; otherwise an identity link only. No enabled/restore flags are inferred from badges
("Healthy", "Continuous"); not showing a field never proves it empty. Measured against a manual
reading of all 11 screenshots: IDs 23/23 precision and recall, identifiers 18/18.
Synthetic image rendering would need an imaging dependency; offline tests use recorded responses.

## D043 — Provisional submission artifact

Root `output.csv` (518 rows) is produced by the strict default configuration with staged intent
and media, from cached validated responses; its diagnostics, disclosures, code/prompt
fingerprints and inference modes are in `runs/phase3-provisional/` and `validate --run-dir`
re-checks the CSV hash binding. Labelled PROVISIONAL in the manifest.

---

# Correctness-hardening pass (independent audit of e0aaef4)

Regression tests: `tests/test_hardening_regressions.py` (17 test functions, all fail on e0aaef4; an
earlier report of 19 counted wrongly).
Full 518-row output: byte-identical to the e0aaef4 provisional output (measured).

## D044 — Deletion authorization carries its scope (audit P0-A)

`Intent.scope` is `ACCOUNT` or `ITEM`; an ITEM deletion also carries `target`, one supplied
data category. Reconciliation: deletion vs not is still decided by the family probability
(D038); ACCOUNT scope additionally needs P(DELETE_ACCOUNT)+P(VERIFY_PRIOR_DELETION) >= 0.60
and a non-SCOPED label, otherwise the deletion is ITEM-scoped (never widened). An ITEM deletion
needs a bounded Jev choice among deletable categories (`prompts/intent_target_jev_v2.json` since D058,
asked only for ITEM outcomes); a target below 0.60 or `SEVERAL_OR_UNCLEAR` leaves the request
UNRESOLVED. The planner assesses only linked records of the target category, never the
account profile; a DELETE without an established scope is refused. Offline, a deletion that
names a specific item without asking for the account or the person's records (or referring
to an account deletion already requested) is UNRESOLVED.
Revised by D062: an account closure the person mentions is not an authorization to delete the
account now; the benchmark requires ITEM scope for every one-item scenario.
Rejected: adding the target question to the cached intent prompt (would re-buy all 518 intent
answers for a question that only ITEM requests need); treating every scoped request as
unresolved (discards grounded item requests).
Measured: no full-set request is ITEM-scoped (508 ACCOUNT, 10 NON_DELETION, unchanged).

## D045 — A conflict Jev detected is never escalated (audit P0-D)

`material_concern`: conflicting_instructions yes; marketing_only yes on any non-NON_DELETION
label; preserve_account yes on an account-scoped or ambiguous label. Such requests stay
UNRESOLVED without a generative call. When a request is escalated for another reason
(low probability, deterministic disagreement), a generative ACCOUNT authorization is rejected
if Jev said the account must be kept. Rejected: asking the generative model to "explain" the
conflict away — its output would still be a model claim against a detected contradiction.

## D046 — Authenticated requests never follow redirects (audit P0-B)

Reproduced: urllib forwarded `Authorization` to a different origin on a 302. All gateway
calls use an opener whose redirect handler refuses every redirect (the API does not redirect;
a POST redirect would be converted to GET anyway). Test: local origin + foreign server.

## D047 — PDF facts bind to their own record section (audit P0-C)

A harvested identifier or retention date belongs to the record ID whose mention most closely
precedes it on the same page; facts before any mention belong to the document's record only
when the document names exactly one. A section naming more than one user reference binds no
identity. Measured: each supplied PDF names one record, so no current observation changed.

## D048 — Media enrichment is available only once it was established (audit P1-2)

A screenshot/PDF observation enriched from structured evidence (system, category, catalog
label) is available from the earliest time at which that evidence already gave the same
answer (`media._settled_at`), never earlier than the capture. Measured: 3 media observations
depend on catalog links logged 7 minutes after capture and are delayed accordingly; output
unchanged. Rejected: the latest time of every consulted source (first implementation) — it
dropped four full-set screenshot findings whose system was already established before the
cutoff and merely re-confirmed later; enrichment only from sources available at capture —
discards those same-session links.

## D049 — Cross-person guard on every ownership link (audit P1-3)

A record whose own eligible state evidence names another known person is never attributed to
the subject, whether reached directly or by traversal (the multi-hop guard of D033 now applies
to direct links). A NOT_FOUND exact lookup that names both the resource and the subject still
links them: the lookup record is the system's own pairing, and the supplied labels verify such
resources (sample cases 11, 13, 14; 36 full-set obligations). Removing that link entirely, as the
audit proposes, is a policy choice left to the project owner. Measured effect of the guard: none.

## D050 — Trace identity is temporal (audit P1-4)

A log trace's lookup keys attach to a line only if they were logged at or before it.
Measured: no change (every `*_started` line precedes its result in the corpus).

## D051 — Subject-level profile absence (audit P1-1)

Kept as SATISFIED (status may be COMPLETE) only when every row in each latest covering
full-table listing carries an identifier and none is the subject's; a row with no identifier
blocks it (UNPROVEN_ABSENCE). The obligation is labelled `SUBJECT_LEVEL_ABSENCE` and produces
no step (no resource ID). Measured on all 95 COMPLETE rows: every listing row carries a full
identity of someone else (11 rows' identities are of people absent from user_identities), so
none flips. Rejected: marking all 95 incomplete — the evidence does establish that the subject
has no profile row in any covering snapshot.

## D052 — Retention and legal hold conditions (audit P1-5)

Verified, not changed. Rule_12 needs a supplied retention end; rule_13 a supplied hold whose
end is after audit_at. Legal-hold records carry `retained_until` and a reason; none carries an
open-ended or released hold flag, so a separate evaluator would have no evidence to read.
Sample cases 11, 13, 16, 31: the security record (audit_record_9394, _4816, _9528, _6909) appears
only in an exact lookup or a snapshot row with no retention field, in any source before or
after the cutoff; the labels expect VERIFY_RECORD_PRESENT. Strict default kept.

## D053 — Budget reservations cover calls in flight (audit P1-6)

Each call reserves a worst-case cost (25 paise per decision, 120 per chat call; the largest
billed calls on record are 1 and 99 paise) before it is sent; spent + reserved + next reserve must fit the ceiling; a live response without a cost
header is charged its reserve. Test: four concurrent calls at a 50-paise ceiling.

## D054 — Cache hits are validated on every read (audit P1-7)

The current validator runs on each cached response, together with a requested-model check; an
invalid entry is deleted and the call is made again. All 526 existing entries validate.

## D055 — Conflicting SQL snapshot times quarantine the snapshot (audit P1-8)

No state or scope is emitted and the source is PARTIAL. Measured: no supplied snapshot conflicts.

## D056 — Gap analysis uses the audited run's evidence (audit P1-9)

The corpus is rebuilt with the run's engine configuration and a cache-only gateway (zero call
ceiling); it must reproduce the run's source status and media extractions, or the analysis
stops. Ownership details are written by the same `gaps` command.

## D057 — Other hardening (audit P2)

Typed answers are validated by one function (`choice_problem`) for intent, target, document
kind and candidate choice; booleans are not probabilities; malformed or non-JSON responses
become ProviderErrors. A resource ID used by two systems is never traversed. A screenshot with
rejected entities or reader-flagged unreadable fields reports PARTIAL. Not changed: the
investigator SQL tool's snapshot-time check (P2-3, not used by inference) and PDF parser
portability (P2-6, documented limitation).

# Follow-up patch (review of 7b8c216)

Regression tests: `tests/test_followup_regressions.py` (6 tests; 5 fail on 7b8c216, the sixth guards
preserved behaviour). Full 518-row output and offline sample predictions: byte-identical.

## D058 — Item scope names one record, or all records of a category only when asked (review 1)

The target question (`intent_target_jev_v2.json`, never answered live, so no cached answer is
lost) adds a yes/no `whole_category`: only a clear yes authorizes every linked record of the
category. Otherwise exactly one record must be grounded from evidence: a linked record of the
category whose ID appears verbatim in the request, or the only linked record of that category.
Several candidates with none named leave the request UNRESOLVED (SCOPE_TARGET_UNGROUNDED). The
model never supplies an identifier. Test: one person, two attachments; "delete the attachment" ->
unresolved; "delete fx_attach_T2" -> only that record; "delete every file I attached" -> both.
Rejected: a Jev choice among candidate records (the request text rarely distinguishes them; a
probability would stand in for evidence).

## D059 — Reservation release and charge are one critical section (review 2)

`Gateway._record(rec, reserve)` releases the reservation and charges the cost under one lock, on
success, invalid-response and failure paths (a non-JSON body is charged its reserve; HTTP and
transport errors only release). Test reproduces the interleaving: a second reservation attempted
after the first response arrived but before it was charged is refused.

## D060 — NOT_FOUND lookups verify absence only (owner's decision on audit P1-3)

A record linked only by not-found lookups naming the subject (`planner.lookup_only`) keeps its
absence verification (VERIFY_RECORD_ABSENT), but cannot justify a finding or remediation
(OWNERSHIP_UNPROVEN, unresolved) and never seeds relationship traversal. Measured: no change on
samples or the full set.

## D061 — A PDF section binds identity only if it names at most one person (review 4)

More than one user reference, email or phone in a record section binds no identity: the text
gives no link between them. Measured: each supplied PDF section names at most one of each.

Owner's decision recorded: strict retention stays the default; sample cases 11, 13, 16, 31 remain
a documented mismatch between the available evidence and the expected answers (D052). Sample
labels are not used to broaden deletion authorization.

## D062 — Sample labels do not broaden authorization (owner's decision)

The first hardening pass let "my account was closed, but item X remains — delete it" authorize
the account scope offline and in the benchmark's expectation, because the supplied labels verify
the account in such cases. Reverted: offline, only an explicit request to delete the account
("delete my account", "the account to be erased") is account-scoped; the benchmark requires ITEM
scope for C_scoped_item. Measured offline-mode sample cost: status 27/32 -> 26/32, steps 134 -> 126
matched, exact rows 2 -> 1 (case_06 and case_28 become disclosed fallbacks: each mentions a closed or
deleted account and a search result, without asking to delete the account now). Staged and full
outputs unchanged.

# TokenBazaar compatibility patch

Regression tests: `tests/test_provider_compat.py` (15 tests, offline, using replay doubles and loopback
servers). The file cannot be imported on fedf680, because the functions it tests do not exist there.

## D063 — Gateway follows the documented TokenBazaar contract, keeping cache keys

- Chat sends the documented `max_completion_tokens`. `max_tokens` stays the Python keyword, so callers
  are unchanged; `max_completion_tokens=` overrides it. The chat cache key still uses the same limit
  value, so validated responses cached under `max_tokens` remain valid. Earlier live calls showed that
  `max_tokens=4000` really capped output at about 3996 tokens, so the old field already acted as the
  same limit. Whether the gateway honours the new field identically is **PENDING** a live check.
- `temperature: 0` is no longer sent. The documentation says it is ignored, and it was never part of
  the cache key. Determinism comes from the validated response cache, not from sampling.
- Optional `tools` / `tool_choice` are passed through to chat. They enter the cache key only when
  supplied, so calls without tools keep their old keys. Tool definitions are checked before sending.
  The tool calls in a response must pass `tool_call_problem`, which runs before the caller's
  validator and is also re-applied to cache hits. It rejects: malformed messages and calls, missing
  or duplicate IDs, unknown function names, arguments that are not a JSON-object string, and
  arguments that violate the tool's schema. The schema check covers type, enum, required, items, and
  unknown keys, which are rejected unless `additionalProperties` is true. It also enforces
  `tool_choice`. The gateway never executes a tool. Rejected: a tool registry or loop, which is Phase 4
  investigator work.
- HTTP 429 waits for `Retry-After`, else for the documented body message "Please wait Ns", capped at
  `MAX_WAIT_S` = 30 s. A malformed, negative or non-finite value falls back to the existing
  exponential backoff. Retries stay bounded by `max_rate_limit_retries`.
- 401, 402 and 403 stop at once. Error messages name the status with its meaning, for example
  "HTTP 503 (service unavailable): TokenBazaar service is temporarily unavailable". They include the
  provider's explanation from the JSON `error.message` / `message` / `detail` fields or the body text,
  with whitespace collapsed, bearer tokens and the API key redacted, and the text truncated to 200
  characters. The request body is never included.
- Usage records add `cached_input_tokens`, from `prompt_tokens_details.cached_tokens` (or
  `input_tokens_details.cached_tokens`) when it is a non-negative integer, and the usage summary sums
  it. Input, output and reasoning tokens, requested and returned models, and the cost header are
  unchanged.
- `list_models()`: a read-only `GET {base}/models` call over the same authenticated, no-redirect
  transport. It returns `{id, kind}` for entries with a non-empty string ID. `kind` is the entry's own
  `kind` (or `type`) string, else None. No tool or vision capability is inferred, because the catalog
  exposes no reliable flags. The live check (HTTP 200; the unversioned path redirects; the three
  project models are listed with the expected kinds) was made earlier and was not repeated.

Measured, with no cached responses in this environment (`.cache/` is gitignored), 0 network calls
and 0 paise:
- All 13 fixtures match their expected output.
- The full offline run and a zero-budget staged run are byte-identical between fedf680 and this patch.
- Both versions request the same 637 cache keys.
- Those keys include all 390 distinct keys behind the 533 cached responses recorded in
  `runs/phase3-hardened/usage.jsonl`.

The root `output.csv` and `runs/` are unchanged. Live staged sample and scope-aware benchmark
measurements stay **PENDING**: the decision and chat endpoints return HTTP 503.

# Review of 7de8e4d (targeted fixes)

Regression tests: `tests/test_review_7de8e4d.py` (6 tests). With the fixes reverted, the planner tests
fail (2 of 2) and the provider tests fail (4 of 4, in 18 subtests).

## D064 — Exact target identifiers; strict tool JSON and schemas; validators settle reserves

- Item targets match identifier tokens exactly. `planner.id_tokens` collects the request's
  `[A-Za-z0-9][A-Za-z0-9_.:-]*` tokens, without emails, so one ID never matches a longer ID it
  prefixes. Suppose the request names an ID-shaped token (it contains `_` and a digit) that is neither
  a linked candidate nor a subject identifier. Then, unless the whole category was asked for, the case
  stays UNRESOLVED (SCOPE_TARGET_UNGROUNDED), and the sole linked record is no longer substituted. No
  supplied request contains such a token, so outputs are unchanged.
- Tool-call arguments are parsed by `strict_json`. It rejects NaN, Infinity, numbers that overflow to
  infinity, duplicate keys at any depth, and nesting deep enough to cause a recursion error.
- Developer tool schemas are checked by `schema_definition_problem` before any reservation or network
  call. Only the enforced subset is allowed: type, enum, properties, required (which must name
  declared properties), boolean additionalProperties, and items, plus the annotations description and
  title. The top level must be an object schema. Any other keyword, such as pattern, anyOf or
  minimum, is refused rather than silently left unchecked.
- A validator or usage extractor that raises becomes an `invalid` usage record. For a live response,
  the reservation is released and charged in the same critical section, as in D059. For a cache hit,
  the entry is discarded and the call is asked again. Before this fix, the exception escaped with the
  reserve still held.

Measured, offline with 0 network calls: the 13 fixtures, the full offline run and the zero-budget
staged run are byte-identical to 7de8e4d, and the same cache keys are requested.

# Phase 4: investigator and independent verifier

Tests: `tests/test_phase4_casefile.py` (6), `test_phase4_investigator.py` (9), `test_phase4_verifier.py` (11)
and `test_phase4_pipeline.py` (4). All run offline: scripted replay models and a synthetic world.

## D065 — Case file: only validated, evidence-backed facts

`casefile.FactValidator` admits OWNERSHIP, STATE, CATEGORY and RETENTION facts. Every fact needs:
- a registered source that was available by audit_at and that a tool has shown in this investigation;
- a locator of at most 12 lines on one page, with a literal copied verbatim from it;
- an exact resource token that a tool returned or a candidate already holds;
- a system grounded by evidence, the manifest origin, or the activity stream.

Each kind then needs its own support:

| Kind | What else must hold |
|---|---|
| OWNERSHIP | The literal names a subject identifier, and the whole page (documents, screenshots) or the cited lines name no other known person (D061). |
| STATE ABSENT | A readback cue, no operation claim, and a machine-readback source (SQL, JSON or log). |
| STATE PRESENT | A presence cue, or a captured document page that names the record (D041). |
| CATEGORY | A supplied category, a cue word, and consistency with the system and with existing evidence. |
| RETENTION | The ISO date appears in the literal next to a retention or hold cue. |

Rules for every kind:
- Activity rows never prove state.
- Cited lines that read like instructions are refused (prompt injection).
- Accepted facts become ordinary observations (`fact_observations`, `Corpus.extended`), and
  `plan_case` re-plans from them. The case file never holds a status or steps.

Rejected: a model-written status or step list, and facts without literals.

## D066 — Investigator: the model picks evidence; code bounds and validates

The loop runs on `Gateway.chat` with native tools, `tool_choice=required` and the strict tool schemas
(D063 and D064). There are five read-only evidence tools plus `propose_fact` and `submit_case_file`. They
are cutoff-filtered and subject-scoped: only this subject's identifiers or handles the tools already
returned may be searched or queried. They are bounded, with truncation flags, and SQL lookups use
allowlisted tables and columns.

Limits:
- 4 routing rounds and 1 schema repair;
- 16 tool calls and per-case caps of 60,000 tokens and 400 paise, under the run ceiling;
- an identical call is refused until new evidence arrives, and a failing tool ends only that call;
- 2 verifier-feedback rounds carry new blocking and routing findings.

Routing is deterministic: fallbacks, unresolved records and verifier routing findings. Verified cases
are never routed, so they cost no call.

Outcome: if the investigator submits and the verifier finds nothing blocking, the case is ACCEPTED.
Otherwise the Phase 3 plan is kept.

Modes:
- **live** refuses to start without a passed `probe-tools` record for the model. That record comes
  from one bounded native tool-call probe (`probe_tool_calling`).
- **replay** serves cached validated responses only, under a zero ceiling, so it never makes a
  network call.

Rejected: a general agent framework, parallel investigations, and choosing a model before the probe.

## D067 — Independent verifier on every plan

`verifier.Verifier` re-derives what it checks from eligible observations, the supplied rules and the
output contract. It does not use the planner's discovery, state or obligation code. It checks:
- **Integrity:** the row passes `check_row`.
- **Citations:**
  - the source is registered and available by audit_at;
  - the cited source holds a non-claim observation of the stated state;
  - no newer copy contradicts it;
  - a COMPLETE absence agrees for every copy, including omissions from exhaustive listings.
- **Ownership:**
  - the record carries a subject identifier or is reached through explicit references;
  - not-found lookups support absence checks only;
  - no record names another person.
- **Policy:**
  - the operations match the supplied rule for the record's category;
  - a conditional retention needs a supplied date;
  - an ITEM request stays in its category.
- **Facts:** each accepted fact is re-checked against its text. A presence fact whose page says the
  record was removed is refused.
- **Coverage** (account and whole-category scope only): omitted linked records, active flows or open
  restores that touch them, and unparsed sources that mention the subject.

Findings are BLOCKING or ADVISORY. Routing findings drive investigation and feedback. A final plan
with a blocking finding is **withheld**: it becomes a disclosed VERIFIER_REJECTED fallback.

Mutation tests cover twelve corruptions. Measured on Phase 3 offline plans: 0 blocking findings on the
32 samples and on all 518 requests.

## D068 — Measured Phase 4 effect (offline; live investigation blocked)

No TokenBazaar key exists in this environment, and the gateway last answered HTTP 503, so no
investigator model has passed a tool-call probe. Results, comparing each mode with itself:
- **Samples, offline intent:** the Phase 4 engine with the verifier, with or without the investigator
  in replay, is byte-identical to Phase 3 offline (b69f2b0). Scores: status 26/32, steps 126/127
  precision and 126/193 recall, exact rows 1/32. 13 of 32 cases are routed; replay stops each one at
  the zero ceiling, with 0 network calls.
- **Full 518, offline intent:** also byte-identical and VALID. 184 cases are routed.
- **Scripted-transcript demonstration** (`evaluation/scripted_transcript_demo.py`; NOT model
  performance): on real evidence, case 07's `support_001` record, found only in the `evidence_58` PDF,
  is recovered. Its finding, remediation and verification match the labels, so recall goes from 126
  to 129 of 193 with precision 129/130. A first ownership proposal without an identifier was refused.
  Exact rows stay 1/32: case 07 also needs the account record, which no evidence links to its owner (D032).

Pending live: the tool-call probe, the investigator on the 13 routed samples and the 184 routed full
cases, and the staged comparison against the 28/32, 142/193, 2/32 baseline.

## D069 — Investigator outcomes follow the safe-fallback contract (review of e98dcea)

Tests: `tests/test_review_e98dcea.py` (4 tests, 7 failing subtests on e98dcea).
- A `submit_case_file` counts only when `_execute` accepted it. A submission refused by the tool limit
  is not a submission.
- Only a successfully submitted case file that the verifier accepts is published. ROUND_LIMIT,
  FEEDBACK_TURN_LIMIT, CASE_BUDGET, RUN_BUDGET and MODEL_ERROR keep the Phase 3 plan, even when valid
  facts were already accepted. The case file still records them.
- Feedback has its own allowance. Evidence routing keeps `max_rounds` (4). Each of at most
  `max_feedback_rounds` (2) feedback rounds may use `max_feedback_turns` (2) model turns. So a case
  makes at most 4 + 2 × 2 = 8 model turns, plus one schema repair, all under the per-case token and
  paise caps and the run ceiling.

## D070 — Two provider profiles with role-based models (TokenBazaar default, Roar AI)

Tests: `tests/test_providers_dual.py` (13 tests). They use the response shapes recorded in the live Roar
smoke tests; no credentials were recorded. `providers.PROFILES` holds one profile per provider, with its
endpoints, credentials and a model for each role. A role set to None is skipped and disclosed, never
substituted:

| Role | TokenBazaar | Roar AI |
|---|---|---|
| `jev_model`: intent, flags, scoped target, PDF document kind | `typesafe/jev-latest` (`/decisions`) | `jev` (`/evaluate`), live-tested |
| `intent_fallback_model`: unresolved or conflicting intent only | `google/gemini-2.5-flash`, served as `google/gemini-3.8-flash` | `gpt-oss-20b`, a candidate: response contract and multilingual behaviour not yet validated |
| `vision_model`: blind screenshot reading | `google/gemini-2.5-flash` | none until one is validated; `--vision-model` must be catalogued with `roar_image_input: yes` |
| `investigator_model`: Phase 4 native tool calls | none (never validated) | `gpt-oss-20b`, live probe passed |

How each provider is used:
- **TokenBazaar.** Base URL, `/decisions`, model IDs, the cost header, reserves (25 and 120 paise) and
  cache keys are unchanged. All 390 historical cache keys are still requested, and the fixture,
  offline and zero-budget staged outputs are byte-identical. It needs `TOKENBAZAAR_API_KEY`, or proxy
  authentication with `--proxy-auth`.
- **Roar AI.** Base URL `https://api.roar-ai.com/v1`. `/evaluate` takes `boolean` questions where
  TokenBazaar takes `noul`. Answers `{type: boolean, probability}` are adapted to the internal `noul`
  shape before the same validators run; choices already match. Usage arrives as
  `inputTokens`/`outputTokens`, there is no cost header, and evaluate responses carry no model field.
  Roar uses `ROAR_API_KEY` locally; without it, no Authorization header is sent and the Claude Cloud
  egress proxy injects it.
- **Caches.** Roar cache keys are prefixed with the provider name, so a provider never serves another
  provider's entry.
- **Costs.** `cost_paise_header` is the actual billed cost, and Roar reports none. `cost_estimate_paise`
  is catalog price × tokens × `USD_PAISE` (1 USD ≈ ₹88, an assumption). The usage summary counts
  `unpriced_live_calls`. The budget charges the actual cost, else the estimate, else the reserve.
  Roar reserves are 5 paise per decision and 50 per chat call.
- **Selection.** `--provider`, `--intent-provider` and `--investigator-provider` choose providers, and
  `--jev-model`, `--intent-fallback-model`, `--vision-model` and `--investigator-model` override role
  models. Probe records are keyed `provider:model`.

The investigator, verifier, deterministic policy, output contract and Phase 4 limits are shared
unchanged. PDFs still use native text and Jev document kind, never a vision model.

Live smoke tests, 2026-10-25 (synthetic inputs only):
- `jev` `/evaluate`: HTTP 200, a valid choice distribution and a boolean probability, 412 input and 55
  output tokens. The first request was refused with HTTP 400 because it used `noul`, and was resent
  once with `boolean`.
- `claude-haiku-5-5` forced tool call: HTTP 200, valid; 510 and 39 tokens.
- `gpt-oss-20b` forced tool call: HTTP 200, valid; 130 and 34 tokens, 8 of them reasoning.

No cost headers were returned. The catalog estimate for all calls together is about USD 0.00011.

## D071 — Roar screenshot reader: `claude-haiku-5-5`

It was benchmarked live on the 11 screenshots against the manual ground truth
(`evaluation/vision_ground_truth.py`):

| Model | Resource IDs | Identifiers | Page kinds vs Phase 3 | Estimated cost for 11 images |
|---|---|---|---|---|
| Phase 3 TokenBazaar reader (`gemini-2.5-flash`, served as `gemini-3.8-flash`) | 23/23 | 18/18 | – | 529 paise (billed) |
| `claude-haiku-5-5` (Roar) | 21/23, no false IDs | 17/18, no false identifiers | 11/11 | 91 paise |

Haiku missed `support_9538` (`evidence_04`), and `analytics_7438` with its email (`evidence_19`). A missed
value yields no evidence rather than wrong evidence, so the reader can lose recall but cannot add
false facts. All 11 responses passed the existing strict extraction validator. The user chose it
over benchmarking `gpt-6-luna`. It is the Roar profile's `vision_model`, still overridable with
`--vision-model`. PDFs still use native text and Jev.

Measured on the 32 samples (Roar jev, investigator off), all 49 responses came from cache, with 0 calls:
the predictions are identical to the run without screenshots. These screenshots carry no evidence for
the sample subjects.

## D072 — Second independent review (against e858907): every finding confirmed and fixed

Tests: `tests/test_review_audit2.py` (18 tests; all 17 original ones failed before these fixes). The 13
fixtures, TokenBazaar outputs and all 390 historical cache keys are unchanged, and Roar sample predictions
are unchanged. No output prediction changed.

| Finding | Root cause | Fix |
|---|---|---|
| P0 ceiling | `main.py` gave intent and investigator gateways the full `--budget-paise` each. 530be0b already trimmed the investigator to what the intent calls left, but only by run order, with no shared accounting | `Gateway.share_budget`: one ledger (lock, counters, ceiling) for both live gateways, so concurrent reservations through either cannot exceed it together. Standalone and replay gateways keep their own ledgers; replay keeps its zero ceiling. |
| P1 shown lines | Facts needed only an inspected source, not displayed lines | Tools report exactly what they displayed; `CaseFile.shown` records lines shown in full; a fact citing any other line is UNSHOWN_LINES |
| P1 hidden handles | SQL handles came from all matching rows (only 20 shown); PDF `identifiers` from all page lines (60 shown); clipped results registered everything | Handles and lines are registered after clipping, only from displayed items whose text survives the preview |
| P1 planner targets | `tokens - plan.subject.tokens` subtracted `(kind, key)` tuples from strings, so the subject's own user ID counted as an unknown named target | Subtract the subject's identifier values and user IDs |
| P1 verifier traversal | Reference traversal adopted another person's record and expanded from IDs reused across systems, so a legitimate plan got OMITTED_RESOURCE | Never adopt a record whose state evidence names another known person; never expand from an ambiguous ID (still independent of planner discovery) |
| P1 stale citation | `all(o is not s for s in support if s is not o)` is always true, so any older supporting observation in the cited source made the citation stale | Stale only if every supporting observation in the cited source is contradicted by a newer one at its copy |
| P1 citation accounting | WRONG_OPERATION pairs on cited steps counted as correct citations | Correct = exact six-field match of an expected cited step |
| P1 gap reconstruction | A manifest without a provider record silently rebuilt as TokenBazaar; a rebuild needed the global cache | Phase 4 runs without a provider record are refused (Phase 3 manifests stay TokenBazaar). Each run now stores its validated responses in `model_cache/`, and the rebuild uses them with a zero ceiling: reproducible with no paid call |
| P2 catalog failure | `load_pricing` let HTTP and transport errors escape | Unpriced operation with `pricing_error` recorded |
| P2 malformed cache | `json.loads` on a corrupt entry crashed the call | A corrupt or non-object entry is a miss and is deleted |
| P2 explicit proxy auth | `--proxy-auth` still sent a local key when one was set | Explicit proxy auth never sends one; the Roar profile default sends a local key if set, else relies on the proxy |
| P2 cost reporting | `model_spent_paise_header` summed the budget ledger (estimates and reserves) | It now sums provider-reported costs only; `budget_charged_paise` is the ledger; `model_cost_paise` splits actual, estimated and unpriced |

## D073 — Complete repository audit, offline fixes (2026-10-25)

Continue on existing local main from 46f22b2. Reproduce failures before repairs: properly frame
loopback HTTP responses; ground facts only in fully displayed source items; protect inputs and
completed runs from output collisions; bind evaluation to evidence bytes; scope state, categories
and fact metadata to the audit cutoff; independently verify output coverage and treated copies;
preserve contradictory-request authorization; bound PDF decompression; close SQL/error streams;
make cache writes atomic under concurrency; reject invalid accounting and admit investigator turns
against remaining budgets. No provider roles or deterministic decision authority change.

The full findings, regression evidence and current measurements are in
`evaluation/CODE_AUDIT_REPORT.md`. New measurements use offline inference, copied response caches,
replay doubles and local HTTP servers only. Historical experiment results, root output, datasets,
credentials, caches and recovery stashes are preserved. No paid calls or push are authorized for
this audit; fixes are committed locally for independent review.

## D074 — Budgeted quality experiments and retained offline improvements

Starting from local main 5cd8b54, compare fresh offline and both cached provider samples before
changing inference. Roar cached baseline remains strongest here: 28/32 statuses, 141/193 correct
steps, 1/32 exact rows. More Haiku rounds and a focused Luna investigator produce no sample gain;
restore the experimental prompt and keep default models/round limits. Two TokenBazaar target
calls return HTTP 503; do not treat those missing cache entries as language-classification quality.

Retain literal English item-target recognition and explicit prior-account-deletion verification,
with stricter safeguards against model scope/category widening; retain native PDF structures in
offline mode and source-diverse paginated search. The independent verifier and all ownership,
cutoff and retention requirements remain in force. Offline sample gains 10 correct steps, 1 status,
and 6 correctly cited expected steps, with no new incorrect step; exact rows remain 1/32.

The cumulative experiment ledger stops at 8,947.98576 paise conservatively accounted, including
unknown failed-call reserves. Catalog estimate is 2,752.399012 paise; provider actual billing is
unavailable. No further paid calls. See `evaluation/QUALITY_EXPERIMENTS.md` for comparisons,
budget assumptions, tests, limitations and the unchanged best staged measurements. Preserve the
original output and historical artifacts; commit locally without pushing.

## D075 — Primary agent owns final proposals; baseline remains selectable

Add `--decision-mode agent-first`: identity/policy/source context → dynamic evidence tools → full
agent-authored authorization, status and structured steps → independent verification and feedback.
Do not run `decide_intents`, `plan_case`, investigator `_replan` or baseline `withhold` on this path.
Preserve verified semantic choices; emit a hash-bound explicit fallback if no proposal verifies.

The first implementation is strictly offline. Jev media is optional and cached; live gateways,
network cache misses, environment loading and pricing requests are disabled on the agent-first CLI.
Tests demonstrate preserved alternative citations, authored COMPLETE/NOT_APPLICABLE, unparsed
attachment discovery changing three baseline steps into six, correction after rejected fabrication,
and unchanged baseline behavior. Strict scope, retention, cutoff and witness gates remain enforced.

Four scripted scenarios improve exact rows from 3/4 to 4/4 and recall from 8/11 to 11/11; this is
an architecture/control test, not real-model accuracy. Public historical caches contain no compatible
new-protocol proposals, so the public agent replay produces only disclosed fallbacks. No new paid
campaign is opened. See `evaluation/AGENT_FIRST_ARCHITECTURE.md` for metrics, limitations and the
separately authorized model experiment still needed before adopting this as a submission path.

## D076 — Bounded live selection and independent authorization review

Continue main from 8779251. Add separately opted-in live primary execution through the existing
provider gateway; require explicit budget/reserve/call limits and a prior tool probe, disable
transport retries and catalog requests, and keep replay as default. Share primary/media/reviewer
spending, persist validated used response caches, and fail safely without planner replacement.

Permit only independently owned and currently proven absent parent-profile verification in a
search-item audit. Require retrieved proof even for blank future-verification CSV citations; never
widen destructive scope. Scope hypothetical-question checks to operative deletion clauses and
preserve incidental questions and prior-removal inspections. Optional distinct-model request-only
review can confirm otherwise unresolved multilingual non-deletion, with strict fail-closed checks.

Thirty new regression/mock integration tests; full discovery 358 and dedicated provider checks 40
pass on Python 3.11 and 3.14, all 13 fixtures pass, Ruff passes, and zero-call replays preserve the
starting baseline and root output. No paid calls or push. Live provider/model quality and billing
remain unverified. See `evaluation/AGENT_FIRST_REVIEW_FIXES.md` for evidence and readiness limits.

## D077 — Real Haiku pilot fails final authorization; retain offline feedback fixes

Continue main at b6a9198 with a separate 1,000-paise exposure cap. TokenBazaar's free catalog is
reachable but its minimal Gemini tool probe returns HTTP 503; preserve 25 paise unknown exposure.
User then selects Roar/Haiku 5.5 and authorizes one further autonomous-tools attempt. Two case_05
runs make 11 successful Roar calls: the first never submits; the second submits two proposals but
neither has valid authorization. It repairs missing remediation/verification, with all three raw
steps matching public truth, but the verifier correctly rejects UNRESOLVED. Both final CSVs are
empty disclosed fallbacks versus an exact deterministic baseline. Do not run case_07 or expand.

Retain offline-tested execution-budget notices, general scope/submission guidance and accurate
UNRESOLVED feedback without widening limits or weakening the verifier. Four regressions added;
362 tests pass on Python 3.11/3.14, Ruff and 13 fixtures pass, captured-response replay makes no
network calls. Final prompt/feedback changes remain unverified live. New exposure is closed at
360.384 paise conservatively, with 165.344 paise catalog-estimated Roar spending at assumed
Rs100/USD and actual billing unavailable. Prior 8,947.98576-paise campaign remains closed and
unchanged. Preserve all original artifacts; commit locally, no push. Full results:
`evaluation/AGENT_FIRST_LIVE_PILOT.md`. The subsequent explicit user instruction PUSH authorizes
publishing the verified commit on main.

## D078 — Verify two real agent outputs with staged permission and finalization

Continue from c8e12c9. Explicit user continuation authorizes the two pilot cases and another Rs10,
raising the cumulative pilot cap to 2,000 paise without resetting any previous event or the older
closed campaign. Retain optional request-first interpretation by the same primary agent, an optional
execution finalization window, and precise native schema/field feedback. No planner-generated
semantics, ownership invention or verifier relaxation. Limits stay eight rounds/40 tools/120k case
tokens. Eight regression tests prove agent semantic independence, unsafe-proposal rejection and
field repair. Full suites pass 370 tests on Python 3.11 and 3.14; fixtures, Ruff and real-cache replay pass.

The selected configuration yields two independently verified non-fallback answers: case_05 is exact
(3/3 steps); case_07 repairs a rejected proposal and matches 3/4 steps. Combined precision 6/6,
recall 6/7, citations 2/2, statuses 2/2, exact rows 1/2. This matches the fresh deterministic baseline;
it improves actual agent completion over prior empty fallbacks, not held-out baseline accuracy.
Do not invent account_007 ownership to match the remaining label. Broader activity search worsened
case_07 by distracting Haiku with an ownership-only link; revert that experiment and preserve its trace.

Close the entire pilot at 1,438.012 paise conservative exposure, 671.725 paise catalog estimate at
assumed Rs100/USD, actual billing unavailable. All failed and successful attempts are included;
prior 8,947.98576-paise campaign stays closed and unchanged. Preserve original root output and logs;
commit locally without pushing. See `evaluation/AGENT_FIRST_LIVE_CONTINUATION.md`.

## D079 — Freeze and evaluate all 32 samples; decline full-scale agent run

The user explicitly authorizes all 32 public samples and a Rs100 cumulative pilot cap. Freeze main
3a8583b's successful two-case settings, reuse its 11 response-cache entries, and process the other
30 cases with Roar Haiku. No prompt/model/limit tuning during inference and no expected-label reads
until it finishes. All 32 cases complete; 165 new API attempts include four invalid responses and
two timeouts, with no transport retries. Four final answers verify and 28 remain disclosed fallbacks.

Full results: exact rows 1/32, statuses 22/32, precision 12/12, recall 12/193, expected cited steps
4/75. Fresh baseline: exact 1/32, statuses 27/32, precision 136/137, recall 136/193, citations 54/75.
The small pilot did not generalize. Preserve the real result; do not silently substitute baseline
steps or relax ownership, policy, source, coverage or verifier constraints. No 518-case paid run.

Close the expanded pilot at 7,086.048 paise cumulative conservative exposure, including 5,648.036
new batch exposure and 2,468.792 paise batch catalog estimate at assumed Rs100/USD. Actual billing
is unavailable; prior 8,947.98576-paise campaign stays closed and untouched. All 32 predictions
validate and replay byte-identically with zero network calls on Python 3.11/3.14. Preserve all 1,371
protected hashes and root output. Commit evaluation evidence locally without pushing. See
`evaluation/AGENT_FIRST_SAMPLE32.md` and `evaluation/agent_first_sample32_metrics.json`.

## D080 — Disclosed policy-undecidable records; complete contract feedback (offline)

An owned record whose supplied rule cannot decide its treatment (for example `rule_12` with no
supplied retention period) made every account-scope agent proposal unverifiable: omission,
retention and deletion were each rejected, while the baseline publishes the rest as `PARTIAL_PLAN`.
The agent may now list such a record under `policy_undecided`. The independent verifier accepts it
only when it also cannot decide the policy, the record is owned, retrieved and unstepped, the scope
is account and the status is `INCOMPLETE`. It is never certified, `COMPLETE` stays impossible, and
retention is never presumed. Contract feedback lists every row problem at once; a cited remediation
no longer crashes the verifier; an `AUTHORIZATION_UNRESOLVED` rejection echoes the agent's own
recorded interpretation without applying it. Offline only: the baseline outputs are byte-identical,
and live accuracy is unmeasured. See `evaluation/CLOUD_HANDOFF.md` (Cloud follow-up).
