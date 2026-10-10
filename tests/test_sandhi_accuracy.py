"""Accuracy regression tests for offline sandhi splitting.

`app._best_word_split` turns one fused pada into words without any context, so its quality has
to be measured against something. That reference is ``tests/data/sandhi_truth.json``: 147 padas
from the sixteen pinned verses, split as a human reader splits them. Provenance differs by group.
The nine older verses (Raghuvaṃśa 1.1–1.7, Gītā 18.66, Śākuntala 1.1) started from Dharmamitra's
answer — it resolves sandhi with sentence context — and were then corrected word by word against the
published padaccheda tables: Dharmamitra gets word boundaries right but not realizations ('jagantaḥ'
for जगतः, 'upahāsya | tām' where the elision belongs inside 'upahāsyatām'). The seven newer verses
(Gītā 2.22, 2.47, 11.15, 15.5, 15.15, Śākuntala 1.7, 1.18) take Dharmamitra's reading after it was
checked against the published पदच्छेदः of that very verse and found to give the same words in the
same order; transparent compounds are listed as their members there too. Those rows therefore test
sanskrit_parser against Dharmamitra, not against an independent human reading — DOCUMENTATION §9
keeps the Dharmamitra-vs-reference figures to the nine hand-corrected verses for that reason.

Two tests:

* an always-on set of padas covering each decision the ranking exists to get right — a finite
  verb must survive whole, an elided conjunction must be separated, and a compound boundary in
  the middle of a pada must not dissolve into word fragments;
* a gated corpus score (`SAMSKRTA_LIVE_GOLDEN=1`, ~25 s): the ranking must keep at least
  `MIN_MATCHES` of the padas. Ranking rules were tuned against that number — making dictionary
  attestation the primary key instead of standalone morphology raises the score from 92/147 to 99,
  and the transparent-compound gate in `_best_word_split` raises it to 103; every ranking that
  splits deeper than that (frequency-summed parts, "prefer more parts") loses padas or more to
  fragments such as `mat | is`. The candidate pool sanskrit_parser generates contains a
  reference-consistent split for 127/147, which is the ceiling any per-word ranking can reach.
"""

import itertools
import json
import os
from pathlib import Path

import pytest

import app

DATA_DIR = Path(__file__).resolve().parent / "data"

TRUTH = json.loads((DATA_DIR / "sandhi_truth.json").read_text(encoding="utf-8"))
BY_PADA = {row["pada"]: row for row in TRUTH}

# Padas chosen for the distinct failure modes the ranking fixes, not sampled at random.
CURATED_PADAS = [
    "mokṣayiṣyāmi",            # finite verb stays one word
    "gamiṣyāmyupahāsyatām",    # verb + noun boundary, no re-analysis of the verb ending
    "haviryā",                 # havis | yā: the elided 'a' belongs to the stem, not a fragment
    "prapannastanubhiravatu",  # three words; 'avatu' must not become ava | tu
    "sūtrasyevāsti",           # sūtrasya | iva | asti: genitive + indeclinable + verb
    "sarvadharmānparityajya",  # long pada split only at real word boundaries
    "saṃpṛktau",               # a dual the dictionary knows whole must not be cut
    "saṅgo'stvakarmaṇi",       # elision at the pāda end: saṅgaḥ | astu | akarmaṇi
    "jīrṇānyanyāni",           # two inflected words joined across a printed pāda hyphen
    "vāmanaḥ",                 # a single attested word must not be split at all
    "sūryaprabhavo",           # compound the dictionary knows, yet read as sūrya | prabhavaḥ
    "vajrasamutkīrṇe",         # long compound cut at its real member boundary, not into shards
    "saṃbhṛtārthānāṃ",        # genitive plural compound: saṃbhṛta | arthānām
]

# Scored over the whole fixture by the gated test: **104/147** with dictionary-validated ranking (dictionary
# attestation as the primary key reaches 99, and the transparent-compound gate carries it past 100), 92/147 without any
# dictionary, and a candidate-pool ceiling of 127. The floor keeps margin because processes rank tied candidates
# differently — the pool itself was measured to drift by one pada between runs, historically 99–104.
MIN_MATCHES = 101

live = pytest.mark.skipif(
    os.environ.get("SAMSKRTA_LIVE_GOLDEN") != "1",
    reason="set SAMSKRTA_LIVE_GOLDEN=1 to score all curated padas (needs sanskrit_parser)",
)


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def splitter():
    """Return split(iast_pada) -> list of IAST parts, loading the engines once per module."""
    from indic_transliteration import sanscript
    from sanskrit_parser.api import Parser

    parser = Parser(output_encoding=sanscript.DEVANAGARI)
    kosha = app.load_kosha()
    if kosha is None:
        pytest.skip("vidyut kosha data is not installed")

    def split(pada_iast: str):
        devanagari = sanscript.transliterate(pada_iast, sanscript.IAST, sanscript.DEVANAGARI)
        return app._best_word_split(parser, devanagari, kosha)

    return split


def lemma_stems(kosha, form_iast: str) -> frozenset:
    """Kosha lemma stems reachable from a surface form; the form itself when unattested.

    Two lookups are merged. The plain one is the form as written. The folded one tolerates the
    word-final orthography the engines choose among themselves — sanskrit_parser writes anusvara
    as 'M' and a final visarga as 's', while the curated reading spells the same stem with the
    sign it had before sandhi ('pāpebhyaḥ' vs 'pāpebhyas'). Without that second lookup a correct
    split is scored as wrong.

    Args:
        kosha: Kosha instance
        form_iast: IAST word form

    Returns:
        Frozenset of SLP1 stems the dictionary associates with the form
    """
    from indic_transliteration import sanscript

    slp1 = sanscript.transliterate(form_iast, sanscript.IAST, sanscript.SLP1)
    folded = "".join({"M": "m", "H": "s"}.get(ch, ch) for ch in slp1).rstrip("sr")
    infos = [app._kosha_entry_info(entry) for entry in app.kosha_lookup(kosha, slp1)]
    infos += [app._kosha_entry_info(entry) for entry in app.kosha_lookup(kosha, folded)]
    stems = {info["stem"] for info in infos if info}
    return frozenset(stems or {slp1, folded})


def pada_matches(kosha, parts, expected_parts) -> bool:
    """Score one split against the curated reading of that pada.

    Args:
        kosha: Kosha instance used for stem comparison
        parts: parts produced by the splitter
        expected_parts: curated parts

    Returns:
        True when every part pairs with exactly one expected part and each pair shares a kosha
        lemma stem (equal part count implied)
    """
    if len(parts) != len(expected_parts):
        return False
    got = [lemma_stems(kosha, part) for part in parts]
    want = [lemma_stems(kosha, part) for part in expected_parts]
    size = len(got)
    return any(all(got[i] & want[order[i]] for i in range(size))
               for order in itertools.permutations(range(size)))


# ---------------------------------------------------------------------------
# Always-on: the specific decisions the ranking exists to make
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pada", CURATED_PADAS)
def test_curated_pada_is_split_like_a_reader(splitter, pada):
    """Compare parts by kosha lemma stem rather than spelling."""
    row = BY_PADA[pada]
    parts = splitter(pada)
    assert pada_matches(app.load_kosha(), parts, row["parts"]), f"{pada}: got {parts}"


# ---------------------------------------------------------------------------
# Gated: corpus-wide score against the curated padaccheda
# ---------------------------------------------------------------------------

@live
def test_corpus_score_meets_the_pinned_floor(splitter):
    kosha = app.load_kosha()
    assert kosha is not None, "vidyut kosha data is required for scoring"
    matched = [row["pada"] for row in TRUTH if pada_matches(kosha, splitter(row["pada"]), row["parts"])]
    missed = sorted(row["pada"] for row in TRUTH if row["pada"] not in matched)
    assert len(matched) >= MIN_MATCHES, (
        f"{len(matched)}/{len(TRUTH)} padas match the curated reading; missed: {missed}"
    )
