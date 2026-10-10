"""Score a proposed sandhi-split ranking rule against the curated padas — offline, in seconds.

`tools/sandhi_ceiling.py --pools pools.json` dumps, for all 147 curated padas of
`tests/data/sandhi_truth.json`, every candidate sanskrit_parser offered together with the three features our ranking
reads from the live analyzer objects: `exact_all` (every part is an exactly attested kosha form), `entry_min` (kosha
entries for the scarcest part) and `standalone` (every part has case+number or is an avyaya). With that file this lab
re-ranks the identical search space through `app._rank_with_kosha` — the shipped function, not a copy of it — so a rule
is judged on exactly what the app would publish, and one run costs ~2 s instead of re-splitting for 25 s.

Only padas inside the pool ceiling can move: misses outside it (⚠️ in the ceiling tool) are upstream splitting faults
and no ordering can fix them.

Rules implemented, with what each one scored on 2026-10-10 (the shipped rule scores 104/147; before it landed the corpus was 103/147):

    current                 what `_rank_with_kosha` publishes today — re-scoring it must reproduce the live score,
                            which is this lab's regression check. It now includes the gate clause below, so `current`
                            itself scores 104/147 and every rejected rule is measured against that.

    old-gate-strictly-better
                            the gate as it shipped until 2026-10-10: a compound split could beat the whole pada only if
                            its scarcest part was *strictly better* attested than the whole word. Scores 103/147 — kept
                            so the landed change stays measurable.

    gate-at-least-whole     `>=` instead of `>`: a split whose scarcest part is merely as well attested as the whole
                            pada may be cut. SHIPPED: 104/147, fixes exactly one pada — `paścārdhena` → paścā | ardhena,
                            where vidyut's kosha records *paścā* and *ardhena* with two entries each, exactly as many as
                            the fused form. Nothing regressed.

    deep-gate-min-len-3     lower the gate's minimum part length from five letters (`_MIN_DEEP_PART_LEN`) to three, so
                            short real members such as *vāk* get through. REJECTED: 84/147 — it shreds dative forms into
                            sandhi fragments (`satyāya` → satī | āya, `prajāyai` → prajās | yai, `navāni` → nava | āni).

    deep-gate-max-parts-4   raise the gate's part ceiling from three to four (`_MAX_DEEP_PARTS`). Measured 105/147 with
                            no regression — it fixes one pada (`śramavivṛtamukhabhraṃśibhiḥ`), which is not worth another
                            live golden regeneration, and four-part cuts have an unmeasured blast radius on words the
                            fixture does not cover. Not shipped.

    deep-gate-len3-and-4    both knobs together: 84/147 — dominated by the length change above.

    no-whole-word-veto      apply the gate even when the whole pada is itself attested, i.e. drop the comparison with
                            the whole word entirely. REJECTED at 105/147 (+1 over the shipped rule): it cuts
                            `prāṃśulabhye`, `yathāparādhadaṇḍānāṃ` and `vinivṛttakāmāḥ` correctly but breaks the invariant
                            ACCURACY §3 documents — `mokṣayiṣyāmi` → mokṣe | iṣyāmi, `madhurāṇāṃ` → madhu | rāṇām.

    attestation-before-count
                            compare how well attested the scarcest part is before comparing part counts. REJECTED:
                            68/147 — common fragments then outrank real readings (`māmekaṃ` keeps *māme* over mām | ekam).

Usage:
    uv run python tools/sandhi_ceiling.py --pools pools.json      # dump the pools once (~25 s)
    uv run python tools/sandhi_lab.py --pools pools.json          # score every rule against them (~2 s each)
    uv run python tools/sandhi_lab.py --pools pools.json --show-changes   # list every pada a rule moved
"""

import argparse
import json
from pathlib import Path

from _env import setup

setup(with_tests=True)

from sandhi_ceiling import pool_parts  # noqa: E402
from test_sandhi_accuracy import pada_matches  # noqa: E402

import app  # noqa: E402


def candidates_of(pool: list) -> list:
    """Rebuild the candidate dicts `app._best_word_split_with_items` hands to the ranker.

    Args:
        pool: cached candidates for one pada, carrying parts/exact_all/entry_min/standalone

    Returns:
        Candidate dicts with the keys `_rank_with_kosha` reads; [] for an empty pool
    """
    candidates = []
    for cand in pool:
        if not isinstance(cand, dict):
            raise SystemExit("cached pool predates the ranking features — re-dump it with "
                             "`tools/sandhi_ceiling.py --pools <file>`")
        parts = tuple(cand["parts"])
        candidates.append({
            "parts": parts,
            "count": len(parts),
            "min_part_len": min(len(p) for p in parts),
            "standalone": cand["standalone"],
            "exact_all": cand["exact_all"],
            "entry_min": cand["entry_min"],
        })
    return candidates


def rank_with_gate(max_parts: int, min_len: int):
    """Ranker factory: the shipped ordering with the transparent-compound gate's two knobs set.

    `app._rank_with_kosha` reads `_MAX_DEEP_PARTS` and `_MIN_DEEP_PART_LEN` from module globals at call time, so the
    experiment reuses the shipped code path instead of duplicating it.

    Args:
        max_parts: gate's maximum number of parts
        min_len: gate's minimum length of every part

    Returns:
        A ranker usable as `rank(candidates) -> candidate`
    """
    def rank(candidates):
        limits = (app._MAX_DEEP_PARTS, app._MIN_DEEP_PART_LEN)
        app._MAX_DEEP_PARTS, app._MIN_DEEP_PART_LEN = max_parts, min_len
        try:
            return app._rank_with_kosha(candidates)
        finally:
            app._MAX_DEEP_PARTS, app._MIN_DEEP_PART_LEN = limits
    return rank


def rank_without_whole_word_veto(candidates):
    """The shipped ordering with the "scarcest part must beat the whole word" clause removed.

    Kept here, not in `app.py`: measured and rejected. Everything else — keys, tie-breaks, gate shape — is copied
    verbatim from `_rank_with_kosha` so only that one clause differs.

    Args:
        candidates: candidate dicts as built by `candidates_of`

    Returns:
        The winning candidate dict
    """
    for cand in candidates:
        cand["deep"] = (
            cand["exact_all"]
            and 1 < cand["count"] <= app._MAX_DEEP_PARTS
            and cand["min_part_len"] >= app._MIN_DEEP_PART_LEN
        )
    return max(
        candidates,
        key=lambda c: (
            c["exact_all"], c["deep"], -c["count"], c["standalone"],
            c["entry_min"], c["min_part_len"], sorted(c["parts"]),
        ),
    )



def rank_variant(gate_at_least_whole: bool = False, attestation_before_count: bool = False):
    """Ranker factory: the shipped ordering with one named clause switched at a time.

    Args:
        gate_at_least_whole: let the transparent-compound gate fire when the split's scarcest part is *as* well
            attested as the whole word, not only strictly better (`>=` instead of `>`)
        attestation_before_count: compare candidates on how well attested their scarcest part is before comparing
            how many parts they have — the fused form `cā | alpaviṣayā` currently wins on part count alone even
            though its rarest part is worse attested than the reference split's

    Returns:
        A ranker usable as `rank(candidates) -> candidate`
    """
    def rank(candidates):
        whole = next((c for c in candidates if c["count"] == 1), None)
        whole_attested = bool(whole and whole["exact_all"])
        whole_entries = whole["entry_min"] if whole else -1
        for cand in candidates:
            beats_whole = (cand["entry_min"] >= whole_entries) if gate_at_least_whole else (cand["entry_min"] > whole_entries)
            cand["deep"] = (
                cand["exact_all"]
                and 1 < cand["count"] <= app._MAX_DEEP_PARTS
                and cand["min_part_len"] >= app._MIN_DEEP_PART_LEN
                and (not whole_attested or beats_whole)
            )
        if attestation_before_count:
            key = lambda c: (c["exact_all"], c["deep"], c["standalone"], c["entry_min"], -c["count"],
                             c["min_part_len"], sorted(c["parts"]))
        else:
            key = lambda c: (c["exact_all"], c["deep"], -c["count"], c["standalone"], c["entry_min"],
                             c["min_part_len"], sorted(c["parts"]))
        return max(candidates, key=key)
    return rank

RULES = {
    "current": app._rank_with_kosha,
    "deep-gate-min-len-3": rank_with_gate(app._MAX_DEEP_PARTS, 3),
    "deep-gate-max-parts-4": rank_with_gate(4, app._MIN_DEEP_PART_LEN),
    "deep-gate-len3-and-4": rank_with_gate(4, 3),
    "no-whole-word-veto": rank_without_whole_word_veto,
    "gate-at-least-whole": rank_variant(gate_at_least_whole=True),
    "attestation-before-count": rank_variant(attestation_before_count=True),
    "gate-gte-and-attestation-first": rank_variant(gate_at_least_whole=True, attestation_before_count=True),
    "old-gate-strictly-better": rank_variant(),
}


def main() -> None:
    """Score every rule over the cached pools and print what each one moves."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pools", required=True, help="candidate pools dumped by tools/sandhi_ceiling.py --pools")
    ap.add_argument("--fixture", default="tests/data/sandhi_truth.json")
    ap.add_argument("--show-changes", type=int, default=0, help="padas each rule fixed or broke (0 for none)")
    args = ap.parse_args()

    root = setup(with_tests=True)
    rows = json.loads((root / args.fixture).read_text(encoding="utf-8"))
    pools = json.loads(Path(args.pools).read_text(encoding="utf-8"))
    kosha = app.load_kosha()
    if kosha is None:
        raise SystemExit("vidyut kosha data is not installed — set VIDYUT_DATA_DIR")

    candidates = {row["pada"]: candidates_of(pools[row["pada"]]) for row in rows}
    ceiling_ok = sum(int(any(pada_matches(kosha, pool_parts(c), row["parts"]) for c in pools[row["pada"]]))
                     for row in rows)
    print(f"corpus: {len(rows)} curated padas from {args.pools}, "
          f"{sum(len(v) for v in candidates.values())} cached candidates; ceiling {ceiling_ok}/{len(rows)}")

    # A pada can appear in more than one verse, so the baseline is kept per row, not keyed by spelling.
    baseline, current_picked = [], 0
    for name, rank in RULES.items():
        changes, picked = [], 0
        for index, row in enumerate(rows):
            pool = candidates[row["pada"]]
            parts = list(rank([dict(c) for c in pool])["parts"]) if pool else []
            ok = bool(pool) and pada_matches(kosha, parts, row["parts"])
            picked += int(ok)
            if name == "current":
                baseline.append((ok, parts))
            elif ok != baseline[index][0]:
                changes.append(("fixed" if ok else "broke", row["pada"], baseline[index][1], parts))
        if name == "current":
            current_picked = picked
        suffix = "" if name == "current" else f"  ({picked - current_picked:+d} vs current)"
        print(f"rule {name}: picked {picked}/{len(rows)}{suffix}")
        for mark, pada, before, after in changes[:args.show_changes]:
            print(f"    {mark} {pada}: {before} -> {after}")


if __name__ == "__main__":
    main()
