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
