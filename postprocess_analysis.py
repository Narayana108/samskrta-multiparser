#!/usr/bin/env python3
"""Post-process raw multi-engine Sanskrit analysis output into a readable form.

Reads:  <base>.raw.json    (raw engine outputs written by app.py)
Writes: <base>.result.json (the condensed reading document)

Output structure::

    {
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


_VIBHAKTI_ORDER = {
    "prathamā": 1,
    "dvitīyā": 2,
    "tṛtīyā": 3,
    "caturthī": 4,
    "pañcamī": 5,
    "ṣaṣṭhī": 6,
    "saptamī": 7,
    "saṃbodhana": 8,
}

def kosha_attest(kosha):
    """Build the dictionary-attestation lookup used to rank readings; None when there is no kosha.

    Args:
        kosha: a vidyut Kosha, or None when its data files are unavailable

    Returns:
        Callable taking an IAST surface form and returning the SLP1 lemma stems recorded for it — probed both
        as written and with the orthography the engines use among themselves (anusvara 'M' → 'm', final
        visarga 'H' → 's', trailing sign dropped) — or None when no dictionary is loaded. `app` owns all
        dictionary access, so its primitives are imported lazily here; this module must stay importable on a
        machine that has no engine data at all.
    """
    if kosha is None:
        return None

    from indic_transliteration import sanscript

    from app import _kosha_entry_info, kosha_lookup

    def stems_for(form: str) -> set:
        slp1 = sanscript.transliterate(form, sanscript.IAST, sanscript.SLP1)
        folded = "".join({"M": "m", "H": "s"}.get(ch, ch) for ch in slp1).rstrip("sr")
        stems = set()
        for probe in (slp1, folded):
            for entry in kosha_lookup(kosha, probe):
                info = _kosha_entry_info(entry)
                if info:
                    stems.add(info["stem"])
        return stems

    return stems_for


def _root_attested(entry: Dict[str, Any], stems: Optional[set]) -> bool:
    """Whether a reading's root is one of the lemma stems recorded for its surface form.

    Args:
        entry: one candidate reading; its `root` may carry sanskrit_parser's `#n` homophony marker
        stems: recorded SLP1 stems for that form, or None when no dictionary signal exists

    Returns:
        True on an exact match — never a prefix match, because vidyut records short homographs ('ah' and 'aha'
        for the form अहम्) that are prefixes of longer stems — and also True when there is no signal at all, so
        forms absent from the kosha keep exactly the order they had.
    """
    if not stems:
        return True
    root = (entry.get("root") or "").split("#", 1)[0]
    if not root:
        return False

    from indic_transliteration import sanscript

    return sanscript.transliterate(root, sanscript.IAST, sanscript.SLP1) in stems




def _morph_rank(entry: Dict[str, Any], attested: bool = True):
    """Rank candidate analyses: dictionary-attested stems first, then the fullest case reading.

    Ties between equally complete readings follow the grammatical case order (prathamā first,
    saṃbodhana last). The previous alphabetical tie-break promoted the vocative over a nominative
    that sanskrit_parser had listed for the very same form — 103 of our golden padas showed
    'saṃbodhana' alone while the raw output also offered prathamā/dvitīyā. Case order comes before
    leanness, so a participle's kṛdanta reading is not demoted below a bare vocative of the same stem
    (jīrṇāni). An avyaya reading outranks a bare compound marker for the same reason: 'avyayam' states
    what the word is, while 'samāsapūrvapadanāmapadam' only says it sits inside a compound — otherwise
    yathā/tathā/ca/eva were published as compound prefixes and their indeclinable reading vanished.

    Dictionary attestation outranks all of that when a kosha is available: a reading built on a stem the
    dictionary does not record for that form loses to one it does record. Measured over the sixteen pinned
    verses (273 published word readings), 8 primaries move and 6 of those become the reading the printed
    editions give — 'asti' becomes √as "is" instead of an invented vocative *asta*, 'hi' becomes the
    indeclinable particle instead of a vocative *ha*, 'yathāvidhi' becomes an avyaya, 'prakṛti' and 'sanni'
    become compound members rather than vocatives, and 'yāti' in saṃyāti becomes √yā "goes". Two move
    sideways: 'vastā', where the true reading (instrumental plural of *vasu*, the eight Vasus) is offered by
    no engine, and BG 18.66 'aham', where the kosha really does record a short noun *aha* ("non-existence")
    for that form, so attestation cannot single out the pronoun *asmad*. A longer-stem preference was also
    measured (42 primaries move, one curated form lost), so it is not used.
    """
    return (
        bool(attested),
        "vibhakti" in entry,
        "vacana" in entry,
        "avyayam" in entry.get("tags", []),
        (-_VIBHAKTI_ORDER.get(entry.get("vibhakti") or "", 0)),
        (-len(entry.get("tags", []))),
        tuple(sorted((k, str(v)) for k, v in entry.items())),
    )


def collect_sp_morphology(sp_output: Dict[str, Any], attest: Optional[Any] = None
                          ) -> Dict[str, List[Dict[str, Any]]]:
    """Map every sanskrit_parser surface form to its readings, best ranked first.

    Walks the raw output and collects every tag group attached to a 'pada' — both the items of the
    whole-line `sandhi_splits` and the `word_morphology` entries recorded for the words the per-word
    ranking chose. Split order and sampled candidate set vary between processes, so candidates are
    ranked — full case+number readings first, then a canonical tie-break — instead of taking whatever
    appears first. Candidates are keyed by the anusvara-normalized form up front; otherwise two
    spellings of one pada ('saṃpṛktau' / 'sampṛktau') compete as separate keys and the winner would be
    decided by insertion order.

    An ambiguous form keeps every distinct reading it was given: vāsāṃsi is nominative, accusative
    and vocative plural at once, so collapsing it to one tag erases an answer the engine did offer.
    The best-ranked reading fills the word's primary fields; the rest travel as `alternates`.

    Args:
        sp_output: the raw `sanskrit_parser` subtree of a raw analysis document
        attest: optional callable mapping an IAST surface form to the SLP1 lemma stems vidyut's kosha records
            for it (built by `kosha_attest`). Given it, readings built on stems the dictionary does not record
            are demoted below recorded ones; without it the ranking is exactly as before.
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
    ranked_all = {}
    for pada, cands in groups.items():
        stems = attest(pada) if attest else None
        order = sorted(cands, key=lambda cand: _morph_rank(cand, _root_attested(cand, stems)), reverse=True)
        seen = set()
        ranked = []
        for cand in order:
            key = json.dumps(cand, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                ranked.append(cand)
        ranked_all[pada] = ranked
    return ranked_all


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


def _dm_coverage(word: str, forms: List[str]) -> float:
    """How completely a run of Dharmamitra token forms rebuilds one pada word.

    SequenceMatcher tolerates the character changes sandhi makes — 'paśca' + 'ardhena' still covers
    'paścārdhena' — while a run that overshoots the word scores badly, which is what stops one pada
    from taking its neighbour's tokens.
    """
    if not forms:
        return 0.0
    target = _norm_anusvara(word).lower()
    concat = _norm_anusvara("".join(forms)).lower()
    if not target or not concat:
        return 0.0
    return difflib.SequenceMatcher(None, target, concat).ratio()


# Longest run of Dharmamitra tokens one pada may be given. Sandhi plus compounds reach six parts in
# this corpus; ten leaves room without letting a single word absorb a whole line.
_MAX_DM_PARTS = 10

# Cost of declaring a Dharmamitra token unmatched instead of filing it under a pada. A token that
# really belongs to a word lifts that word's coverage by far more than this, so the move only wins
# when the token earns nothing where it stands — which is how 'mā', invented ahead of
# 'karmaṇy eva adhikāraḥ te' in the Bhagavadgītā 2.47 stream, ends up reported instead of being glued
# onto a pada whose other tokens are already right.
_DM_SKIP_PENALTY = 0.02

# A word-level answer must rebuild at least this much of the pada it was requested for. Dharmamitra
# pads some single-word requests with pieces of a phrase it recognises ('ṛta | iva' ahead of
# 'vāsāṃsi'); those extras are dropped instead of being shown as a reading.
_MIN_WORD_REQUEST_COVERAGE = 0.75


def _best_dm_subrun(word: str, forms: List[str]) -> tuple:
    """Best contiguous run of token forms for one word, as (start, length, coverage)."""
    best_start, best_length, best_score = 0, 0, 0.0
    for start in range(len(forms)):
        for end in range(start + 1, min(start + _MAX_DM_PARTS, len(forms)) + 1):
            score = _dm_coverage(word, forms[start:end])
            if score > best_score:
                best_start, best_length, best_score = start, end - start, score
    return best_start, best_length, best_score


def group_dm_tokens_by_word(
    input_words: List[str],
    tokens: List[Dict[str, Any]],
) -> tuple:
    """Group flat Dharmamitra tokens under the pada word they came from.

    Monotonic dynamic program over (word, token) positions: each word takes a contiguous run of at
    most ``_MAX_DM_PARTS`` tokens, any token may instead be declared unmatched for
    ``_DM_SKIP_PENALTY``, and the total coverage is maximized. The greedy walk this replaces tested
    only whether a token's first letter appeared somewhere in the current word, so
    'grīvābhaṅgābhirāmaṃ' — which contains an 'm' — swallowed 'muhur | anupatati' and left the next
    pada empty.

    Returns:
        (groups, unmatched): one list of tokens per input word in stream order, plus the tokens that
        belong to no pada (Dharmamitra inventions or leftovers), which callers must report rather than
        hide.
    """
    forms = [_norm_anusvara(t.get("form", "")) for t in tokens]
    n_words, n_tokens = len(input_words), len(tokens)

    # best[i][j]: highest total coverage reachable for words i.. when tokens j.. are still unused.
    # take[i][j]: -1 skips token j, 0 gives word i an empty group, >0 is the run length for word i.
    best = [[0.0] * (n_tokens + 1) for _ in range(n_words + 1)]
    take = [[0] * (n_tokens + 1) for _ in range(n_words + 1)]
    for j in range(n_tokens - 1, -1, -1):
        best[n_words][j] = best[n_words][j + 1] - _DM_SKIP_PENALTY
        take[n_words][j] = -1
    for i in range(n_words - 1, -1, -1):
        for j in range(n_tokens, -1, -1):
            best[i][j], take[i][j] = best[i + 1][j], 0
            if j < n_tokens and best[i][j + 1] - _DM_SKIP_PENALTY > best[i][j]:
                best[i][j], take[i][j] = best[i][j + 1] - _DM_SKIP_PENALTY, -1
            for k in range(j + 1, min(j + _MAX_DM_PARTS, n_tokens) + 1):
                candidate = best[i + 1][k] + _dm_coverage(input_words[i], forms[j:k])
                if candidate > best[i][j]:
                    best[i][j], take[i][j] = candidate, k - j

    groups: List[List[Dict[str, Any]]] = [[] for _ in input_words]
    unmatched: List[Dict[str, Any]] = []
    word_index = token_index = 0
    while word_index < n_words or token_index < n_tokens:
        move = take[word_index][token_index] if word_index < n_words else -1
        if move == -1:
            unmatched.append(tokens[token_index])
            token_index += 1
        elif move == 0:
            word_index += 1
        else:
            groups[word_index] = tokens[token_index:token_index + move]
            token_index += move
            word_index += 1
    return groups, unmatched


def collect_dm_word_requests(dm_output: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Map each pada form to the tokens Dharmamitra returned for it on a word-level request.

    app.py requests a single word when the verse-level pass left that pada with no token of its own;
    the answers live under ``pada_followups`` in the raw document so this second pass replays them
    offline. Those answers have no sentence context, which is why they are labelled where used, and
    only the best contiguous run inside an answer is kept: a request for 'vāsāṃsi' came back as
    'ṛta | iva | vāsāṃsi', and the two extra pieces rebuild nothing of that pada.
    """
    answers: Dict[str, List[Dict[str, Any]]] = {}
    for entry in dm_output.get("pada_followups") or []:
        if not isinstance(entry, dict):
            continue
        pada = entry.get("pada")
        tokens = [t for t in (entry.get("tokens") or []) if isinstance(t, dict) and t.get("form")]
        if not (isinstance(pada, str) and pada and tokens):
            continue
        start, length, score = _best_dm_subrun(pada, [_norm_anusvara(t["form"]) for t in tokens])
        if score >= _MIN_WORD_REQUEST_COVERAGE:
            answers[pada] = tokens[start:start + length]
    return answers


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


def _sp_word_entry(form: str, sp_morph: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Render one sanskrit_parser surface form as a readable word entry.

    The best-ranked reading fills the primary fields; every other distinct reading the engine gave
    this form is listed under 'alternates' rather than dropped.
    """
    entry: Dict[str, Any] = {"form": form}
    readings = sp_morph.get(_norm_anusvara(form)) or []
    morph = readings[0] if readings else {}
    for key in ("root", "vibhakti", "vacana", "linga", "tags"):
        if morph.get(key):
            entry[key] = morph[key]
    if len(readings) > 1:
        entry["alternates"] = readings[1:]
    return entry


def build_padas(
    input_words: List[str],
    sp_decomp: Dict[str, List[str]],
    sp_morph: Dict[str, List[Dict[str, Any]]],
    dm_groups: List[List[Dict[str, Any]]],
    dm_available: bool,
    dm_word_requests: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """Build the per-pada comparison entries (engine-keyed).

    dm_available records whether Dharmamitra produced any tokens at all. When
    it did not, an empty group means "no data for this engine", so no
    differences are fabricated; when it did, a pada with no DM tokens is a real
    disagreement and gets a null-side difference region.

    dm_word_requests supplies tokens Dharmamitra returned for a pada form on a
    word-level follow-up request (no sentence context). They fill only groups the
    verse-level stream left empty, and such a side is labelled 'request: "pada"' so
    the reader can see where the reading came from.
    """
    padas: List[Dict[str, Any]] = []
    requests = dm_word_requests or {}

    for word, dm_group in zip(input_words, dm_groups):
        sp_parts = sp_decomp.get(_norm_anusvara(word), [word])

        from_word_request = False
        if not dm_group and word in requests:
            dm_group = requests[word]
            from_word_request = True

        dm_forms = [t["form"] for t in dm_group]

        entry: Dict[str, Any] = {"pada": word}

        if dm_group:
            entry["dharmamitra"] = {
                "padaccheda": dm_forms,
                "words": [_dm_word_entry(t) for t in dm_group],
            }
            if from_word_request:
                entry["dharmamitra"]["request"] = "pada"
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


def postprocess(raw: Dict[str, Any], attest: Optional[Any] = None) -> Dict[str, Any]:
    """Condense raw engine output into the readable padaccheda/padas form.

    Args:
        raw: Raw multi-engine analysis output (multi-engine-analysis.json content)
        attest: optional dictionary lookup mapping a surface form to its kosha lemma stems, used to rank word
            readings (`collect_sp_morphology`); None keeps the previous ranking

    Returns:
        Processed dict with input, padaccheda and padas; plus engine_errors when an
        engine reported a failure instead of results. The raw document's 'mode' key is
        ignored — it is a command-line choice, not part of the reading.

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
    sp_morph = collect_sp_morphology(sp_output, attest)
    dm_tokens = collect_dm_tokens(dm_output)
    dm_requests = collect_dm_word_requests(dm_output)
    dm_groups, dm_unmatched = group_dm_tokens_by_word(input_words, dm_tokens)
    requested = [
        entry.get("pada")
        for entry in (dm_output.get("pada_followups") or [])
        if isinstance(entry, dict) and entry.get("pada")
    ]
    unused_requests = [p for p in requested if p not in dm_requests]
    if unused_requests:
        print(
            "Note: Dharmamitrā's word-level answers rebuilt none of these padas, so they were not used: "
            + " | ".join(str(p) for p in unused_requests),
            file=sys.stderr,
        )

    padas = build_padas(
        input_words,
        sp_decomp,
        sp_morph,
        dm_groups,
        bool(dm_tokens or dm_requests),
        dm_requests,
    )

    processed: Dict[str, Any] = {
        "input": inp,
        "padaccheda": build_padaccheda(padas),
        "padas": padas,
    }

    # Tokens Dharmamitrā returned that rebuild no pada of the verse — its own additions or leftovers.
    # They are recorded rather than quietly dropped so a reader can see what the engine produced.
    if dm_unmatched:
        processed["dharmamitra_unmatched"] = [t.get("form", "") for t in dm_unmatched]

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
    parser.add_argument(
        "--no-dictionary", action="store_true",
        help="Rank word readings without the vidyut kosha (older behaviour; faster, and no dictionary needed)",
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

    attest = None
    if not args.no_dictionary:
        try:
            from app import load_kosha

            attest = kosha_attest(load_kosha())
        except Exception as exc:
            print(f"Warning: word readings will not be checked against the dictionary: {exc}", file=sys.stderr)

    try:
        processed = postprocess(raw, attest)
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
