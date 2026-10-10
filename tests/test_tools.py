"""The measurement tools in `tools/` are part of the deliverable: they regenerate ACCURACY.md's tables.

Two of them are offline and fast, so they run here; if one stops reproducing the figures quoted in
ACCURACY.md §1 and §2 this suite fails instead of the docs quietly rotting. The third
(`tools/sandhi_ceiling.py`) calls the splitter over all 147 padas (~25 s) and is only exercised under
`SAMSKRTA_LIVE_GOLDEN=1`, alongside the other live measurements.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def run_tool(name: str, *args: str) -> str:
    """Run one tool with the project environment and return its stdout.

    Args:
        name: script name inside ``tools/``
        args: command-line arguments

    Returns:
        Captured stdout (stderr is left to pytest so upstream warnings stay visible)
    """
    completed = subprocess.run([sys.executable, f"tools/{name}", *args], cwd=ROOT,
                               capture_output=True, text=True, timeout=300)
    assert completed.returncode == 0, f"{name} exited {completed.returncode}: {completed.stderr[-800:]}"
    return completed.stdout


def test_meter_audit_reproduces_the_metre_scoreboard():
    """ACCURACY §1: five named verses, and the akshara grid equals the edition everywhere."""
    out = run_tool("meter_audit.py")
    assert "named 5/16" in out
    assert "akshara grid equals the edition on 16/16" in out
    assert "❌" not in out, f"a published metre name went wrong:\n{out}"


def test_meter_audit_explains_every_null_with_a_measured_reason():
    """ACCURACY §1: each unnamed verse says why — a declared length that was vetoed, or no suggestion."""
    out = run_tool("meter_audit.py", "--nulls")
    assert "11 verses shown" in out
    assert "→ ⚠️ unnamed" in out
    for line in out.splitlines():
        if line.startswith("  why       "):
            assert ("vetoed" in line or "no metre at all" in line or "refuse to pick" in line), line


def test_morphology_ranks_reproduces_the_grammar_scoreboard():
    """ACCURACY §2: four primaries correct, the right reading still offered for every other form."""
    out = run_tool("morphology_ranks.py")
    assert "equals the reference on 4/9 forms" in out
    assert "NOT OFFERED" not in out, f"the engine stopped offering a known reading:\n{out}"


@pytest.mark.skipif(
    os.environ.get("SAMSKRTA_LIVE_GOLDEN") != "1",
    reason="set SAMSKRTA_LIVE_GOLDEN=1 to run the ~25 s ceiling measurement over all curated padas",
)
def test_sandhi_ceiling_splits_the_misses_by_fault():
    """ACCURACY §3: score, pool ceiling and the ⚠️/❌ split come from one process."""
    out = run_tool("sandhi_ceiling.py", "--examples", "0")
    figures = dict(re.findall(r"(picked|ceiling) (\d+)/147", out))
    assert int(figures["picked"]) >= 99, out          # the same floor as the gated corpus score
    assert int(figures["ceiling"]) >= 125, out        # the pool ceiling drifts by one pada per process
    assert "pool-limited" in out and "ranking-limited" in out, out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
