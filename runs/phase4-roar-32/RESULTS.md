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

## B3: `gemini-3.8-flash` investigator (your choice after `google/gemini-2.5-flash` was unreachable)

Background: TokenBazaar returned HTTP 503 to the one probe (nothing billed), and Roar does not list
`gemini-2.5-flash`. `gemini-3.8-flash` is the model TokenBazaar actually served for `gemini-2.5-flash`
requests. Its probe passed.

| | B3 |
|---|---|
| Predictions | identical to A: 28/32 statuses, 1/32 exact, 141/142 precision, 141/193 recall |
| Routed / investigated / accepted | 15 / 2 that ran / 0; 13 stopped at RUN_BUDGET before or during their first call |
| Tool use | batches calls (10 and 8 in its two cases, 9 model turns, 19 tool calls); 4 `inspect_media` TOOL_ERRORs (wrong page or non-PDF handle) |
| Outcome of the two cases | case 01 and case 02 submitted NO_SUPPORTED_ACTION; correct under the strict ownership rule (account records have no grounded link) |
| Estimated cost | 705.6 paise for 9 calls (59–126 paise each; 6.6–11.5k input tokens per turn); actual billing unreported |

Unlike `gpt-oss-20b`, it batches and submits. But at about ₹3 per investigated case, all 15 routed
cases would cost about ₹40–45, beyond the ₹15 limit. Experiment total, estimated:
A 10 + B 348 + crashed B2 attempt ≈ 85 + B2 276 + B3 706 ≈ **1,425 paise (₹14.3)**.

The estimate overshot the 700-paise ceiling by 5.6 paise, because the flat 50-paise chat reserve was below
the real cost of a Gemini turn. Fixed: a priced call now reserves its worst case (request bytes / 3 as
input tokens, plus the full output limit, at catalog price).
