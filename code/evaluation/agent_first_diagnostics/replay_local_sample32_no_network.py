"""Safely diagnose whether the ignored old sample-32 cache matches current code.

Requires the local ignored `.runs/agent-sample32-cckl3ldg/` experiment artifacts. Copies inputs
into a temporary directory and patches both HTTP methods to fail, so this script cannot call a model.
It never writes into `.runs/` and does not claim cache replay is a test of model quality.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / ".runs/agent-sample32-cckl3ldg"


def main() -> int:
    source = ARTIFACTS / "replay.py"
    if not source.is_file():
        raise SystemExit(f"Local ignored replay script not found: {source}")
    with tempfile.TemporaryDirectory(prefix="ghost-agent-safe-replay-") as directory:
        replay_root = Path(directory)
        (replay_root / "agent").mkdir()
        shutil.copytree(ARTIFACTS / "agent/model_cache", replay_root / "agent/model_cache")
        shutil.copy(ARTIFACTS / "agent/run_manifest.json", replay_root / "agent/run_manifest.json")
        shutil.copy(ARTIFACTS / "agent.csv", replay_root / "agent.csv")
        script = source.read_text(encoding="utf-8")
        script, count = re.subn(
            r"ROOT=Path\('.*?'\)", f"ROOT=Path({str(ROOT)!r})", script, count=1, flags=re.MULTILINE
        )
        if count != 1:
            raise SystemExit("Could not safely adapt the local replay script's repository path")
        script = script.replace(
            "assert (R/f'{tag}.csv').read_bytes()==(R/'agent.csv').read_bytes()",
            "identical=(R/f'{tag}.csv').read_bytes()==(R/'agent.csv').read_bytes()",
        )
        script = script.replace(
            "assert s['agent_first']['accepted_cases']==original['agent_first']['accepted_cases']",
            "accepted_same=(s['agent_first']['accepted_cases']==original['agent_first']['accepted_cases'])",
        )
        script = script.replace(
            "'identical_output':True,'accepted_cases':s['agent_first']['accepted_cases']",
            "'identical_output':identical,'accepted_cases':s['agent_first']['accepted_cases'],"
            "'accepted_same':accepted_same",
        )
        if "identical_output':identical" not in script:
            raise SystemExit("Could not safely adapt the replay equality checks")
        replay_file = replay_root / "replay.py"
        replay_file.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(replay_file), "current"],
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
