"""system_activity.csv rows as observations.

Most rows are operation CLAIMS (a worker submitted, reported, or completed
something) and never establish resulting state. Two check events report an
observed condition, and LINK_RESOURCE_IDENTITY records an explicit catalog link.
"""
from __future__ import annotations

import re

from ..observations import CLAIM, LINK, PRESENT, STATE, Observation, reference_tokens, tokens_from

_FLOW_ENABLED = re.compile(r'confirmed that data flow (\S+) remains enabled')
_RESTORE_OPEN = re.compile(r'confirmed that archive (\S+) can still be restored')
_CATALOG_LINK = re.compile(r'(?:linked|confirmed) (\S+) \([^)]*\) (?:to|belongs only to) (\S+) and (\S+?)[.;]')


def activity_observations(activity) -> Observation:
    a = activity
    base = dict(source_id=a.activity_id, available_at=a.event_time, observed_at=a.event_time,
                locator='row', excerpt=f'{a.event_type} {a.reported_result}: {a.details}'[:400],
                system_id=a.target_system, resource_id=a.resource_id,
                attributes=(('event_type', a.event_type), ('reported_result', a.reported_result),
                            ('source_system', a.source_system)))
    if a.reported_result == 'SUCCESS':
        m = _FLOW_ENABLED.search(a.details)
        if a.event_type == 'CHECK_DATA_FLOW' and m and m.group(1).rstrip('.') == a.resource_id:
            return Observation(kind=STATE, proof='CHECK', category='DATA_FLOW', presence=PRESENT,
                               enabled=True, **base)
        m = _RESTORE_OPEN.search(a.details)
        if a.event_type == 'CHECK_RESTORE_ACCESS' and m and m.group(1).rstrip('.') == a.resource_id:
            return Observation(kind=STATE, proof='CHECK', category='BACKUP_ARCHIVE', presence=PRESENT,
                               restore_allowed=True, **base)
        m = _CATALOG_LINK.search(a.details)
        if a.event_type == 'LINK_RESOURCE_IDENTITY' and m and m.group(1) == a.resource_id:
            # "... linked <resource> (<label>) to <identifier> and <user reference>."
            return Observation(kind=LINK, proof='CATALOG_LINK',
                               tokens=tokens_from(m.group(2)) | reference_tokens(m.group(3)), **base)
    return Observation(kind=CLAIM, proof='CLAIM', **base)
