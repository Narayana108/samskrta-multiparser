"""Score a proposed word-reading ranking rule against the pinned corpus — offline, in seconds.

The reading set for every word is already committed: each `tests/data/results/<stem>.result.json` holds the
published primary fields plus every rejected reading under `alternates`, which is exactly what
`postprocess_analysis._morph_rank` ordered. So a new rule can be evaluated by re-ordering those readings —
fifteen curated forms used by `tests/test_morphology_accuracy.py` say whether it helps or hurts. Because the
result documents are what the shipped ranking produced, re-running a rule that has already landed must move
nothing — that is this lab's regression check.

Rules implemented:

    current                 what `_morph_rank` publishes today (the baseline)
    demote-unattested-root  push any reading whose `root` is not a lemma stem the vidyut kosha records for
                            that surface form to the back, keeping relative order otherwise — SHIPPED in
                            `postprocess_analysis._morph_rank`; measured on the pre-shipped corpus it moved 8 of
                            the 273 primaries and made 6 of them what the printed editions read ('asti', 'hi',
                            'yathāvidhi', 'prakṛti', 'sanni', 'yāti'), while inventing nothing: no curated form
                            lost its reading. It cannot help where a short homograph really is recorded (BG 18.66
                            'aham': the kosha lists *aha*, "non-existence") or where the true reading was never
                            emitted at all (AS 1.1 'vastā' = instrumental plural of *vasu*)
    attested-then-longest-root
                            same demotion, but among *attested* readings the longer lemma stem wins — vidyut's
                            kosha records short homographs (`ah`, `aha` for अहम्) that are prefixes of the right
                            stem (`asmad`). REJECTED: 36 primaries move and a curated form loses its reading, so
                            the blast radius is four times the shipped rule's for one less correct form

Usage:
    uv run python tools/morphology_lab.py                       # every rule, side by side
    uv run python tools/morphology_lab.py --show-changes        # list every primary the rule moved
"""

import argparse
import json
from collections import Counter

from _env import setup

setup(with_tests=True)

from test_morphology_accuracy import BY_ID, _matches  # noqa: E402
from test_sandhi_accuracy import lemma_stems  # noqa: E402

from indic_transliteration import sanscript  # noqa: E402

import app  # noqa: E402


def readings_for(word: dict) -> list:
    """Rebuild the ordered reading list postprocessing had to choose from.

    Args:
        word: a published word object (`form`, grammar fields, `alternates`)

    Returns:
        [primary, *alternates] — the full set `_morph_rank` sorted
    """
    primary = {key: value for key, value in word.items() if key != "alternates"}
    return [primary] + list(word.get("alternates") or [])


def matched_stem(reading: dict, stems: set):
    """The kosha lemma stem a reading's root matches exactly, or None.

    Exact membership only — never prefix matching. vidyut's kosha lists short homographs for many surface
    forms (`ah` and `aha` are recorded lemmas for the form अहम्), and treating them as matches for longer
    stems would let a rule bless precisely the truncations it is meant to distrust.

    Args:
        reading: one published reading (its `root`, possibly with a `#n` homophony marker)
        stems: SLP1 lemma stems the kosha records for that word's surface form

    Returns:
        The matching SLP1 stem, or None when the root is not recorded for this form
    """
    root = (reading.get("root") or "").split("#", 1)[0]
    if not root:
        return None
    slp1 = sanscript.transliterate(root, sanscript.IAST, sanscript.SLP1)
    return slp1 if slp1 in stems else None


def ordering_key(rule: str, kosha, form: str):
    """Sort key implementing one ranking rule; the published order is always the final tie-break.

    Args:
        rule: `demote-unattested-root` (dictionary attestation only) or `attested-then-longest-root`
            (attestation first, then the longer lemma stem wins over a short homograph of it)
        kosha: vidyut Kosha from ``app.load_kosha``
        form: surface form being ranked

    Returns:
        function((index, reading)) -> tuple, ascending = best first
    """
    stems = lemma_stems(kosha, form)

    def key(item):
        index, reading = item
        stem = matched_stem(reading, stems)
        if rule == "demote-unattested-root":
            return (0 if stem else 1, index)
        return (0 if stem else 1, -len(stem or ""), index)

    return key


def apply_rule(rule: str, readings: list, kosha, form: str) -> dict:
    """Pick the primary one rule would publish.

    Args:
        rule: `current`, `demote-unattested-root` or `attested-then-longest-root`
        readings: ordered reading list for one word (primary first, as published)
        kosha: vidyut Kosha
        form: surface form of that word

    Returns:
        The chosen reading
    """
    if rule == "current":
        return readings[0]
    if rule in ("demote-unattested-root", "attested-then-longest-root"):
        return sorted(enumerate(readings), key=ordering_key(rule, kosha, form))[0][1]
    raise SystemExit(f"unknown rule {rule!r}")


def describe(reading: dict) -> str:
    """Short label for one reading, used in the change list.

    Args:
        reading: a published reading

    Returns:
        `root=<root> <vibhakti>/<vacana>` style summary
    """
    bits = [f"root={reading.get('root')}", str(reading.get("vibhakti") or reading.get("lakara") or "?")]
    if reading.get("vacana"):
        bits.append(str(reading["vacana"]))
    return " ".join(bits)


def main() -> None:
    """Print each rule's verdict on the nine curated forms and how many corpus primaries it moves."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rules", default="current,demote-unattested-root,attested-then-longest-root")
    parser.add_argument("--show-changes", action="store_true", help="list every primary the rules moved")
    args = parser.parse_args()

    root = setup(with_tests=True)
    kosha = app.load_kosha()
    if kosha is None:
        raise SystemExit("vidyut kosha data is not installed — set VIDYUT_DATA_DIR")

    stems = sorted(p.name[: -len(".result.json")] for p in (root / "tests/data/results").glob("*.result.json"))
    words = []  # (stem, reading list)
    for stem in stems:
        doc = json.loads((root / f"tests/data/results/{stem}.result.json").read_text(encoding="utf-8"))
        for pada in doc["padas"]:
            for word in (pada.get("sanskrit_parser") or {}).get("words", []):
                words.append((stem, word["form"], readings_for(word)))

    rules = [rule.strip() for rule in args.rules.split(",") if rule.strip()]
    baseline = {f"{stem}/{form}": apply_rule(rules[0], readings, kosha, form)
                for stem, form, readings in words}
    print(f"corpus: {len(words)} published word readings over {len(stems)} verses\n")

    for rule in rules:
        picked = {}
        changed = []
        for stem, form, readings in words:
            chosen = apply_rule(rule, readings, kosha, form)
            key = f"{stem}/{form}"
            picked[key] = chosen
            if rule != rules[0] and describe(chosen) != describe(baseline[key]):
                changed.append((key, baseline[key], chosen))
        score = sum(any(_matches(picked[f"{row['verse']}/{row['form']}"], exp) for exp in row["expected"])
                    for row in BY_ID.values())
        print(f"rule {rule}: curated forms with the right reading published first: {score}/{len(BY_ID)}")
        if rule != rules[0]:
            print(f"  primaries moved: {len(changed)} of {len(words)} "
                  f"({Counter(key.split('/')[0] for key, _, _ in changed)})")
            if args.show_changes:
                for key, before, after in changed:
                    print(f"    {key}\n        was  {describe(before)}\n        now  {describe(after)}")
        print()


if __name__ == "__main__":
    main()
