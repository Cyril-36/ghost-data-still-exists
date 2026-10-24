"""Request intent: does the text authorize deletion?

TEMPORARY, LIMITED implementation: deterministic English patterns only.
Non-English or mixed/unclear text returns UNRESOLVED; it never defaults to
deletion and never to NOT_APPLICABLE. Model-based multilingual classification
replaces this in a later phase. Workflow status is never consulted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DELETE, NON_DELETION, UNRESOLVED = 'DELETE', 'NON_DELETION', 'UNRESOLVED'

_ENGLISH = {'the', 'my', 'please', 'and', 'i', 'to', 'of', 'is', 'that', 'this', 'with', 'it', 'me', 'you', 'can',
            'your', 'do', 'not', 'was', 'have', 'be', 'for', 'too', 'any', 'all', 'are', 'from', 'what', 'would'}
# Common function words of other Latin-script languages: text that looks more like
# one of these than like English is outside this classifier's competence.
_OTHER_LATIN = {'el', 'la', 'los', 'las', 'mi', 'por', 'favor', 'que', 'una', 'para', 'con', 'mis', 'cuenta', 'datos',
                'minha', 'meus', 'não', 'conta', 'dados', 'você', 'uma', 'le', 'les', 'des', 'mon', 'mes', 'vous',
                'je', 'est', 'une', 'pour', 'compte', 'données', 'der', 'die', 'das', 'und', 'ich', 'mein', 'nicht',
                'il', 'di', 'per', 'mio', 'del', 'de', 'et', 'e', 'y'}
# Any letter outside Latin script (e.g. Japanese, Korean) is out of scope; typographic quotes are fine.
_NON_LATIN = re.compile('[^\u0000-\u024f\u2018-\u201f\u2026]')
# A negated clause ("do not close", "without removing", "not asking for deletion")
# is removed before deletion cues are searched, so it cannot authorize deletion.
_NEGATED = re.compile(r"\b(?:not|don't|dont|do not|never|without|no longer want to)\b[^.;!?]*", re.I)
_DELETION = re.compile(
    r"\b(?:delet\w*|eras(?:e|ed|ure|ing)|wipe|forget me|purge)\b"
    r"|\bremov\w*\b[^.;!?]{0,40}\b(?:account|profile|personal|data|details|records|cop(?:y|ies)|traces?"
    r"|result|file|export|information|everything|it|them)\b"
    r"|\bclos\w*\b[^.;!?]{0,25}\baccount\b|\baccount\b[^.;!?]{0,25}\bclos\w*"
    # verification of a deletion the person already requested
    r"|\b(?:account|profile)\b[^.;!?]{0,60}\b(?:is|was|has been|have (?:already )?been) (?:gone|removed|deleted|erased)\b",
    re.I)
_PRESERVE = re.compile(
    r"\bkeep (?:my|the) account\b|\bkeep the account as it is\b|\bleave (?:my|the) (?:profile|account)\b"
    r"|\bleave the account active\b|\bcontinue using\b|\bstill (?:need|want|use)\b"
    r"|\bnot asking for deletion\b|\bnot a request to (?:erase|delete|remove|close)\b|\b(?:do not|don't|dont) (?:close|delete|erase|remove)\b"
    r"|\bwithout (?:removing|deleting|erasing|closing)\b"
    r"|\b(?:marketing|promotional)\b[^.;!?]*\bonly\b|\bonly\b[^.;!?]*\b(?:marketing|promotional)\b", re.I)


@dataclass(frozen=True)
class Intent:
    decision: str            # DELETE | NON_DELETION | UNRESOLVED
    reason: str
    spans: tuple[str, ...] = ()
    method: str = 'deterministic-english-v1'
    signals: tuple = ()          # raw typed answers behind a model-backed decision


def classify(text: str) -> Intent:
    words = re.findall(r"[a-zà-ÿ']+", text.lower())
    english = sum(w in _ENGLISH for w in words)
    other = sum(w in _OTHER_LATIN for w in words)
    if not words or english == 0 or other >= english or _NON_LATIN.search(text):
        return Intent(UNRESOLVED, 'language not supported by the deterministic English classifier')
    keep = [m.group(0) for m in _PRESERVE.finditer(text)]
    deletion = [m.group(0) for m in _DELETION.finditer(_NEGATED.sub(' ', text))]
    if deletion and not keep:
        return Intent(DELETE, 'explicit deletion request', tuple(deletion))
    if keep and not deletion:
        return Intent(NON_DELETION, 'explicit preservation or non-deletion scope', tuple(keep))
    if keep and deletion:
        return Intent(UNRESOLVED, 'both deletion and preservation statements', tuple(keep + deletion))
    return Intent(UNRESOLVED, 'no deletion or preservation statement recognized')
