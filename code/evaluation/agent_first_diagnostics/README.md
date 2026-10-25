# Sample-32 agent-first diagnostic handoff

This package contains a whitelist-only extraction from the preserved local run at
`.runs/agent-sample32-cckl3ldg/`. It has one compact entry for each of the 32 public sample cases and
every submitted proposal. It records proposals before verification, per-proposal verifier rejection
codes, stop reasons, token/call counts, tool usage, retrieval counts and whether the proposal's
steps matched the expected sample output after inference.

It intentionally excludes customer request text, personal-looking identifiers, agent reasoning,
source excerpts, witness literals, raw tool arguments/results, provider prompts and responses, API
headers, credentials, and cache files. The labels in this diagnostics-only artifact are for evaluation;
production inference does not load this package.

The test `code/tests/test_agent_root_cause.py::RootCauseFixes.test_verifier_hint_can_be_investigated_before_corrected_agent_submission`
is the compact offline reproduction of the feedback deadlock: a rejected proposal gets a missing
resource hint, the next agent round can search that hinted ID, the result is independently checked,
and a corrected agent proposal is verified. The fixture uses only synthetic IDs and a scripted
provider; it does not replay or imply new model behavior.

No raw cached model response is required to reproduce these fixes. Run:

```sh
python3 -m unittest discover -s code/tests -p 'test_agent_root_cause.py' -v
```
