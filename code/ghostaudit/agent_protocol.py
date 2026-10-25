"""Structural serialization of an agent proposal; never infer or add semantic steps."""
import json

from .contracts import ContractError, Step, STEP_FIELDS
from .output import canonical_steps, steps_to_json


def normalize_proposal(proposal):
    """Sort/number unchanged steps and rebind witnesses by the agent's unique step IDs.

    Conflicting/duplicate steps, ambiguous IDs and orphan witnesses are errors, not repairs.
    Authorization, status, resources, operations, states and evidence IDs are never changed.
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
        'semantics': 'unchanged; structural ordering/numbering only'}
