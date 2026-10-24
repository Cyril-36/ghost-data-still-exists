"""Parse every source once and build exact indexes.

The corpus is global and time-agnostic; every query takes a cutoff and returns
only observations whose source was available AND whose state was observed at
or before that cutoff.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .inputs import Context
from .observations import Observation, Scope, SourceReport
from .parsers import parse_source
from .parsers.activity import activity_observations


def eligible(item, cutoff: datetime) -> bool:
    return item.available_at <= cutoff and item.observed_at <= cutoff


@dataclass
class Corpus:
    context: Context
    observations: list[Observation]
    scopes: list[Scope]
    reports: list[SourceReport]
    by_resource: dict = field(default_factory=lambda: defaultdict(list))
    by_token: dict = field(default_factory=lambda: defaultdict(list))
    by_source: dict = field(default_factory=lambda: defaultdict(list))
    scopes_by_key: dict = field(default_factory=lambda: defaultdict(list))
    file_tokens: dict = field(default_factory=lambda: defaultdict(set))
    system_categories: dict = field(default_factory=lambda: defaultdict(set))

    def __post_init__(self):
        for obs in self.observations:
            self.file_tokens[obs.source_id] |= obs.tokens
            if obs.category and obs.system_id:
                self.system_categories[obs.system_id].add(obs.category)
            if obs.resource_id:
                self.by_resource[obs.resource_id].append(obs)
            for token in obs.tokens:
                self.by_token[token].append(obs)
            self.by_source[obs.source_id].append(obs)
        for scope in self.scopes:
            self.file_tokens[scope.source_id] |= scope.member_tokens
            self.scopes_by_key[(scope.system_id, scope.category)].append(scope)

    def resource_observations(self, resource_id, cutoff):
        return [o for o in self.by_resource.get(resource_id, ()) if eligible(o, cutoff)]

    def token_observations(self, token, cutoff):
        return [o for o in self.by_token.get(token, ()) if eligible(o, cutoff)]

    def scopes_for(self, system_id, category, cutoff):
        return [s for s in self.scopes_by_key.get((system_id, category), ()) if eligible(s, cutoff)]

    def covers(self, scope, subject_tokens) -> bool:
        """Captures are subject-scoped: a listing proves absence only for people that
        the same capture demonstrably covers (their identifiers appear in it)."""
        return bool(subject_tokens) and bool(self.file_tokens.get(scope.source_id, set()) & subject_tokens)

    def coverage(self) -> dict:
        status = defaultdict(list)
        for r in self.reports:
            status[r.status].append(r.source_id)
        return {k: sorted(v) for k, v in sorted(status.items())}


def build_corpus(context: Context) -> Corpus:
    observations, scopes, reports = [], [], []
    for entry in context.manifest:
        raw = (context.root / entry.file_path).read_bytes()
        obs, scs, report = parse_source(entry, raw)
        observations += obs
        scopes += scs
        reports.append(report)
    observations += [activity_observations(a) for a in context.activities]
    return Corpus(context, observations, scopes, reports)
