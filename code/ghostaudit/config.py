"""Engine configuration. Defaults are the strict, evidence-first behaviour.

Each relaxation exists only so its effect can be measured as an isolated
experiment; a run records the exact configuration it used (run_manifest.json).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EngineConfig:
    # Omission from a SUBJECT_SCOPED export proves absence for subjects that the same
    # capture references. The subject set is inferred, not declared, so this is off by
    # default: only FULL_TABLE listings and exact lookups prove absence.
    subject_scoped_omission: bool = False
    # Treat a security/legal record with no supplied retention period as retained.
    # Off: the rule condition stays unproven (plan section 12.3).
    presume_retention: bool = False
    # Accept ownership links from sources available after audit_at (state still cut off).
    # Off: both timestamps must be eligible (plan section 10.1).
    post_cutoff_ownership_links: bool = False
    # Interpret PDFs/screenshots (needs a gateway for document kind and vision reading).
    use_media: bool = True

    def describe(self) -> dict:
        return asdict(self)

    @property
    def is_default(self) -> bool:
        return self == EngineConfig()


STRICT = EngineConfig()
