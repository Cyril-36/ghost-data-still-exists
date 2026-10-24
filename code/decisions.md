# Implementation decisions

Each entry: decision, why, evidence, and where it lives. Phase 1 = contracts,
validation, serialization, fixtures A/C, scoring. No inference exists yet.

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

## D005 — At most one step per (phase, system_id, resource_id)

`canonical_steps` merges exact duplicates and rejects conflicting ones (e.g.
DELETE and REDACT for the same resource). All 32 samples comply. This makes
replica copies that share a resource ID one output obligation, as plan §10.4
requires.

## D006 — INCOMPLETE plans must be internally consistent

For each resource: a finding needs its mapped remediation
(REMAINING→DELETE, EXPOSED_PII→REDACT|ANONYMIZE, ACTIVE_FLOW→DISABLE,
RESTORE_RISK→BLOCK). A remediation needs a finding and the matching final
verification. Verification-only resources (already compliant, or retained) are
allowed. Sources: plan §16.3; AGENTS.md §6.5 "Verification must cover the
required final state for each remediated record". All 32 samples comply.
Implemented in `output._check_plan_chain`.

## D007 — INCOMPLETE requires at least one FINDING (strict)

The contract says INCOMPLETE contains findings. Plan §17 allows a disclosed
best-effort fallback (`INCOMPLETE []`), but that requires run-linked disclosure
artifacts that do not exist yet. Until they do, the validator rejects such rows
rather than allowing unexplained fallbacks. Revisit when the publisher is built.

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
