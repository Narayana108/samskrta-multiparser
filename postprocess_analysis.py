#!/usr/bin/env python3
"""Post-process raw multi-engine Sanskrit analysis output into a readable form.

Reads:  <base>.raw.json    (raw engine outputs written by app.py)
Writes: <base>.result.json (the condensed reading document)

Output structure::

    {
      "mode": "shloka",
      "input": {"devanagari": "...", "iast": "..."},
      "padaccheda": {                       # flat word sequence per engine
        "dharmamitra": "vāc | arthau | iva | ...",
        "sanskrit_parser": "vāgarthās | viva | ..."
      },
      "padas": [                            # one entry per pada as written
        {
          "pada": "vāgarthāviva",
          "dharmamitra": {
            "padaccheda": ["vāc", "arthau", "iva"],
            "words": [{"form": "vāc", "lemma": "vāc", "type": "sūnantāḥ"}, ...]
          },
          "sanskrit_parser": {
            "padaccheda": ["vāgarthās", "viva"],
            "words": [{"form": "vāgarthās", "root": "vāgartha",
                       "vibhakti": "prathamā", "vacana": "bahu", ...]
          },
          "differences": [                  # only when the engines disagree
            {"sanskrit_parser": [...], "dharmamitra": [...]}
          ]
        }
      ],
      "chandas": {                          # vidyut's verse-level meter summary
        "vrtta": null,                      # named only when every pāda agrees
        "candidates": ["sragdharā"],        # vṛtta names vidyut did suggest
        "pada_count": 4,
        "classified_pada_count": 3,
        "aksharas_per_pada": [21, 21, 21, 21]
      },                                    # only when vidyut produced results
      "engine_errors": {                    # only when an engine failed
        "dharmamitra": "Dharmamitra API request timed out"
      }
    }

IAST everywhere except ``input.devanagari``. No external deps (stdlib only).
"""

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Output naming and writing (shared by both CLIs)
# ---------------------------------------------------------------------------
# One input yields a pair of documents under one base path:
#   raghuvamsha-1.1.txt → results/raghuvamsha-1.1.raw.json
#                       → results/raghuvamsha-1.1.result.json

RAW_SUFFIX = ".raw.json"
RESULT_SUFFIX = ".result.json"


def output_base(path_str: str) -> str:
    """Normalize a user-supplied -o value into an output base path.

    A trailing '.json' is dropped, as are the generated '.raw'/'.result' infixes, so
    'results/foo', 'results/foo.json' and 'results/foo.result.json' all name the same
    pair of documents.
    """
    base = path_str.removesuffix(".json")
    return base.removesuffix(".raw").removesuffix(".result")


def raw_path(base: str) -> Path:
    """Raw document belonging to an output base."""
    return Path(f"{base}{RAW_SUFFIX}")


def result_path(base: str) -> Path:
    """Result document belonging to an output base."""
    return Path(f"{base}{RESULT_SUFFIX}")


def write_document(path: Path, payload: Dict[str, Any], indent: Optional[int] = 2) -> None:
    """Write a JSON document, creating the parent directory when it is missing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=indent, ensure_ascii=False), encoding="utf-8")


def print_input_and_chandas(path: Path, indent: Optional[int] = 2) -> None:
    """Print only the top-level 'input' and 'chandas' objects of a result document.

    The file is re-read from disk so stdout shows exactly what was written. Nothing
    else reaches stdout: padas, engine data and size summaries stay in the files or on
    stderr. A document with no chanda summary prints null for it.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {"input": doc.get("input"), "chandas": doc.get("chandas")},
            indent=indent,
            ensure_ascii=False,
        )
    )

# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------
# Cross-engine comparison is anusvara-normalized: the same word may surface as
# 'saṃpṛktau' (input) and 'sampṛktau' (engine output).

def _norm_anusvara(s: str) -> str:
    """Normalize anusvara variants for cross-engine comparison."""
    return s.replace("\u1e43", "m")


# sanskrit_parser emits IAST grammatical tags such as 'prathamāvibhaktiḥ',
# 'bahuvacanam', 'puṃlliṅgam'. Map them to canonical short fields.

_VIBHAKTI_RE = re.compile(
    r"^(prathamā|dvitīyā|tṛtīyā|caturthī|pañcamī|ṣaṣṭhī|saptamī|saṃbodhana)vibhaktiḥ?$"
)
_VACANA_MAP = {
    "ekavacanam": "eka", "dvivacanam": "dvi", "bahuvacanam": "bahu",
}
_LINGAS = {"puṃlliṅgam", "strīliṅgam", "napuṃsakaliṅgam"}


def parse_sp_tag_group(group: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse one sanskrit_parser tag group into compact fields.

    Args:
        group: e.g. {"root": "vāgartha",
                     "tags": ["bahuvacanam", "prathamāvibhaktiḥ", "puṃlliṅgam"]}

    Returns:
        Dict with root plus any of vibhakti/vacana/linga and a residual 'tags'
        list for unrecognized tags; None when the group carries no usable data.
    """
    result: Dict[str, Any] = {}
    other: List[str] = []

    root = group.get("root", "")
    if root:
        result["root"] = root
    for raw in group.get("tags", []):
        tag = raw.strip()
        m = _VIBHAKTI_RE.match(tag)
        if m:
            result["vibhakti"] = m.group(1)
        elif tag in _VACANA_MAP:
            result["vacana"] = _VACANA_MAP[tag]
        elif tag in _LINGAS:
            result["linga"] = tag
        else:
            other.append(tag)

    if other:
        result["tags"] = other
    return result or None


def _morph_rank(entry: Dict[str, Any]):
    """Rank candidate analyses: full case readings beat bare compound markers."""
    return (
        "vibhakti" in entry,
        "vacana" in entry,
        -len(entry.get("tags", [])),
        tuple(sorted((k, str(v)) for k, v in entry.items())),
    )


def collect_sp_morphology(sp_output: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Map every sanskrit_parser surface form to its best parsed morphology.

    Walks the raw output and collects every tag group attached to a 'pada'
    across all sandhi splits. Split order varies between processes, so
    candidates are ranked — full case+number readings first, then a canonical
    tie-break — instead of taking whatever appears first. Candidates are keyed
    by the anusvara-normalized form up front; otherwise two spellings of one
    pada ('saṃpṛktau' / 'sampṛktau') compete as separate keys and the winner
    would be decided by insertion order.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            pada = node.get("pada")
            tags = node.get("morphological_tags")
            if isinstance(pada, str) and pada and isinstance(tags, list):
                for group in tags:
                    if isinstance(group, dict):
                        parsed = parse_sp_tag_group(group)
                        if parsed:
                            groups.setdefault(_norm_anusvara(pada), []).append(parsed)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(sp_output)
    return {pada: max(cands, key=_morph_rank) for pada, cands in groups.items()}


def collect_sp_decompositions(sp_output: Dict[str, Any]) -> Dict[str, List[str]]:
    """Map input word -> sanskrit_parser split parts (keys anusvara-normalized).

    Keys that collide after normalization are resolved deterministically
    (more parts first, then lexicographic) instead of by dict insertion order.
    """
    wd = sp_output.get("word_decompositions") or {}
    best: Dict[str, List[str]] = {}
    for word, parts in sorted(wd.items()):
        if not isinstance(parts, list):
            continue
        cand = [p for p in parts if isinstance(p, str)]
        if not cand:
            continue
        key = _norm_anusvara(word)
        current = best.get(key)
        if current is None or (-len(cand), cand) < (-len(current), current):
            best[key] = cand
    return best


def collect_dm_tokens(dm_output: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect Dharmamitra tokens (form + kosha lemma/type when enriched)."""
    tokens = dm_output.get("tokens") or []
    return [
        t for t in tokens
        if isinstance(t, dict) and isinstance(t.get("form"), str) and t["form"]
    ]


def group_dm_tokens_by_word(
    input_words: List[str],
    tokens: List[Dict[str, Any]],
) -> List[List[Dict[str, Any]]]:
    """Group flat Dharmamitra tokens under the input word they came from.

    Greedy in-order walk: keep consuming tokens while the token's first letter
    (anusvara-normalized) occurs somewhere in the current input word. Loose on
    purpose — sandhi changes token interiors ('vāc' inside 'vāgarthāviva'), so
    exact prefix matching fails; a shared first letter within the word is the
    practical signal that we are still inside this pada.
    """
    groups: List[List[Dict[str, Any]]] = []
    ti = 0
    for word in input_words:
        letters = set(_norm_anusvara(word).lower())
        group: List[Dict[str, Any]] = []
        while ti < len(tokens):
            head = _norm_anusvara(tokens[ti].get("form", ""))[:1].lower()
            if head and head in letters:
                group.append(tokens[ti])
                ti += 1
            else:
                break
        groups.append(group)
    # Trailing tokens that matched no word attach to the last pada.
    if groups and ti < len(tokens):
        groups[-1].extend(tokens[ti:])
    return groups


def _diff_regions(sp: List[str], dm: List[str]) -> List[Dict[str, Any]]:
    """Align two token sequences and report differing regions.

    Uses difflib on anusvara-normalized forms so 'saṃpṛktau' == 'sampṛktau'.
    Each region lists the SP parts and DM parts that differ; None means the
    other engine had no part at that position (split-count mismatch).
    """
    diffs: List[Dict[str, Any]] = []
    sm = difflib.SequenceMatcher(
        None, [_norm_anusvara(x) for x in sp], [_norm_anusvara(y) for y in dm]
    )
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        diffs.append({
            "sanskrit_parser": sp[i1:i2] or None,
            "dharmamitra": dm[j1:j2] or None,
        })
    return diffs


# ---------------------------------------------------------------------------
# Output assembly
# ---------------------------------------------------------------------------

def _dm_word_entry(token: Dict[str, Any]) -> Dict[str, Any]:
    """Render one Dharmamitra token as a readable word entry."""
    entry: Dict[str, Any] = {"form": token["form"]}
    if token.get("lemma"):
        entry["lemma"] = token["lemma"]
    if token.get("kosha_type"):
        entry["type"] = token["kosha_type"]
    return entry


def _sp_word_entry(form: str, sp_morph: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Render one sanskrit_parser surface form as a readable word entry."""
    entry: Dict[str, Any] = {"form": form}
    morph = sp_morph.get(_norm_anusvara(form)) or {}
    for key in ("root", "vibhakti", "vacana", "linga", "tags"):
        if morph.get(key):
            entry[key] = morph[key]
    return entry


def build_padas(
    input_words: List[str],
    sp_decomp: Dict[str, List[str]],
    sp_morph: Dict[str, Dict[str, Any]],
    dm_groups: List[List[Dict[str, Any]]],
    dm_available: bool,
) -> List[Dict[str, Any]]:
    """Build the per-pada comparison entries (engine-keyed).

    dm_available records whether Dharmamitra produced any tokens at all. When
    it did not, an empty group means "no data for this engine", so no
    differences are fabricated; when it did, a pada with no DM tokens is a real
    disagreement and gets a null-side difference region.
    """
    padas: List[Dict[str, Any]] = []

    for word, dm_group in zip(input_words, dm_groups):
        sp_parts = sp_decomp.get(_norm_anusvara(word), [word])
        dm_forms = [t["form"] for t in dm_group]

        entry: Dict[str, Any] = {"pada": word}

        if dm_group:
            entry["dharmamitra"] = {
                "padaccheda": dm_forms,
                "words": [_dm_word_entry(t) for t in dm_group],
            }
        else:
            entry["dharmamitra"] = None

        entry["sanskrit_parser"] = {
            "padaccheda": sp_parts,
            "words": [_sp_word_entry(f, sp_morph) for f in sp_parts],
        }

        if dm_available:
            diffs = _diff_regions(sp_parts, dm_forms)
            if diffs:
                entry["differences"] = diffs

        padas.append(entry)

    return padas


def build_padaccheda(padas: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """Join each engine's per-pada parts into one readable line.

    An engine with no parts at all yields None rather than an empty string, so
    "no data" and "one empty word" stay distinguishable.
    """
    sp_seq: List[str] = []
    dm_seq: List[str] = []
    for entry in padas:
        sp_seq.extend(entry["sanskrit_parser"]["padaccheda"])
        if entry["dharmamitra"]:
            dm_seq.extend(entry["dharmamitra"]["padaccheda"])
    return {
        "dharmamitra": " | ".join(dm_seq) if dm_seq else None,
        "sanskrit_parser": " | ".join(sp_seq) if sp_seq else None,
    }


def postprocess(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Condense raw engine output into the readable padaccheda/padas form.

    Args:
        raw: Raw multi-engine analysis output (multi-engine-analysis.json content)

    Returns:
        Processed dict with mode, input, padaccheda and padas; plus
        engine_errors when an engine reported a failure instead of results.

    Raises:
        ValueError: When the raw output has no usable 'input.iast' string.
    """
    inp = raw.get("input") or {}
    input_iast = inp.get("iast") if isinstance(inp, dict) else None
    if not isinstance(input_iast, str) or not input_iast.strip():
        raise ValueError("raw output has no usable 'input.iast' string")

    engines = raw.get("engine_outputs") or {}
    sp_output = engines.get("sanskrit_parser") or {}
    dm_output = engines.get("dharmamitra") or {}
    vidyut_output = engines.get("vidyut") or {}

    engine_errors: Dict[str, str] = {}
    for name, out in (
        ("sanskrit_parser", sp_output),
        ("dharmamitra", dm_output),
        ("vidyut", vidyut_output),
    ):
        if isinstance(out, dict) and out.get("error"):
            engine_errors[name] = str(out["error"])

    input_words = [w for w in input_iast.split() if w]

    sp_decomp = collect_sp_decompositions(sp_output)
    sp_morph = collect_sp_morphology(sp_output)
    dm_tokens = collect_dm_tokens(dm_output)
    dm_groups = group_dm_tokens_by_word(input_words, dm_tokens)

    padas = build_padas(input_words, sp_decomp, sp_morph, dm_groups, bool(dm_tokens))

    processed: Dict[str, Any] = {
        "mode": raw.get("mode"),
        "input": inp,
        "padaccheda": build_padaccheda(padas),
        "padas": padas,
    }

    # vidyut's verse-level meter summary passes through as it is; the raw output
    # keeps the per-pāda detail under engine_outputs.vidyut.meter.
    if isinstance(vidyut_output.get("chandas"), dict):
        processed["chandas"] = vidyut_output["chandas"]

    if engine_errors:
        processed["engine_errors"] = engine_errors
    return processed


def main() -> int:
    """CLI entry point for the processed pass.

    Resolves an output base from -o (or from the raw file given with -i), rewrites
    '<base>.raw.json' into '<base>.result.json' — creating parent directories as
    needed — and prints only that document's top-level 'input' and 'chandas' objects
    to stdout.

    Returns:
        0 on success; 1 when no base could be resolved, the raw file is missing or
        unreadable, or its JSON is not a raw analysis document.
    """
    parser = argparse.ArgumentParser(
        description="Post-process raw multi-engine Sanskrit analysis output"
    )
    parser.add_argument(
        "-i", "--input", default=None,
        help="Raw JSON document ('<base>.raw.json'); used to derive the base when -o is absent",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="Output base path; writes '<base>.result.json' (a trailing '.json' is stripped)",
    )
    args = parser.parse_args()

    if args.output:
        base = output_base(args.output)
    elif args.input:
        base = output_base(args.input)
    else:
        print(
            "Error: give an output base with -o BASE, or a raw document with -i <base>.raw.json",
            file=sys.stderr,
        )
        return 1

    input_file = raw_path(base)
    output_file = result_path(base)

    try:
        raw_text = input_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"Error: Input file '{input_file}' not found", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Error reading input: {exc}", file=sys.stderr)
        return 1

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        print(f"Error parsing JSON from '{input_file}': {exc}", file=sys.stderr)
        return 1

    try:
        processed = postprocess(raw)
    except ValueError as exc:
        print(f"Error: {exc} (is '{input_file}' a raw app.py output?)", file=sys.stderr)
        return 1

    write_document(output_file, processed)

    raw_size = len(raw_text.encode("utf-8"))
    processed_size = output_file.stat().st_size
    reduction = (1 - processed_size / raw_size) * 100 if raw_size else 0.0
    print(
        f"\nRaw: {raw_size:,} bytes → processed: {processed_size:,} bytes ({reduction:.1f}% smaller)",
        file=sys.stderr,
    )

    print_input_and_chandas(output_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
