"""Canonical step ordering, output validation and deterministic CSV publication.

This module checks the *structure* of an answer: legal tuples, status/phase
rules, citation existence and eligibility, ordering and numbering. It cannot
tell whether a cited source actually supports the asserted state.
"""
from __future__ import annotations

import csv
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from .contracts import (
    FINDING_REMEDIATIONS, OUTPUT_HEADER, REMEDIATION_VERIFICATION, STATUSES, STEP_FIELDS,
    ContractError, EvidenceRef, OutputRow, Request, Step,
)
from .inputs import read_csv


def case_sort_key(case_id: str):
    """Row order: numeric for `case_<digits>` (the supplied input order), else literal.

    Used only for ordering rows; never for identity or resource ownership.
    """
    match = re.fullmatch(r'case_(\d+)', case_id)
    return (0, int(match[1]), case_id) if match else (1, 0, case_id)


def canonical_steps(steps: Iterable[Step]) -> tuple[Step, ...]:
    """Merge identical steps, reject contradictory ones, and sort canonically.

    A slot is (phase, system, resource, facet): one resource may carry a record
    step and a restore step, but never two different record steps in one phase.
    """
    by_slot: dict[tuple[str, str, str, str], Step] = {}
    for step in steps:
        slot = (step.phase, step.system_id, step.resource_id, step.facet)
        previous = by_slot.setdefault(slot, step)
        if previous != step:
            raise ContractError(f'conflicting steps for {slot}: {previous} vs {step}')
    return tuple(sorted(by_slot.values(), key=lambda s: s.sort_key))


def steps_to_json(steps: Sequence[Step]) -> str:
    objects = [{'step_no': n, 'phase': s.phase, 'system_id': s.system_id,
                'resource_id': s.resource_id, 'operation': s.operation,
                'state': s.state, 'evidence_id': s.evidence_id}
               for n, s in enumerate(steps, start=1)]
    return json.dumps(objects, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _no_duplicate_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ContractError(f'duplicate JSON key {key!r}')
        obj[key] = value
    return obj


def _reject_constant(name):
    raise ContractError(f'non-standard JSON constant {name}')


def parse_steps_json(text: str) -> tuple[Step, ...]:
    """Parse steps_json strictly: exactly seven keys per step, step_no = 1..N."""
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ContractError(f'steps_json is not valid JSON: {exc}') from exc
    if not isinstance(data, list):
        raise ContractError('steps_json must be a JSON array')
    steps = []
    for expected_no, obj in enumerate(data, start=1):
        if not isinstance(obj, dict) or set(obj) != set(STEP_FIELDS):
            got = sorted(obj) if isinstance(obj, dict) else type(obj).__name__
            raise ContractError(f'step {expected_no}: expected exactly fields {STEP_FIELDS}, got {got}')
        if type(obj['step_no']) is not int or obj['step_no'] != expected_no:
            raise ContractError(f'step {expected_no}: step_no must be the integer {expected_no}, got {obj["step_no"]!r}')
        steps.append(Step(**{k: obj[k] for k in STEP_FIELDS if k != 'step_no'}))
    return tuple(steps)


def check_row(row: OutputRow, request: Request, evidence: Mapping[str, EvidenceRef] | None = None,
              disclosed_fallback: bool = False):
    """Validate one row against its request; raise ContractError with the case ID."""
    try:
        _check_row(row, request, evidence, disclosed_fallback)
    except ContractError as exc:
        raise ContractError(f'{row.case_id}: {exc}') from exc


def _check_row(row, request, evidence, disclosed_fallback=False):
    status, steps = row.deletion_status, row.steps
    if status not in STATUSES:
        raise ContractError(f'unknown deletion_status {status!r}')
    if tuple(steps) != canonical_steps(steps):
        raise ContractError('steps are duplicated or not in canonical order')
    phases = {s.phase for s in steps}

    if status == 'NOT_APPLICABLE' and steps:
        raise ContractError('NOT_APPLICABLE must have an empty step array')
    if status == 'COMPLETE' and phases != {'VERIFICATION'}:
        raise ContractError('COMPLETE needs one or more VERIFICATION steps and nothing else')
    if status == 'INCOMPLETE':
        if 'FINDING' not in phases:
            # Only a run-disclosed best-effort fallback (plan section 17) may lack findings,
            # and then it can only list final-state checks, never remediation.
            if not disclosed_fallback:
                raise ContractError('INCOMPLETE needs at least one FINDING (or a disclosed fallback)')
            if phases - {'VERIFICATION'}:
                raise ContractError('a fallback row without findings may contain only VERIFICATION steps')
        _check_plan_chain(steps)

    for step in steps:
        must_cite = status == 'COMPLETE' or step.phase == 'FINDING'
        if must_cite and not step.evidence_id:
            raise ContractError(f'{step.operation} {step.resource_id} must cite evidence')
        if not must_cite and step.evidence_id:
            raise ContractError(f'proposed {step.operation} {step.resource_id} must have empty evidence_id')
        if must_cite and evidence is not None:
            ref = evidence.get(step.evidence_id)
            if ref is None:
                raise ContractError(f'unknown evidence_id {step.evidence_id!r}')
            if ref.available_at > request.audit_at:
                raise ContractError(f'evidence_id {step.evidence_id!r} available at '
                                    f'{ref.available_at.isoformat()} is after audit_at')


def _check_plan_chain(steps):
    """Each finding gets its remediation; each remediation its matching final check."""
    by_resource: dict[tuple[str, str, str], dict[str, str]] = {}
    for s in steps:
        by_resource.setdefault((s.system_id, s.resource_id, s.facet), {})[s.phase] = s.operation
    for (system_id, resource_id, facet), ops in by_resource.items():
        name = f'{system_id}/{resource_id} ({facet})'
        finding, remedy, check = ops.get('FINDING'), ops.get('REMEDIATION'), ops.get('VERIFICATION')
        if finding and remedy not in FINDING_REMEDIATIONS[finding]:
            raise ContractError(f'{name}: {finding} needs one of {FINDING_REMEDIATIONS[finding]}, got {remedy}')
        if remedy and not finding:
            raise ContractError(f'{name}: {remedy} has no supporting finding')
        if remedy and check != REMEDIATION_VERIFICATION[remedy]:
            raise ContractError(f'{name}: {remedy} must be verified by {REMEDIATION_VERIFICATION[remedy]}, got {check}')


def validate_output(rows: Sequence[OutputRow], requests: Sequence[Request],
                    evidence: Mapping[str, EvidenceRef] | None = None,
                    fallback_ids: frozenset[str] = frozenset()):
    """Exact case set, no duplicates, canonical row order, and every row valid.

    `fallback_ids` are cases disclosed as unresolved by the run that produced the
    rows; nothing else may relax the INCOMPLETE-needs-a-finding rule.
    """
    by_case = {r.case_id: r for r in requests}
    seen = set()
    for row in rows:
        if row.case_id in seen:
            raise ContractError(f'duplicate output row for {row.case_id}')
        if row.case_id not in by_case:
            raise ContractError(f'output row for unknown case {row.case_id}')
        seen.add(row.case_id)
        check_row(row, by_case[row.case_id], evidence, row.case_id in fallback_ids)
    missing = [c for c in by_case if c not in seen]
    if missing:
        raise ContractError(f'{len(missing)} case(s) missing from output, e.g. {missing[:5]}')
    ids = [r.case_id for r in rows]
    if ids != sorted(ids, key=case_sort_key):
        raise ContractError('rows are not sorted by case_id')


def read_output(path: Path) -> list[OutputRow]:
    path = Path(path)
    rows = []
    for line_no, raw in enumerate(read_csv(path, OUTPUT_HEADER), start=2):
        try:
            steps = parse_steps_json(raw['steps_json'])
        except ContractError as exc:
            raise ContractError(f'{path.name}:{line_no} ({raw["case_id"]}): {exc}') from exc
        rows.append(OutputRow(raw['case_id'], raw['deletion_status'], steps))
    return rows


def write_output(path: Path, rows: Iterable[OutputRow], requests: Sequence[Request],
                 evidence: Mapping[str, EvidenceRef] | None = None,
                 fallback_ids: frozenset[str] = frozenset()) -> list[OutputRow]:
    """Canonicalize, validate, write atomically, then read back and re-validate.

    The destination is replaced only if every check passes, so a failed run never
    clobbers a previous valid artifact.
    """
    rows = sorted((OutputRow(r.case_id, r.deletion_status, canonical_steps(r.steps)) for r in rows),
                  key=lambda r: case_sort_key(r.case_id))
    validate_output(rows, requests, evidence, fallback_ids)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle, lineterminator='\n')
            writer.writerow(OUTPUT_HEADER)
            for row in rows:
                writer.writerow((row.case_id, row.deletion_status, steps_to_json(row.steps)))
            handle.flush()
            os.fsync(handle.fileno())
        written = read_output(temp)
        validate_output(written, requests, evidence, fallback_ids)
        if written != rows:
            raise ContractError('read-back of written CSV differs from the validated rows')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return rows
