"""Golden-document tests for the four pinned verses in ``tests/data/results/``.

Each verse has a pinned pair: ``<stem>.raw.json`` (everything the engines produced) and
``<stem>.result.json`` (the condensed reading document). Two halves of this module:

* **Offline, always runs** — the pinned raw document must postprocess into exactly the
  pinned reading document, and both documents must satisfy the structural and semantic
  invariants recorded per verse below. Nothing here touches an engine or the network.
* **Live, opt-in** — set ``SAMSKRTA_LIVE_GOLDEN=1`` to re-run the real engines on each
  fixture and compare against the goldens. The raw document's ``dharmamitra`` and ``vidyut``
  subtrees must match byte-for-byte, and so must the reading document's Dharmamitra column,
  its metre summary and its pada sequence. ``engine_outputs.sanskrit_parser`` — and with it
  the reading document's sanskrit_parser column — is deliberately *not* compared: candidate
  order for tied splits and the vakya watchdog are process-dependent (see DOCUMENTATION §3),
  which a BG 18.66 regeneration showed (``mokṣe|iṣyā|āmi`` vs ``mokṣe|iṣi|āmi``). The live
  half also checks that an IAST fixture yields the same dharmamitra/vidyut subtrees as its
  Devanagari twin. It needs the Dharmamitra API, so it is excluded by default.

Regenerate the goldens with:

    uv run python app.py shloka -f pretty -i tests/data/<stem>.txt -o tests/data/results/<stem>
"""

import json
import os
from pathlib import Path

import pytest

import app
import postprocess_analysis

DATA_DIR = Path(__file__).resolve().parent / "data"
GOLDEN_DIR = DATA_DIR / "results"

VERSES = [
    "raghuvamsha-1.1",
    "raghuvamsha-1.2",
    "abhijnaana_shakuntala-1.1",
    "bhagavad_gita-18.66",
]

# What each verse must say. These are the reading document's own words, not a restatement
# of the golden file: they pin the analysis facts (word count, metre shape, and where the
# two compared engines disagree) so a wrong-but-self-consistent regeneration fails here.
EXPECTED = {
    "raghuvamsha-1.1": {
        "padas": 7,
        "aksharas_per_pada": [8, 8, 8, 8],
        "chandas_candidates": ["madalekhā", "śuddhavirāṭ"],
        "dharmamitra_padaccheda": (
            "vāc | arthau | iva | saṃpṛktau | vāc | artha | pratipattaye | jagantaḥ "
            "| pitarau | vande | pārvatī | parameśvarau"
        ),
        "sanskrit_parser_padaccheda": (
            "vāgarthās | viva | sampṛktau | vāgartha | pratipattaye | jagatas | pitarau "
            "| vande | pārvatī | parameśvarau"
        ),
    },
    "raghuvamsha-1.2": {
        "padas": 9,
        "aksharas_per_pada": [8, 8, 8, 8],
        "chandas_candidates": ["jaloddhatagati"],
        "dharmamitra_padaccheda": (
            "kva | sūrya | prabhavaḥ | vaṃśaḥ | kva | ca | alpa | viṣayā | matiḥ "
            "| titīrṣuḥ | dustaram | mohāt | uḍupena | asmi | sāgaram"
        ),
    },
    "abhijnaana_shakuntala-1.1": {
        # 21 aksharas per pāda: an odd total, so _split_into_padas keeps each line whole
        # rather than cutting it at a boundary no metre has.
        "padas": 26,
        "aksharas_per_pada": [21, 21, 21, 21],
        "chandas_candidates": ["sragdharā"],
    },
    "bhagavad_gita-18.66": {
        "padas": 10,
        "aksharas_per_pada": [8, 8, 8, 8],
        "chandas_candidates": ["mṛgī"],
        "dharmamitra_padaccheda": (
            "sarva | dharmān | parityajya | mām | ekam | śaraṇam | vraja | aham | tvām "
            "| sarva | pāpebhyaḥ | mokṣayiṣyāmi | mā | śucaḥ"
        ),
    },
}


def load_golden(stem: str, suffix: str) -> dict:
    path = GOLDEN_DIR / f"{stem}{suffix}"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Offline: the pinned pair must be self-consistent and complete
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stem", VERSES)
def test_golden_pair_exists(stem):
    raw = GOLDEN_DIR / f"{stem}.raw.json"
    result = GOLDEN_DIR / f"{stem}.result.json"
    assert raw.is_file() and raw.stat().st_size > 0, f"missing golden {raw}"
    assert result.is_file() and result.stat().st_size > 0, f"missing golden {result}"


@pytest.mark.parametrize("stem", VERSES)
def test_postprocess_reproduces_the_result_document(stem):
    """postprocess(pinned raw) must equal the pinned reading document exactly."""
    rebuilt = postprocess_analysis.postprocess(load_golden(stem, ".raw.json"))
    assert rebuilt == load_golden(stem, ".result.json")


@pytest.mark.parametrize("stem", VERSES)
def test_result_document_bytes_are_stable(stem, tmp_path):
    """Re-serializing the golden document through write_document reproduces its bytes."""
    document = load_golden(stem, ".result.json")
    target = tmp_path / f"{stem}.result.json"
    postprocess_analysis.write_document(target, document, indent=2)
    assert target.read_bytes() == (GOLDEN_DIR / f"{stem}.result.json").read_bytes()


@pytest.mark.parametrize("stem", VERSES)
def test_raw_document_shape(stem):
    raw = load_golden(stem, ".raw.json")
    assert list(raw) == ["input", "mode", "engine_outputs"]
    assert raw["mode"] == "shloka"
    assert set(raw["engine_outputs"]) == {"sanskrit_parser", "dharmamitra", "vidyut"}
    for engine, payload in raw["engine_outputs"].items():
        assert "error" not in payload, f"{engine} failed in the golden run: {payload['error']}"
    # Every engine gets Devanagari except Dharmamitra (IAST), and both scripts are kept.
    assert raw["input"]["script"] == "Devanagari"
    assert raw["input"]["devanagari"] != raw["input"]["iast"]


@pytest.mark.parametrize("stem", VERSES)
def test_result_document_shape(stem):
    result = load_golden(stem, ".result.json")
    expected = EXPECTED[stem]
    assert list(result) == ["mode", "input", "padaccheda", "padas", "chandas"]
    assert result["mode"] == "shloka"
    assert "engine_errors" not in result  # a clean golden run has no failed engine

    chandas = result["chandas"]
    assert chandas["aksharas_per_pada"] == expected["aksharas_per_pada"]
    assert chandas["candidates"] == expected["chandas_candidates"]
    assert chandas["pada_count"] == 4

    padas = result["padas"]
    assert len(padas) == expected["padas"]
    for pada in padas:
        assert set(pada) >= {"pada", "dharmamitra", "sanskrit_parser"}
        # A null side is real behaviour: Dharmamitra's token stream can run out at a
        # pada when it split an earlier word into more tokens than there were padas.
        sides = [s for s in (pada["dharmamitra"], pada["sanskrit_parser"]) if s is not None]
        assert sides, f"{pada['pada']}: neither engine contributed"
        for side in sides:
            assert isinstance(side["padaccheda"], list)

    padaccheda = result["padaccheda"]
    for key, value in (
        ("dharmamitra_padaccheda", "dharmamitra"),
        ("sanskrit_parser_padaccheda", "sanskrit_parser"),
    ):
        if key in expected:
            assert padaccheda[value] == expected[key]


# ---------------------------------------------------------------------------
# Offline: per-verse edge cases the corpus is meant to cover
# ---------------------------------------------------------------------------

def test_raghuvamsha_one_dot_one_keeps_both_readings_of_the_compound():
    """वागर्थाविव: Dharmamitra splits it to the word boundary, sanskrit_parser does not."""
    result = load_golden("raghuvamsha-1.1", ".result.json")
    pada = next(p for p in result["padas"] if p["pada"] == "vāgarthāviva")
    assert pada["dharmamitra"]["padaccheda"] == ["vāc", "arthau", "iva"]
    assert pada["sanskrit_parser"]["padaccheda"] == ["vāgarthās", "viva"]
    assert pada["differences"], "a disagreement this large must be recorded"


def test_raghuvamsha_one_dot_two_lets_the_token_streams_drift_apart():
    """मोहादुडुपेन: Dharmamitra's tokens were spent on earlier padas, so its side is null
    while sanskrit_parser still offers a three-word reading; most padas disagree."""
    result = load_golden("raghuvamsha-1.2", ".result.json")
    pada = next(p for p in result["padas"] if p["pada"] == "mohāduḍupenāsmi")
    assert pada["dharmamitra"] is None, "the drift must stay visible as a null side"
    assert pada["sanskrit_parser"]["padaccheda"] == ["mohāt", "uḍupena", "asmi"]
    differing = [p for p in result["padas"] if p.get("differences")]
    assert len(differing) >= 5


def test_abhijnaana_shakuntala_keeps_odd_length_padas_whole():
    """21-akshara pādas: vidyut classifies the whole line, and the long verse still aligns."""
    result = load_golden("abhijnaana_shakuntala-1.1", ".result.json")
    assert result["chandas"]["pada_count"] == 4
    assert result["chandas"]["classified_pada_count"] >= 1
    dm_words = [w for p in result["padas"] if p["dharmamitra"] for w in p["dharmamitra"]["words"]]
    assert len(dm_words) > 30, "a four-line verse must yield its full token stream"


def test_bhagavad_gita_eighty_sixty_six_pins_the_harder_split():
    """मामेकं and मोक्षयिष्यामि: Dharmamitra reads whole words where the splitter fragments.

    Dharmamitra's extra token is attached to the preceding pada, so each pin names the
    pada that actually carries it.
    """
    result = load_golden("bhagavad_gita-18.66", ".result.json")
    assert "mām | ekam" in result["padaccheda"]["dharmamitra"]
    assert "māme | akam" in result["padaccheda"]["sanskrit_parser"]

    pada = next(p for p in result["padas"] if p["pada"] == "māmekaṃ")
    assert pada["dharmamitra"]["padaccheda"] == ["ekam"]
    assert pada["sanskrit_parser"]["padaccheda"] == ["māme", "akam"]

    pada = next(p for p in result["padas"] if p["pada"] == "mokṣayiṣyāmi")
    assert pada["dharmamitra"]["padaccheda"][0] == "mokṣayiṣyāmi"
    assert len(pada["sanskrit_parser"]["padaccheda"]) > 1, "the splitter must fragment it"


# ---------------------------------------------------------------------------
# Live: regenerate the pair with the real engines (SAMSKRTA_LIVE_GOLDEN=1)
# ---------------------------------------------------------------------------

live = pytest.mark.skipif(
    os.environ.get("SAMSKRTA_LIVE_GOLDEN") != "1",
    reason="set SAMSKRTA_LIVE_GOLDEN=1 to re-run the real engines against the goldens",
)


def run_app(tmp_path: Path, fixture: str, base_stem: str) -> int:
    """Run app.main() on a fixture, writing the pair under tmp_path."""
    argv = [
        "app.py",
        "shloka",
        "-f",
        "pretty",
        "-i",
        str(DATA_DIR / fixture),
        "-o",
        str(tmp_path / base_stem),
    ]
    import sys

    original = sys.argv
    sys.argv = argv
    try:
        return app.main()
    finally:
        sys.argv = original


def subtree(document: dict, engine: str) -> str:
    payload = document["engine_outputs"].get(engine) or {}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


@live
@pytest.mark.parametrize("stem", VERSES)
def test_live_run_reproduces_the_golden_pair(stem, tmp_path, capsys):
    rc = run_app(tmp_path, f"{stem}.txt", stem)
    assert rc == 0, "a live golden run must not lose a local engine"

    raw_file = postprocess_analysis.raw_path(str(tmp_path / stem))
    result_file = postprocess_analysis.result_path(str(tmp_path / stem))
    assert raw_file.is_file() and result_file.is_file()

    generated_raw = json.loads(raw_file.read_text(encoding="utf-8"))
    golden_raw = load_golden(stem, ".raw.json")

    # Dharmamitra and vidyut are stable; sanskrit_parser's candidates (and therefore the
    # reading document's splitter column) are not — see DOCUMENTATION §3.
    for engine in ("dharmamitra", "vidyut"):
        assert subtree(generated_raw, engine) == subtree(golden_raw, engine), engine
    generated_result = json.loads(result_file.read_text(encoding="utf-8"))
    golden_result = load_golden(stem, ".result.json")
    assert generated_result["padaccheda"]["dharmamitra"] == golden_result["padaccheda"]["dharmamitra"]
    assert [p["pada"] for p in generated_result["padas"]] == [p["pada"] for p in golden_result["padas"]]
    assert generated_result["chandas"] == golden_result["chandas"]

    sp = generated_raw["engine_outputs"]["sanskrit_parser"]
    assert sp["sandhi_splits"], "the splitter must still produce line-level candidates"
    # One decomposition per distinct pada of the golden reading document; the dict is keyed by
    # surface form, so repeated words (kva … kva) share one entry. Keys come from sanskrit_parser's
    # own transliteration, hence the anusvara normalization on both sides.
    decomposed = {postprocess_analysis._norm_anusvara(w) for w in sp["word_decompositions"]}
    padas = {postprocess_analysis._norm_anusvara(p["pada"]) for p in golden_result["padas"]}
    assert decomposed == padas
    assert all(parts for parts in sp["word_decompositions"].values())

    printed = json.loads(capsys.readouterr().out)
    assert list(printed) == ["input", "chandas"]


@live
@pytest.mark.parametrize("stem", VERSES)
def test_live_iast_fixture_agrees_with_the_devanagari_golden(stem, tmp_path):
    """The IAST twin must reach the same Dharmamitra and vidyut subtrees."""
    rc = run_app(tmp_path, f"{stem}.iast.txt", f"{stem}-iast")
    assert rc == 0

    raw_file = postprocess_analysis.raw_path(str(tmp_path / f"{stem}-iast"))
    generated = json.loads(raw_file.read_text(encoding="utf-8"))
    golden_raw = load_golden(stem, ".raw.json")
    for engine in ("dharmamitra", "vidyut"):
        assert subtree(generated, engine) == subtree(golden_raw, engine), engine
