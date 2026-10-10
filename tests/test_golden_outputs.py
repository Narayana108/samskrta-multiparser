"""Golden-document tests for the sixteen pinned verses in ``tests/data/results/``.

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
  order for tied splits is process-dependent (see DOCUMENTATION §3), which a BG 18.66
  regeneration showed (``mokṣe|iṣyā|āmi`` vs ``mokṣe|iṣi|āmi``). The live half also checks that
  an IAST fixture yields the same dharmamitra/vidyut subtrees as its Devanagari twin.
  It needs the Dharmamitra API, so it is excluded by default.

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
    "raghuvamsha-1.3",
    "raghuvamsha-1.4",
    "raghuvamsha-1.5",
    "raghuvamsha-1.6",
    "raghuvamsha-1.7",
    # Bhagavad Gītā: anuṣṭubh (2.47) and jagatī-family pādas of eleven aksharas (2.22, 11.15,
    # 15.5, 15.15), with avagraha elisions and printed hyphens at the pāda junctions.
    "bhagavad_gita-2.22",
    "bhagavad_gita-2.47",
    "bhagavad_gita-11.15",
    "bhagavad_gita-15.5",
    "bhagavad_gita-15.15",
    # Abhijñānaśākuntala in sragdharā (21 aksharas per pāda) and mālinī (15).
    "abhijnaana_shakuntala-1.7",
    "abhijnaana_shakuntala-1.18",
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
            "vāgartha | āviva | sampṛktau | vāgartha | pratipattaye | jagatas | pitarau "
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
    # Raghuvaṃśa 1.3–1.7 come from the same recension whose padaccheda tables are published at
    # sanskritsahitya.org; they widen the corpus past the two-word disagreements of 1.1–1.2 to
    # elision (upahāsyatām), avagraha (vaṃśeऽस्मिन्) and long yathā-compounds.
    "raghuvamsha-1.3": {
        "padas": 7,
        "aksharas_per_pada": [8, 8, 8, 8],
        "chandas_candidates": ["vasumatī"],
        "dharmamitra_padaccheda": (
            "mandaḥ | kavi | yaśaḥ | prārthī | gamiṣyāmi | upahāsya | tām | prāṃśu | labhye "
            "| phale | lobhāt | udbāhuḥ | iva | vāmanaḥ"
        ),
        # sanskrit_parser reads the elision inside one word, which is right where Dharmamitra
        # invents a 'tām' that no sandhi rule produced.
        "sanskrit_parser_padaccheda": (
            "mandas | kavi | yaśas | prārthī | gamiṣyāmi | upahāsyatām | prāṃśulabhye | phale "
            "| lobhāt | udbāhus | iva | vāmanas"
        ),
    },
    "raghuvamsha-1.4": {
        "padas": 9,
        "aksharas_per_pada": [8, 8, 8, 8],
        "chandas_candidates": [],
        "dharmamitra_padaccheda": (
            "atha | vā | kṛta | vāgdvāre | vaṃśe | asmin | pūrva | sūribhiḥ | maṇau | vajra "
            "| samutkīrṇe | sūtrasya | iva | asti | mama | gatiḥ"
        ),
        # The avagraha token reaches sanskrit_parser fused and it splits it into three words; the
        # long compound is cut at its member boundary by the transparent-compound gate.
        "sanskrit_parser_padaccheda": (
            "atha | vā | kṛta | vāgdvāre | vaṃśe | asmin | pūrvasūribhis | maṇau "
            "| vajra | samutkīrṇe | sūtrasya | iva | asti | me | gatis"
        )
    },
    "raghuvamsha-1.5": {
        # One pada per half-line here: the verse is written as two long fused compounds, and
        # neither engine's token stream reaches the compound members (see DOCUMENTATION §3).
        "padas": 2,
        "aksharas_per_pada": [8, 8, 8, 8],
        "chandas_candidates": [],
        "dharmamitra_padaccheda": (
            "saḥ | aham | ājanma | śuddhānām | āphala | udaya | karmaṇām | ā | samudra "
            "| kṣitīśānām | ānāka | ratha | vartmanām"
        ),
    },
    "raghuvamsha-1.6": {
        "padas": 4,
        "aksharas_per_pada": [8, 8, 8, 8],
        "chandas_candidates": ["upasthita"],
        "dharmamitra_padaccheda": (
            "yathāvidhi | huta | agnīnām | yathākāma | arcita | arthinām | yathā | aparādha "
            "| daṇḍānām | yathā | kāla | prabodhinām"
        ),
    },
    "raghuvamsha-1.7": {
        "padas": 8,
        "aksharas_per_pada": [8, 8, 8, 8],
        "chandas_candidates": [],
        "dharmamitra_padaccheda": (
            "tyāgāya | saṃbhṛta | arthānām | satyāya | mita | bhāṣiṇām | yaśase | vijigīṣūṇām "
            "| prajāyai | gṛhamedhinām"
        ),
    },
    # Bhagavad Gītā 2.22 and 15.5 are printed with a hyphen where the word runs over the pāda
    # junction (… विहाय जीर्णान्य्- / अन्यानि …). `preprocess_input` closes that break, so the engines get
    # one continuous sandhi word — which is what yields four 11-akshara pādas instead of leaving a
    # virama-final fragment no rule can decompose. vidyut's 145-vṛtta table has no anuṣṭubh, so the
    # candidates below are the jagatī-family metres its scanner accepts for these pādas.
    "bhagavad_gita-2.22": {
        "padas": 14,
        "aksharas_per_pada": [11, 11, 11, 11],
        "chandas_candidates": ["indravajrā", "indravaṃśā", "upendravajrā", "vaṃśastha"],
        # Dharmamitrā's verse-level pass skipped the two opening words; each got a request of its own,
        # so the reading line covers the whole verse and those sides carry 'request': 'pada'.
        "dharmamitra_padaccheda": (
            "vāsāṃsi | jīrṇāni | yathā | vihāya | navāni | gṛhṇāti | naraḥ | aparāṇi | tathā "
            "| śarīrāṇi | vihāya | jīrṇāni | anyāni | saṃyāti | navāni | dehī"
        ),
        # Both engines cut the joined word at the pāda boundary; only sanskrit_parser also cuts the
        # finite verb sam + yāti.
        "sanskrit_parser_padaccheda": (
            "vāsāṃsi | jīrṇāni | yathā | vihāya | navāni | gṛhṇāti | naras | aparāṇi "
            "| tathā | śarīrāṇi | vihāya | jīrṇāni | anyāni | sam | yāti | navāni | dehī"
        ),
    },
    "bhagavad_gita-2.47": {
        "padas": 8,
        "aksharas_per_pada": [8, 8, 8, 8],
        "chandas_candidates": ["candralekhā", "vasumatī"],
        # Dharmamitrā put a 'mā' ahead of the first pāda, where it rebuilds no pada; it is reported in
        # 'dharmamitra_unmatched' instead of being filed under a word. Everything else matches the
        # published padaccheda (karmaṇi | eva | adhikāraḥ | te … saṅgaḥ | astu | akarmaṇi).
        "dharmamitra_padaccheda": (
            "karmaṇi | eva | adhikāraḥ | te | mā | phaleṣu | kadācana | mā | karma | phala "
            "| hetuḥ | bhūḥ | mā | te | saṅgaḥ | astu | akarmaṇi"
        ),
        "dharmamitra_unmatched": ["mā"],
        # sanskrit_parser's reading of the first pāda is not pinned: eight back-to-back runs on this
        # one line produced six different cuts (karmaṇye | vā, karmaṇye | ava, karmaṇi | eva,
        # karmaṇyā | iva …, with adhikāras | te or adhikāra | ste), because its sandhi graph has many
        # equally-scored paths and gensim/sentencepiece — the lexical scorer it would rank them with —
        # is not installed here (it says so on every run). Pinning one arbitrary draw would freeze a
        # coin flip; DOCUMENTATION §9 records the instability, and tests/test_sandhi_accuracy.py counts
        # this pada as a miss or a match per run instead.
    },
    "bhagavad_gita-11.15": {
        "padas": 11,
        "aksharas_per_pada": [11, 11, 11, 11],
        "chandas_candidates": ["indravajrā", "indravaṃśā", "upendravajrā"],
        "dharmamitra_padaccheda": (
            "paśyāmi | devān | tava | deva | dehe | sarvān | tathā | bhūta | viśeṣa "
            "| saṅghān | brahmāṇam | īśam | kamalāsana | stham | ṛṣīn | ca | sarvān "
            "| uragān | ca | divyān"
        ),
        # deva + dehe becomes devām | stava. The gate's attestation tie now cuts the karmadhāraya at the wrong
        # joint — kamalā | āsanastham instead of Dharmamitrā's kamalāsana | stham, still counted as a miss.
        "sanskrit_parser_padaccheda": (
            "paśyāmi | devām | stava | deva | dehe | sarvān | tathā | bhūta | viśeṣa "
            "| saṅghān | brahmāṇam | īśam | kamalā | āsanastham | ṛṣīn | ca | sarvān | uragān "
            "| ca | divyān"
        ),
    },
    "bhagavad_gita-15.5": {
        "padas": 8,
        "aksharas_per_pada": [11, 11, 11, 11],
        "chandas_candidates": ["indravajrā", "indravaṃśā"],
        # The hyphenated junction leaves one long word; Dharmamitra walks it, sanskrit_parser
        # fragments saṃjñaiḥ into san | jñais (counted as a miss by the accuracy floor).
        "dharmamitra_padaccheda": (
            "nirmāna | mohāḥ | jita | saṅga | doṣāḥ | adhyātma | nityāḥ | vinivṛtta "
            "| kāmāḥ | dvandvaiḥ | vimuktāḥ | sukha | duḥkha | saṃjñaiḥ | gacchanti "
            "| amūḍhāḥ | padam | avyayam | tat"
        ),
        "sanskrit_parser_padaccheda": (
            "nirmānam | ohā | jitasaṅga | doṣā | adhyātma | nityā | vinivṛttakāmās "
            "| dvandvais | vimuktās | sukhaduḥkha | san | jñais | gacchantī | amūḍhās "
            "| padam | avyayam | tat"
        ),
    },
    "bhagavad_gita-15.15": {
        "padas": 12,
        "aksharas_per_pada": [11, 11, 11, 11],
        "chandas_candidates": ["indravajrā", "indravaṃśā"],
        "dharmamitra_padaccheda": (
            "sarvasya | ca | aham | hṛdi | sanniviṣṭaḥ | mattaḥ | smṛtiḥ | jñānam "
            "| apohanam | ca | vedaiḥ | ca | sarvaiḥ | aham | eva | vedyaḥ | vedānta "
            "| kṛt | veda | vid | eva | ca | aham"
        ),
        # ca + aham twice becomes cās | ham — the reading document keeps it visible.
        "sanskrit_parser_padaccheda": (
            "sarvasya | cās | ham | hṛdi | sanni | viṣṭas | mattas | smṛtis | jñānam "
            "| apohanam | ca | vedais | ca | sarvais | raham | eva | vedyas | vedāntakṛt "
            "| veda | videva | cās | ham"
        ),
    },
    # Abhijñānaśākuntala 1.7 (sragdharā, 21 aksharas per pāda) and 1.18 (mālinī, 15): long
    # kavya compounds on odd-length pādas that vidyut still classifies confidently.
    "abhijnaana_shakuntala-1.7": {
        "padas": 15,
        "aksharas_per_pada": [21, 21, 21, 21],
        "chandas_candidates": ["sragdharā"],
        # Dharmamitrā's verse pass never reached the opening compound; a request for that one pada
        # supplied it, so every word of this sragdharā verse is covered. sanskrit_parser cuts
        # grīvās | bhaṅgā and — since the gate accepts an attestation tie — paścā | ardhena as well.
        "dharmamitra_padaccheda": (
            "grīvā | bhaṅga | abhirāmam | muhur | anupatati | syandane | baddha | dṛṣṭiḥ "
            "| paśca | ardhena | praviṣṭaḥ | śara | patana | bhayāt | bhūyasā | pūrva | kāyam "
            "| darbhaiḥ | ardha | avalīḍhaiḥ | śrama | vivṛta | mukha | bhraṃśibhiḥ | kīrṇa "
            "| vartmā | paśya | udagra | pluta | tvāt | viyati | bahutaram | stokam | urvyām "
            "| prayāti"
        ),
        "sanskrit_parser_padaccheda": (
            "grīvās | bhaṅgā | abhirāmam | muhur | anu | patati | syandane | baddha "
            "| dṛṣṭis | paścā | ardhena | praviṣṭas | śara | patana | bhayāt | bhūyasā | pūrva "
            "| kāyam | darbhais | ardhāvalīḍhais | śrama | vivṛtam | ukha | bhraṃśibhis "
            "| kīrṇa | vartmā | paśya | udagraplutatvāt | viyati | bahutaram | stokam "
            "| urvyām | prayāti"
        ),
    },
    "abhijnaana_shakuntala-1.18": {
        "padas": 15,
        "aksharas_per_pada": [15, 15, 15, 15],
        "chandas_candidates": ["malinī"],
        # na + ākṛtīnām: Dharmamitra keeps the long vowel of the noun, sanskrit_parser moves it
        # into the negation (nā | kṛtīnām).
        "dharmamitra_padaccheda": (
            "sarasijam | anuviddham | śaivalena | api | ramyam | malinam | api | himāṃśoḥ "
            "| lakṣma | lakṣmīm | tanoti | iyam | adhika | manojñā | valkalena | api "
            "| tanvī | kim | iva | hi | madhurāṇām | maṇḍanam | na | ākṛtīnām"
        ),
        "sanskrit_parser_padaccheda": (
            "sarasijam | anuviddham | śaivale | anāpi | ramyam | malinam | api | himāṃśos "
            "| lakṣma | lakṣmīm | tanoti | iyam | adhika | manojñā | valkale | anāpi | tanvī "
            "| kim | iva | hi | madhurāṇām | maṇḍanam | nā | kṛtīnām"
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



@pytest.fixture(scope="session")
def dictionary_attest():
    """The dictionary lookup the reading documents are ranked with.

    Word readings are ordered with vidyut's kosha in the loop (`postprocess_analysis.kosha_attest`), so a pinned
    reading document is reproducible only when that data is present; it is a pinned dependency of this repo, so
    its absence is an error rather than a skip.
    """
    helper = postprocess_analysis.kosha_attest(app.load_kosha())
    if helper is None:
        pytest.fail("vidyut kosha data (VIDYUT_DATA_DIR) is required to reproduce the pinned readings")
    return helper


@pytest.mark.parametrize("stem", VERSES)
def test_postprocess_reproduces_the_result_document(stem, dictionary_attest):
    """postprocess(pinned raw) must equal the pinned reading document exactly."""
    rebuilt = postprocess_analysis.postprocess(load_golden(stem, ".raw.json"), dictionary_attest)
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
    assert list(raw) == ["input", "engine_outputs"]
    assert set(raw["engine_outputs"]) == {"sanskrit_parser", "dharmamitra", "vidyut"}
    for engine, payload in raw["engine_outputs"].items():
        assert "error" not in payload, f"{engine} failed in the golden run: {payload['error']}"
    # Both working scripts are recorded, cleaned of separators and numbering; which romanization
    # the user typed is not part of either document.
    assert list(raw["input"]) == ["devanagari", "iast"]
    noise = "।॥0123456789०१२३४५६७८९"
    for field in raw["input"].values():
        assert not any(char in field for char in noise), f"{field!r} still holds numbering"
    assert raw["input"]["devanagari"] != raw["input"]["iast"]


@pytest.mark.parametrize("stem", VERSES)
def test_result_document_shape(stem):
    result = load_golden(stem, ".result.json")
    expected = EXPECTED[stem]
    extra = ["dharmamitra_unmatched"] if "dharmamitra_unmatched" in expected else []
    assert list(result) == ["input", "padaccheda", "padas", *extra, "chandas"]
    assert result.get("dharmamitra_unmatched", []) == expected.get("dharmamitra_unmatched", [])
    assert "engine_errors" not in result  # a clean golden run has no failed engine

    chandas = result["chandas"]
    assert chandas["aksharas_per_pada"] == expected["aksharas_per_pada"]
    assert chandas["candidates"] == expected["chandas_candidates"]
    assert chandas["pada_count"] == 4

    padas = result["padas"]
    assert len(padas) == expected["padas"]
    for pada in padas:
        assert set(pada) >= {"pada", "dharmamitra", "sanskrit_parser"}
        # A null side stays real behaviour: Dharmamitrā can skip a word in the verse pass, and its own
        # word-level request may still return nothing usable for that pada.
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
    """वागर्थाविव: Dharmamitra reads three words (vāk | arthau | iva), sanskrit_parser cuts the
    compound in two but keeps the dual ending fused; neither reading is discarded."""
    result = load_golden("raghuvamsha-1.1", ".result.json")
    pada = next(p for p in result["padas"] if p["pada"] == "vāgarthāviva")
    assert pada["dharmamitra"]["padaccheda"] == ["vāc", "arthau", "iva"]
    assert pada["sanskrit_parser"]["padaccheda"] == ["vāgartha", "āviva"]
    assert pada["differences"], "a disagreement this large must be recorded"


def test_raghuvamsha_one_dot_two_keeps_every_word_on_its_own_pada():
    """मोहादुडुपेन: both engines read mohāt | uḍupena | asmi.

    The greedy aligner this corpus used to pin spent Dharmamitrā's tokens on earlier padas and left
    this pada null; the coverage DP keeps every token inside a word it can rebuild, so five of the
    nine padas differ instead of most of them."""
    result = load_golden("raghuvamsha-1.2", ".result.json")
    pada = next(p for p in result["padas"] if p["pada"] == "mohāduḍupenāsmi")
    assert pada["dharmamitra"]["padaccheda"] == ["mohāt", "uḍupena", "asmi"]
    assert pada["sanskrit_parser"]["padaccheda"] == ["mohāt", "uḍupena", "asmi"]
    assert "differences" not in pada, "the two engines say the same thing here"
    differing = [p for p in result["padas"] if p.get("differences")]
    assert len(differing) == 5


def test_abhijnaana_shakuntala_keeps_odd_length_padas_whole():
    """21-akshara pādas: vidyut classifies the whole line, and the long verse still aligns."""
    result = load_golden("abhijnaana_shakuntala-1.1", ".result.json")
    assert result["chandas"]["pada_count"] == 4
    assert result["chandas"]["classified_pada_count"] >= 1
    dm_words = [w for p in result["padas"] if p["dharmamitra"] for w in p["dharmamitra"]["words"]]
    assert len(dm_words) > 30, "a four-line verse must yield its full token stream"


def test_bhagavad_gita_eighty_sixty_six_pins_the_harder_split():
    """मामेकं and मोक्षयिष्यामि: one split the offline ranker now gets right, one it does not.

    Dharmamitrā's 'mām' is filed on its own pada, where the published padaccheda puts it (mām | ekam).
    The finite verb survives whole because every candidate that cut it lost on dictionary attestation;
    the fused `mām + ekam` is still mis-cut by sanskrit_parser, which the accuracy floor in
    ``tests/test_sandhi_accuracy.py`` counts as a miss rather than hiding.
    """
    result = load_golden("bhagavad_gita-18.66", ".result.json")
    assert "mām | ekam" in result["padaccheda"]["dharmamitra"]
    assert "māme | akam" in result["padaccheda"]["sanskrit_parser"]

    pada = next(p for p in result["padas"] if p["pada"] == "māmekaṃ")
    assert pada["dharmamitra"]["padaccheda"] == ["mām", "ekam"]
    assert pada["sanskrit_parser"]["padaccheda"] == ["māme", "akam"]

    pada = next(p for p in result["padas"] if p["pada"] == "mokṣayiṣyāmi")
    assert pada["dharmamitra"]["padaccheda"][0] == "mokṣayiṣyāmi"
    assert pada["sanskrit_parser"]["padaccheda"] == ["mokṣayiṣyāmi"], (
        "an attested finite verb must not be fragmented into mokṣe | iṣi | yāmi"
    )


def test_raghuvamsha_one_dot_three_keeps_each_token_on_the_word_it_rebuilds():
    """गमिष्याम्युपहास्यताम्: Dharmamitrā invents a 'tām'; the offline ranker keeps one word.

    Its 'prāṃśu' belongs to the following pada and now stays there, so the streams no longer drift
    across padas; the invented 'tām' is still shown here because it does rebuild part of this word."""
    result = load_golden("raghuvamsha-1.3", ".result.json")
    pada = next(p for p in result["padas"] if p["pada"] == "gamiṣyāmyupahāsyatām")
    assert pada["dharmamitra"]["padaccheda"] == ["gamiṣyāmi", "upahāsya", "tām"]
    assert pada["sanskrit_parser"]["padaccheda"] == ["gamiṣyāmi", "upahāsyatām"]
    # Dharmamitrā decomposes the compound in the next pada while sanskrit_parser leaves it whole.
    pada = next(p for p in result["padas"] if p["pada"] == "prāṃśulabhye")
    assert pada["dharmamitra"]["padaccheda"] == ["prāṃśu", "labhye"]
    assert pada["sanskrit_parser"]["padaccheda"] == ["prāṃśulabhye"]


def test_raghuvamsha_one_dot_four_splits_the_avagraha_token_without_restoring_the_vowel():
    """वंशेऽस्मिन्पूर्वसूरिभिः: the avagraha stays in the token, so the meter keeps 8 aksharas."""
    result = load_golden("raghuvamsha-1.4", ".result.json")
    assert result["chandas"]["aksharas_per_pada"] == [8, 8, 8, 8], (
        "restoring the elided 'a' would report a 9-akshara pāda"
    )
    pada = next(p for p in result["padas"] if p["pada"].startswith("vaṃśe"))
    assert pada["sanskrit_parser"]["padaccheda"] == ["vaṃśe", "asmin", "pūrvasūribhis"]


def test_bhagavad_gita_two_twenty_two_closes_the_printed_pada_break():
    """तथा शरीराणि विहाय जीर्णान्य्- / अन्यानि …: the edition's hyphen marks one word, not two fragments.

    The fixture keeps the printed line break and `preprocess_input` closes it, so both engines see the
    continuous sandhi form and cut it at the pāda boundary as ``jīrṇāni | anyāni``. Handing an engine a
    virama-final ``जীর্ণान्य्`` instead would report five pādas for this verse and leave a token nothing
    can decompose.
    """
    result = load_golden("bhagavad_gita-2.22", ".result.json")
    assert result["chandas"]["pada_count"] == 4
    assert result["chandas"]["aksharas_per_pada"] == [11, 11, 11, 11]
    pada = next(p for p in result["padas"] if p["pada"] == "jīrṇānyanyāni")
    assert pada["dharmamitra"]["padaccheda"] == ["jīrṇāni", "anyāni"]
    assert pada["sanskrit_parser"]["padaccheda"] == ["jīrṇāni", "anyāni"]


def test_bhagavad_gita_fifteen_five_keeps_the_junction_word_disagreement_visible():
    """सुखदुःखसंज्ञैर्- / गच्छन्त्यमूढाः: Dharmamitra walks the joined word, sanskrit_parser breaks
    saṃjñaiḥ into san | jñais. The wrong reading stays in the document instead of being smoothed over."""
    result = load_golden("bhagavad_gita-15.5", ".result.json")
    pada = next(p for p in result["padas"] if p["pada"] == "sukhaduḥkhasaṃjñairgacchantyamūḍhāḥ")
    assert pada["dharmamitra"]["padaccheda"] == [
        "sukha", "duḥkha", "saṃjñaiḥ", "gacchanti", "amūḍhāḥ"
    ]
    assert pada["sanskrit_parser"]["padaccheda"][:3] == ["sukhaduḥkha", "san", "jñais"]


def test_bhagavad_gita_two_forty_seven_restores_the_avagraha_without_expanding_it():
    """मा ते सङ्गोऽस्त्वकर्मणि: both engines give the elided 'a' of astu its own word, while the avagraha
    itself stays unexpanded — which is why the pāda still counts eight aksharas."""
    result = load_golden("bhagavad_gita-2.47", ".result.json")
    assert "saṅgaḥ | astu | akarmaṇi" in result["padaccheda"]["dharmamitra"]
    assert "saṅgas | astu | akarmaṇi" in result["padaccheda"]["sanskrit_parser"]
    assert result["chandas"]["aksharas_per_pada"] == [8, 8, 8, 8]


def test_shakuntala_malini_and_sragdhara_padas_are_classified_whole():
    """Mālinī (15 aksharas) and sragdharā (21): vidyut names exactly one metre for each verse, so an odd
    pāda length is a property of the metre, not a splitting failure."""
    for stem, shape, vrtta in [
        ("abhijnaana_shakuntala-1.7", [21, 21, 21, 21], "sragdharā"),
        ("abhijnaana_shakuntala-1.18", [15, 15, 15, 15], "malinī"),
    ]:
        chandas = load_golden(stem, ".result.json")["chandas"]
        assert chandas["pada_count"] == 4
        assert chandas["aksharas_per_pada"] == shape
        assert chandas["candidates"] == [vrtta]


@pytest.mark.parametrize("stem", VERSES)
def test_every_chosen_word_carries_an_analysis(stem):
    """No word the ranker chose may reach the reading document as a bare form.

    Morphology for the chosen words comes from ``word_morphology`` in the raw document; when that
    source is missing, whichever words happen to appear in a sampled whole-line split keep their
    root/case/number and the rest go null — different words lose their analysis on every run.
    """
    result = load_golden(stem, ".result.json")
    bare = [w["form"] for p in result["padas"]
            for w in (p.get("sanskrit_parser") or {}).get("words", []) if "root" not in w]
    assert not bare, f"{stem}: words without an analysis: {bare}"


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

    # Candidates are produced one pada-line at a time, and vakya (sentence) parsing is gone:
    # every entry names exactly one input line and carries no sentence-graph payload.
    assert {k for e in sp["sandhi_splits"] for k in e} == {
        "line_index",
        "split_index",
        "split",
        "items",
    }
    assert len({e["line_index"] for e in sp["sandhi_splits"]}) > 1, (
        "a multi-line verse must be split line by line, not as one string"
    )

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
