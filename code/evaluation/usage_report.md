# Usage report — final submission (D084)

**Final run (`runs/final-d081/`, produced `output.csv` SHA-256 `626e88d1…41a9d0`): 0 model calls, 0 tokens,
₹0.** It replays `code/recorded_decisions.json`: 518 Jev intent answers, 4 Jev PDF document kinds and 11
vision screenshot readings, all recorded by the original full run documented below. That run's
acquisition cost is the cost behind every model decision in the final output; the replay adds nothing.

| Model decision | Provider / model | Calls behind the final output |
|---|---|---|
| Request intent | TokenBazaar `typesafe/jev-latest` → `typesafe/jev-1.13-20260917` | 518 |
| PDF document kind | TokenBazaar `typesafe/jev-latest` → `typesafe/jev-1.13-20260917` | 4 |
| Screenshot reading | TokenBazaar `google/gemini-2.5-flash` → `google/gemini-3.8-flash` | 11 |
| **Total** | | **533** (≈1.03 per request) |

Tokens and costs for these calls are in the acquisition table below (unchanged). Agent-first experiments,
including the 2026-10-25 live cohort at the end of this file, are separate and did not produce `output.csv`.

## Original run (Phase 3)

**Roar AI smoke tests (D070):** 4 live requests, all synthetic:
- `jev` evaluate: HTTP 400 schema rejection, then HTTP 200 (412/55 tokens);
- `claude-haiku-5-5` tool call: 510/39 tokens;
- `gpt-oss-20b` tool call: 130/34 tokens.

Roar returned no cost headers. The catalog estimate for all of them is about USD 0.00011 (under 1 paisa).

**Phase 4 (D065–D068):** no model call was made. The investigator ran only in replay mode, where cached
responses are the only source and a zero ceiling refuses everything else, and in a scripted-transcript
demonstration. Both spent **0 paise** and used 0 tokens. The full-dataset run behind `output.csv` is still
the Phase 3 run below. The investigator's live cost (role `investigator`) will be reported per model once a
probe passes and a bounded live run is made.

**Hardening pass:** the hardened code regenerated the same `output.csv`, byte-identical, into
`runs/phase3-hardened/`. It made 0 network calls and 533 validated cache hits, and every cache entry
was re-validated on read. Its acquisition cost is the one below. The pass spent **0 paise**: every live
attempt, made while the gateway was returning HTTP 503, failed and none was billed. The new item-scope
target question (`intent_target_jev_v2`) has not yet been answered live. The follow-up patch also
spent 0 paise (one uncached Jev probe returned HTTP 503).

This report covers the run that produced the provisional root `output.csv`
(518 rows, SHA-256 `968454758d71…89982f`, run directory `runs/phase3-provisional/`).

**How the responses were obtained.** Every model response used by that run came
from the validated response cache. The final invocation made **0 network calls**:
`model_calls: 0`, `model_cache_hits: 533` in `run_manifest.json`. Those cached
responses were acquired by earlier live runs of the same code and prompts, with
the same cache keys (endpoint, requested model, prompt hash, input). Both costs
are reported below, so the cost of producing the output from scratch is visible.

Gateway: TokenBazaar `https://tokenbazaar.in/api/public/v1`. Costs come from the
gateway header `x-tokenbaazar-cost-paise` (100 paise = ₹1). Token counts are
what the gateway returned.

## Acquisition cost of the responses behind output.csv

| Role | Endpoint | Requested model | Returned model | Calls | Input tokens | Output tokens (of which reasoning) | Cost (paise) |
|---|---|---|---|---|---|---|---|
| Intent (one typed request per case: 1 choice + 3 yes/no) | `/decisions` | `typesafe/jev-latest` | `typesafe/jev-1.13-20260917` | 518 | 382,091 | 65,806 (0) | 518 |
| PDF document kind | `/decisions` | `typesafe/jev-latest` | `typesafe/jev-1.13-20260917` | 4 | 3,004 | 308 (0) | 4 |
| Blind screenshot reader | `/chat/completions` | `google/gemini-2.5-flash` | `google/gemini-3.8-flash` | 11 | 16,229 | 19,868 (11,649) | 529 |
| Intent generative fallback | `/chat/completions` | `google/gemini-2.5-flash` | — | 0 | 0 | 0 | 0 |
| **Total** | | | | **533** | **401,324** | **85,982** | **1,051 (≈ ₹10.51)** |

Per request (518):
- **Tokens:** 401,324 input + 85,982 output = 487,306 total, about **940 per request** (intent alone is about 865).
- **Cost:** about **2.03 paise per request**.

Jev also reports a USD cost of $0.016111 for the 518 intent calls. The header
figure in paise is used for budgeting.

Latency (live Jev intent calls in full run 1): 361 successful calls, 555 s total,
1.54 s mean, at concurrency 4.

## Rate limiting and failures

Full run 1 hit HTTP 429 on 8 Jev calls after 2 retries. Those cases escalated
safely to the generative fallback (2 calls, 34 paise). The retry policy then
changed to honour `Retry-After`, with up to 5 bounded rate-limit retries. A
rerun recovered all 518 Jev answers with zero 429s, and the output was
identical. Log: `runs/phase3-provisional/benchmarks/full_run1_usage_with_429s.jsonl`.

## Development and evaluation spend in Phase 3 (not part of the output run)

| Activity | Cost (paise) |
|---|---|
| Bounded capability probes (decisions/chat shapes, model IDs, headers) | ~24 |
| Intent benchmark v1 + v2 (72 synthetic + 32 samples; Jev, generative, staged) | 1,289 |
| Media acquisition (PDF kind + screenshots, including one retry for an unparseable reply) | 533 |
| Full-set runs (including the 429 run and its generative escalations) | 402 |
| **Total live spend** | **≈ 2,248 (≈ ₹22.48)** |

Every live run uses a hard ceiling (`--budget-paise`, 1,500 by default), checked
before each call, and controlled concurrency (4).

## Notes

- **Model substitution:** the gateway serves `google/gemini-2.5-flash` as `google/gemini-3.8-flash`. The configuration keeps the requested ID, and every usage record stores both (decisions D036).
- **Credentials:** read only from `TOKENBAZAAR_API_KEY`. No key value is logged or stored.
- **Per-attempt records:** `runs/phase3-provisional/usage.jsonl` lists every attempt of the output run. All 533 records have `mode: cache`.

## Agent-first live cohort, 2026-10-25 (separate from the run behind output.csv)

| Role | Provider / model | Live calls | Invalid | Input tokens | Output tokens | Cost |
|---|---|---|---|---|---|---|
| primary_agent | Roar / `claude-haiku-5-5` | 94 | 5 (schema) | 1,219,255 | 41,927 | no cost header; 4,700 paise conservative exposure |
| tool_probe | Roar / `claude-haiku-5-5` | 1 | 0 | 510 | 39 | no cost header |

Across 13 single-case runs this is about 94k input tokens and 7 calls per case run. The conservative total is
₹47.00 (plus the probe), within the owner's ₹50 ceiling. Scaling the earlier 32-case catalog estimate
(₹24.69 for 2.17M input tokens) gives roughly ₹14 expected, but that is an estimate, not an invoice.
No transport retries were made. The probe was recorded only in a scratch copy of the cache; the
repository cache is unchanged.
