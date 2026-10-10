"""Re-measure the sandhi figures quoted in ACCURACY.md §3 — score, ceiling, and who is at fault.

One process prints every figure so they stay comparable:

    picked          how many of the 147 curated padas our published split matches (the headline)
    ceiling         how many have *any* reference-consistent candidate in sanskrit_parser's pool — the
                    most any ranking could score without better upstream splitting
    pool-limited    misses outside the ceiling: the answer is not in the engine's pool at all ⚠️
    ranking-limited misses inside the ceiling where a correct candidate existed and our ordering did
                    not take it ❌ — this is the room left for ranking work

Splitting every pada twice (once through `_best_word_split`, once raw) makes this a live run: about
25 s with the bundled data, no network.

Usage:
    uv run python tools/sandhi_ceiling.py                      # counts plus a few examples of each miss
    uv run python tools/sandhi_ceiling.py --pools pools.json   # also dump candidate pools so ranking
                                                               # experiments can run offline on identical
                                                               # search spaces (DOCUMENTATION §9)
    uv run python tools/sandhi_ceiling.py --reuse pools.json   # ceiling only, from cached pools

Scoring is imported from ``tests/test_sandhi_accuracy.py`` so this tool and the test suite can never
drift apart on what "matches" means.
"""

import argparse
import json
from pathlib import Path

from _env import setup

setup(with_tests=True)

from indic_transliteration import sanscript  # noqa: E402
from sanskrit_parser.api import Parser  # noqa: E402
from test_sandhi_accuracy import pada_matches  # noqa: E402

import app  # noqa: E402


def candidate_pools(rows, parser) -> dict:
    """Collect sanskrit_parser's raw candidate pool for every curated pada.

    Mirrors the filtering inside ``app._best_word_split_with_items`` (deduplicate, drop any part of
    length one) so a cached pool is exactly the search space our ranking chooses from.

    Args:
        rows: truth rows, each with an IAST ``pada``
        parser: sanskrit_parser Parser built for Devanagari output

    Returns:
        {pada: [list of IAST parts, ...]} in engine order — that order is unspecified and drifts
        between processes, which is why the score itself moves by a few padas from run to run
    """
    pools = {}
    for row in rows:
        devanagari = sanscript.transliterate(row["pada"], sanscript.IAST, sanscript.DEVANAGARI)
        seen, pool = set(), []
        for split in parser.split(devanagari, limit=10) or []:
            parts = tuple(app.devanagari_to_iast(word.devanagari()) for word in split.split)
            if parts in seen or any(len(part) <= 1 for part in parts):
                continue
            seen.add(parts)
            pool.append(list(parts))
        pools[row["pada"]] = pool
    return pools


def to_devanagari(rows) -> dict:
    """Map each curated pada (IAST) to its Devanagari spelling, for calling the splitter.

    Args:
        rows: truth rows

    Returns:
        {pada_iast: pada_devanagari}
    """
    return {row["pada"]: sanscript.transliterate(row["pada"], sanscript.IAST, sanscript.DEVANAGARI)
            for row in rows}


def main() -> None:
    """Print the figures, concrete examples of each miss kind, and optionally cache the pools."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", default="tests/data/sandhi_truth.json")
    parser.add_argument("--examples", type=int, default=4, help="misses to print per category (0 for none)")
    parser.add_argument("--pools", help="write the candidate pools to this JSON file")
    parser.add_argument("--reuse", help="read pools from this JSON file instead of splitting again")
    args = parser.parse_args()

    root = setup(with_tests=True)
    rows = json.loads((root / args.fixture).read_text(encoding="utf-8"))
    kosha = app.load_kosha()
    if kosha is None:
        raise SystemExit("vidyut kosha data is not installed — set VIDYUT_DATA_DIR")

    devanagari = to_devanagari(rows)
    engine = Parser(output_encoding=sanscript.DEVANAGARI)
    if args.reuse:
        pools = json.loads(Path(args.reuse).read_text(encoding="utf-8"))
    else:
        pools = candidate_pools(rows, engine)
        if args.pools:
            Path(args.pools).write_text(json.dumps(pools, ensure_ascii=False), encoding="utf-8")
            print(f"wrote {len(pools)} candidate pools to {args.pools}")

    picked_ok = ceiling_ok = 0
    pool_limited, ranking_limited = [], []
    for row in rows:
        pada = row["pada"]
        in_pool = any(pada_matches(kosha, parts, row["parts"]) for parts in pools[pada])
        if args.reuse:
            picked_ok += 0
            ceiling_ok += int(in_pool)
            if not in_pool:
                pool_limited.append((pada, row["parts"], pools[pada][:3]))
            continue
        got = app._best_word_split(engine, devanagari[pada], kosha)
        ok_picked = pada_matches(kosha, got, row["parts"])
        picked_ok += int(ok_picked)
        ceiling_ok += int(in_pool or ok_picked)
        if not (in_pool or ok_picked):
            pool_limited.append((pada, row["parts"], pools[pada][:3]))
        elif not ok_picked:
            ranking_limited.append((pada, got, row["parts"]))

    if args.reuse:
        # cached pools hold candidates only; our own ranking needs the splitter, so it is not scored here
        print(f"picked n/a (cached pools carry candidates only)   ceiling {ceiling_ok}/{len(rows)}")
    else:
        print(f"picked {picked_ok}/{len(rows)}   ceiling {ceiling_ok}/{len(rows)}")
    print(f"pool-limited (no reference-consistent candidate in the pool): {len(pool_limited)}")
    if not args.reuse:
        room = ceiling_ok - picked_ok
        print(f"ranking-limited (a correct candidate existed, our ranking did not take it): "
              f"{room} — max gain available to any ranking change is +{room}")
    for pada, want, pool in pool_limited[:args.examples]:
        print(f"  ⚠️ {pada}\n      truth      {want}\n      best of pool {pool}")
    for pada, got, want in ranking_limited[:args.examples]:
        print(f"  ❌ {pada}\n      truth      {want}\n      we picked    {got}")


if __name__ == "__main__":
    main()
