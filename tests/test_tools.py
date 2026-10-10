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
    """ACCURACY §2: ten primaries correct, and every remaining miss carries a documented limit."""
    out = run_tool("morphology_ranks.py")
    assert "equals the reference on 10/15 forms" in out
    assert "NOT OFFERED" not in out, f"the engine stopped offering a known reading:\n{out}"
    lines = out.splitlines()
    wrong = [i for i, line in enumerate(lines) if "❌" in line]
    assert len(wrong) == 5, out
    for i in wrong:
        assert lines[i + 1].startswith("  limit"), f"a wrong primary with no documented reason:\n{lines[i]}"


def test_morphology_lab_shows_the_shipped_rule_as_a_no_op_and_keeps_the_rejected_one():
    """ACCURACY §2/§6: re-scoring the corpus with the rule that shipped must move nothing; the rejected
    longer-stem variant stays measured so it is not tried again blind."""
    out = run_tool("morphology_lab.py")
    assert "corpus: 278 published word readings over 16 verses" in out
    assert "rule current: curated forms with the right reading published first: 10/15" in out
    assert "primaries moved: 0 of 278" in out, f"the lab no longer matches what postprocess publishes:\n{out}"
    assert "rule attested-then-longest-root: curated forms with the right reading published first: 9/15" in out
    assert "primaries moved: 36 of 278" in out


@pytest.mark.skipif(
    os.environ.get("SAMSKRTA_LIVE_GOLDEN") != "1",
    reason="set SAMSKRTA_LIVE_GOLDEN=1 to run the ~25 s ceiling measurement over all curated padas",
)
def test_sandhi_ceiling_splits_the_misses_by_fault(tmp_path):
    """ACCURACY §3: score, pool ceiling and the ⚠️/❌ split come from one process."""
    out = run_tool("sandhi_ceiling.py", "--examples", "0")
    figures = dict(re.findall(r"(picked|ceiling) (\d+)/147", out))
    assert int(figures["picked"]) >= 99, out          # the same floor as the gated corpus score
    assert int(figures["ceiling"]) >= 125, out        # the pool ceiling drifts by one pada per process
    assert "pool-limited" in out and "ranking-limited" in out, out

    # The cached-pool path is how a ranking idea gets measured against an identical search space: dump the pools and
    # their ranking features once, then re-rank them through app._rank_with_kosha without calling the splitter again.
    # If that stops reproducing the live figures, the cache no longer represents what our ranking chooses from.
    pools = tmp_path / "pools.json"
    dumped = run_tool("sandhi_ceiling.py", "--examples", "0", "--pools", str(pools))
    assert pools.exists() and "candidate pools" in dumped, dumped
    reused = run_tool("sandhi_ceiling.py", "--examples", "0", "--reuse", str(pools))
    cached = dict(re.findall(r"(picked|ceiling) (\d+)/147", reused))
    for figure in ("picked", "ceiling"):
        assert abs(int(cached[figure]) - int(figures[figure])) <= 1, f"{figure} drifted:\n{reused}"
    assert "ranking-limited" in reused, reused

    # `tools/sandhi_lab.py` re-ranks those identical pools, so its baseline row must match what the ceiling tool just
    # measured from the live engines; if it drifts, the lab is no longer judging what the app would publish. Rules the
    # lab documents as measured-and-rejected must still lose to that baseline.
    lab = run_tool("sandhi_lab.py", "--pools", str(pools))
    scores = dict(re.findall(r"rule (\S+): picked (\d+)/147", lab))
    assert abs(int(scores["current"]) - int(figures["picked"])) <= 1, lab
    for rejected in ("deep-gate-min-len-3", "attestation-before-count"):
        assert int(scores[rejected]) < int(scores["current"]), f"{rejected} is no longer a rejection:\n{lab}"
    assert "old-gate-strictly-better" in scores, lab


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
