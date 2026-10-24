import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import helpers  # noqa: F401
from ghostaudit.contracts import Identity, IdentityChange
from ghostaudit.corpus import Corpus
from ghostaudit.identity import IdentityIndex, resolve_subject
from ghostaudit.intent import DELETE, NON_DELETION, UNRESOLVED, classify
from ghostaudit.observations import ABSENT, LOGICAL, PRESENT, STATE, Observation, Scope
from ghostaudit.state import reconstruct

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)
AUDIT = datetime(2026, 2, 1, tzinfo=UTC)


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def ident(uid, email, phone='+1 202 555 0100', updated=T0):
    return Identity(uid, 'Same Name', email, phone, sha(email), updated)


def change(aid, uid, when, field, old, new):
    return IdentityChange(aid, uid, when, field, old, new)


def index(identities, changes=()):
    return IdentityIndex(SimpleNamespace(identities=identities, audit_log=list(changes)))


class IdentityTests(unittest.TestCase):
    def setUp(self):
        moved = T0 + timedelta(days=5)
        self.idx = index(
            [ident('u1', 'new@example.test', updated=moved), ident('u2', 'other@example.test'),
             ident('u3', 'later@example.test', updated=AUDIT + timedelta(days=3))],
            [change('a1', 'u1', moved, 'email', 'Old@Example.test', 'new@example.test'),
             change('a2', 'u1', moved, 'email_hash', sha('Old@Example.test'), sha('new@example.test')),
             change('a3', 'u3', AUDIT + timedelta(days=3), 'email', 'before@example.test', 'later@example.test')])

    def tokens(self, email):
        return resolve_subject(email, AUDIT, self.idx)

    def test_current_and_historical_email_resolve_to_one_user(self):
        for email in ('new@example.test', 'old@example.test'):
            with self.subTest(email):
                self.assertEqual(self.tokens(email).user_ids, ['u1'])

    def test_case_difference_matches_but_hash_uses_original_bytes(self):
        s = self.tokens('NEW@EXAMPLE.TEST')
        self.assertEqual(s.user_ids, ['u1'])
        hashes = {i.key for i in s.identifiers if i.kind == 'hash'}
        self.assertIn(sha('Old@Example.test'), hashes)            # historical hash from original bytes
        self.assertNotIn(sha('old@example.test'), hashes)         # not from the casefolded key

    def test_future_identity_change_is_not_known_at_cutoff(self):
        s = self.tokens('before@example.test')
        self.assertEqual(s.user_ids, ['u3'])
        self.assertNotIn(('email', 'later@example.test'), s.tokens)  # new value only valid after audit_at
        self.assertEqual(self.tokens('later@example.test').user_ids, [])  # unknown at the cutoff

    def test_unrelated_and_ambiguous(self):
        self.assertEqual(self.tokens('new.person@example.test').user_ids, [])
        self.assertIn('IDENTITY_UNMATCHED', self.tokens('nobody@example.test').issues[0])
        shared = index([ident('x1', 'shared@example.test'), ident('x2', 'Shared@example.test')])
        s = resolve_subject('shared@example.test', AUDIT, shared)
        self.assertEqual(s.user_ids, [])
        self.assertTrue(s.issues[0].startswith('IDENTITY_AMBIGUOUS'))

    def test_closure_adds_identifiers_only_from_explicit_profile_rows(self):
        def obs(category, tokens, kind=STATE):
            return Observation('fx', T0, T0, 'r', '', kind, 'account_service', 'fx_r', category=category,
                               tokens=frozenset(tokens))
        corpus = Corpus(SimpleNamespace(), [
            obs('USER_PROFILE', {('user', 'u2'), ('email', 'alias@example.test')}),
            obs('SEARCH_INDEX_ENTRY', {('user', 'u2'), ('email', 'not.an.alias@example.test')}),
            obs('USER_PROFILE', {('user', 'u2'), ('email', 'new@example.test')})], [], [])
        s = resolve_subject('other@example.test', AUDIT, self.idx, corpus)
        self.assertIn(('email', 'alias@example.test'), s.tokens)
        self.assertNotIn(('email', 'not.an.alias@example.test'), s.tokens)
        self.assertNotIn(('email', 'new@example.test'), s.tokens)        # belongs to u1: conflict, not merged
        self.assertTrue(any(i.startswith('IDENTITY_CONFLICT') for i in s.issues))


def state_obs(when, presence, location=LOGICAL, available=None, source='fx_src', **kw):
    return Observation(source, available or when, when, f'{source}-{when.isoformat()}-{location}', '', STATE,
                       'account_service', 'fx_a', location=location, category='USER_PROFILE', presence=presence, **kw)


def corpus_of(observations, scopes=()):
    return Corpus(SimpleNamespace(), list(observations), list(scopes), [])


class StateTests(unittest.TestCase):
    SUBJECT = frozenset({('user', 'fx_u')})

    def state(self, observations, scopes=(), subject=SUBJECT):
        return reconstruct(corpus_of(observations, scopes), 'account_service', 'fx_a', AUDIT, subject)

    def test_latest_eligible_observation_wins(self):
        s = self.state([state_obs(T0, PRESENT), state_obs(T0 + timedelta(days=3), ABSENT, source='later')])
        self.assertEqual((s.presence, s.presence_proof.source_id), (ABSENT, 'later'))

    def test_future_evidence_is_ignored_by_either_timestamp(self):
        late_observed = state_obs(AUDIT + timedelta(seconds=1), ABSENT, source='late1')
        late_available = state_obs(T0 + timedelta(days=9), ABSENT, available=AUDIT + timedelta(seconds=1), source='late2')
        s = self.state([state_obs(T0, PRESENT), late_observed, late_available])
        self.assertEqual(s.presence, PRESENT)

    def test_replica_copy_keeps_resource_present(self):
        s = self.state([state_obs(T0 + timedelta(days=4), ABSENT, location='primary'),
                        state_obs(T0 + timedelta(days=2), PRESENT, location='failover_replica')])
        self.assertEqual((s.presence, s.present_locations), (PRESENT, ['failover_replica']))

    def test_omission_proves_absence_only_for_covered_subjects(self):
        listing = Scope('fx_list', T0 + timedelta(days=5), T0 + timedelta(days=5), 'account_service', 'USER_PROFILE',
                        LOGICAL, frozenset({'fx_other'}), frozenset({('user', 'fx_other')}))
        covering = Scope('fx_list2', T0 + timedelta(days=6), T0 + timedelta(days=6), 'account_service',
                         'USER_PROFILE', LOGICAL, frozenset(), frozenset({('user', 'fx_u')}))
        self.assertEqual(self.state([state_obs(T0, PRESENT)], [listing]).presence, PRESENT)
        s = self.state([state_obs(T0, PRESENT)], [listing, covering])
        self.assertEqual((s.presence, s.presence_proof.source_id), (ABSENT, 'fx_list2'))
        self.assertEqual(self.state([state_obs(T0, PRESENT)], [covering], subject=frozenset()).presence, PRESENT)

    def test_same_time_contradiction_is_reported_not_resolved(self):
        s = self.state([state_obs(T0, PRESENT, source='a'), state_obs(T0, ABSENT, source='b')])
        self.assertIsNone(s.presence)
        self.assertTrue(any(i.startswith('CONTRADICTORY_STATE') for i in s.issues))

    def test_claims_never_change_state(self):
        claim = Observation('fx_act', T0 + timedelta(days=2), T0 + timedelta(days=2), 'row', '', 'CLAIM',
                            'account_service', 'fx_a', presence=None)
        s = self.state([state_obs(T0, PRESENT), claim])
        self.assertEqual((s.presence, len(s.claims)), (PRESENT, 1))


class IntentTests(unittest.TestCase):
    CASES = {
        'Please delete my account and its personal data.': DELETE,
        'Can you remove that search result too?': DELETE,
        'Please confirm that the profile is gone.': DELETE,
        'I want to close this account permanently. Backups must not be able to restore my profile.': DELETE,
        'Stop promotional emails only. Keep my account and purchase history.': NON_DELETION,
        'I can\'t log in and need help resetting it. Do not close the account.': NON_DELETION,
        'Send me a copy of my data. This is not a request to erase it.': NON_DELETION,
        'Please fix the misspelled name without removing any of my records.': NON_DELETION,
        'Delete my account but do not delete my order history.': UNRESOLVED,   # scoped: needs a model
        'What would happen to my orders?': UNRESOLVED,
        'Por favor, eliminen mi cuenta.': UNRESOLVED,                          # non-English: not supported yet
        'このアカウントを削除してください。': UNRESOLVED,
    }

    def test_english_only_deterministic_classifier(self):
        for text, expected in self.CASES.items():
            with self.subTest(text):
                self.assertEqual(classify(text).decision, expected)

    def test_reported_status_is_not_an_input(self):
        self.assertEqual(classify.__code__.co_argcount, 1)


if __name__ == '__main__':
    unittest.main()
