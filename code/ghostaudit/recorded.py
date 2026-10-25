"""Replay of recorded model decisions: Jev request intents, PDF document kinds and screenshot readings.

A clean run can reproduce the submitted output without API keys or the original response cache.
Each recorded answer is bound by SHA-256 to the exact request text or evidence file it was given.
Intents are re-reconciled with the current deterministic rules and agreement check, exactly as the
staged mode treats a fresh Jev answer. A disagreement stays UNRESOLVED, because the generative fallback
is not replayed. No sample label or expected output is read.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .contracts import ContractError
from .intent import UNRESOLVED, Intent, classify
from .intent_models import IntentOutcome, Signals, _agrees, reconcile

FORMAT = 'ghostaudit-recorded-decisions-v1'
FLAGS = ('preserve_account', 'marketing_only', 'conflicting_instructions')


def sha256(data) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode('utf-8')).hexdigest()


@dataclass
class RecordedDecisions:
    path: Path
    sha256: str
    provenance: dict
    intents: dict
    media: dict

    @classmethod
    def load(cls, path) -> RecordedDecisions:
        raw = Path(path).read_bytes()
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('format') != FORMAT:
            raise ContractError(f'{path}: not a {FORMAT} file')
        return cls(Path(path), sha256(raw), data['provenance'], data['intents'], data['media'])

    def intent_outcomes(self, requests) -> dict:
        outcomes = {}
        for r in requests:
            rec = self.intents.get(r.case_id)
            if rec is None:
                raise ContractError(f'{r.case_id}: no recorded intent decision')
            if rec['request_sha256'] != sha256(r.request_text):
                raise ContractError(f'{r.case_id}: request text differs from the recorded decision')
            sig = Signals('jev', rec['label'], rec['p'], tuple((k, rec[k]) for k in FLAGS), rec['model'],
                          account_probability=rec.get('account_probability'))
            jev, det = reconcile(sig), classify(r.request_text)
            if jev.decision != UNRESOLVED and _agrees(det, jev):
                outcomes[r.case_id] = IntentOutcome(jev, 'jev', det, jev)
            else:
                reason = jev.reason if jev.decision == UNRESOLVED else \
                    f'deterministic {det.decision} disagrees with recorded jev {jev.decision}'
                outcomes[r.case_id] = IntentOutcome(Intent(UNRESOLVED, f'{reason}; generative fallback not replayed',
                                                           (), 'recorded-v1', jev.signals), 'unresolved', det, jev)
        return outcomes

    def media_for(self, context) -> dict:
        """{file_id: recorded reading}, each checked against the evidence bytes it was read from."""
        files = {e.file_id: e for e in context.manifest}
        unknown = sorted(set(self.media) - set(files))
        if unknown:
            raise ContractError(f'recorded media readings for unknown files {unknown}')
        readings = {}
        for file_id, rec in self.media.items():
            if rec['file_sha256'] != sha256((context.root / files[file_id].file_path).read_bytes()):
                raise ContractError(f'{file_id}: evidence bytes differ from the recorded reading')
            readings[file_id] = rec
        return readings

    def describe(self) -> dict:
        return {'path': str(self.path), 'sha256': self.sha256, 'intents': len(self.intents),
                'media': len(self.media), 'provenance': self.provenance}


def export_run(run_dir: Path, dataset: Path, requests_path: Path, out: Path) -> dict:
    """Write the minimal recorded-decision artifact for an existing run (its intents and model media)."""
    from .inputs import load_context, load_requests
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / 'run_manifest.json').read_text(encoding='utf-8'))
    requests = {r.case_id: r for r in load_requests(requests_path)}
    if manifest['requests_sha256'] != sha256(Path(requests_path).read_bytes()):
        raise ContractError('requests file differs from the one the run used')
    intents = {}
    for line in (run_dir / 'case_plans.jsonl').read_text(encoding='utf-8').splitlines():
        plan = json.loads(line)
        intent = plan['intent']
        if intent['stage'] != 'jev':
            raise ContractError(f'{plan["case_id"]}: only Jev-stage decisions can be replayed, got {intent["stage"]}')
        signals = dict(tuple(s) for s in intent['signals'])
        intents[plan['case_id']] = {'request_sha256': sha256(requests[plan['case_id']].request_text),
                                    'label': signals['label'], 'p': signals['p'],
                                    **{k: signals[k] for k in FLAGS}, 'model': signals['model'],
                                    'recorded_decision': intent['decision']}
    if set(intents) != set(requests):
        raise ContractError('the run does not cover exactly the requests')
    context = load_context(dataset)
    files = {e.file_id: e for e in context.manifest}
    media = {}
    for file_id, ext in sorted(json.loads((run_dir / 'media_extractions.json').read_text(encoding='utf-8')).items()):
        digest = sha256((context.root / files[file_id].file_path).read_bytes())
        kind = ext.get('kind')
        if kind and kind.get('model'):
            media[file_id] = {'file_sha256': digest, 'document_kind': {k: kind[k] for k in ('choice', 'p', 'model')}}
        elif 'page_kind' in ext and ext.get('model'):
            media[file_id] = {'file_sha256': digest, 'screenshot': {k: ext.get(k) for k in (
                'page_kind', 'page_title', 'entities', 'rejected_ids', 'unreadable', 'model')}}
    provenance = {'source_run': str(run_dir), 'engine_version': manifest.get('engine_version'),
                  'source_output_sha256': manifest['output_sha256'], 'requests_sha256': manifest['requests_sha256'],
                  'prompt_sha256': manifest.get('prompt_sha256'), 'model_calls': manifest.get('model_calls'),
                  'model_cache_hits': manifest.get('model_cache_hits'),
                  'models': sorted(manifest.get('model_usage', {})),
                  'note': 'Answers obtained by earlier live runs of the same prompts; labels were never used.'}
    data = {'format': FORMAT, 'provenance': provenance, 'intents': dict(sorted(intents.items())), 'media': media}
    Path(out).write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    return {'intents': len(intents), 'media': len(media), 'sha256': sha256(Path(out).read_bytes())}
