"""Benchmark one Roar vision model on the 11 screenshots against the manual ground truth."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pathlib import Path
from ghostaudit.inputs import load_context
from ghostaudit.media import interpret_screenshot
from ghostaudit.corpus import build_corpus
from ghostaudit.parsers import sniff
from ghostaudit.providers import Gateway, ProviderConfig
from evaluation.vision_ground_truth import score_vision
model, ceiling = sys.argv[1], float(sys.argv[2])
ctx = load_context(Path('dataset'))
corpus = build_corpus(ctx)                                   # structured evidence only (for system enrichment)
gw = Gateway(ProviderConfig(provider='roar', vision_model=model, max_cost_paise=ceiling, max_retries=0))
gw.load_pricing()
ext, statuses = {}, {}
for entry in ctx.manifest:
    raw = (ctx.root / entry.file_path).read_bytes()
    if sniff(raw[:4096]) != 'image':
        continue
    obs, report, extraction = interpret_screenshot(entry, raw, corpus, gw)
    ext[entry.file_id], statuses[entry.file_id] = extraction, (report.status, report.issues[:1])
ref = json.load(open('runs/phase3-hardened/media_extractions.json'))
kinds = sum(1 for f, e in ext.items() if e.get('page_kind') and e.get('page_kind') == (ref.get(f) or {}).get('page_kind'))
s = score_vision(ext)
recs = [u for u in gw.usage if u.role == 'vision_reader']
print(json.dumps({'model': model, 'id_precision': s['id_precision'], 'id_recall': s['id_recall'],
                  'identifier_precision': s['identifier_precision'], 'identifier_recall': s['identifier_recall'],
                  'page_kind_agrees_with_phase3': f'{kinds}/{len(ext)}',
                  'statuses': {k: v[0] for k, v in statuses.items()},
                  'problems': {f: d for f, d in s['per_file'].items() if any(d.values())},
                  'calls': len(recs), 'invalid_or_error': sum(1 for u in recs if u.status != 'ok'),
                  'tokens_in': sum(u.input_tokens or 0 for u in recs), 'tokens_out': sum(u.output_tokens or 0 for u in recs),
                  'estimated_paise': round(sum(u.cost_estimate_paise or 0 for u in recs), 2),
                  'actual_reported_paise': sum(u.cost_paise_header or 0 for u in recs)}, indent=1))
json.dump(ext, open(f'vision_{model}.json', 'w'), default=str)
