#!/usr/bin/env python3
"""samskrta-multiparser — Unified multi-engine Sanskrit analyzer.

Runs three independent engines (sanskrit_parser, Dharmamitra API, vidyut)
on the same input — Devanagari or any romanization vidyut lipi detects (IAST,
SLP1, Harvard-Kyoto, ITRANS) — and writes a pair of JSON documents under one base
name: the raw engine dump and its condensed reading.

Architecture:
    app.py (CLI entry point)
    ├── to_devanagari()             # Detect input script, canonicalize to Devanagari
    ├── preprocess_input()          # Replace separators with spaces, collapse whitespace
    ├── devanagari_to_iast()        # Convert Devanagari → IAST
    ├── run_sanskrit_parser()       # Local: sandhi + morphology + vakya
    ├── run_dharmamitra()           # Remote: independent unsandhiing (surface forms only)
    ├── run_vidyut()                # Local: kosha + prakriya + meter + sandhi
    └── main()                      # Engines, then both documents of the output pair

The condensed pass lives in postprocess_analysis.py (stdlib only). app.py calls its
postprocess() to write '<base>.result.json' next to '<base>.raw.json'; that module is
also a standalone CLI for re-processing an existing raw document and imports nothing
from this one.

Engine capabilities:
    - sanskrit_parser: Sandhi splitting, morphological tags, vakya (sentence) parsing
    - dharmamitra: independent sandhi splitting over the network; its response is surface forms only
    - vidyut: Kosha dictionary lookup, dhatu/pratipadika prakriya, meter classification,
              recursive sandhi splitting via DFS through kosha dictionary

Usage:
    python app.py pada                      # single-word analysis; reads the one .txt in input/
    python app.py shloka -i input/my_shloka.txt   # full-line analysis, file chosen explicitly
    python app.py shloka -i -               # read from stdin

Output:
    - '<base>.raw.json': complete engine output; base defaults to results/<input stem>
      (override with -o BASE — a trailing '.json' is stripped, directories are created)
    - '<base>.result.json': the condensed comparison document for the same base
    - stdout: only the result document's top-level 'input' and 'chandas' objects

Error policy:
    Dharmamitra is a remote service and therefore optional: when it cannot be
    reached the run continues, its key holds {"error": "..."} and stderr gets a
    warning. sanskrit_parser and vidyut are local dependencies — if either cannot
    run at all, the same error shape is recorded but the process exits 1.
"""

import argparse
import concurrent.futures
import json
import logging
import multiprocessing
import os
import re
import string
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import postprocess_analysis  # second pass: '<base>.result.json' from the raw document

# ---------------------------------------------------------------------------
# Library log levels
# ---------------------------------------------------------------------------
# sanskrit_parser configures logging for itself at import time: ``sanskrit_parser/__init__.py`` sets
# its own logger to DEBUG and attaches a stderr StreamHandler, which buries the analysis under split
# traces (3.5 MB of them for one śloka). Only that debug level is turned off — every warning and error
# the library raises still reaches stderr — and it has to be re-applied after each import of it.


def _quiet_library_logging() -> None:
    """Set the sanskrit_parser/sanskrit_util loggers back to WARNING."""
    logging.getLogger("sanskrit_parser").setLevel(logging.WARNING)
    logging.getLogger("sanskrit_util").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# API and data directory settings for all three engines.

API_URL = "https://dharmamitra.org/api/tagging/"
API_HEADERS = {
    # Public demo credential; override with DHARMAMITRA_AUTH="Basic ..." if it rotates.
    "Authorization": os.environ.get(
        "DHARMAMITRA_AUTH", "Basic b2xkc3R1ZGVudDpiZWhhcHB5"
    ),
    "Content-Type": "application/json",
}
API_MODE = "unsandhied-lemma-morphosyntax"

# Hard ceiling on one Dharmamitra response body. A verse answer is a few kilobytes; anything past
# this is an upstream fault (or a hijacked endpoint) and would otherwise be buffered, JSON-parsed,
# tokenised segment by segment, written into both documents, and re-parsed by every later offline
# postprocess run.
_MAX_RESPONSE_BYTES = 1 << 20


def _env_number(name: str, default: float) -> float:
    """Read a numeric environment override without letting a malformed value crash the import.

    These settings are read at import time, before argparse runs and long before either output
    document exists, so an unparseable value has to be reported on stderr and defaulted rather than
    raised as a traceback out of module import.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        print(
            f"Warning: ignoring invalid {name}={raw!r}; using {default:g}",
            file=sys.stderr,
        )
        return default

# Total wall-clock budget for the whole Dharmamitra exchange, both attempts included. The
# request runs while the local engines work, so an unreachable API must not stretch the run
# past this many seconds. Override with DHARMAMITRA_TIMEOUT_SECS=<seconds>.
DHARMAMITRA_DEADLINE_SECS = _env_number("DHARMAMITRA_TIMEOUT_SECS", 20.0)


# Vidyut data directory — contains kosha, prakriya, chandas, sandhi, cheda subdirectories
# Override via VIDYUT_DATA_DIR environment variable
DATA_DIR = os.environ.get(
    "VIDYUT_DATA_DIR",
    str(Path(__file__).resolve().parent / "data-0.4.0"),
)


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------
# Functions that clean input of any script and convert it to Devanagari.

# Word and verse separators people type in either script: the dandas (। ॥), the ASCII pipes
# and dots romanized input uses for them, hyphens such as the one in 'vāc-artha', commas,
# slashes. Sanskrit text needs no ASCII punctuation at all, so every one of these becomes
# whitespace. The avagraha (ऽ) is deliberately NOT among them: it writes a syllable that a
# sandhi rule elided ('वंशेऽस्मिन्' for वंशे अस्मिन्), and the vowel it stands for is not
# pronounced, so deleting it or restoring 'अ' in its place changes the akshara count of the
# pāda and the meter vidyut reports. sanskrit_parser reads such a fused token by itself:
# वंशेऽस्मिन्पूर्वसूरिभिः → vaṃśe | asmin | pūrvasūribhiḥ.
_SEPARATORS = str.maketrans({char: " " for char in string.punctuation + "।॥"})

# Verse and line numbers pasted along with the text ('… व्रज ।\n… शुचः ॥ 66॥', Devanagari
# digits such as '॥६६॥'). A Sanskrit word never contains a digit, so whatever is left after
# transliteration is numbering noise and becomes whitespace too.
_DIGITS = str.maketrans({char: " " for char in "0123456789०१२३४५६७८९"})

# Printed editions mark a word that continues across a line break with a hyphen at the end of the
# first line ('… विहाय जीर्णान्य्-' / 'अन्यानि संयाति …' in भगवद्गीता २.२२). Both halves belong to one word,
# so the break is closed up before separators become spaces — otherwise the engines are handed a
# virama-final fragment that no sandhi rule can split.
_HYPHENATED_LINE_BREAK = re.compile(r"-\s*\n\s*")


def preprocess_input(text: str) -> str:
    """Clean raw input: close hyphenated line breaks, replace separators with spaces, collapse whitespace.

    Applies to any script — Devanagari after `to_devanagari`, or a romanization
    handed to it directly. Every separator in `_SEPARATORS` becomes a space and digits are
    dropped the same way (verse numbers like '॥ 66॥'); runs of whitespace inside a line collapse
    to one, so 'vāc-artha', 'vāc artha.' and 'vāc  artha' analyze identically. The avagraha is
    left exactly where it stands — see the note on `_SEPARATORS`.
    Line structure is preserved: only the padding around each line is trimmed.

    Args:
        text: Raw input text

    Returns:
        Cleaned text, ready for the engines
    """
    # A hyphen at the end of a line is the printed-edition marker that the word continues on the next
    # line (भगवद्गीता २.२२ prints "…जीर्णान्य्-" / "अन्यानि…"): join the two halves into one word instead
    # of leaving a virama-final fragment for the engines to choke on.
    joined = _HYPHENATED_LINE_BREAK.sub("", text)
    cleaned = joined.translate(_SEPARATORS).translate(_DIGITS)
    return "\n".join(" ".join(line.split()) for line in cleaned.split("\n")).strip()


def detect_script(text: str) -> str:
    """Return the name of the script vidyut lipi detects for `text`.

    Args:
        text: Raw input text in any script or romanization lipi supports

    Returns:
        Scheme name, e.g. 'Devanagari', 'Iast', 'Slp1', 'HarvardKyoto'
    """
    from vidyut.lipi import detect
    return detect(text).name


def to_devanagari(text: str) -> Tuple[str, str]:
    """Canonicalize input of any lipi-supported script to Devanagari.

    Devanagari input is returned unchanged so `input.devanagari` keeps exactly the
    bytes the user supplied. Romanizations (IAST, SLP1, Harvard-Kyoto, ITRANS, ...)
    are converted with lipi's own detection; its IAST scheme also maps '.' and '..'
    back to dandas, so verse punctuation survives canonicalization.

    Args:
        text: Raw input text

    Returns:
        (detected script name, Devanagari text)
    """
    from vidyut.lipi import detect, transliterate, Scheme
    scheme = detect(text)
    if scheme.name == "Devanagari":
        return scheme.name, text
    return scheme.name, transliterate(text, scheme, Scheme.Devanagari)


def devanagari_to_iast(devanagari_text: str) -> str:
    """Convert cleaned Devanagari text to IAST using vidyut lipi.
    
    IAST (International Alphabet of Sanskrit Transliteration) is the project-wide
    output script: Unicode with diacritics, not ASCII.
    
    Args:
        devanagari_text: Cleaned Devanagari text
    
    Returns:
        IAST-encoded string
    """
    from vidyut.lipi import transliterate, Scheme
    return transliterate(devanagari_text, Scheme.Devanagari, Scheme.Iast)


def _convert_devanagari_to_iast(obj: Any) -> Any:
    """Recursively convert all Devanagari strings in a dict/list to IAST.
    
    Args:
        obj: Any JSON-serializable object (dict, list, str, etc.)
    
    Returns:
        The same structure with Devanagari strings converted to IAST
    """
    if isinstance(obj, dict):
        return {_convert_devanagari_to_iast(k): _convert_devanagari_to_iast(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_devanagari_to_iast(item) for item in obj]
    elif isinstance(obj, str):
        if any('\u0900' <= c <= '\u097F' for c in obj):
            try:
                from vidyut.lipi import transliterate, Scheme
                return transliterate(obj, Scheme.Devanagari, Scheme.Iast)
            except Exception as exc:
                # The Devanagari is returned unchanged so the run still produces a document, but the
                # caller and every later reader must know those strings are not IAST.
                _warn_once("vidyut lipi devanagari to iast", exc)
                return obj
        return obj
    else:
        return obj


def read_input(filename: str) -> str:
    """Read and strip trailing whitespace from input file.
    
    Args:
        filename: Path to input file, or '-' to read from stdin
    
    Returns:
        Stripped input text
    
    Raises:
        FileNotFoundError: If file doesn't exist
    """
    if filename == "-":
        return sys.stdin.read().strip()
    with open(filename, "r", encoding="utf-8") as f:
        return f.read().strip()


INPUT_DIR = "input"


def default_input() -> tuple:
    """Choose the input file when `-i` is absent.

    The only place an implicit input may live is `input/`, which ships empty and is gitignored, so a
    verse typed there can never be committed by accident and its stem names both output documents.
    The old root-level fallbacks (`input.txt`, `shloka_input.txt`, `pada_input.txt`) are gone: they sat
    outside that directory, their names matched nothing else in the project, and a mode-specific name
    decided by `sys.argv` rather than by what the user actually put on disk.

    Returns:
        (path, None) when exactly one `.txt` file sits in `input/`, else (None, error message).
    """
    candidates = sorted(Path(INPUT_DIR).glob("*.txt"))
    if len(candidates) == 1:
        return str(candidates[0]), None
    if candidates:
        names = ", ".join(path.name for path in candidates)
        return None, f"{INPUT_DIR}/ holds {len(candidates)} files ({names}); pass -i FILE to choose one"
    return None, f"no input file: put a .txt in {INPUT_DIR}/ or pass -i FILE"


_warned_sites: set = set()


def _warn_once(site: str, exc: Exception) -> None:
    """Report an engine-side failure once per site.

    Warnings go to stderr so the JSON on stdout stays machine-readable while a
    swallowed library error still becomes visible.
    """
    if site not in _warned_sites:
        _warned_sites.add(site)
        print(f"Warning: {site}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# IAST label maps — PyO3 enum values → IAST
# ---------------------------------------------------------------------------
# These maps convert internal enum string representations to canonical
# IAST forms for consistent output across all engines.

VIBHAKTI_IAST = {
    "praTamA": "prathamā",
    "dvitIyA": "dvitīyā",
    "tftIyA": "tṛtīyā",
    "caturTI": "caturthī",
    "paYcamI": "pañcamī",
    "zazWI": "ṣaṣṭhī",
    "saptamI": "saptamī",
    "samboDanam": "saṃbodhanam",
}

LAKARA_IAST = {
    "la~w": "laṭ",
    "li~w": "liṭ",
    "lu~w": "luṭ",
    "lf~w": "lṛṭ",
    "le~w": "leṭ",
    "lo~w": "loṭ",
    "la~N": "laṅ",
    "viDili~N": "vidhiliṅ",
    "ASIrli~N": "āśīrliṅ",
    "lu~N": "luṅ",
    "lf~N": "lṛṅ",
}

PURUSHA_IAST = {
    "praTama": "prathama-puruṣaḥ",
    "maDyama": "madhyama-puruṣaḥ",
    "uttama": "uttama-puruṣaḥ",
}

VACANA_IAST = {
    "eka": "ekavacanam",
    "dvi": "dvivacanam",
    "bahu": "bahuvacanam",
}

LINGA_IAST = {
    "puM": "puṃlliṅgam",
    "strI": "strīliṅgam",
    "napuMsaka": "napuṃsakaliṅgam",
}

GANA_IAST = {
    "BvAdi": "bhvādiḥ",
    "adAdi": "ādādiḥ",
    "juhotyAdi": "juhotyādiḥ",
    "divAdi": "divādiḥ",
    "svAdi": "svādiḥ",
    "tudAdi": "tudādiḥ",
    "ruDAdi": "rudhādiḥ",
    "tanAdi": "tanādiḥ",
    "kryAdi": "krīyādiḥ",
    "curAdi": "curādiḥ",
    "kaRqvAdi": "kaṇḍvādiḥ",
}

PRAYOGA_IAST = {
    "kartari": "kartari",
    "karmaRi": "karmaṇi",
    "BAve": "bhāve",
}


# ---------------------------------------------------------------------------
# sanskrit_parser engine adapter
# ---------------------------------------------------------------------------
# Wraps the sanskrit_parser Python package to provide:
# - Sandhi splitting (splitting compounded words into constituent pada)
# - Morphological tag extraction (root, vibhakti, vacana, linga, etc.)
# - Vakya (sentence) parsing with dependency graphs

def _slp1_to_iast(slp1: str) -> str:
    """Transliterate an SLP1 (or already-IAST) Sanskrit string to IAST.

    Args:
        slp1: SLP1, IAST or Devanagari encoded string; Devanagari codepoints
            pass through untouched and are converted later by main's
            `_convert_devanagari_to_iast` wrapper

    Returns:
        IAST string
    """
    from indic_transliteration import sanscript
    try:
        return sanscript.transliterate(slp1, sanscript.SLP1, sanscript.IAST)
    except Exception as exc:
        # Raw SLP1 (`z`, `Ri`) in an IAST field is worse than useless downstream unless it is flagged.
        _warn_once("indic_transliteration slp1 to iast", exc)
        return slp1


def _morphological_tags_to_json(
    tags,
) -> List[Dict[str, Any]]:
    """Convert morphological tags to a JSON-serializable list of dicts.

    Args:
        tags: List of (root, tag_set) tuples from sanskrit_parser

    Returns:
        List of dicts with 'root' and sorted 'tags', transliterated toward
        IAST here and finalized by main's Devanagari-to-IAST wrapper.
    """
    if tags is None:
        return []
    result = []
    for root, tag_set in tags:
        result.append(
            {
                "root": _slp1_to_iast(str(root)),
                "tags": sorted(_slp1_to_iast(str(t)) for t in tag_set),
            }
        )
    return result


# ---------------------------------------------------------------------------
# Candidate validation against vidyut's kosha
# ---------------------------------------------------------------------------
# Ranking sandhi candidates needs a dictionary check that is stricter than stem
# stripping: an exactly attested form rules out the plausible-looking fragments
# sanskrit_parser loves to produce. The kosha is loaded once per process and is
# optional — without it `_best_word_split` degrades to morphology-only ranking.

_KOSHA_CACHE: Optional[Any] = None
_KOSHA_UNAVAILABLE = False
_KOSHA_LOCK = threading.Lock()


def load_kosha() -> Optional[Any]:
    """Load vidyut's kosha once per process, caching the result.

    The lock matters because the Dharmamitra request now runs on its own thread: two
    callers must never build the same ~200 MB index twice.

    Returns:
        Kosha instance, or None when vidyut or its data directory is unavailable.
    """
    global _KOSHA_CACHE, _KOSHA_UNAVAILABLE
    if _KOSHA_CACHE is not None or _KOSHA_UNAVAILABLE:
        return _KOSHA_CACHE
    with _KOSHA_LOCK:
        if _KOSHA_CACHE is not None or _KOSHA_UNAVAILABLE:
            return _KOSHA_CACHE
        try:
            from vidyut.kosha import Kosha

            _KOSHA_CACHE = Kosha(Path(DATA_DIR) / "kosha")
        except Exception:
            _KOSHA_UNAVAILABLE = True
    return _KOSHA_CACHE


def _kosha_exact(kosha, dev_word: str) -> Tuple[bool, int]:
    """Look a Devanagari form up in the kosha with and without stem stripping.

    Args:
        kosha: Kosha instance from `load_kosha`
        dev_word: Devanagari word form

    Returns:
        (exact_hit, entry_count) where exact_hit is True when the surface form
        itself is a kosha key and entry_count counts entries found with the
        stem-stripping fallback of `kosha_lookup`.
    """
    from indic_transliteration import sanscript

    slp1 = sanscript.transliterate(dev_word, sanscript.DEVANAGARI, sanscript.SLP1)
    try:
        exact = bool(kosha.get(slp1))
    except KeyError:
        exact = False
    return exact, len(kosha_lookup(kosha, slp1))


# Bounds of the transparent-compound gate in `_best_word_split`: a split only beats a shorter
# candidate when it has at most this many parts and every part is at least this long. Measured on
# tests/data/sandhi_truth.json — 2-3 parts with parts >= 5 letters scores 104/147; allowing 1-part
# fragments or 4-letter parts loses padas to junk such as `mat | is` (84/147).
_MAX_DEEP_PARTS = 3
_MIN_DEEP_PART_LEN = 5

def _is_standalone_word(parser, word_obj) -> bool:
    """Check if a word has standalone morphology (case+number or avyaya)."""
    tags = parser.sandhi_analyzer.getMorphologicalTags(word_obj, tmap=True)
    if not tags:
        return False
    for root, tag_set in tags:
        tag_strs = [str(t) for t in tag_set]
        has_case = any('viBaktiH' in t for t in tag_strs)
        has_number = any('vacanam' in t for t in tag_strs)
        is_avyaya = any(t == 'avyayam' for t in tag_strs)
        if (has_case and has_number) or is_avyaya:
            return True
    return False


def _rank_morphology_only(candidates):
    """Rank candidate splits by morphology alone, without any dictionary check.

    Args:
        candidates: one dict per candidate with `parts`, `count`, `min_part_len` and `standalone`

    Returns:
        The winning candidate dict
    """
    return max(
        candidates,
        key=lambda c: (c["standalone"], c["min_part_len"], -c["count"], sorted(c["parts"])),
    )


def _rank_with_kosha(candidates):
    """Rank candidate splits using kosha attestation; keys documented in `_best_word_split`.

    Args:
        candidates: one dict per candidate with `parts`, `count`, `exact_all`, `entry_min`,
            `min_part_len` and `standalone`

    Returns:
        The winning candidate dict
    """
    whole = next((c for c in candidates if c["count"] == 1), None)
    whole_attested = bool(whole and whole["exact_all"])
    whole_entries = whole["entry_min"] if whole else -1
    for cand in candidates:
        cand["deep"] = (
            cand["exact_all"]
            and 1 < cand["count"] <= _MAX_DEEP_PARTS
            and cand["min_part_len"] >= _MIN_DEEP_PART_LEN
            and (not whole_attested or cand["entry_min"] >= whole_entries)
        )
    return max(
        candidates,
        key=lambda c: (
            c["exact_all"],
            c["deep"],
            -c["count"],
            c["standalone"],
            c["entry_min"],
            c["min_part_len"],
            sorted(c["parts"]),
        ),
    )


def _best_word_split(parser, dev_word: str, kosha: Optional[Any] = None) -> List[str]:
    """Pick a deterministic, validated split of one Devanagari word.

    parser.split() candidate order varies between processes, so all candidates are ranked instead
    of trusting splits[0]. With a kosha the ranking keys are, in order:

    1. every part is an exactly attested kosha form — this rejects fragments such as
       ``gam | iṣi | āmī`` or ``ava | tu``, so finite verbs stay whole (``mokṣayiṣyāmi``);
    2. the transparent-compound gate: a two- or three-part split whose every part is at least five
       letters long outranks shorter candidates, but only when its scarcest part is at least as well
       attested as the whole word. That cuts ``sūryaprabhavas``, ``vajrasamutkīrṇe`` and
       ``saṃbhṛtārthānām`` into their members while leaving a verb such as ``mokṣayiṣyāmi`` alone;
    3. fewer parts — an atom the dictionary knows as one word is left alone;
    4. every part has standalone morphology (case+number, or avyaya);
    5. the rarest part is as common as possible (most kosha entries for its scarcest part);
    6. longest shortest part, then the sorted part list, keeping output byte-stable.

    Measured on the 147 curated padas of ``tests/data/sandhi_truth.json`` (see
    ``tests/test_sandhi_accuracy.py``): this rule reproduces the reference reading for 104 of them;
    morphology-only ranking — the fallback used when no kosha is available — reaches 92, and the
    candidate pool contains a reference-consistent split for 127. Over all the distinct words of the
    sixteen pinned verses the gate changes nine splits, every one of them a compound read as
    its members; nothing else moves.

    Args:
        parser: sanskrit_parser Parser instance
        dev_word: Devanagari word to decompose
        kosha: optional Kosha from `load_kosha` for dictionary validation

    Returns:
        List of IAST parts; [dev_word] when no split is found.
    """
    _, items = _best_word_split_with_items(parser, dev_word, kosha)
    return [devanagari_to_iast(w.devanagari()) for w in items] or [devanagari_to_iast(dev_word)]


def _best_word_split_with_items(parser, dev_word: str, kosha: Optional[Any] = None):
    """Split one Devanagari word and return the winning parts with their analyzer objects.

    Same search and ranking as `_best_word_split`; also hands back the sanskrit_parser word
    objects of the chosen split so callers can ask for their morphology. The whole-line splits in
    `sandhi_splits` only cover whatever candidates the library sampled, so without this the reading
    document loses case/number/root data for words it did choose (observed as `"form": "pāpebhyas"`
    with no other fields).

    Args:
        parser: sanskrit_parser Parser instance
        dev_word: Devanagari word to decompose
        kosha: optional Kosha from `load_kosha` for dictionary validation

    Returns:
        (IAST parts, word objects) — both empty when no split is found.
    """
    splits = parser.split(dev_word, limit=10) or []
    candidates = []
    seen = set()
    for s in splits:
        parts = tuple(devanagari_to_iast(w.devanagari()) for w in s.split)
        if parts in seen or any(len(p) <= 1 for p in parts):
            continue
        seen.add(parts)
        cand = {
            "parts": parts,
            "items": list(s.split),
            "count": len(parts),
            "min_part_len": min(len(p) for p in parts),
            "standalone": all(_is_standalone_word(parser, w_obj) for w_obj in s.split),
        }
        if kosha is not None:
            exact_flags, entry_counts = [], []
            for w_obj in s.split:
                exact_hit, entry_count = _kosha_exact(kosha, w_obj.devanagari())
                exact_flags.append(exact_hit)
                entry_counts.append(entry_count)
            cand["exact_all"] = all(exact_flags)
            cand["entry_min"] = min(entry_counts)
        candidates.append(cand)

    if not candidates:
        return [], []
    if kosha is None:
        best = _rank_morphology_only(candidates)
    else:
        best = _rank_with_kosha(candidates)
    return list(best["parts"]), best["items"]


def _analyze_line(parser, kosha, line_index: int, line: str, limit: int) -> Dict[str, Any]:
    """Analyze one pada-line: whole-line sandhi candidates plus per-word decompositions.

    Everything here is local to the line, which is what makes the work parallelizable —
    see `_sp_line_task`.

    Args:
        parser: sanskrit_parser Parser with Devanagari output encoding
        kosha: vidyut kosha for dictionary-verified ranking, or None
        line_index: Position of this line in the input, recorded on every candidate
        line: One cleaned pada-line of Devanagari text
        limit: Candidate-pool size handed to the splitter

    Returns:
        Dict with line_index, sandhi_splits, word_decompositions and word_morphology
    """
    # sanskrit_parser returns None — not an empty list — when it cannot split the text at
    # all, which happens whenever a token is not a word it knows (a typo, or a mangled
    # avagraha). Record that as no candidates instead of crashing the engine.
    sandhi_splits: List[Dict[str, Any]] = []
    for split_idx, split in enumerate(parser.split(line, limit=limit) or []):
        items = split.split
        items_json = []
        for item in items:
            tags = parser.sandhi_analyzer.getMorphologicalTags(item, tmap=True)
            items_json.append(
                {
                    "pada": devanagari_to_iast(item.devanagari()),
                    "morphological_tags": _morphological_tags_to_json(tags),
                }
            )
        sandhi_splits.append(
            {
                "line_index": line_index,
                "split_index": split_idx,
                "split": [devanagari_to_iast(item.devanagari()) for item in items],
                "items": items_json,
            }
        )

    # Per-word decompositions: split each input word on its own so results do not depend on
    # candidate ordering within a line (which varies per process). The chosen parts carry their
    # morphological tags as well — `sandhi_splits` only covers whatever candidates the library
    # sampled for one line, so a word this pass picked can be absent there and would lose its
    # root/case/number in the reading document.
    word_decompositions: Dict[str, List[str]] = {}
    word_morphology: List[Dict[str, Any]] = []
    for wdev in line.split():
        if not any('\u0900' <= c <= '\u097F' for c in wdev):
            continue
        parts, items = _best_word_split_with_items(parser, wdev, kosha)
        if not parts:
            parts = [devanagari_to_iast(wdev)]
        key = devanagari_to_iast(wdev).replace("\u1e43", "m")
        word_decompositions[key] = parts
        for w_obj in items:
            tags = parser.sandhi_analyzer.getMorphologicalTags(w_obj, tmap=True)
            word_morphology.append(
                {
                    "pada": devanagari_to_iast(w_obj.devanagari()),
                    "morphological_tags": _morphological_tags_to_json(tags),
                }
            )

    return {
        "line_index": line_index,
        "sandhi_splits": sandhi_splits,
        "word_decompositions": word_decompositions,
        "word_morphology": word_morphology,
    }


# ---------------------------------------------------------------------------
# Long inputs: the per-line work in a process pool
# ---------------------------------------------------------------------------
# Both halves of the engine scale with the text — measured ~0.24 s of graph search per pada-line
# and ~70 ms of kosha ranking per word, against ~3 s for one whole śloka — so the pool only kicks
# in for longer inputs. It is a *process* pool because vidyut's Rust bindings never release the
# GIL and sanskrit_parser is pure Python: threads cannot run any of this faster. Each worker
# holds its own Parser (~130 MB) and kosha index (~220 MB), so workers stay few; SAMSKRTA_WORKERS
# overrides the count (1 forces the single-process path).

SP_PARALLEL_MIN_LINES = int(_env_number("SAMSKRTA_MIN_PARALLEL_LINES", 8))
_SP_PARSER = None


def _make_sp_parser():
    """Import sanskrit_parser and return a Devanagari-output Parser.

    The package sets its own loggers to DEBUG with a stderr StreamHandler at import time, so the level
    override has to run *after* it — without that call every run floods stderr with megabytes of split
    traces (measured 3.5 MB for one śloka).
    """
    from indic_transliteration import sanscript
    from sanskrit_parser.api import Parser

    _quiet_library_logging()
    return Parser(output_encoding=sanscript.DEVANAGARI)


def _sp_worker_init() -> None:
    """Build this worker process's sanskrit_parser once, before its first line."""
    global _SP_PARSER
    _SP_PARSER = _make_sp_parser()


def _sp_line_task(task: Tuple[int, str, int]) -> Dict[str, Any]:
    """Pool entry point: analyze one line in a worker; the result is plain JSON data."""
    line_index, line, limit = task
    if _SP_PARSER is None:
        _sp_worker_init()
    return _analyze_line(_SP_PARSER, load_kosha(), line_index, line, limit)


def _sp_worker_count(line_count: int) -> int:
    """How many processes the per-line work is worth for an input this size."""
    if line_count < SP_PARALLEL_MIN_LINES:
        return 1
    # Two by default: a worker costs ~350 MB of resident memory, and that is already enough to
    # halve the per-line time on long inputs. SAMSKRTA_WORKERS raises it on bigger machines.
    requested = int(_env_number("SAMSKRTA_WORKERS", 0)) or min(2, os.cpu_count() or 1)
    return max(1, min(requested, line_count))


def run_sanskrit_parser(input_text: str, mode: str) -> Dict[str, Any]:
    """Run the sanskrit_parser engine on Devanagari text.

    Line-level sandhi candidates are produced one pada-line at a time (the graph search is
    superlinear in the number of words it spans), and every input word is additionally split
    on its own with kosha-verified ranking. Long inputs run that per-line work in a small
    process pool; short ones stay single-process.

    Args:
        input_text: Cleaned Devanagari text
        mode: 'pada' for single-word or 'shloka' for full-line analysis

    Returns:
        Dict with mode, input, sandhi_splits, word_decompositions and word_morphology
    """
    limit = 10 if mode == "pada" else 5
    # Split one pada-line at a time: the sandhi graph search is superlinear in the number of
    # words it spans, so handing sanskrit_parser a whole verse (or file) as one string explodes
    # the candidate pool. Per line keeps the work bounded, and independent lines are exactly what
    # the process pool can run side by side.
    lines = [ln for ln in input_text.split("\n") if ln.strip()] or [input_text]

    workers = _sp_worker_count(len(lines))
    if workers > 1:
        # 'spawn' rather than the default fork: this engine runs while the Dharmamitra request is
        # in flight on another thread, and forking under a live thread can inherit its locks.
        context = multiprocessing.get_context("spawn")
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=workers, initializer=_sp_worker_init, mp_context=context
        ) as pool:
            line_results = list(
                pool.map(_sp_line_task, [(i, ln, limit) for i, ln in enumerate(lines)])
            )
    else:
        parser = _make_sp_parser()
        kosha = load_kosha()
        line_results = [
            _analyze_line(parser, kosha, i, ln, limit) for i, ln in enumerate(lines)
        ]

    # `pool.map` returns results in input order, so the merged document is identical to what the
    # single-process path produces: every candidate keeps the line it came from.
    sandhi_splits: List[Dict[str, Any]] = []
    word_decompositions: Dict[str, List[str]] = {}
    word_morphology: List[Dict[str, Any]] = []
    for res in line_results:
        sandhi_splits.extend(res["sandhi_splits"])
        word_decompositions.update(res["word_decompositions"])
        word_morphology.extend(res["word_morphology"])

    return {
        "mode": mode,
        "input": input_text,
        "sandhi_splits": sandhi_splits,
        "word_decompositions": word_decompositions,
        "word_morphology": word_morphology,
    }


# ---------------------------------------------------------------------------
# Dharmamitra API engine adapter
# ---------------------------------------------------------------------------
# Sends IAST text to the Dharmamitra API and returns structured JSON
# with sandhi-split tokens and morphological tags.

def _parse_tokens(raw_output: str) -> List[Dict[str, Any]]:
    """Parse underscore-separated API output into structured tokens.

    Untaggable positions come back as empty fields (`____iva_`); they are
    dropped here because there is nothing to record about them.

    Args:
        raw_output: Raw API response string with underscore-separated tokens

    Returns:
        List of token dicts with a 'form' field
    """
    return [{"form": seg} for seg in raw_output.split("_") if seg]


def _read_capped_body(response, deadline: float) -> tuple:
    """Read a streamed response body under a size cap and the shared deadline.

    Args:
        response: A `requests` response obtained with `stream=True`.
        deadline: Absolute `time.monotonic()` bound for the whole exchange.

    Returns:
        (body bytes, None) when the body fits and arrived in time, else (None, error message).
    """
    declared = response.headers.get("Content-Length")
    if declared is not None and declared.isdigit() and int(declared) > _MAX_RESPONSE_BYTES:
        return None, (
            f"Dharmamitra API response too large: {declared} bytes announced, "
            f"limit {_MAX_RESPONSE_BYTES}"
        )

    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        total += len(chunk)
        if total > _MAX_RESPONSE_BYTES:
            return None, (
                f"Dharmamitra API response too large: more than {_MAX_RESPONSE_BYTES} bytes streamed"
            )
        chunks.append(chunk)
        if time.monotonic() > deadline:
            return None, "Dharmamitra API request timed out while reading the response body"
    return b"".join(chunks), None


def _dharmamitra_raw_output(text: str, deadline: float) -> tuple:
    """POST one Dharmamitra tagging request and return (raw_output, error).

    Args:
        text: Single text to tag; the API takes a list but every caller here sends one item.
        deadline: Absolute time.monotonic() budget shared by both attempts.

    Returns:
        (underscore-separated raw output, None) on success, else (None, error message).
    """
    import requests

    data = {
        "texts": [text],
        "mode": API_MODE,
        "input_encoding": "auto",
        "human_readable_tags": True,
        "output_format": "dict",
    }

    # Both attempts share one wall-clock budget instead of a fixed timeout each plus a blind sleep
    # between them: the API is occasionally briefly unreachable, but this call runs alongside the
    # local engines, so its waiting has to stay bounded. `timeout` only bounds individual socket
    # operations, so the body read re-checks the deadline chunk by chunk — a server that trickles
    # bytes stays inside every per-op timeout while the exchange itself never ends. Each attempt is
    # fully self-contained: nothing from a failed attempt (not even its response object) survives
    # into the next one, so an HTTP error body can never be parsed as if it were an answer.
    last_failure = "Dharmamitra API request never completed"
    for _ in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            response = requests.post(
                API_URL, headers=API_HEADERS, json=data, timeout=remaining, stream=True
            )
            response.raise_for_status()
            # The body read belongs inside the same handlers: with `stream=True` the socket faults of a
            # stalled or truncated upstream (ReadTimeout, ChunkedEncodingError) surface from
            # iter_content(), long after raise_for_status() passed. Left outside, they escape this
            # function and abort `main()` before either document is written — the per-pada follow-up loop
            # calls it with no try of its own.
            body, failure = _read_capped_body(response, deadline)
        except requests.exceptions.Timeout:
            last_failure = "Dharmamitra API request timed out"
            continue
        except requests.exceptions.RequestException as exc:
            last_failure = f"Dharmamitra API unavailable: {exc}"
            continue

        if body is None:
            last_failure = failure
            continue

        try:
            text_body = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            return None, f"Dharmamitra API returned a non-UTF-8 body: {exc}"

        try:
            payload = json.loads(text_body)
        except ValueError as exc:
            return None, f"Dharmamitra API returned non-JSON body: {exc}"

        results = payload.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], str):
            return None, f"Dharmamitra API response shape unexpected: {str(payload)[:200]}"
        return results[0], None

    return None, last_failure


def run_dharmamitra(iast_text: str, iast_lines: List[str]) -> Dict[str, Any]:
    """Send IAST text to Dharmamitra API and return structured JSON.

    Args:
        iast_text: IAST-encoded input text
        iast_lines: List of IAST-encoded input lines

    Returns:
        Dict with api_endpoint, mode, input_lines, raw_output, and tokens
    """
    # The API silently drops everything after a line whose end has trailing
    # whitespace before the newline, so collapse spaces around newlines.
    clean_text = "\n".join(
        line.strip() for line in iast_text.split("\n") if line.strip()
    )

    raw_output, error = _dharmamitra_raw_output(
        clean_text, time.monotonic() + DHARMAMITRA_DEADLINE_SECS
    )
    if error:
        return {"error": error}

    return {
        "api_endpoint": API_URL,
        "mode": API_MODE,
        "input_lines": iast_lines,
        "raw_output": raw_output,
        "tokens": _parse_tokens(raw_output),
    }


# Dharmamitra's verse-level pass leaves some padas with no token at all: its tagger fails on a few
# long compounds in context while answering the very same word correctly on its own. Those gaps are
# filled by one extra word-level request each, recorded in the raw document so the offline second
# pass replays them without the network.
_MAX_PADA_FOLLOWUPS = 8


def fill_missing_pada_readings(
    dharmamitra_results: Dict[str, Any],
    processed: Dict[str, Any],
) -> int:
    """Ask Dharmamitra for every pada its verse-level answer left untagged (in place).

    Args:
        dharmamitra_results: Dharmamitra output from run_dharmamitra; 'pada_followups' is appended.
        processed: Result document built from the same raw output, whose padas show which
            Dharmamitra sides came back empty.

    Returns:
        Number of padas that gained tokens from a word-level request.
    """
    if dharmamitra_results.get("error") or not dharmamitra_results.get("tokens"):
        return 0

    missing = [e["pada"] for e in processed.get("padas", []) if not e.get("dharmamitra")]
    if not missing:
        return 0
    if len(missing) > _MAX_PADA_FOLLOWUPS:
        print(
            f"Note: {len(missing)} padas had no Dharmamitra tokens; requesting only the first "
            f"{_MAX_PADA_FOLLOWUPS}.",
            file=sys.stderr,
        )
        missing = missing[:_MAX_PADA_FOLLOWUPS]

    deadline = time.monotonic() + DHARMAMITRA_DEADLINE_SECS
    followups: List[Dict[str, Any]] = []
    filled = 0
    for pada in missing:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(
                "Note: Dharmamitra word requests ran out of time; the rest stay untagged.",
                file=sys.stderr,
            )
            break
        raw_output, error = _dharmamitra_raw_output(pada, deadline)
        if error:
            print(
                f"Warning: Dharmamitra word request for '{pada}' failed: {error}",
                file=sys.stderr,
            )
            continue
        tokens = _parse_tokens(raw_output)
        followups.append({"pada": pada, "raw_output": raw_output, "tokens": tokens})
        if tokens:
            filled += 1
        else:
            print(
                f"Note: Dharmamitra returned no tokens for '{pada}' on its own either.",
                file=sys.stderr,
            )

    if followups:
        dharmamitra_results.setdefault("pada_followups", []).extend(followups)
    return filled


# ---------------------------------------------------------------------------
# vidyut engine adapter
# ---------------------------------------------------------------------------
# Runs vidyut for:
# - Kosha dictionary lookup (grammatical entries)
# - Sandhi splitting (recursive compound decomposition via DFS)
# - Prakriya (derivation steps for dhatus and pratipadikas)
# - Meter classification (chandas)

# vidyut marks Vedic accents inside dhatu upadeśas ('~' anudātta, '\' svarita);
# they must be stripped before transliterating a root for display.
_ACCENT_MARKERS = str.maketrans("", "", "~\\'")


def _slp1_to_iast_vidyut(slp1_text: str) -> str:
    """Convert SLP1 text to IAST via vidyut lipi.

    Vedic accent markers are dropped first: lipi would otherwise turn them into
    stray combining signs in the output.

    Args:
        slp1_text: SLP1-encoded text

    Returns:
        IAST string
    """
    from vidyut.lipi import transliterate, Scheme
    if not slp1_text:
        return ""
    return transliterate(slp1_text.translate(_ACCENT_MARKERS), Scheme.Slp1, Scheme.Iast)


def _kosha_entry_info(entry: Any) -> Optional[Dict[str, str]]:
    """Classify one kosha entry as {'type', 'lemma', 'stem'} (IAST type/lemma).

    Returns None when the entry carries no lemma. 'stem' keeps the raw SLP1
    lemma so callers can compare it against surface spellings.
    """
    entry_repr = repr(entry)
    if "Tinanta" in entry_repr:
        word_type = "tīnantāḥ"
    elif getattr(entry, "is_avyaya", False):
        word_type = "avyayam"
    else:
        word_type = "sūnantāḥ"
    lemma = getattr(entry, "lemma", "") or getattr(
        getattr(entry, "pratipadika_entry", None), "lemma", ""
    )
    if not lemma:
        return None
    return {"type": word_type, "lemma": _slp1_to_iast_vidyut(lemma), "stem": lemma}


def _kosha_info_from_entries(entries: List[Any]) -> Optional[Dict[str, str]]:
    """First usable classification among a word's kosha entries."""
    for entry in entries:
        info = _kosha_entry_info(entry)
        if info:
            return info
    return None


def enrich_dharmamitra_lemmas(dharmamitra_results: Dict[str, Any]) -> None:
    """Add vidyut kosha lemma info to each Dharmamitra token (in place).

    The Dharmamitra API returns untagged surface tokens only; the local
    vidyut kosha supplies lemma and word type. Conversion goes IAST ->
    Devanagari -> SLP1 via sanscript because that chain matches the kosha's
    key spelling (vidyut's own Iast->Slp1 yields 'arTau' where the kosha
    stores 'arTO').

    Args:
        dharmamitra_results: Dharmamitra engine output dict with 'tokens' list
    """
    from indic_transliteration import sanscript

    kosha = load_kosha()
    if kosha is None:
        # No dictionary to enrich with; the Dharmamitra forms stand as they are.
        return

    def finalize(infos):
        """Collapse matched entries into one lemma field (list when ambiguous)."""
        lemmas = sorted({i["lemma"] for i in infos})
        types = sorted({i["type"] for i in infos})
        return {
            "type": types[0] if len(types) == 1 else types,
            "lemma": lemmas[0] if len(lemmas) == 1 else lemmas,
        }

    def lookup(slp1: str, iast_form: str):
        # Pause (sandhi-final) spelling: a stem written ...c/j/ś surfaces as
        # ...k/g/ṣ, and the kosha keys only inflected forms ('vAc' misses,
        # 'vAk' hits lemma vac).
        pause = {"c": "k", "j": "g", "z": "S"}
        base = slp1[:-1] if slp1.endswith("H") else slp1
        keys = [slp1, base]
        if base and base[-1] in pause:
            keys.append(base[:-1] + pause[base[-1]])

        infos = []
        seen = set()

        def collect(entries):
            for entry in entries:
                info = _kosha_entry_info(entry)
                if info and info["lemma"] not in seen:
                    seen.add(info["lemma"])
                    infos.append(info)

        for key in keys:
            try:
                collect(kosha.get(key) or [])
            except KeyError:
                pass
        if infos:
            exact = [i for i in infos if any(k.lower().startswith(i["stem"].lower()) for k in keys)]
            return finalize(exact or infos)

        # Stem fallback, guarded by a shared 3-character prefix: without the
        # guard homographic dhatu stems win (kosha.get('arTa') -> arTi for an
        # 'arTO' surface).
        def matches(info):
            a = info["lemma"].replace("\u1e43", "m")[:3].lower()
            b = iast_form.replace("\u1e43", "m")[:3].lower()
            return len(a) >= 3 and a == b

        for n in (1, 2, 3):
            if len(slp1) - n < 4:
                break
            try:
                collect(kosha.get(slp1[:-n]) or [])
            except KeyError:
                continue
            guarded = [i for i in infos if matches(i)]
            if guarded:
                return finalize(guarded)
        return None

    token_groups = [dharmamitra_results.get("tokens") or []]
    for entry in dharmamitra_results.get("pada_followups") or []:
        if isinstance(entry, dict):
            token_groups.append(entry.get("tokens") or [])

    for tokens in token_groups:
        for token in tokens:
            form = token.get("form", "")
            if not form:
                continue
            dev = sanscript.transliterate(form, sanscript.IAST, sanscript.DEVANAGARI)
            slp1 = sanscript.transliterate(dev, sanscript.DEVANAGARI, sanscript.SLP1)
            info = lookup(slp1, form)
            if info:
                token["lemma"] = info["lemma"]
                token["kosha_type"] = info["type"]


def kosha_lookup(kosha, word: str) -> list:
    """Look up a word in the kosha dictionary, with stem normalization fallback.
    
    First tries exact match, then progressively shorter stems (stripping 1-3
    SLP1 characters) to handle surface forms like nominative ``vAco`` → stem
    ``vAca``.
    
    Args:
        kosha: Kosha dictionary instance
        word: SLP1-encoded word to look up
    
    Returns:
        List of grammatical entries, or empty list if not found
    """
    try:
        entries = kosha.get(word)
        if entries:
            return entries
    except KeyError:
        pass

    # Fallback: strip up to three trailing SLP1 characters (case endings).
    # Stop before the stem gets too short, otherwise the same key is queried
    # again or a spurious short-word entry matches.
    for length in range(1, 4):
        if len(word) - length < 2:
            break
        stem = word[:-length]
        try:
            entries = kosha.get(stem)
            if entries:
                return entries
        except KeyError:
            pass

    return []


def _is_debug_text(text: str) -> bool:
    """Check if a prakriya step text is an English debug/logging string.
    
    Args:
        text: Text to check
    
    Returns:
        True if text appears to be debug/logging output
    """
    if not text:
        return False
    if '==' in text or '::' in text:
        return True
    if 'trying' in text or 'run_' in text:
        return True
    if '(' in text and text.split('(')[-1].strip().islower():
        return True
    return False


def _format_pada_entry_json(entry) -> Dict[str, Any]:
    """Format a PadaEntry as a JSON-serializable dict.
    
    Args:
        entry: PadaEntry from vidyut prakriya
    
    Returns:
        Dict with type, pratipadika, artha, linga, vibhakti, vacana,
        dhatu, gana, prayoga, lakara, purusha as applicable
    """
    from vidyut.lipi import transliterate, Scheme
    result = {}

    if entry.is_avyaya:
        result["type"] = "avyayam"
    else:
        entry_repr = repr(entry)
        if "Tinanta" in entry_repr:
            result["type"] = "tīnantāḥ"
        else:
            result["type"] = "sūnantāḥ"

    if hasattr(entry, 'pratipadika_entry') and hasattr(entry, 'linga'):
        pe = entry.pratipadika_entry
        if pe:
            result["pratipadika"] = _slp1_to_iast_vidyut(pe.lemma)
            if not pe.is_avyaya and hasattr(pe, 'artha_sa') and pe.artha_sa:
                result["artha"] = _slp1_to_iast_vidyut(pe.artha_sa)

        if entry.linga:
            result["linga"] = LINGA_IAST.get(str(entry.linga), str(entry.linga))
        if entry.vibhakti:
            result["vibhakti"] = VIBHAKTI_IAST.get(str(entry.vibhakti), str(entry.vibhakti))
        if entry.vacana:
            result["vacana"] = VACANA_IAST.get(str(entry.vacana), str(entry.vacana))

    if hasattr(entry, 'dhatu_entry') and hasattr(entry, 'prayoga'):
        de = entry.dhatu_entry
        if de:
            result["dhatu"] = _slp1_to_iast_vidyut(de.clean_text)
            if de.dhatu and de.dhatu.gana:
                result["gana"] = GANA_IAST.get(str(de.dhatu.gana), str(de.dhatu.gana))
            if de.artha_sa:
                result["artha"] = _slp1_to_iast_vidyut(de.artha_sa)

            result["prayoga"] = PRAYOGA_IAST.get(str(entry.prayoga), str(entry.prayoga))
            result["lakara"] = LAKARA_IAST.get(str(entry.lakara), str(entry.lakara))
            result["purusha"] = PURUSHA_IAST.get(str(entry.purusha), str(entry.purusha))
            result["vacana"] = VACANA_IAST.get(str(entry.vacana), str(entry.vacana))

    return result


def _format_prakriya_steps(prakriya) -> List[Dict[str, Any]]:
    """Format prakriya derivation steps as a list of JSON-serializable dicts.
    
    Args:
        prakriya: Prakriya object from vidyut vyakarana
    
    Returns:
        List of step dicts with step number, sutra, source, terms_iast, changed_iast
    """
    steps = []
    history = prakriya.history
    if not history:
        return steps

    for step_idx, step in enumerate(history, 1):
        sutra = step.code if step.code else ""
        source_repr = repr(step.source) if hasattr(step, 'source') else ""
        source = source_repr.replace("Source.", "") if source_repr else ""

        if hasattr(step, 'result') and step.result:
            terms = step.result
            if terms and isinstance(terms[0], str):
                filtered = [t for t in terms if not _is_debug_text(t)]
            else:
                filtered = [t for t in terms if not _is_debug_text(t.text)]
                filtered = [t for t in filtered if t.text]

            if not filtered:
                continue

            if isinstance(terms[0], str):
                term_texts = ' '.join(filtered)
                changed_iast = [_slp1_to_iast_vidyut(t) for t in filtered]
            else:
                term_texts = ' '.join(t.text for t in filtered)
                changed_iast = [_slp1_to_iast_vidyut(t.text) for t in filtered if t.was_changed]

            if term_texts:
                steps.append({
                    "step": step_idx,
                    "sutra": sutra,
                    "source": source,
                    "terms_iast": _slp1_to_iast_vidyut(term_texts),
                    "changed_iast": changed_iast,
                })
            else:
                steps.append({
                    "step": step_idx,
                    "sutra": sutra,
                    "source": source,
                    "terms_iast": "",
                    "changed_iast": [],
                })
        else:
            steps.append({
                "step": step_idx,
                "sutra": sutra,
                "source": source,
                "terms_iast": "",
                "changed_iast": [],
            })

    return steps


# SLP1 writes every vowel as one code point (e = e, E = ai, o = o, O = au,
# f = ṛ, x = ḷ), so an akshara is exactly one character from this set.
_VOWELS = set("aAiIuUfFxXoOeE")

# Marks that close an akshara without adding a vowel of their own: anusvāra,
# visarga, virāma and the Vedic accents vidyut writes in SLP1.
_AKSHARA_MARKS = set("MH~\\'")


def _count_aksharas(slp1_token: str) -> int:
    """Count syllabic nuclei (aksharas) in an SLP1 token.

    Consonant clusters share the vowel that follows them, and anusvara (M),
    visarga (H) and avagraha carry no vowel of their own, so counting vowel
    code points gives the akshara count directly.
    """
    return sum(1 for char in slp1_token if char in _VOWELS)


def _split_into_padas(slp1_line: str) -> List[str]:
    """Split a SLP1 verse line into halves at the akshara midpoint.

    vidyut's Chandas classifier works per pāda, so an anuṣṭubh line of four
    padas must be cut where its cumulative akshara count reaches exactly half the
    line total; a character-count proxy misplaces that cut. The scan walks vowel
    code points rather than tokens because sandhi routinely joins words across a
    pāda boundary (मोहाद् + उडुपेन written मोहादुडुपेन) and a whole pāda can be a single
    compound ('आसमुद्रक्षितीशानाम्'), so the midpoint can fall inside a token — harmless here,
    since these halves feed only the classifier. Token count is no guard for that reason: two
    fused compounds still hold four pādas. A line whose total is odd (the 21-akshara pādas of
    Abhijñānaśākuntalam 1.1) or too short to be a pair of pādas is returned whole instead of
    being cut at the nearest boundary, which would hand vidyut half-verses no meter has. The
    line arrives free of verse punctuation: `preprocess_input` removed it already.

    Args:
        slp1_line: SLP1-encoded line of text

    Returns:
        List of SLP1 halves (single element when the line cannot be split)
    """
    tokens = slp1_line.split()
    total = sum(_count_aksharas(t) for t in tokens)
    if total <= 12 or total % 2:
        return [slp1_line]

    half = total // 2
    cumulative = 0
    boundary = None
    for index, char in enumerate(slp1_line):
        if char in _VOWELS:
            cumulative += 1
            if cumulative == half:
                # An SLP1 vowel closes its akshara; the next character starts the
                # consonants of the following one.
                boundary = index + 1
                break
    if boundary is None:
        return [slp1_line]

    cut = boundary
    while cut < len(slp1_line) and slp1_line[cut] in _AKSHARA_MARKS:
        cut += 1

    halves = [slp1_line[:cut].strip(), slp1_line[cut:].strip()]
    if not all(halves):
        return [slp1_line]
    return halves


def _get_kosha_info(kosha, word: str) -> Optional[Dict[str, Any]]:
    """Look up a word and classify its kosha entries.

    Args:
        kosha: Kosha dictionary instance
        word: SLP1-encoded word

    Returns:
        Dict with 'type' ('sūnantāḥ' / 'tīnantāḥ' / 'avyayam') and 'lemma',
        both IAST; None when the word has no usable kosha entry.
    """
    return _kosha_info_from_entries(kosha_lookup(kosha, word))


def _has_standalone_entry(entries: List[Any]) -> bool:
    """Check if entries include at least one standalone (non-derived) word."""
    for entry in entries:
        entry_repr = repr(entry)
        if "Krdanta" not in entry_repr and "Tinanta" not in entry_repr:
            return True
    return False


def _is_quality_split(splitter, kosha, word: str) -> List[Any]:
    """Return the semantically valid binary splits of a word.

    Filters out spurious matches where very short strings match verb roots.
    A split qualifies when:
      - both parts are ≥ 4 characters,
      - both parts have at least two kosha entries (a lone entry is usually noise),
      - both parts have at least one non-derived (standalone) entry.

    Args:
        splitter: Splitter instance for sandhi rules
        kosha: Kosha dictionary instance
        word: SLP1-encoded word to split

    Returns:
        List of (split, first_info, second_info) tuples for valid splits
    """
    results = []
    for i in range(1, len(word)):
        try:
            splits = list(splitter.split_at(word, i))
        except Exception as exc:
            _warn_once("vidyut sandhi split_at", exc)
            continue
        for split in splits:
            if not split.is_valid:
                continue
            if len(split.first) < 4 or len(split.second) < 4:
                continue
            # One FST lookup per part; this loop is the hot path of the engine.
            first_entries = kosha_lookup(kosha, split.first)
            second_entries = kosha_lookup(kosha, split.second)
            if len(first_entries) < 2 or len(second_entries) < 2:
                continue
            if not _has_standalone_entry(first_entries):
                continue
            if not _has_standalone_entry(second_entries):
                continue
            results.append(
                (
                    split,
                    _kosha_info_from_entries(first_entries),
                    _kosha_info_from_entries(second_entries),
                )
            )
    return results


def _chain_score(chain: List[Dict[str, Any]]):
    """Score a split chain: more kosha-attested parts and fewer parts win."""
    kosha_count = sum(1 for part in chain if part.get("kosha"))
    return (kosha_count, -len(chain))


def recursive_split(splitter, kosha, word: str, max_depth: int = 4, max_parts: int = 4) -> List[List[Dict[str, Any]]]:
    """Recursively resolve multi-part compounds using DFS.
    
    Records intact dictionary matches as leaf chains (macro-compound level)
    and continues exploring deeper splits (micro-phonetic level).
    
    Recurses into both first and second halves to produce chains like:
      - [वाक्, अर्थ, इव] (deepest)
      - [वागर्थ, इव] (medium)
      - [वागर्था, विव] (shallowest)
    
    Args:
        splitter: Splitter instance for sandhi rules
        kosha: Kosha instance for dictionary lookup
        word: SLP1-encoded word to split
        max_depth: Maximum recursion depth
        max_parts: Maximum number of parts in a chain (prevents over-splitting)
    
    Returns:
        List of split chains, each chain is a list of dicts:
        [{"part": slp1_text, "iast": iast_text, "kosha": {...}|None}, ...]
    """
    results = []

    # Record intact word as leaf chain (macro-compound level)
    kosha_entries = kosha_lookup(kosha, word)
    if kosha_entries:
        results.append([{
            "part": word,
            "iast": _slp1_to_iast_vidyut(word),
            "kosha": _kosha_info_from_entries(kosha_entries),
            "depth": 0,
        }])

    if max_depth <= 0:
        return results

    # Explore deeper splits (micro-phonetic level)
    quality_splits = _is_quality_split(splitter, kosha, word)

    for split, first_info, second_info in quality_splits:
        # Recursively resolve the first half
        first_chains = recursive_split(splitter, kosha, split.first, max_depth - 1, max_parts)
        # Recursively resolve the second half
        second_chains = recursive_split(splitter, kosha, split.second, max_depth - 1, max_parts)

        # If first half has deeper splits, combine each first chain with each second chain
        if first_chains and second_chains:
            for fc in first_chains:
                for sc in second_chains:
                    full_chain = fc + sc
                    if len(full_chain) <= max_parts:
                        results.append(full_chain)
        elif first_chains:
            # Only first half has deeper splits
            for fc in first_chains:
                full_chain = fc + [{"part": split.second, "iast": _slp1_to_iast_vidyut(split.second), "kosha": second_info, "depth": 1}]
                if len(full_chain) <= max_parts:
                    results.append(full_chain)
        elif second_chains:
            # Only second half has deeper splits (original behavior)
            for sc in second_chains:
                full_chain = [{"part": split.first, "iast": _slp1_to_iast_vidyut(split.first), "kosha": first_info, "depth": 1}] + sc
                if len(full_chain) <= max_parts:
                    results.append(full_chain)
        else:
            # Neither half has deeper splits — record this split as a leaf
            results.append([
                {"part": split.first, "iast": _slp1_to_iast_vidyut(split.first), "kosha": first_info, "depth": 1},
                {"part": split.second, "iast": _slp1_to_iast_vidyut(split.second), "kosha": second_info, "depth": 1},
            ])

    # Sort results: prefer chains with more kosha matches, fewer parts
    results.sort(key=_chain_score, reverse=True)

    return results


def run_vidyut(devanagari_text: str) -> Dict[str, Any]:
    """Run vidyut engine: kosha lookup, sandhi splitting, prakriya, meter.

    Args:
        devanagari_text: Cleaned Devanagari text

    Returns:
        Dict with kosha, prakriya, meter (per-pāda detail) and chandas
        (verse-level summary) sections
    """
    from vidyut.lipi import transliterate, Scheme
    from vidyut.prakriya import (
        Vyakarana, Dhatu, Pratipadika, Pada, Gana, Lakara, Purusha, Vacana, Prayoga
    )
    from vidyut.chandas import Chandas
    from vidyut.sandhi import Splitter

    # Initialize vidyut modules
    try:
        if not Path(DATA_DIR).exists():
            return {"error": "Vidyut data directory not found"}
        kosha = load_kosha()
        if kosha is None:
            return {"error": "Vidyut kosha could not be loaded"}
        vyakarana = Vyakarana()
        chandas = Chandas(Path(DATA_DIR) / "chandas" / "meters.tsv")
        splitter = Splitter.from_csv(Path(DATA_DIR) / "sandhi" / "rules.csv")
    except Exception as exc:
        return {"error": f"Vidyut initialization failed: {exc}"}

    # Convert Devanagari input to SLP1 for processing
    slp1_text = transliterate(devanagari_text, Scheme.Devanagari, Scheme.Slp1)

    # Tokenize by whitespace; preprocess_input already removed every separator,
    # so a token is exactly what sits between two spaces.
    tokens = [
        token
        for line in slp1_text.strip().split("\n")
        for token in line.split()
    ]

    all_dhatus = set()
    all_pratipadikas = set()
    words = []

    for slp1_token in tokens:
        iast = _slp1_to_iast_vidyut(slp1_token)

        entries = kosha_lookup(kosha, slp1_token)

        word_entry = {
            "iast": iast,
            "is_compound": False,
        }

        if entries:
            # Single word — full grammatical breakdown
            is_verb = any("Tinanta" in repr(e) for e in entries)
            if is_verb:
                entries = [e for e in entries if "Tinanta" in repr(e)]

            # Format entries and deduplicate
            seen_entries = set()
            unique_entries = []
            for e in entries[:8]:
                entry_dict = _format_pada_entry_json(e)
                # Deduplicate on the whole formatted reading: verb entries
                # carry dhatu/lakara/purusha instead of pratipadika/linga/
                # vibhakti/vacana, so a partial key collapses distinct verbs.
                key = json.dumps(entry_dict, sort_keys=True, ensure_ascii=False)
                if key not in seen_entries:
                    seen_entries.add(key)
                    unique_entries.append(entry_dict)

            word_entry["grammatical_entries"] = unique_entries
            word_entry["is_verb"] = is_verb

            # Extract dhatu/pratipadika
            for entry in entries:
                pe = getattr(entry, 'pratipadika_entry', None)
                if pe and pe.lemma:
                    all_pratipadikas.add(pe.lemma)
                if "Tinanta" not in repr(entry):
                    continue
                de = entry.dhatu_entry
                if de and de.dhatu and de.dhatu.aupadeshika:
                    # DhatuEntry.clean_text is the accent-free dictionary spelling.
                    all_dhatus.add((de.dhatu.aupadeshika, str(de.dhatu.gana), de.clean_text or ""))
        else:
            chains = recursive_split(splitter, kosha, slp1_token, max_depth=2, max_parts=4)

            if chains:
                word_entry["is_compound"] = True

                # Score chains: prefer more kosha entries, fewer parts
                chains.sort(key=_chain_score, reverse=True)

                # Build flat sandhi_splits list
                word_entry["sandhi_splits"] = []
                seen = set()
                for chain in chains:
                    parts_iast = [part["iast"] for part in chain]
                    split_str = " + ".join(parts_iast)
                    if split_str not in seen:
                        seen.add(split_str)
                        word_entry["sandhi_splits"].append(split_str)
                    if len(word_entry["sandhi_splits"]) >= 5:
                        break
            else:
                word_entry["unknown"] = True

        words.append(word_entry)

    # Dhatu prakriyas
    gana_map = {
        "BvAdi": Gana.Bhvadi,
        "adAdi": Gana.Adadi,
        "juhotyAdi": Gana.Juhotyadi,
        "divAdi": Gana.Divadi,
        "svAdi": Gana.Svadi,
        "tudAdi": Gana.Tudadi,
        "ruDAdi": Gana.Rudhadi,
        "tanAdi": Gana.Tanadi,
        "kryAdi": Gana.Kryadi,
        "curAdi": Gana.Curadi,
        "kaRqvAdi": Gana.Kandvadi,
    }

    dhatus_prakriya = []
    for aupadeshika, gana_name, clean_text in sorted(all_dhatus):
        gana = gana_map.get(gana_name, Gana.Bhvadi)
        dhatu = Dhatu.mula(aupadeshika, gana)
        # aupadeshika keeps the accent marks Dhatu.mula needs; display uses the
        # dictionary spelling.
        dhatu_dev = _slp1_to_iast_vidyut(clean_text or aupadeshika)

        dhatu_entry = {
            "dhatu": dhatu_dev,
            "krdantas": [],
            "tinantas": [],
        }

        try:
            prakriyas = vyakarana.derive(dhatu)
            if prakriyas:
                dhatu_entry["krdantas"] = _format_prakriya_steps(prakriyas[0])
        except Exception as exc:
            _warn_once("vidyut krdanta derivation", exc)

        combos = [
            (Lakara.Lat, Purusha.Madhyama, Vacana.Eka),
            (Lakara.Lan, Purusha.Madhyama, Vacana.Eka),
            (Lakara.Lot, Purusha.Madhyama, Vacana.Eka),
        ]
        for lakara, purusha, vacana in combos:
            try:
                pada = Pada.Tinanta(
                    dhatu=dhatu,
                    prayoga=Prayoga.Kartari,
                    lakara=lakara,
                    purusha=purusha,
                    vacana=vacana,
                )
                tinantas = vyakarana.derive(pada)
                if tinantas and tinantas[0].history and tinantas[0].history[-1].result:
                    result = tinantas[0].history[-1].result
                    if result and isinstance(result[0], str):
                        final = ' '.join(result)
                    else:
                        final = ' '.join(t.text for t in result if t.text)
                    dev = _slp1_to_iast_vidyut(final)
                    lakara_dev = LAKARA_IAST.get(str(lakara), str(lakara))
                    purusha_dev = PURUSHA_IAST.get(str(purusha), str(purusha))
                    vacana_dev = VACANA_IAST.get(str(vacana), str(vacana))
                    dhatu_entry["tinantas"].append({
                        "label": f"{lakara_dev}/{purusha_dev}/{vacana_dev}",
                        "form": dev,
                    })
            except Exception as exc:
                _warn_once("vidyut tinanta derivation", exc)

        dhatus_prakriya.append(dhatu_entry)

    # Pratipadika prakriyas
    pratipadikas_prakriya = []
    for lemma in sorted(all_pratipadikas)[:5]:
        try:
            pratipadika = Pratipadika.basic(lemma)
            prakriyas = vyakarana.derive(pratipadika)
            if prakriyas:
                pratipadikas_prakriya.append({
                    "lemma": _slp1_to_iast_vidyut(lemma),
                    "steps": _format_prakriya_steps(prakriyas[0]),
                })
        except Exception as exc:
            _warn_once("vidyut pratipadika derivation", exc)

    # Meter classification
    meter_results = []
    slp1_lines = slp1_text.strip().split("\n")

    for slp1_line in slp1_lines:
        slp1_line = slp1_line.strip()
        if not slp1_line:
            continue
        iast_line = _slp1_to_iast_vidyut(slp1_line)
        padas = _split_into_padas(slp1_line)
        pada_results = []

        for pada_slp1 in padas:
            match = chandas.classify(pada_slp1)
            pada_result = {
                "iast": _slp1_to_iast_vidyut(pada_slp1),
                "meter": None,
                "akshara_count": 0,
                "weight_pattern": "",
            }

            if match.padya:
                vrtta_iast = _slp1_to_iast_vidyut(match.padya)
                pada_result["meter"] = vrtta_iast

            if match.aksharas:
                # vidyut returns one akshara group per pāda; keep all of them
                # instead of letting the last group overwrite earlier ones.
                patterns = [
                    ''.join(a.weight for a in pada_aksharas)
                    for pada_aksharas in match.aksharas
                ]
                if patterns:
                    pada_result["akshara_count"] = sum(len(p) for p in patterns)
                    pada_result["weight_pattern"] = " | ".join(patterns)

            pada_results.append(pada_result)

        meter_results.append({
            "line_iast": iast_line,
            "padas": pada_results,
        })

    # vidyut's classifier answers per pāda and matches gaṇa prefixes, so a name can be structurally
    # impossible for the pāda it was given. The table's declared lengths let `_summarize_chandas`
    # keep such a name as a candidate without letting it decide `vrtta`.
    metre_lengths: Dict[str, set] = {}
    try:
        metre_lengths = {
            _slp1_to_iast_vidyut(name): counts
            for name, counts in _meter_lengths(
                Path(DATA_DIR) / "chandas" / "meters.tsv"
            ).items()
        }
    except Exception as exc:
        _warn_once("vidyut metre table transliteration", exc)

    return {
        "kosha": words,
        "prakriya": {
            "dhatus": dhatus_prakriya,
            "pratipadikas": pratipadikas_prakriya,
        },
        "meter": meter_results,
        "chandas": _summarize_chandas(meter_results, metre_lengths),
    }


def _meter_lengths(meters_tsv: Path) -> Dict[str, set]:
    """Read vidyut's metre table into {SLP1 name: declared akshara counts}.

    `meters.tsv` holds one metre per line as '<name>\t<vrtta|jati>\t<pattern>', where every pattern
    character is one akshara (G guru, L laghu) and '|' only groups the pattern for readability —
    sragDarA is 'GGGGLGG|LLLLLLG|GLGGLGG', i.e. 21 aksharas. The declared counts are what let us see
    through vidyut's classifier: it matches gaṇa prefixes, so an 11-akshara pāda whose scan is a
    prefix of indravaṃśā's 12-akshara pattern gets named 'indravaṃśā' anyway.

    Args:
        meters_tsv: path to vidyut's metre table

    Returns:
        Mapping of SLP1 metre name to the set of akshara counts its patterns declare
    """
    lengths: Dict[str, set] = {}
    try:
        with meters_tsv.open(encoding="utf-8") as handle:
            for line in handle:
                fields = line.rstrip("\n").split("\t")
                if len(fields) < 3 or not fields[0]:
                    continue
                lengths.setdefault(fields[0], set()).add(
                    sum(len(group) for group in fields[2].split("|") if group)
                )
    except OSError as exc:
        _warn_once("vidyut metre table read", exc)
    return lengths


def _summarize_chandas(
    meter_results: List[Dict[str, Any]],
    metre_lengths: Optional[Dict[str, set]] = None,
) -> Dict[str, Any]:
    """Summarize vidyut's per-pāda classifications for the whole verse.

    vidyut classifies one pāda at a time against data-0.4.0/chandas/meters.tsv — 145 vṛttas, no jāti
    metres at all, so neither anuṣṭubh (the 8·8·8·8 śloka of the Bhagavadgītā and Raghuvamṣa I) nor
    upajāti (the mixed 11-akshara triṣṭubh of BG 2.22 / 11.15) is in its table. `vrtta` names the verse
    only when every structurally possible pāda name agrees; vidyut's own suggestions stay listed in
    `candidates`, next to the akshara counts that show the actual shape.

    A name whose declared length does not match the pāda it was given (vidyut matching a gaṇa prefix,
    e.g. indravaṃśā's 12 aksharas for an 11-akshara pāda) is kept as a candidate but excluded from the
    vote and reported on stderr — otherwise one impossible name silently turns 'indravajrā in three
    pādas' into `null`. Pādas vidyut leaves unclassified (the final syllable of a pāda is free in
    length, its patterns are not) do not veto a name the other pādas agree on; `classified_pada_count`
    records how much of the verse it actually scanned.

    Args:
        meter_results: per-line pāda classifications built by run_vidyut
        metre_lengths: {IAST metre name: declared akshara counts}, from `_meter_lengths`

    Returns:
        Verse-level dict with vrtta, candidates and pada statistics
    """
    padas = [pada for line in meter_results for pada in line["padas"]]
    names = [pada["meter"] for pada in padas if pada["meter"]]
    plausible: List[str] = []
    for pada in padas:
        name = pada["meter"]
        if not name:
            continue
        declared = (metre_lengths or {}).get(name)
        if declared and pada["akshara_count"] not in declared:
            print(
                f"Note: vidyut named {name} ({'/'.join(str(c) for c in sorted(declared))} aksharas) "
                f"for a pāda of {pada['akshara_count']}; its classifier matches gaṇa prefixes, so the "
                "name stays a candidate only and cannot decide the verse.",
                file=sys.stderr,
            )
            continue
        plausible.append(name)
    agreed = plausible[0] if plausible and len(set(plausible)) == 1 else None
    return {
        "vrtta": agreed,
        "candidates": sorted(set(names)),
        "pada_count": len(padas),
        "classified_pada_count": len(names),
        "aksharas_per_pada": [pada["akshara_count"] for pada in padas],
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
# Orchestrates all three engines and writes the final JSON output.

def _log_engine_failure(name: str, detail: str, fatal: bool) -> None:
    """Print one engine failure line to stderr.

    Args:
        name: Engine key used in engine_outputs
        detail: Human-readable cause
        fatal: True prints 'Error' for the local dependencies (sanskrit_parser,
            vidyut), whose absence makes the run exit non-zero; False prints
            'Warning' for Dharmamitra, a remote service whose loss only costs its
            own section of the output.
    """
    print(f"{'Error' if fatal else 'Warning'}: {name}: {detail}", file=sys.stderr)


def _run_local_engine(run: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """Run a local engine and normalize any failure to the shared error shape.

    A raised exception and an engine returning its own {"error": ...} object —
    vidyut does that when its data directory is missing — both mean the
    dependency could not do its job, so the caller logs it and fails the run.
    """
    try:
        results = run()
    except Exception as exc:
        return {"error": f"unavailable: {exc}"}
    if isinstance(results, dict) and results.get("error"):
        return {"error": str(results["error"])}
    return results


def main() -> int:
    """Main entry point.

    Parses CLI arguments, reads input, runs all three engines, writes both documents
    of the output pair ('<base>.raw.json' and '<base>.result.json', creating the
    directory when needed), and prints only the result document's top-level 'input'
    and 'chandas' objects to stdout.

    Returns:
        0 on success; 1 when a local engine (sanskrit_parser or vidyut) could not
        run, or when an output file could not be written — in both cases the documents
        that were produced are still on disk. A missing Dharmamitra service only logs
        a warning and still returns 0.
    """
    parser = argparse.ArgumentParser(
        description="samskrta-multiparser — Unified multi-engine Sanskrit analyzer"
    )
    parser.add_argument(
        "mode",
        choices=["pada", "shloka"],
        help="Analysis mode: 'pada' for single-word, 'shloka' for full-line analysis",
    )
    parser.add_argument(
        "-i", "--input",
        default=None,
        help="Path to input file in Devanagari or a romanization (use '-' for stdin); "
             "default: the single .txt file in input/",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output base path; writes '<base>.raw.json' and '<base>.result.json' "
             "(a trailing '.json' is stripped). Default: results/<input stem>",
    )
    parser.add_argument(
        "-f", "--format",
        choices=["json", "pretty"],
        default="pretty",
        help="Output format: 'json' (compact) or 'pretty' (indented, default)",
    )

    args = parser.parse_args()

    # Determine input file. Without -i there is exactly one place an input may live: input/.
    if args.input:
        input_file = args.input
    else:
        input_file, problem = default_input()
        if problem:
            print(f"Error: {problem}", file=sys.stderr)
            return 1
    try:
        raw_text = read_input(input_file)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error reading input: {e}", file=sys.stderr)
        return 1

    _, devanagari_text = to_devanagari(raw_text)

    # Preprocess
    cleaned = preprocess_input(devanagari_text)
    lines = [preprocess_input(line) for line in cleaned.split("\n") if line.strip()]
    iast_text = devanagari_to_iast(cleaned)
    iast_lines = [devanagari_to_iast(line) for line in lines]

    # Build output structure
    # The documents record the text in both working scripts only; which romanization the user typed
    # is an input detail, not part of the analysis. Neither is the mode: it is chosen on the command
    # line and every section of the document already says what kind of reading it is.
    output = {
        "input": {
            "devanagari": cleaned,
            "iast": iast_text,
        },
        "engine_outputs": {},
    }

    # sanskrit_parser and vidyut are local dependencies: if either cannot run at
    # all the comparison is meaningless, so the process must exit non-zero even
    # though the remaining engines still contribute their sections.
    failed_local_engines: List[str] = []

    # Dharmamitra is a network call, so it runs on its own thread and overlaps the two local
    # engines. Only I/O truly overlaps — vidyut's Rust bindings never release the GIL and
    # sanskrit_parser is pure Python — but an unreachable API used to add up to a minute of
    # purely serial waiting here. It stays a logged warning, not a failed run.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as dharmamitra_pool:
        dharmamitra_future = dharmamitra_pool.submit(
            run_dharmamitra, iast_text, iast_lines
        )

        sp_results = _run_local_engine(
            lambda: _convert_devanagari_to_iast(run_sanskrit_parser(cleaned, args.mode))
        )
        if sp_results.get("error"):
            failed_local_engines.append("sanskrit_parser")
            _log_engine_failure("sanskrit_parser", sp_results["error"], fatal=True)
        output["engine_outputs"]["sanskrit_parser"] = sp_results

        # Run vidyut engine (Devanagari input; converts to SLP1 internally)
        vidyut_results = _run_local_engine(
            lambda: _convert_devanagari_to_iast(run_vidyut(cleaned))
        )
        if vidyut_results.get("error"):
            failed_local_engines.append("vidyut")
            _log_engine_failure("vidyut", vidyut_results["error"], fatal=True)
        output["engine_outputs"]["vidyut"] = vidyut_results

        dharmamitra_results = {}
        try:
            dharmamitra_results = dharmamitra_future.result()
        except Exception as exc:
            dharmamitra_results = {"error": f"Dharmamitra engine failed: {exc}"}

    if dharmamitra_results.get("error"):
        _log_engine_failure("dharmamitra", str(dharmamitra_results["error"]), fatal=False)
    output["engine_outputs"]["dharmamitra"] = dharmamitra_results

    # Enrich Dharmamitra tokens with lemmas from the vidyut kosha
    if dharmamitra_results.get("tokens"):
        try:
            enrich_dharmamitra_lemmas(dharmamitra_results)
        except Exception as exc:
            print(f"Warning: Dharmamitra lemma enrichment failed: {exc}", file=sys.stderr)

    indent = 2 if args.format == "pretty" else None

    # One input, one base path, two documents. Without -o the base comes from the
    # input file's name under results/; stdin has no name, so the mode is used.
    if args.output:
        base = postprocess_analysis.output_base(args.output)
    else:
        stem = Path(input_file).stem if input_file != "-" else args.mode
        base = str(Path("results") / stem)
    raw_file = postprocess_analysis.raw_path(base)
    result_file = postprocess_analysis.result_path(base)

    # Word readings are ranked with the vidyut dictionary in the loop: a reading built on a stem the kosha does
    # not record for that form loses to one it does record. Measured over the sixteen pinned verses this moves
    # 8 of 273 published primaries, 6 of them onto the reading the printed editions give (see ACCURACY §6).
    # It is None when the dictionary data is absent, which keeps the previous ranking.
    attest = postprocess_analysis.kosha_attest(load_kosha())

    try:
        processed = postprocess_analysis.postprocess(output, attest)
    except ValueError as exc:
        print(f"Error building the result document: {exc}", file=sys.stderr)
        return 1

    # A pada Dharmamitra left untagged gets a request of its own; the answers are stored in the raw
    # document, so rebuilding the result document offline reproduces them.
    if fill_missing_pada_readings(dharmamitra_results, processed):
        try:
            enrich_dharmamitra_lemmas(dharmamitra_results)
        except Exception as exc:
            print(f"Warning: Dharmamitra lemma enrichment failed: {exc}", file=sys.stderr)
        processed = postprocess_analysis.postprocess(output, attest)

    # Dharmamitrā sometimes returns more tokens than the verse has words. They are listed in the result
    # document and named here rather than folded into a pada they do not belong to.
    unmatched = processed.get("dharmamitra_unmatched") or []
    if unmatched:
        print(
            f"Note: Dharmamitrā returned {len(unmatched)} token(s) that match no pada of this verse "
            f"({' | '.join(str(form) for form in unmatched)}); see 'dharmamitra_unmatched' in the result document.",
            file=sys.stderr,
        )

    try:
        postprocess_analysis.write_document(raw_file, output, indent)
        postprocess_analysis.write_document(result_file, processed, indent)
    except OSError as exc:
        print(f"Error writing output: {exc}", file=sys.stderr)
        return 1

    # stdout stays machine-readable: exactly the two objects, nothing else.
    postprocess_analysis.print_input_and_chandas(result_file, indent)

    if failed_local_engines:
        print(
            f"Run incomplete: {' and '.join(failed_local_engines)} "
            "could not run; see the error entries in the output.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
