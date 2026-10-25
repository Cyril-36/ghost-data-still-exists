# Phase 4 live 32-sample experiment on Roar AI (2026-10-25)

All three configurations use Roar `jev` for intent (`--intent jev`: no generative fallback) and PDF document kind. There
is no screenshot reader (no validated Roar vision model). Root `output.csv` and the sample labels are untouched.

| | A: investigator off | B: investigator live (`gpt-oss-20b`) | B2: B + pacing guidance and final-turn notice |
|---|---|---|---|
| Statuses correct | 28/32 | 28/32 (identical predictions) | 28/32 (identical predictions) |
| Exact rows | 1/32 | 1/32 | 1/32 |
| Step precision / recall | 141/142, 141/193 | same | same |
| Citations correct (expected cited steps) | 56/75 | same | same |
| Resource obligations missing / additional (of 111) | 51 / 0 | same | same |
| Routed / investigated / accepted | 15 / 0 / – | 15 / 15 / 0 | 15 / 15 / 0 |
| Stop reasons | – | ROUND_LIMIT 14, MODEL_ERROR 1 | ROUND_LIMIT 13, MODEL_ERROR 1, SUBMITTED (no supported action) 1 |
| Rounds / tool calls | – | 59 / 59 | 58 / 58 |
| Facts accepted / rejected | – | 1 / 0 (never submitted) | 2 / 1 (never submitted) |
| Verifier passes / rejections | – | 0 / 0 | 1 / 0 |
| Live calls (cache hits) | 38 (0) | 60 (38) | 45 (52) |
| Estimated cost (catalog price; Roar reports no actual billing) | 10.2 paise | 347.6 paise | 275.6 paise |

A first B2 attempt crashed on a connection reset that was not treated as a transport failure (fixed). Its completed
turns were cached and reused, about 14 calls or roughly ₹0.85 estimated. One TokenBazaar
`google/gemini-2.5-flash` probe returned HTTP 503 and billed nothing. The experiment total is about ₹7.2
estimated; actual billing is unknown, because Roar sends no cost header.

Historical reference C (TokenBazaar staged: Jev plus generative fallback and screenshot reading, different models and
evidence conditions, not an identical experiment): 28/32 statuses, 142/193 recall, 142/143 precision, 2/32 exact.

## Why the investigator changed nothing

The cause is model tool-use quality, not evidence rules or the verifier:
- `gpt-oss-20b` made exactly one tool call per turn, even when told it may batch calls.
- It spent its 4 routing rounds exploring. In B2, 13 of 15 cases still hit the round limit after an
  explicit final-turn notice.
- Its calls were relevant: subject searches, source inspection, timelines. In case 07 it found and
  inspected the `evidence_58` PDF holding `support_001`. Its 3 accepted facts (case 07 catalog link
  `object_19`, case 14 `account_3677`, case 32 `tombstone_9178`) were never submitted, so under D069 they
  were correctly not published.
- One fact was rejected: its literal did not appear at the cited locator.
- Two model errors were provider-side HTTP 400s ("failed to parse tool call arguments", "reply did not
  match"); neither was retried.
- The verifier never rejected anything, because almost nothing reached it.

The remaining misses in A are unchanged from the error analysis: 31 OWNERSHIP_UNGROUNDED, 11
RETENTION_CONDITION_UNPROVEN, 5 POST_CUTOFF_ONLY and 3 NOT_IN_STRUCTURED_DATA. Ownership and retention
stay strict, by decision.
