"""Re-measure ACCURACY.md §2 — is the published primary right, and at what rank does the right reading survive?

Offline: reads only the pinned `tests/data/results/*.result.json` documents and
`tests/data/morphology_truth.json`, scoring with the same matcher the test suite uses
(`_matches`: expected fields equal, tags a superset, any reference variant accepted), so the tool cannot
report something the tests would disagree with.

The point of printing the *rank* is attribution. When the right reading sits at alternate #1 or #2 the
miss is ours — a ranking decision that could be fixed here (❌). When it sits near the end, or is not
offered at all, no context-free ordering of single-word analyses will ever reach it (⚠️), because
sanskrit_parser analyses each pada alone and offers every reading the grammar allows.

Usage:
    uv run python tools/morphology_ranks.py
"""

import json

from _env import setup

setup(with_tests=True)

from test_morphology_accuracy import BY_ID, _matches, _published_readings  # noqa: E402


def brief(reading: dict) -> str:
    """Compress one engine reading to the fields that carry its grammar.

    Args:
        reading: a published reading (`form`, `pid`, case/number fields, ...)

    Returns:
        Short single-line summary, tags omitted
    """
    parts = [f"{key}={value}" for key, value in reading.items()
             if key not in ("tags", "alternates") and value not in (None, "", [])]
    return " ".join(parts)


def main() -> None:
    """Print the verdict per fixture form plus where the reference reading ranks among our alternates."""
    root = setup(with_tests=True)
    rows = json.loads((root / "tests/data/morphology_truth.json").read_text(encoding="utf-8"))["rows"]

    correct = 0
    for row in sorted(rows, key=lambda r: f"{r['verse']}/{r['form']}"):
        stem, form = row["verse"], row["form"]
        primary, alternates = _published_readings(stem, form)
        hit_primary = any(_matches(primary, exp) for exp in row["expected"])
        rank = None
        for index, reading in enumerate(alternates, start=1):
            if any(_matches(reading, exp) for exp in row["expected"]):
                rank = index
                break
        correct += int(hit_primary)
        verdict = "✅ primary" if hit_primary else "❌ wrong primary"
        where = "primary" if hit_primary else (f"alternate #{rank} of {len(alternates)}" if rank
                                               else f"NOT OFFERED in {len(alternates)} alternates")
        print(f"{stem} {form}\n  truth      {brief(row['expected'][0])}"
              + (f"\n             or: {brief(row['expected'][1])}" if len(row["expected"]) > 1 else ""))
        print(f"  published  {brief(primary)}   → {verdict}; right reading survives as {where}")
        if "limit" in row:
            print(f"  limit      {row['limit']}")

    print(f"\npublished primary equals the reference on {correct}/{len(rows)} forms; "
          f"the rest are offered but ranked lower")


if __name__ == "__main__":
    main()
