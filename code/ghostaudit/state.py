"""Reconstruct a resource's state at audit_at from eligible observations.

- Only STATE observations count; operation claims never set state.
- Each physical location (primary, read_replica, failover_replica, ...) keeps
  its own history. Its state is its latest eligible observation.
- A resource absent from an exhaustive Scope of its own system and category is
  absent at that location at the scope's time (SNAPSHOT_OMISSION). Absence is
  never inferred from a non-exhaustive file.
- Any present copy keeps the resource present: a clean primary cannot hide a
  replica that still holds the record.
- Equally recent contradictory observations at one location are reported as a
  conflict, not resolved by tie-breaking.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .corpus import Corpus
from .observations import ABSENT, PRESENT, STATE, Observation


@dataclass
class ResourceState:
    system_id: str
    resource_id: str
    categories: set = field(default_factory=set)
    presence: str | None = None
    presence_proof: Observation | None = None
    present_locations: list = field(default_factory=list)
    exposed: tuple | None = None
    exposure_proof: Observation | None = None
    exposure_by_location: dict = field(default_factory=dict)
    enabled: bool | None = None
    enabled_proof: Observation | None = None
    restore_allowed: bool | None = None
    restore_proof: Observation | None = None
    retention_until: object = None
    claims: list = field(default_factory=list)
    issues: list = field(default_factory=list)


def _order(o: Observation):
    return (o.observed_at, o.available_at, o.source_id, o.locator)


def _latest(observations, attribute, issues, label):
    """Latest observation asserting `attribute`; report same-time contradictions."""
    relevant = [o for o in observations if getattr(o, attribute) is not None]
    if not relevant:
        return None
    relevant.sort(key=_order)
    last = relevant[-1]
    rivals = {getattr(o, attribute) for o in relevant if o.observed_at == last.observed_at}
    if len(rivals) > 1:
        issues.append(f'CONTRADICTORY_STATE: {label} has conflicting {attribute} at {last.observed_at.isoformat()}')
        return None
    return last


def reconstruct(corpus: Corpus, system_id: str, resource_id: str, cutoff,
                subject_tokens: frozenset = frozenset()) -> ResourceState:
    """`subject_tokens` enables omission-based absence from captures that cover that subject."""
    state = ResourceState(system_id, resource_id)
    observations = []
    for o in corpus.resource_observations(resource_id, cutoff):
        if o.system_id != system_id:
            continue
        if o.kind == STATE:
            observations.append(o)
            if o.category:
                state.categories.add(o.category)
        else:
            state.claims.append(o)

    if not state.categories and len(corpus.system_categories.get(system_id, ())) == 1:
        state.categories = set(corpus.system_categories[system_id])
        state.issues.append(f'NOTE: category taken from the only category {system_id} holds')

    # Omission from an exhaustive listing of the same system and category, in a capture
    # that covers this subject.
    for category in state.categories:
        for scope in corpus.scopes_for(system_id, category, cutoff):
            if resource_id not in scope.members and corpus.covers(scope, subject_tokens):
                observations.append(Observation(
                    source_id=scope.source_id, available_at=scope.available_at, observed_at=scope.observed_at,
                    locator=f'{scope.locator} (omission)', excerpt=f'{resource_id} not listed in {scope.locator or "collection"}',
                    kind=STATE, proof='SNAPSHOT_OMISSION', system_id=system_id, resource_id=resource_id,
                    location=scope.location, category=category, presence=ABSENT))

    by_location = {}
    for o in observations:
        by_location.setdefault(o.location, []).append(o)

    latest_presence = {}
    for location, obs in by_location.items():
        last = _latest(obs, 'presence', state.issues, f'{resource_id}@{location}')
        if last is not None:
            latest_presence[location] = last
    present = [o for o in latest_presence.values() if o.presence == PRESENT]
    if present:
        state.presence = PRESENT
        state.present_locations = sorted(o.location for o in present)
        state.presence_proof = max(present, key=_order)
    elif latest_presence and len(latest_presence) == len(by_location):
        state.presence = ABSENT
        state.presence_proof = max(latest_presence.values(), key=_order)

    # Exposure is assessed for every surviving copy on its own: one exposing copy keeps the
    # record non-compliant however clean the other copies are, and a copy whose exposure
    # was never assessed leaves the outcome unknown (never borrowed from another copy).
    per_copy = {}
    for location in state.present_locations:
        per_copy[location] = _latest(by_location[location], 'exposed', state.issues, f'{resource_id}@{location}')
    state.exposure_by_location = {loc: (o.exposed if o else None) for loc, o in per_copy.items()}
    exposing = [o for o in per_copy.values() if o is not None and o.exposed]
    if exposing:
        state.exposure_proof = max(exposing, key=_order)
        state.exposed = tuple(sorted({f for o in exposing for f in o.exposed}))
    elif per_copy and all(o is not None for o in per_copy.values()):
        state.exposure_proof = max(per_copy.values(), key=_order)
        state.exposed = ()
    state.enabled_proof = _latest(observations, 'enabled', state.issues, resource_id)
    if state.enabled_proof is not None:
        state.enabled = state.enabled_proof.enabled
    state.restore_proof = _latest(observations, 'restore_allowed', state.issues, resource_id)
    if state.restore_proof is not None:
        state.restore_allowed = state.restore_proof.restore_allowed
    retention = _latest(observations, 'retention_until', state.issues, resource_id)
    state.retention_until = retention.retention_until if retention else None
    return state
