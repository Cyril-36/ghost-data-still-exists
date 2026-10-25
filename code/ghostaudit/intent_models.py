"""Model-backed request intent: Jev typed decisions, deterministic reconciliation, staged escalation.

Stages (cheapest first; every stage can only narrow, never widen, authorization):
  1. Jev (`/decisions`): one call per request -> `intent` choice + three yes/no flags.
     Different requests are never mixed into one Jev state.
  2. Deterministic reconciliation of the four answers (thresholds below). Contradictions
     or low-probability answers stay UNRESOLVED. A deletion is ACCOUNT-scoped only when
     the account-wide labels are themselves probable; otherwise it is ITEM-scoped.
  3. The deterministic English classifier is an independent check: if it gives a definite
     decision that disagrees with Jev, the request is escalated, not auto-resolved.
  4. Only escalated requests go to the generative model, batched with opaque handles,
     and its answer counts only if every supporting span is a verbatim substring of the
     request and it does not contradict a definite deterministic decision. A conflict
     that Jev's flags detected is never escalated: no later stage can waive it.
  5. An ITEM-scoped deletion needs its target: one bounded Jev choice among data
     categories plus a yes/no on whether every item of that kind is meant. Without a
     probable single category it stays UNRESOLVED; which record is meant is grounded by
     the planner from evidence (planner.plan_case), never by a model.
Jev probabilities are treated as model scores, not calibrated correctness.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from .intent import ACCOUNT, DELETE, ITEM, NON_DELETION, UNRESOLVED, Intent, classify
from .providers import Gateway, ProviderError, fingerprint

PROMPTS = Path(__file__).resolve().parent.parent / 'prompts'
JEV_MODEL = 'typesafe/jev-latest'
GENERATIVE_MODEL = 'google/gemini-2.5-flash'
LABELS = ('DELETE_ACCOUNT', 'VERIFY_PRIOR_DELETION', 'SCOPED_DELETION', 'NON_DELETION', 'AMBIGUOUS')
DELETION_LABELS = {'DELETE_ACCOUNT', 'VERIFY_PRIOR_DELETION', 'SCOPED_DELETION'}
FLAGS = ('preserve_account', 'marketing_only', 'conflicting_instructions')
CHOICE_MIN = 0.60     # top intent probability needed to act on the choice
YES, NO = 0.70, 0.30  # noul probability bands; between them the flag is "uncertain"


def _load_jev():
    spec = json.loads((PROMPTS / 'intent_jev_v1.json').read_text(encoding='utf-8'))
    return spec, f'{spec["version"]}:{fingerprint(spec)[:12]}'


def _load_target():
    spec = json.loads((PROMPTS / 'intent_target_jev_v2.json').read_text(encoding='utf-8'))
    return spec, f'{spec["version"]}:{fingerprint(spec)[:12]}'


def _load_generative():
    text = (PROMPTS / 'intent_generative_v1.md').read_text(encoding='utf-8')
    return text, f'intent-generative-v1:{fingerprint(text)[:12]}'


@dataclass(frozen=True)
class Signals:
    """Typed answers from one model, before reconciliation."""
    source: str                      # jev | generative
    label: str
    probability: float | None        # Jev probability of the chosen label's FAMILY (see reconcile); None for generative
    flags: tuple                     # ((name, value), ...) value: float prob (Jev) or bool (generative)
    model: str | None = None
    spans: tuple = ()
    account_probability: float | None = None   # Jev: P(DELETE_ACCOUNT) + P(VERIFY_PRIOR_DELETION)

    def flag(self, name) -> str:
        """'yes' | 'no' | 'uncertain'."""
        value = dict(self.flags).get(name)
        if isinstance(value, bool):
            return 'yes' if value else 'no'
        if value is None:
            return 'uncertain'
        return 'yes' if value >= YES else 'no' if value <= NO else 'uncertain'


def reconcile(sig: Signals) -> Intent:
    """Deterministic mapping of typed answers to an authorization decision."""
    method = f'{sig.source}-reconciled-v1'
    signals = (('label', sig.label), ('p', sig.probability), *sig.flags, ('model', sig.model))
    if sig.probability is not None and sig.probability < CHOICE_MIN:
        return Intent(UNRESOLVED, f'low {sig.source} choice probability {sig.probability:.2f}', sig.spans, method, signals)
    if sig.label == 'AMBIGUOUS':
        return Intent(UNRESOLVED, 'request is ambiguous or hypothetical', sig.spans, method, signals)
    conflicting = sig.flag('conflicting_instructions')
    if conflicting != 'no':
        return Intent(UNRESOLVED, f'conflicting instructions flag is {conflicting}', sig.spans, method, signals)
    preserve, marketing = sig.flag('preserve_account'), sig.flag('marketing_only')
    if sig.label in DELETION_LABELS:
        if marketing != 'no':
            return Intent(UNRESOLVED, f'deletion choice disagrees with marketing_only={marketing}',
                          sig.spans, method, signals)
        if sig.label == 'SCOPED_DELETION' or (sig.account_probability is not None
                                              and sig.account_probability < CHOICE_MIN):
            # Deletion is probable, account-wide deletion is not: authorize only one item.
            # Keeping the account is consistent with deleting one item.
            return Intent(DELETE, f'{sig.label}: item-scoped deletion', sig.spans, method, signals, scope=ITEM)
        if preserve != 'no':
            return Intent(UNRESOLVED, f'account deletion disagrees with preserve_account={preserve}',
                          sig.spans, method, signals)
        return Intent(DELETE, f'{sig.label} with consistent flags', sig.spans, method, signals, scope=ACCOUNT)
    if sig.label == 'NON_DELETION':
        return Intent(NON_DELETION, 'non-deletion request', sig.spans, method, signals)
    return Intent(UNRESOLVED, f'unknown label {sig.label!r}', sig.spans, method, signals)


# --- Jev -------------------------------------------------------------------------------------

def _probability(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1


def choice_problem(payload, name, labels) -> str | None:
    """Validation of the typed `choice` answer `name` in a /decisions payload against its labels."""
    answers = payload.get('answers')
    answer = answers.get(name) if isinstance(answers, dict) else None
    if not isinstance(answer, dict) or answer.get('choice') not in labels:
        return 'choice outside the declared labels'
    probs = answer.get('probabilities')
    if not isinstance(probs, dict) or set(probs) != set(labels):
        return 'probabilities do not cover exactly the declared labels'
    if not all(map(_probability, probs.values())) or abs(sum(probs.values()) - 1) > 0.05:
        return 'probabilities are not a distribution'
    return None


def _validate_jev(questions):
    def check(payload):
        answers = payload.get('answers')
        if not isinstance(answers, dict) or set(answers) != set(questions):
            return f'answers {sorted(answers or {}) if isinstance(answers, dict) else answers!r} != questions'
        problem = choice_problem(payload, 'intent', questions['intent']['criteria'])
        if problem:
            return f'intent: {problem}'
        for name in FLAGS:
            v = answers[name].get('noul') if isinstance(answers[name], dict) else None
            if not _probability(v):
                return f'{name} noul value {v!r} invalid'
        return None
    return check


def jev_signals(gateway: Gateway, text: str) -> Signals:
    spec, version = _load_jev()
    questions = spec['questions']
    payload = gateway.decide('intent', JEV_MODEL, spec['state_template'].format(text=text), questions, version,
                             _validate_jev(questions))
    a = payload['answers']
    label = a['intent']['choice']
    probs = a['intent']['probabilities']
    # Authorization depends on the family (delete vs not), not on which deletion sub-label
    # won: probability split between VERIFY_PRIOR and SCOPED is still certainty about deletion.
    family = DELETION_LABELS if label in DELETION_LABELS else {label}
    return Signals('jev', label, float(sum(probs[k] for k in family)),
                   tuple((f, float(a[f]['noul'])) for f in FLAGS), payload.get('model'),
                   account_probability=float(probs['DELETE_ACCOUNT'] + probs['VERIFY_PRIOR_DELETION']))


def jev_target(gateway: Gateway, text: str) -> tuple[str | None, float, bool, str | None]:
    """(data category or None if not probable/unclear, its probability, whether every item of that
    category is meant (only on a clear yes), returned model) for an ITEM-scoped request."""
    spec, version = _load_target()
    questions = spec['questions']
    labels = questions['target']['criteria']

    def check(p):
        whole = (p.get('answers') or {}).get('whole_category')
        return choice_problem(p, 'target', labels) or (
            None if isinstance(whole, dict) and _probability(whole.get('noul')) else 'whole_category noul invalid')
    payload = gateway.decide('intent_target', JEV_MODEL, spec['state_template'].format(text=text), questions,
                             version, check)
    answer = payload['answers']['target']
    target, p = answer['choice'], float(answer['probabilities'][answer['choice']])
    grounded = target != 'SEVERAL_OR_UNCLEAR' and p >= CHOICE_MIN
    whole = payload['answers']['whole_category']['noul'] >= YES
    return (target if grounded else None), p, whole, payload.get('model')


# --- generative fallback ----------------------------------------------------------------------

_FENCE = re.compile(r'^\s*```(?:json)?\s*\n(.*)\n\s*```\s*$', re.S)


def _strict_json(text):
    m = _FENCE.match(text)
    body = m.group(1) if m else text
    def no_dupes(pairs):
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError('duplicate JSON key')
        return dict(pairs)
    return json.loads(body, object_pairs_hook=no_dupes)


def _item_problem(r, text):
    if r.get('intent') not in LABELS or any(not isinstance(r.get(f), bool) for f in FLAGS):
        return 'invalid fields'
    spans = r.get('spans')
    if not isinstance(spans, list) or not spans or any(
            not isinstance(sp, str) or not sp.strip() or sp not in text for sp in spans):
        return 'spans are not verbatim substrings of the request'
    return None


def generative_signals(gateway: Gateway, items: list[tuple[str, str]], batch_size: int = 5,
                       max_tokens: int = 8000) -> dict:
    """items: [(handle, text)] -> {handle: Signals | str(error)}. Handles are opaque, never case IDs.

    The batch response must parse and contain exactly the batch's handles; each item is
    then validated on its own, so one bad item does not discard the others. An
    unparseable batch is retried once (it is never cached).
    """
    prompt, version = _load_generative()
    out = {}
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        texts = dict(batch)
        user = json.dumps({'requests': [{'handle': h, 'text': t} for h, t in batch]}, ensure_ascii=False)

        def check(payload, texts=texts):
            try:
                results = _strict_json(payload['choices'][0]['message']['content'])['results']
                handles = [r.get('handle') for r in results]
            except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
                return f'unparseable response: {exc}'
            if sorted(handles) != sorted(texts):
                return f'handles {handles} do not match the batch'
            return None
        payload, error = None, ''
        for _attempt in range(2):
            try:
                payload = gateway.chat('intent_fallback', GENERATIVE_MODEL,
                                       [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': user}],
                                       version, check, max_tokens=max_tokens)
                break
            except ProviderError as exc:
                error = str(exc)
                if 'unparseable' not in error:
                    break
        if payload is None:
            out.update({h: f'PROVIDER_FAILURE: {error}' for h, _ in batch})
            continue
        for r in _strict_json(payload['choices'][0]['message']['content'])['results']:
            problem = _item_problem(r, texts[r['handle']])
            out[r['handle']] = f'INVALID_MODEL_OUTPUT: {problem}' if problem else Signals(
                'generative', r['intent'], None, tuple((f, r[f]) for f in FLAGS), payload.get('model'),
                tuple(r['spans']))
    return out


# --- staged decision ---------------------------------------------------------------------------

@dataclass
class IntentOutcome:
    intent: Intent
    stage: str               # deterministic | jev | generative | unresolved
    deterministic: Intent
    jev: Intent | None = None
    generative: Intent | None = None
    escalated_because: str = ''


def _agrees(det: Intent, other: Intent) -> bool:
    return det.decision == UNRESOLVED or det.decision == other.decision


def material_concern(sig: Signals) -> str | None:
    """An authorization conflict the typed answers detected; no later stage may waive it."""
    if sig.flag('conflicting_instructions') == 'yes':
        return 'conflicting instructions'
    if sig.label != 'NON_DELETION' and sig.flag('marketing_only') == 'yes':
        return 'request limited to marketing'
    if sig.label not in ('SCOPED_DELETION', 'NON_DELETION') and sig.flag('preserve_account') == 'yes':
        return 'asks to keep the account'
    return None


def ground_target(gateway, text, intent: Intent) -> Intent:
    try:
        target, p, whole, model = jev_target(gateway, text)
    except ProviderError as exc:
        return Intent(UNRESOLVED, f'scoped deletion target: PROVIDER_FAILURE: {exc}', intent.spans, 'staged-v1',
                      intent.signals)
    signals = intent.signals + (('target', target), ('target_p', p), ('whole_category', whole),
                                ('target_model', model))
    if target is None:
        return Intent(UNRESOLVED, f'scoped deletion target not grounded (p={p:.2f})', intent.spans, 'staged-v1',
                      signals)
    return replace(intent, signals=signals, target=target, whole_category=whole)


def decide_intents(texts: dict, gateway: Gateway | None, use_generative: bool = True,
                   concurrency: int = 4) -> dict:
    """texts: {key: request_text} -> {key: IntentOutcome}. gateway=None means offline (deterministic only)."""
    det = {k: classify(t) for k, t in texts.items()}
    if gateway is None:
        return {k: IntentOutcome(d, 'deterministic', d) for k, d in det.items()}

    def run_jev(item):
        k, t = item
        try:
            return k, jev_signals(gateway, t), ''
        except ProviderError as exc:
            return k, None, f'PROVIDER_FAILURE: {exc}'
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        jev = {k: (s, err) for k, s, err in pool.map(run_jev, sorted(texts.items()))}

    outcomes, escalate = {}, []
    for k in sorted(texts):
        s, err = jev[k]
        j = reconcile(s) if s is not None else None
        d = det[k]
        if j is not None and j.decision != UNRESOLVED and _agrees(d, j):
            outcomes[k] = IntentOutcome(j, 'jev', d, j)
            continue
        concern = material_concern(s) if s is not None else None
        reason = err or (j.reason if j.decision == UNRESOLVED else
                         f'deterministic {d.decision} disagrees with jev {j.decision}')
        if concern:
            outcomes[k] = IntentOutcome(Intent(UNRESOLVED, f'{reason}; not escalated: jev detected {concern}',
                                               (), 'staged-v1', j.signals), 'unresolved', d, j)
            continue
        outcomes[k] = IntentOutcome(Intent(UNRESOLVED, reason, (), 'staged-v1'), 'unresolved', d, j,
                                    escalated_because=reason)
        escalate.append(k)

    if use_generative and escalate:
        handles = {f'r{i + 1}': k for i, k in enumerate(escalate)}
        gen = generative_signals(gateway, [(h, texts[k]) for h, k in handles.items()])
        for h, k in handles.items():
            o = outcomes[k]
            g = gen.get(h)
            if not isinstance(g, Signals):
                o.intent = Intent(UNRESOLVED, f'{o.escalated_because}; generative: {g}', (), 'staged-v1')
                continue
            o.generative = reconcile(g)
            s = jev[k][0]
            widens = (o.generative.scope == ACCOUNT and s is not None and s.flag('preserve_account') == 'yes')
            if o.generative.decision != UNRESOLVED and _agrees(o.deterministic, o.generative) and not widens:
                o.intent, o.stage = o.generative, 'generative'
            else:
                o.intent = Intent(UNRESOLVED, f'{o.escalated_because}; generative {o.generative.decision}: '
                                              f'{o.generative.reason}', o.generative.spans, 'staged-v1')
    for k, o in outcomes.items():
        if o.intent.decision == DELETE and o.intent.scope == ITEM:
            o.intent = ground_target(gateway, texts[k], o.intent)
            if o.intent.decision == UNRESOLVED:
                o.stage = 'unresolved'
    return outcomes


# --- bounded choice among grounded candidates (for the investigator phase) ----------------------

def choose_candidate(gateway: Gateway, role: str, question: str, context: str,
                     candidates: dict[str, str], min_probability: float = CHOICE_MIN) -> tuple[str | None, dict]:
    """Let Jev pick ONE handle from a finite, already-grounded candidate set.

    `candidates` maps opaque handles (e.g. source or interpretation handles built by
    deterministic code) to short descriptions. Jev never sees anything it could turn into
    a new ID: it can only return a handle, which is checked against the set. Returns
    (handle or None if below `min_probability`, probabilities).
    """
    if not 2 <= len(candidates) <= 12 or any(not h or not d for h, d in candidates.items()):
        raise ValueError('need 2-12 non-empty candidates')
    questions = {'choice': {'type': 'choice', 'instructions': question, 'criteria': dict(candidates)}}

    payload = gateway.decide(role, JEV_MODEL, context, questions, f'choose-candidate-v1:{role}',
                             lambda p: choice_problem(p, 'choice', candidates))
    answer = payload['answers']['choice']
    handle = answer['choice']
    probs = answer['probabilities']
    return (handle if probs[handle] >= min_probability else None), probs
