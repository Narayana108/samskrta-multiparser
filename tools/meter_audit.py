"""Re-measure ACCURACY.md §1 — the metre table, verse by verse, with the reason for every null.

Offline: it reads only committed artefacts (`tests/data/meter_truth.json`, the pinned
`tests/data/results/*.result.json` documents and vidyut's `data-0.4.0/chandas/meters.tsv`) through the
same function the app uses for its vetoes, so nothing here re-implements the scanner or the naming rules.

For each verse it prints what the edition says, what we publish, whether the akshara grid matches, and —
for every metre name vidyut suggested but we did not publish — *why* it was dropped, decided by the two
rules in `_summarize_chandas`: a candidate whose declared akshara count contradicts the scanned pāda is
vetoed, and a name is published only when all classified pādas agree.

Usage:
    uv run python tools/meter_audit.py            # every verse
    uv run python tools/meter_audit.py --nulls    # only the verses we leave unnamed
"""

import argparse
import json

from _env import setup

setup()

from indic_transliteration import sanscript  # noqa: E402

import app  # noqa: E402


def declared_lengths(name: str, table: dict):
    """Look up a metre's declared akshara counts in vidyut's own table.

    Args:
        name: metre name as the result document prints it (IAST)
        table: mapping returned by ``app._meter_lengths``, keyed by SLP1 names

    Returns:
        Set of declared akshara counts, or None when vidyut's table has no row for that metre at all
    """
    slp1 = sanscript.transliterate(name, sanscript.IAST, sanscript.SLP1)
    return table.get(slp1, table.get(name))


_LONG_IN_SLP1 = str.maketrans({"A": "a", "I": "i", "U": "u", "F": "f", "X": "x"})


def slp1_readings(text: str) -> set:
    """Every SLP1 spelling this text could mean, reading it as IAST or as Devanagari.

    Args:
        text: metre name in either script

    Returns:
        Set of SLP1 strings — one per source script; the wrongly-guessed script is harmless because the
        comparison below only needs one reading to agree
    """
    return {sanscript.transliterate(text, source, sanscript.SLP1)
            for source in (sanscript.IAST, sanscript.DEVANAGARI)}


def same_name(published, edition) -> bool:
    """Whether two metre names denote the same vṛtta once romanisation and vowel length are ignored.

    vidyut's table row is `malinI` while the printed edition gives मालिनी / mālinī — one and the same
    fifteen-akshara metre, with a short first vowel in upstream data. Raw string equality would report a
    correct published name as wrong; comparing SLP1 skeletons with vowel length erased reports it truthfully:
    right identification, upstream spelling. A genuinely different metre still fails — `indravajrā` and
    `upendravajrā` have different skeletons — so this never papers over a real naming error.

    Args:
        published: the name we publish (vidyut's spelling), possibly None
        edition: the name the printed edition gives, in IAST or Devanagari

    Returns:
        True when both sides denote the same metre
    """
    if not published:
        return False
    folded = {reading.translate(_LONG_IN_SLP1) for reading in slp1_readings(published)}
    return bool(folded & {r.translate(_LONG_IN_SLP1) for r in slp1_readings(edition)})


def explain(chandas: dict, scanned: list, table: dict) -> str:
    """Render the fate of every metre vidyut suggested for one verse we left unnamed.

    Args:
        chandas: the `chandas` block of a result document (`vrtta`, `candidates`, `pada_count`, ...)
        scanned: `aksharas_per_pada` as we publish them
        table: declared-length table from ``app._meter_lengths``

    Returns:
        One-line explanation of why no name was published
    """
    candidates = chandas.get("candidates") or []
    if not candidates:
        return (f"vidyut classified {chandas['classified_pada_count']}/{chandas['pada_count']} pādas "
                f"and suggested no metre at all")
    notes = []
    for name in candidates:
        lengths = declared_lengths(name, table)
        if lengths is None:
            notes.append(f"{name}: no row in meters.tsv")
        elif not any(count in scanned for count in lengths):
            notes.append(f"{name}: declares {'/'.join(str(c) for c in sorted(lengths))}, pāda scans "
                         f"{'/'.join(str(s) for s in sorted(set(scanned)))} → vetoed")
        else:
            notes.append(f"{name}: length-consistent ({sorted(lengths)}) but the classified pādas "
                         f"agree on different metres → we refuse to pick")
    return "; ".join(notes)


def main() -> None:
    """Print the §1 table as text, with a verdict and — for nulls — the measured reason."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--nulls", action="store_true", help="only verses whose metre we leave unnamed")
    args = parser.parse_args()

    root = setup()
    truth = json.loads((root / "tests/data/meter_truth.json").read_text(encoding="utf-8"))
    table = app._meter_lengths(root / "data-0.4.0/chandas/meters.tsv")

    verses = sorted(stem for stem in truth if not stem.startswith("_"))
    named = shape_ok = shown = 0
    for stem in verses:
        row = truth[stem]
        doc = json.loads((root / f"tests/data/results/{stem}.result.json").read_text(encoding="utf-8"))
        chandas = doc["chandas"]
        ours = chandas.get("vrtta")
        scanned = chandas.get("aksharas_per_pada") or row["aksharas_per_pada"]
        grid_ok = scanned == row["aksharas_per_pada"]
        shape_ok += int(grid_ok)
        named += int(bool(ours))
        if args.nulls and ours:
            continue
        shown += 1
        if any(same_name(ours, row[key]) for key in ("chandas_iast", "chandas_devanagari")):
            verdict = ("✅ correct" if ours in (row["chandas_iast"], row["chandas_devanagari"])
                       else f"✅ right metre, vidyut spells it {ours} where the edition prints "
                            f"{row['chandas_iast']}")
        elif not ours:
            verdict = "⚠️ unnamed"
        else:
            verdict = f"❌ wrong ({ours})"
        print(f"{stem}\n  edition   {row['chandas_devanagari']} ({row['chandas_iast']}) "
              f"{row['aksharas_per_pada']}")
        print(f"  we say    {ours or 'null'}   shape {'✅' if grid_ok else '❌ ' + str(scanned)}   "
              f"classified {chandas['classified_pada_count']}/{chandas['pada_count']}   → {verdict}")
        if not ours:
            print(f"  why       {explain(chandas, scanned, table)}")

    print(f"\nnamed {named}/{len(verses)} · akshara grid equals the edition on {shape_ok}/{len(verses)} "
          f"· {shown} verses shown")


if __name__ == "__main__":
    main()
