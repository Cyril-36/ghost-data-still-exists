"""Structural serialization of an agent proposal; never infer or add semantic steps."""
import json
from dataclasses import replace

from .contracts import ContractError, Step, STEP_FIELDS
from .output import canonical_steps, steps_to_json


def normalize_proposal(proposal):
    """Sort/number unchanged steps and rebind witnesses by the agent's unique step IDs.

    In an INCOMPLETE row every REMEDIATION and VERIFICATION step is future work, which the output
    contract writes with an empty evidence_id; a citation the agent put there is cleared and recorded.
    Conflicting/duplicate steps, ambiguous IDs and orphan witnesses are errors, not repairs.
    Authorization, status, resources, operations, states and FINDING evidence are never changed.
    """
    originals = proposal['steps']
    ids = [s['step_no'] for s in originals]
    if any(type(n) is not int or n < 1 for n in ids) or len(set(ids)) != len(ids):
        raise ContractError('proposal step_no values must be unique positive integers')
    steps = []
    for row in originals:
        if set(row) != set(STEP_FIELDS):
            raise ContractError('proposal steps must have exactly the output step fields')
        steps.append(Step(**{k: row[k] for k in STEP_FIELDS if k != 'step_no'}))
    cleared = []
    if proposal['deletion_status'] == 'INCOMPLETE':
        for i, step in enumerate(steps):
            if step.phase != 'FINDING' and step.evidence_id:
                cleared.append({'operation': step.operation, 'system_id': step.system_id,
                                'resource_id': step.resource_id, 'evidence_id': step.evidence_id})
                steps[i] = replace(step, evidence_id='')
    ordered = canonical_steps(steps)
    if len(ordered) != len(steps):
        raise ContractError('duplicate agent steps must be resolved by the agent')
    positions = {step: n for n, step in enumerate(ordered, 1)}
    remap = {number: positions[step] for number, step in zip(ids, steps)}
    witnesses = []
    seen = set()
    for witness in proposal['witnesses']:
        number = witness['step_no']
        if type(number) is not int or number not in remap or number in seen:
            raise ContractError('witness step_no must reference exactly one existing proposal step')
        seen.add(number)
        witnesses.append({**witness, 'step_no': remap[number]})
    serialized = json.loads(steps_to_json(ordered))
    changed = serialized != originals
    return {**proposal, 'steps': serialized, 'witnesses': witnesses}, {
        'changed': changed, 'step_number_map': {str(k): v for k, v in remap.items()} if changed else {},
        'cleared_future_evidence': cleared,
        'semantics': 'unchanged; ordering/numbering and future-step evidence serialization only'}
