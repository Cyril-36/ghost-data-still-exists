"""Supplied deletion policy (deletion_rules.csv) -> required treatment per resource.

Rules are loaded at runtime. Each recognized category has a condition
evaluator; an unknown category, an unexpected action for a known category, or a
condition that cannot be evaluated becomes a policy issue, never a default
DELETE. DATA_FLOW is a process, not a data category: an active flow tied to the
subject must be disabled regardless of the data it copies.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Recognized semantics: category -> action the supplied rule is expected to state.
EXPECTED_ACTIONS = {
    'USER_PROFILE': 'DELETE', 'SECONDARY_IDENTIFIER': 'DELETE', 'SEARCH_INDEX_ENTRY': 'DELETE',
    'CACHE_ENTRY': 'DELETE', 'SUPPORT_MESSAGE': 'REDACT', 'SUPPORT_ATTACHMENT': 'DELETE',
    'ANALYTICS_PROFILE': 'ANONYMIZE', 'PRODUCT_TELEMETRY': 'ANONYMIZE', 'USER_EXPORT': 'DELETE',
    'MARKETING_PROFILE': 'DELETE', 'EMAIL_DELIVERY_LOG': 'REDACT', 'SECURITY_AUDIT_LOG': 'RETAIN',
    'LEGAL_HOLD_RECORD': 'RETAIN', 'BACKUP_ARCHIVE': 'RETAIN', 'ACCOUNT_TOMBSTONE': 'REDACT',
}


@dataclass(frozen=True)
class Treatment:
    rule_id: str
    action: str            # DELETE | REDACT | ANONYMIZE | RETAIN | DISABLE_FLOW | BLOCK_RESTORE
    note: str = ''


class PolicyError(Exception):
    """The supplied policy cannot decide this resource's treatment."""


class Policy:
    def __init__(self, rules, presume_retention: bool = False):
        self.presume_retention = presume_retention
        self.by_category = {}
        self.issues = []
        for rule in rules:
            if rule.data_category in self.by_category:
                self.issues.append(f'POLICY_CONFLICT: several rules for {rule.data_category}')
            self.by_category[rule.data_category] = rule
        for category, rule in self.by_category.items():
            expected = EXPECTED_ACTIONS.get(category)
            if expected is None:
                self.issues.append(f'UNSUPPORTED_CATEGORY: rule {rule.rule_id} category {category} has no implemented semantics')
            elif expected != rule.required_action:
                self.issues.append(f'POLICY_CONFLICT: {rule.rule_id} {category} says {rule.required_action}, '
                                   f'implemented semantics expect {expected}')

    def treatment(self, category: str, *, retention_until: datetime | None, audit_at: datetime) -> Treatment:
        if category == 'DATA_FLOW':
            return Treatment('', 'DISABLE_FLOW', 'active processes that copy the subject must be disabled')
        rule = self.by_category.get(category)
        if rule is None:
            raise PolicyError(f'UNSUPPORTED_CATEGORY: no supplied rule for {category}')
        expected = EXPECTED_ACTIONS.get(category)
        if expected != rule.required_action:
            raise PolicyError(f'POLICY_CONFLICT: {rule.rule_id} {category} action {rule.required_action} not implemented')
        if category in ('SECURITY_AUDIT_LOG', 'LEGAL_HOLD_RECORD'):
            if retention_until is None and self.presume_retention:   # experiment only (EngineConfig)
                return Treatment(rule.rule_id, 'RETAIN', 'EXPERIMENT presume_retention: no retention period supplied')
            if retention_until is None:
                # The rule's condition needs supplied retention/hold evidence; its absence is
                # not evidence that the condition holds.
                raise PolicyError(f'UNPROVEN_CONDITION: {rule.rule_id} "{rule.condition}" - no eligible '
                                  'retention or hold period was supplied for this record')
            if retention_until < audit_at:
                raise PolicyError(f'POLICY_CONFLICT: {rule.rule_id} retention ended {retention_until.isoformat()}; '
                                  'no supplied rule covers the expired record')
            return Treatment(rule.rule_id, 'RETAIN', f'supplied retention/hold active until {retention_until.isoformat()}')
        if category == 'BACKUP_ARCHIVE':
            return Treatment(rule.rule_id, 'BLOCK_RESTORE', 'archive retained; restore access must be blocked')
        if category == 'SUPPORT_MESSAGE':
            return Treatment(rule.rule_id, 'REDACT', 'support conversations carry shared agent/order context')
        if category == 'SUPPORT_ATTACHMENT':
            return Treatment(rule.rule_id, 'DELETE', 'attachment linked only to the deletion subject')
        return Treatment(rule.rule_id, rule.required_action)
