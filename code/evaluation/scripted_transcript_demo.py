"""SCRIPTED-TRANSCRIPT DEMONSTRATION on real sample evidence. Evaluation-only; NOT model performance.

A hand-written tool-call transcript stands in for the investigator model on sample case_07 (every other
routed case submits NO_SUPPORTED_ACTION). It exercises the real tools, fact validator, verifier and
deterministic re-plan on the actual evidence_58 PDF, to show what the Phase 4 path does when a model
chooses those actions. Its output is never a submission. The case is selected by its request text, which is
exactly why this file is not, and must never be, part of inference.

    python3 code/evaluation/scripted_transcript_demo.py .runs/phase4-demo
"""
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ghostaudit.audit import run_audit
from ghostaudit.investigator import InvestigatorConfig
from ghostaudit.providers import ReplayGateway
ids = itertools.count()
def completion(calls):
    return {'id': f'r{next(ids)}', 'model': 'scripted-transcript', 'usage': {'prompt_tokens': 0, 'completion_tokens': 0},
            'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': f'c{next(ids)}', 'type': 'function', 'function': {'name': n, 'arguments': json.dumps(a)}} for n, a in calls]}}]}
F = dict(system_id='support_platform', resource_id='support_001', source_id='evidence_58')
CASE07 = [
    [('inspect_media', {'file_id': 'evidence_58', 'page': 'p1'}), ('inspect_media', {'file_id': 'evidence_58', 'page': 'p2'})],
    [('propose_fact', {**F, 'kind': 'OWNERSHIP', 'locator': 'p1:L2-L8', 'value': '',
                       'literal': 'Support reference CS-4827 / support_001'}),
     ('propose_fact', {**F, 'kind': 'OWNERSHIP', 'locator': 'p1:L5-L8', 'value': '',
                       'literal': 'Detail reference CS-4827 / support_001 Detail received 18 Jun 2026 Detail Priya Raman Detail priya.raman@gmail.com'}),
     ('propose_fact', {**F, 'kind': 'CATEGORY', 'locator': 'p2:L2', 'value': 'SUPPORT_ATTACHMENT',
                       'literal': 'Continued - Attachment properties and upload history'}),
     ('propose_fact', {**F, 'kind': 'STATE', 'locator': 'p1:L2', 'value': 'PRESENT',
                       'literal': 'Support reference CS-4827 / support_001'})],
    [('submit_case_file', {'resolution': 'RESOLVED'})]]
def respond(path, body):
    brief = json.loads(body['messages'][1]['content'])
    turn = sum(1 for m in body['messages'] if m['role'] == 'assistant')
    script = CASE07 if 'identity document I sent to support' in brief['request']['text'] else []
    return completion(script[turn] if turn < len(script) else [('submit_case_file', {'resolution': 'NO_SUPPORTED_ACTION'})])
out = Path(sys.argv[1])
s = run_audit(Path('dataset'), Path('dataset/sample_deletion_requests.csv'), out / 'predictions.csv', out,
              investigator=InvestigatorConfig('scripted-transcript'), investigator_gateway=ReplayGateway(respond),
              media_cache=json.load(open('runs/phase3-hardened/media_extractions.json')))
print(json.dumps(s['phase4']))
for line in open(out / 'case_files.jsonl'):
    d = json.loads(line)
    if d['case_id'] == 'case_07':
        print('case_07', d['outcome'], d['stop_reason'], [f['kind'] for f in d['facts']], [r['reason'][:60] for r in d['rejected']])
