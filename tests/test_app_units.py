"""Offline unit tests for the pure helpers in ``app.py``.

Nothing here opens a socket. ``app.main()`` is driven only with every engine
stubbed out, the kosha-backed cases read just the local vidyut data directory
pinned by ``tests/conftest.py``, and every assertion compares literal values
(sets are sorted before comparison where order is unspecified).
"""

import io
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import app
import postprocess_analysis
from vidyut.chandas import Chandas


# ---------------------------------------------------------------------------
# Preprocessing and input reading
# ---------------------------------------------------------------------------


def test_preprocess_strips_dandas_and_trims():
    assert app.preprocess_input("  श्रीरामः।  ") == "श्रीरामः"
    # A danda becomes whitespace, so the words on either side stay separate.
    assert app.preprocess_input("अ। ब॥\n\n") == "अ ब"


def test_preprocess_is_idempotent():
    once = app.preprocess_input("नमो|भगवते॥ [line]")
    # Pipes, dandas and brackets all collapse to single spaces; a second pass
    # changes nothing.
    assert once == "नमो भगवते line"
    assert app.preprocess_input(once) == once


def test_preprocess_drops_verse_numbers_and_devanagari_digits():
    # Pasted verse numbering must never reach an engine or a pada list.
    assert app.preprocess_input(
        "सर्वधर्मान्परित्यज्य मामेकं शरणं व्रज ।\n"
        "अहं त्वां सर्वपापेभ्यो मोक्षयिष्यामि मा शुचः ॥ 66॥"
    ) == ("सर्वधर्मान्परित्यज्य मामेकं शरणं व्रज\n"
          "अहं त्वां सर्वपापेभ्यो मोक्षयिष्यामि मा शुचः")
    assert app.preprocess_input("अ ब ॥६६॥") == "अ ब"
    assert app.preprocess_input("1. अग्निम ईले 2.") == "अग्निम ईले"


@pytest.mark.parametrize(
    "typed",
    ["vāc-artha", "vāc artha.", "vāc  artha,", "vāc/artha", "vāc\\artha", "vāc artha"],
)
def test_preprocess_makes_roman_separators_equivalent(typed):
    # However the two words are separated, the engines must see the same text.
    assert app.preprocess_input(typed) == "vāc artha"


def test_read_input_strips_trailing_whitespace(tmp_path):
    path = tmp_path / "shloka.txt"
    path.write_text("पूर्णमासं गच्छति\n\n   ", encoding="utf-8")
    assert app.read_input(str(path)) == "पूर्णमासं गच्छति"


def test_read_input_keeps_inner_newlines(tmp_path):
    path = tmp_path / "two-lines.txt"
    path.write_text("अ\nब\n", encoding="utf-8")
    assert app.read_input(str(path)) == "अ\nब"


def test_read_input_reads_stdin_for_dash(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("  वाग्वदेव  \n"))
    assert app.read_input("-") == "वाग्वदेव"


def test_read_input_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        app.read_input(str(tmp_path / "absent.txt"))


def test_read_input_requires_utf8(tmp_path):
    path = tmp_path / "latin1.txt"
    path.write_bytes(b"\xff\xfe gacChati\n")
    with pytest.raises(UnicodeDecodeError):
        app.read_input(str(path))


# ---------------------------------------------------------------------------
# SLP1 tag -> IAST label maps
# ---------------------------------------------------------------------------


def test_lakara_map_pins_iast_values():
    assert app.LAKARA_IAST["lf~N"] == "lṛṅ"
    assert app.LAKARA_IAST["la~N"] == "laṅ"
    assert app.LAKARA_IAST["viDili~N"] == "vidhiliṅ"
    assert set(app.LAKARA_IAST) == {
        "la~w",
        "li~w",
        "lu~w",
        "lf~w",
        "le~w",
        "lo~w",
        "la~N",
        "viDili~N",
        "ASIrli~N",
        "lu~N",
        "lf~N",
    }
    # Keys stay vidyut SLP1 tags; no accent/Anusvara markers leak into IAST values.
    assert all(key.isascii() for key in app.LAKARA_IAST)
    assert not any(mark in v for v in app.LAKARA_IAST.values() for mark in "~\\")


def test_prayoga_map_passes_through_kartari():
    assert app.PRAYOGA_IAST["kartari"] == "kartari"
    assert app.PRAYOGA_IAST["karmaRi"] == "karmaṇi"
    assert app.PRAYOGA_IAST["BAve"] == "bhāve"
    assert set(app.PRAYOGA_IAST) == {"kartari", "karmaRi", "BAve"}


def test_vibhakti_and_agreement_maps_pin_iast_labels():
    assert app.VIBHAKTI_IAST["zazWI"] == "ṣaṣṭhī"
    assert app.VIBHAKTI_IAST["samboDanam"] == "saṃbodhanam"
    assert set(app.VIBHAKTI_IAST) == {
        "praTamA",
        "dvitIyA",
        "tftIyA",
        "caturTI",
        "paYcamI",
        "zazWI",
        "saptamI",
        "samboDanam",
    }
    assert app.VACANA_IAST["dvi"] == "dvivacanam"
    assert app.LINGA_IAST["napuMsaka"] == "napuṃsakaliṅgam"
    assert app.GANA_IAST["kaRqvAdi"] == "kaṇḍvādiḥ"


# ---------------------------------------------------------------------------
# Akshara counting and pada splitting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("gacChati", 3),
        ("deva", 2),
        ("saMpfktO", 3),  # a + f (ṛ) + O (au)
        ("jagataH", 3),  # visarga carries no vowel of its own
        ("vAgarTAviva", 5),
    ],
)
def test_count_aksharas_counts_vowel_code_points(token, expected):
    assert app._count_aksharas(token) == expected


def test_count_aksharas_ignores_marks_and_counts_clusters_once():
    assert app._count_aksharas("") == 0
    assert app._count_aksharas("H") == 0  # visarga alone is no nucleus
    assert app._count_aksharas("M") == 0  # anusvara
    assert app._count_aksharas("fxxeO") == 5  # vocalic r/l and digraphs count once


def test_akshara_count_matches_vidyuts_scan_of_the_same_word():
    # vidyut's chandas scanner uses the same vowel set as `_count_aksharas` — SLP1 `R`/`RR` (ṛ ṝ) are
    # not vowels there either, so `saṃpṛktau` scans as four groups. Both sides of that agreement are
    # pinned: `akshara_count` is defined as what vidyut classified, so changing one set without the
    # other makes the meter section contradict itself.
    from pathlib import Path

    chandas = Chandas(Path(app.DATA_DIR) / "chandas" / "meters.tsv")
    for word in ("saMpaRktau", "karma", "vAgarTAviva"):
        scanned = [a.text for group in chandas.classify(word).aksharas for a in group]
        assert len(scanned) == app._count_aksharas(word), scanned


def test_split_anustubh_line_at_akshara_midpoint():
    # 5 + 3 | 8 aksharas: the cut lands where the running total reaches half (8).
    line = "vAgarTAviva saMpfktO vAgarTapratipattaye"
    assert app._split_into_padas(line) == [
        "vAgarTAviva saMpfktO",
        "vAgarTapratipattaye",
    ]


def test_split_preserves_every_token():
    # Here the midpoint coincides with a word boundary, so no token is broken.
    line = "vAgarTAviva saMpfktO vAgarTapratipattaye"
    halves = app._split_into_padas(line)
    assert halves == ["vAgarTAviva saMpfktO", "vAgarTapratipattaye"]
    assert " ".join(halves).split() == line.split()


def test_split_cut_follows_aksharas_not_characters():
    # Raghuvaṃśa 1.2 first line: 1 + 5 + 2 | 1 + 5 + 2 aksharas. The boundary at
    # exactly half (8) falls after three tokens; a character count would cut
    # inside the long second token instead.
    line = "kva sUryaprabhavo vaMSaH kva cAlpaviSayA matiH"
    assert app._split_into_padas(line) == [
        "kva sUryaprabhavo vaMSaH",
        "kva cAlpaviSayA matiH",
    ]


def test_split_cut_lands_after_three_of_four_tokens():
    # Raghuvaṃśa 1.1 second line: 3 + 3 + 2 | 8 aksharas (total 16, half 8).
    line = "jagataH pitarO vande pArvatIparameSvarO"
    assert app._split_into_padas(line) == [
        "jagataH pitarO vande",
        "pArvatIparameSvarO",
    ]


def test_split_cuts_inside_a_token_when_sandhi_joins_the_padas():
    # Raghuvaṃśa 1.2 second line: तितीर्षुर्दुस्तरं मोहाद् | उडुपेनास्मि सागरम् is
    # written with sandhi, so no word boundary sits at akshara 8. The cut goes
    # through mohāduḍupena and both halves still measure eight aksharas.
    line = "titIrzurdustaraM mohAduqupenAsmi sAgaram"
    halves = app._split_into_padas(line)
    assert halves == ["titIrzurdustaraM mohA", "duqupenAsmi sAgaram"]
    assert [sum(app._count_aksharas(t) for t in h.split()) for h in halves] == [8, 8]


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("", "nothing to split"),
        ("gacChati deva", "two tokens"),
        ("dUrUpavaktrAmhasA sahasrakIrNanAshanaH", "two tokens, 15 aksharas"),
        ("dUrUpavaktrAmhasA devaH jAgarTI", "three tokens, exactly 12 aksharas"),
        ("te jagatyAM guNA jAgarTI bhAvayanti", "five tokens, odd total of 13"),
    ],
)
def test_split_passes_through_lines_that_cannot_be_halved(line, reason):
    assert app._split_into_padas(line) == [line], reason


@pytest.mark.parametrize(
    "line",
    [
        "dUrUpavaktrAmhasA sahasrakIrNanAshanaH paramaM",  # 18 aksharas
        "te jagatyAM guNA jAgarTI bhAvayanti ca",  # 14 aksharas
        "sa ca dUrUpavaktrAmhasAkIrNanAshanaH",  # 14 aksharas
    ],
)
def test_split_halves_even_lines_without_losing_characters(line):
    halves = app._split_into_padas(line)
    total = sum(app._count_aksharas(t) for t in line.split())
    assert len(halves) == 2
    counts = [sum(app._count_aksharas(t) for t in half.split()) for half in halves]
    assert counts == [total // 2, total // 2]
    # A mid-token cut neither drops nor duplicates a character.
    assert "".join(halves).replace(" ", "") == line.replace(" ", "")


# ---------------------------------------------------------------------------
# Dharmamitra token parsing
# ---------------------------------------------------------------------------


def test_parse_tokens_records_only_the_form():
    assert app._parse_tokens("vāc_iva_pitarau") == [
        {"form": "vāc"},
        {"form": "iva"},
        {"form": "pitarau"},
    ]


def test_parse_tokens_drops_untaggable_positions():
    assert app._parse_tokens("____iva_") == [{"form": "iva"}]
    assert app._parse_tokens("_vande_") == [{"form": "vande"}]
    assert app._parse_tokens("") == []
    assert app._parse_tokens("___") == []


# ---------------------------------------------------------------------------
# Kosha entry classification (stand-in entries; no vidyut objects)
# ---------------------------------------------------------------------------


class _Entry:
    """Stand-in kosha entry: the classifier only inspects repr() and attributes."""

    def __init__(
        self,
        kind="Pratipadika",
        lemma=None,
        is_avyaya=False,
        pratipadika_entry=None,
    ):
        self.kind = kind
        self.is_avyaya = is_avyaya
        if lemma is not None:
            self.lemma = lemma
        if pratipadika_entry is not None:
            self.pratipadika_entry = pratipadika_entry

    def __repr__(self):
        return f"<vidyut.kosha.{self.kind} object>"


def test_kosha_entry_info_classifies_tinanta():
    assert app._kosha_entry_info(_Entry(kind="Tinanta", lemma="vand")) == {
        "type": "tīnantāḥ",
        "lemma": "vand",
        "stem": "vand",
    }


def test_kosha_entry_info_prefers_tinanta_over_avyaya():
    info = app._kosha_entry_info(_Entry(kind="Tinanta", lemma="vand", is_avyaya=True))
    assert info["type"] == "tīnantāḥ"


def test_kosha_entry_info_classifies_avyayam():
    assert app._kosha_entry_info(_Entry(lemma="iva", is_avyaya=True)) == {
        "type": "avyayam",
        "lemma": "iva",
        "stem": "iva",
    }


def test_kosha_entry_info_defaults_to_sunanta_and_keeps_slp1_stem():
    assert app._kosha_entry_info(_Entry(lemma="vAc")) == {
        "type": "sūnantāḥ",
        "lemma": "vāc",  # IAST for display
        "stem": "vAc",  # raw SLP1 for surface comparison
    }


def test_kosha_entry_info_strips_vedic_accent_from_displayed_lemma():
    assert app._kosha_entry_info(_Entry(lemma="vad~")) == {
        "type": "sūnantāḥ",
        "lemma": "vad",
        "stem": "vad~",
    }


@pytest.mark.parametrize(
    "slp1,expected",
    [
        ("vad~\\", "vad"),  # anudātta pair
        ("vad\\'", "vad"),  # svarita pair
        ("vadi~\\", "vadi"),  # accent after the vowel
        ("vad", "vad"),  # unaccented upadeśa untouched
        ("", ""),
    ],
)
def test_slp1_to_iast_vidyut_strips_both_accent_marks(slp1, expected):
    assert app._slp1_to_iast_vidyut(slp1) == expected


def test_kosha_entry_info_falls_back_to_pratipadika_lemma():
    entry = _Entry(kind="Krdanta", pratipadika_entry=SimpleNamespace(lemma="arTa"))
    assert app._kosha_entry_info(entry) == {
        "type": "sūnantāḥ",
        "lemma": "artha",
        "stem": "arTa",
    }


@pytest.mark.parametrize(
    "entry",
    [_Entry(), _Entry(kind="Tinanta"), _Entry(lemma="")],
    ids=["no-lemma", "tinanta-no-lemma", "empty-lemma"],
)
def test_kosha_entry_info_needs_a_lemma(entry):
    assert app._kosha_entry_info(entry) is None


def test_kosha_info_from_entries_returns_first_usable():
    entries = [_Entry(), _Entry(lemma="vAc"), _Entry(kind="Tinanta", lemma="vand")]
    assert app._kosha_info_from_entries(entries) == {
        "type": "sūnantāḥ",
        "lemma": "vāc",
        "stem": "vAc",
    }


def test_kosha_info_from_entries_without_usable_entry():
    assert app._kosha_info_from_entries([]) is None
    assert app._kosha_info_from_entries([_Entry(), _Entry(lemma="")]) is None


# ---------------------------------------------------------------------------
# Sandhi split-chain helpers
# ---------------------------------------------------------------------------


def test_has_standalone_entry_accepts_only_non_derived_entries():
    assert app._has_standalone_entry([_Entry(kind="Pratipadika", lemma="vAc")]) is True
    assert app._has_standalone_entry([_Entry(kind="Tinanta", lemma="vand")]) is False
    assert app._has_standalone_entry([_Entry(kind="Krdanta", lemma="kft")]) is False


def test_has_standalone_entry_finds_one_among_derived_entries():
    derived_first = [_Entry(kind="Tinanta"), _Entry(kind="Pratipadika")]
    derived_last = [_Entry(kind="Pratipadika"), _Entry(kind="Krdanta")]
    assert app._has_standalone_entry(derived_first) is True
    assert app._has_standalone_entry(derived_last) is True
    assert app._has_standalone_entry([]) is False


def test_chain_score_counts_attested_parts():
    chain = [{"kosha": {"lemma": "vac"}}, {"form": "iva"}, {"kosha": None}]
    assert app._chain_score(chain) == (1, -3)
    assert app._chain_score([]) == (0, 0)


def test_chain_score_prefers_attested_then_shorter():
    two_attested = [{"kosha": {"lemma": "vac"}}, {"kosha": {"lemma": "pitṛ"}}]
    three_attested = two_attested + [{"kosha": {"lemma": "vand"}}]
    one_attested = [{"kosha": {"lemma": "vac"}}, {"form": "iva"}]

    assert app._chain_score(two_attested) > app._chain_score(one_attested)
    # Same number of attested parts: the shorter chain wins.
    assert app._chain_score(two_attested) > app._chain_score(
        [{"kosha": {"lemma": "vac"}}, {"kosha": {"lemma": "vand"}}, {"form": "iva"}]
    )
    chains = [one_attested, two_attested, three_attested]
    assert max(chains, key=app._chain_score) is three_attested


# ---------------------------------------------------------------------------
# Kosha-backed lookups (local vidyut data only)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def kosha():
    """Vidyut's local kosha; skipped when the pinned data directory is absent."""
    from vidyut.kosha import Kosha

    kosha_dir = Path(app.DATA_DIR) / "kosha"
    if not kosha_dir.is_dir():
        pytest.skip(f"no kosha directory at {kosha_dir}")
    return Kosha(kosha_dir)


def test_kosha_lookup_resolves_surface_forms_to_lemmas(kosha):
    def info_sets(word):
        infos = [app._kosha_entry_info(e) for e in app.kosha_lookup(kosha, word)]
        assert all(info is not None for info in infos)
        return {i["lemma"] for i in infos}, {i["stem"] for i in infos}

    vac_lemmas, vac_stems = info_sets("vAk")
    assert "vac" in vac_stems and "vac" in vac_lemmas

    artha_lemmas, artha_stems = info_sets("arTO")
    assert artha_stems == {"arTa", "arTi"}  # two homographic stems
    assert artha_lemmas == {"artha", "arthi"}

    jagat_lemmas, jagat_stems = info_sets("jagataH")  # stem fallback: -H ending
    assert jagat_lemmas == {"jagat"}
    assert jagat_stems == {"jagat"}
    assert app._get_kosha_info(kosha, "jagataH")["type"] == "sūnantāḥ"

    # Nothing matches, not even after the 1-3 character stem fallback.
    assert app.kosha_lookup(kosha, "xyzzyx") == []


def test_enrich_dharmamitra_lemmas_fills_in_lemma_and_type(kosha):
    """The local kosha supplies what Dharmamitra leaves out (no network here).

    ``kosha`` is requested only to guard the data directory: the function opens
    its own handle and mutates the result dict in place.
    """
    results = {
        "tokens": [
            {"form": "vāc"},
            {"form": "saṃpṛktau"},
            {"form": "pitarau"},
            {"form": "vande"},
            {"form": "xyzzy"},
            {},
        ]
    }

    app.enrich_dharmamitra_lemmas(results)

    by_form = {t.get("form"): t for t in results["tokens"]}
    assert by_form["saṃpṛktau"]["lemma"] == "saṃpṛc"
    assert by_form["pitarau"]["lemma"] == "pitṛ"
    assert by_form["vande"]["lemma"] == "vand"
    # 'vāc' is homographic (vac / vāc), so the ambiguity stays a sorted list.
    assert by_form["vāc"]["lemma"] == ["vac", "vāc"]
    assert {t["kosha_type"] for t in results["tokens"] if "kosha_type" in t} == {
        "sūnantāḥ"
    }
    # Unattested forms and formless tokens are left untouched.
    assert by_form["xyzzy"] == {"form": "xyzzy"}
    assert {} in results["tokens"]


# ---------------------------------------------------------------------------
# Input files and script detection (tests/data, never the repo's input.txt)
# ---------------------------------------------------------------------------

INPUT_DIR = Path(__file__).resolve().parent / "data"
VERSES = [
    "raghuvamsha-1.1",
    "raghuvamsha-1.2",
    "raghuvamsha-1.3",
    "raghuvamsha-1.4",
    "raghuvamsha-1.5",
    "raghuvamsha-1.6",
    "raghuvamsha-1.7",
    "bhagavad_gita-18.66",
    "abhijnaana_shakuntala-1.1",
    # Bhagavad Gītā metres beyond anuṣṭubh: jagatī-family pādas (indravajra/upajāti) and the
    # avagraha-heavy 2.22/2.47.
    "bhagavad_gita-2.22",
    "bhagavad_gita-2.47",
    "bhagavad_gita-11.15",
    "bhagavad_gita-15.5",
    "bhagavad_gita-15.15",
    # Abhijñānaśākuntala: sragdharā (21 aksharas) and mālinī (15), both long-compound verses.
    "abhijnaana_shakuntala-1.7",
    "abhijnaana_shakuntala-1.18",
]


def _input(verse: str, suffix: str = ".txt") -> str:
    return (INPUT_DIR / f"{verse}{suffix}").read_text(encoding="utf-8")


@pytest.mark.parametrize("verse", VERSES)
def test_devanagari_inputs_are_detected_and_left_untouched(verse):
    raw = _input(verse)
    script, devanagari = app.to_devanagari(raw)
    assert script == "Devanagari"
    # input.devanagari must keep exactly the bytes the user supplied.
    assert devanagari == raw


@pytest.mark.parametrize("verse", VERSES)
def test_iast_inputs_canonicalize_to_the_same_text(verse):
    script, devanagari = app.to_devanagari(_input(verse, ".iast.txt"))
    assert script == "Iast"
    # lipi maps the IAST '.'/'..' danda spellings back to ।/॥, so the two files
    # describe one and the same text.
    assert devanagari == _input(verse)
    assert app.preprocess_input(devanagari) == app.preprocess_input(app.to_devanagari(_input(verse))[1])


@pytest.mark.parametrize("verse", VERSES)
def test_preprocessed_verses_carry_no_separators(verse):
    import string

    cleaned = app.preprocess_input(app.to_devanagari(_input(verse))[1])
    assert not any(char in cleaned for char in string.punctuation + "।॥")
    # Line structure survives: no empty, padded or doubled-space lines remain.
    assert all(line and line == " ".join(line.split()) for line in cleaned.split("\n"))


def test_preprocess_cleans_devanagari_and_roman_input_alike():
    assert app.preprocess_input("वागर्थौ | इव ॥") == "वागर्थौ इव"
    assert app.preprocess_input("vāc-arthau, iva.") == "vāc arthau iva"


def test_preprocess_closes_a_hyphenated_line_break_into_one_word():
    """Printed editions hyphenate a word that runs over the pāda junction; both halves are one sandhi word.

    Leaving ``…संज्ञैर्-`` as its own token hands every engine a virama-final fragment it cannot decompose, and
    reports the verse with an extra pāda.
    """
    assert app.preprocess_input("सुखदुःखसंज्ञैर्-\nगच्छन्त्यमूढाः") == "सुखदुःखसंज्ञैर्गच्छन्त्यमूढाः"
    assert app.preprocess_input("jīrṇāny-\nanyāni") == "jīrṇānyanyāni"


@pytest.mark.parametrize(
    ("verse", "totals"),
    [
        ("raghuvamsha-1.1", [16, 16]),
        ("raghuvamsha-1.2", [16, 16]),
        # Four 21-akshara pādas in sragdharā and four 15-akshara pādas in mālinī: no even-meter
        # split exists for either verse, so each printed line stays one pāda.
        ("abhijnaana_shakuntala-1.1", [21, 21, 21, 21]),
        ("abhijnaana_shakuntala-1.7", [21, 21, 21, 21]),
        ("abhijnaana_shakuntala-1.18", [15, 15, 15, 15]),
        # Jagatī-family verses: eleven aksharas per pāda is odd, so the lines go to vidyut whole.
        ("bhagavad_gita-11.15", [11, 11, 11, 11]),
        ("bhagavad_gita-15.15", [11, 11, 11, 11]),
        # Bhagavad Gītā 2.47 is typed as two half-verses of sixteen aksharas each.
        ("bhagavad_gita-2.47", [16, 16]),
        # These two editions break a word at the pāda junction with a hyphen; closing that join makes
        # one printed line twenty-two aksharas long, which splits into two equal eleven-akshara halves.
        ("bhagavad_gita-2.22", [11, 11, 22]),
        ("bhagavad_gita-15.5", [11, 11, 22]),
    ],
)
def test_verse_line_totals_and_pada_splitting(verse, totals):
    from vidyut.lipi import transliterate, Scheme

    cleaned = app.preprocess_input(app.to_devanagari(_input(verse))[1])
    slp1_lines = [
        transliterate(line, Scheme.Devanagari, Scheme.Slp1)
        for line in cleaned.split("\n")
        if line.strip()
    ]
    assert len(slp1_lines) == len(totals)
    assert [
        sum(app._count_aksharas(token) for token in line.split()) for line in slp1_lines
    ] == totals

    for line, total in zip(slp1_lines, totals):
        halves = app._split_into_padas(line)
        if total % 2 == 0:
            # Even-meter lines split into two akshara-equal halves.
            assert len(halves) == 2
            counts = [sum(app._count_aksharas(t) for t in part.split()) for part in halves]
            assert counts == [total // 2, total // 2]
        else:
            # Odd pādas go to vidyut whole rather than being cut at a false boundary.
            assert halves == [line]


# ---------------------------------------------------------------------------
# Verse-level chanda summary (vidyut classifications, no vidyut call)
# ---------------------------------------------------------------------------

def _pada(meter=None, aksharas=8):
    return {"iast": "x", "meter": meter, "akshara_count": aksharas, "weight_pattern": ""}


def test_chandas_summary_names_a_meter_only_when_every_pada_agrees():
    lines = [{"line_iast": "a b", "padas": [_pada("anuṣṭubh"), _pada("anuṣṭubh")]}]
    assert app._summarize_chandas(lines) == {
        "vrtta": "anuṣṭubh",
        "candidates": ["anuṣṭubh"],
        "pada_count": 2,
        "classified_pada_count": 2,
        "aksharas_per_pada": [8, 8],
    }


def test_chandas_summary_lists_candidates_when_padas_disagree():
    # Raghuvaṃśa 1.1 as vidyut's own catalogue sees it: two pādas match lookalike
    # vṛttas, two match nothing at all — the verse gets no name but keeps both
    # suggestions and the honest 8·8·8·8 shape.
    lines = [{"line_iast": "l", "padas": [_pada("madalekhā"), _pada("śuddhavirāṭ"), _pada(), _pada()]}]
    summary = app._summarize_chandas(lines)
    assert summary["vrtta"] is None
    assert summary["candidates"] == ["madalekhā", "śuddhavirāṭ"]
    assert summary["pada_count"] == 4
    assert summary["classified_pada_count"] == 2
    assert summary["aksharas_per_pada"] == [8, 8, 8, 8]


def test_chandas_summary_of_no_classified_padas():
    assert app._summarize_chandas([]) == {
        "vrtta": None,
        "candidates": [],
        "pada_count": 0,
        "classified_pada_count": 0,
        "aksharas_per_pada": [],
    }


# ---------------------------------------------------------------------------
# Engine failure policy: local engines are fatal, Dharmamitra is not
# ---------------------------------------------------------------------------

@pytest.fixture()
def cli(tmp_path, monkeypatch):
    """Run ``app.main()`` offline on a tiny file with the given engine stubs.

    Returns the exit code and the raw document (``<base>.raw.json``); ``-o`` is always
    passed so no test ever writes into the project's own ``results/`` directory.
    """
    def run(**engines):
        for name, impl in engines.items():
            monkeypatch.setattr(app, name, impl)
        src = tmp_path / "in.txt"
        src.write_text("agnim īḷe", encoding="utf-8")
        base = str(tmp_path / "analysis")
        monkeypatch.setattr(
            sys, "argv", ["app.py", "shloka", "-i", str(src), "-o", base]
        )
        code = app.main()
        raw_file = postprocess_analysis.raw_path(base)
        return code, json.loads(raw_file.read_text(encoding="utf-8"))
    return run


def test_run_exits_1_when_vidyut_reports_unavailable(cli, capsys):
    code, doc = cli(
        run_sanskrit_parser=lambda *a: {"word_decompositions": {}},
        run_dharmamitra=lambda *a: {"tokens": []},
        run_vidyut=lambda *a: {"error": "Vidyut data directory not found"},
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "Error: vidyut: Vidyut data directory not found" in err
    assert doc["engine_outputs"]["vidyut"] == {"error": "Vidyut data directory not found"}


def test_run_exits_1_when_sanskrit_parser_cannot_be_used(cli, capsys):
    def missing(*a):
        raise RuntimeError("sanskrit_parser is not installed")

    code, doc = cli(
        run_sanskrit_parser=missing,
        run_dharmamitra=lambda *a: {"tokens": []},
        run_vidyut=lambda *a: {"kosha": [], "prakriya": {}, "meter": [], "chandas": {}},
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "Error: sanskrit_parser: unavailable: sanskrit_parser is not installed" in err
    assert doc["engine_outputs"]["sanskrit_parser"]["error"].startswith("unavailable:")


def test_run_exits_0_when_only_dharmamitra_is_down(cli, capsys):
    def offline(*a):
        raise OSError("connection refused")

    code, doc = cli(
        run_sanskrit_parser=lambda *a: {"word_decompositions": {}},
        run_dharmamitra=offline,
        run_vidyut=lambda *a: {"kosha": [], "prakriya": {}, "meter": [], "chandas": {}},
    )
    assert code == 0
    err = capsys.readouterr().err
    assert "Warning: dharmamitra: Dharmamitra engine failed: connection refused" in err
    assert doc["engine_outputs"]["dharmamitra"] == {
        "error": "Dharmamitra engine failed: connection refused"
    }


# ---------------------------------------------------------------------------
# Concurrency: the Dharmamitra wait overlaps the local engines, and its two
# attempts share one bounded budget instead of a timeout each plus a sleep
# ---------------------------------------------------------------------------

def test_dharmamitra_wait_overlaps_the_local_engines(cli):
    spans = {}

    def timed(name, seconds, result):
        def run(*_args):
            start = time.monotonic()
            time.sleep(seconds)
            spans[name] = (start, time.monotonic())
            return dict(result)
        return run

    code, _doc = cli(
        run_sanskrit_parser=timed("sp", 0.25, {"word_decompositions": {}}),
        run_dharmamitra=timed("dm", 0.15, {"tokens": []}),
        run_vidyut=timed(
            "vidyut", 0.0, {"kosha": [], "prakriya": {}, "meter": [], "chandas": {}}
        ),
    )
    assert code == 0
    # The request has to be in flight while sanskrit_parser works: a serial pipeline would
    # start the API call only after the local engines finished.
    assert spans["dm"][0] < spans["sp"][1], "the network wait must overlap the local work"
    assert spans["dm"][1] > spans["sp"][0]


def test_dharmamitra_retries_share_one_deadline(monkeypatch):
    class Timeout(Exception):
        pass

    asked = []

    def post(url, headers=None, json=None, timeout=None, stream=False):  # noqa: A002 - API shape
        asked.append(timeout)
        time.sleep(0.2)
        raise Timeout()

    fake_requests = SimpleNamespace(
        post=post,
        exceptions=SimpleNamespace(Timeout=Timeout, RequestException=Timeout),
    )
    monkeypatch.setattr(app, "DHARMAMITRA_DEADLINE_SECS", 0.5)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    started = time.monotonic()
    result = app.run_dharmamitra("agnim īḷe", ["agnim īḷe"])
    elapsed = time.monotonic() - started

    # The first attempt gets the whole budget, the second only what is left of it, and no
    # blind sleep is inserted between them.
    assert asked[0] == pytest.approx(0.5, abs=0.05)
    assert 0 < asked[1] < asked[0]
    assert elapsed < 1.0
    assert result == {"error": "Dharmamitra API request timed out"}


def test_an_http_error_body_is_never_parsed_as_an_answer(monkeypatch):
    # A gateway error page that happens to be JSON in the answer shape must not become a reading.
    # The old retry loop kept the failed response object alive, so an HTTP 5xx on attempt 1 plus a
    # timeout on attempt 2 fell through to parsing that error body as if it were the API's answer.
    class HttpError(Exception):
        pass

    class Timeout(Exception):
        pass

    bodies_read = []
    asked = []

    class ErrorResponse:
        headers = {"Content-Length": "42"}

        def raise_for_status(self):
            raise HttpError("503 Service Unavailable")

        def iter_content(self, chunk_size=0):
            bodies_read.append(True)
            yield b'{"results": ["tampered_output"]}'

    def post(url, headers=None, json=None, timeout=None, stream=False):  # noqa: A002 - API shape
        asked.append(stream)
        if len(asked) == 1:
            return ErrorResponse()
        raise Timeout()

    monkeypatch.setitem(
        sys.modules,
        "requests",
        SimpleNamespace(
            post=post,
            exceptions=SimpleNamespace(Timeout=Timeout, RequestException=HttpError),
        ),
    )

    result = app.run_dharmamitra("agnim īḷe", ["agnim īḷe"])

    assert asked == [True, True], "the body must be streamed so it can be capped"
    assert bodies_read == [], "a failed attempt's body must never be read"
    assert result == {"error": "Dharmamitra API request timed out"}
    assert "tampered_output" not in json.dumps(result)


def test_an_announced_oversized_response_is_refused_before_streaming(monkeypatch):
    class ChattyHeaders:
        headers = {"Content-Length": str(app._MAX_RESPONSE_BYTES + 1)}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=0):
            raise AssertionError("an announced oversized body must not be streamed")
            yield b""  # pragma: no cover - makes this a generator

    monkeypatch.setitem(
        sys.modules,
        "requests",
        SimpleNamespace(
            post=lambda *a, **k: ChattyHeaders(),
            exceptions=SimpleNamespace(Timeout=Exception, RequestException=Exception),
        ),
    )

    result = app.run_dharmamitra("agnim īḷe", ["agnim īḷe"])
    assert result["error"].startswith("Dharmamitra API response too large")
    assert "tampered" not in json.dumps(result)


def test_a_streamed_body_past_the_cap_is_dropped(monkeypatch):
    class EndlessResponse:
        headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=0):
            while True:
                yield b"x" * 65536

    monkeypatch.setitem(
        sys.modules,
        "requests",
        SimpleNamespace(
            post=lambda *a, **k: EndlessResponse(),
            exceptions=SimpleNamespace(Timeout=Exception, RequestException=Exception),
        ),
    )

    result = app.run_dharmamitra("agnim īḷe", ["agnim īḷe"])
    assert "more than" in result["error"] and "bytes streamed" in result["error"]


def test_the_body_reader_runs_on_a_real_requests_response():
    # The fakes above only implement the methods this client happens to call, so they cannot catch a
    # method name that does not exist on `requests.Response` at all — an `iter_bytes` reader passed every
    # fake-based test and then failed all sixteen live golden runs. This builds the real object.
    import io

    import requests
    from urllib3.response import HTTPResponse

    payload = b'{"results": ["agni_"]}'
    response = requests.Response()
    response.status_code = 200
    response.raw = HTTPResponse(
        body=io.BytesIO(payload),
        headers={"Content-Length": str(len(payload))},
        preload_content=False,
        decode_content=False,
    )
    response.headers = {"Content-Length": str(len(payload))}

    body, failure = app._read_capped_body(response, time.monotonic() + 5)
    assert failure is None
    assert json.loads(body)["results"] == ["agni_"]


def test_malformed_numeric_env_overrides_warn_and_default(monkeypatch, capsys):
    # These settings are read at import time; a bad value must not turn into an import traceback.
    monkeypatch.setenv("SAMSKRTA_WORKERS", "two")
    assert app._env_number("SAMSKRTA_WORKERS", 0) == 0
    monkeypatch.setenv("DHARMAMITRA_TIMEOUT_SECS", "20s")
    assert app._env_number("DHARMAMITRA_TIMEOUT_SECS", 20.0) == 20.0
    err = capsys.readouterr().err
    assert "ignoring invalid SAMSKRTA_WORKERS='two'" in err
    assert "ignoring invalid DHARMAMITRA_TIMEOUT_SECS='20s'" in err


def test_word_requests_get_an_absolute_deadline(monkeypatch):
    # The follow-up loop hands the request helper an absolute time.monotonic() deadline. Handing it a
    # duration instead made every word request fail instantly with 'never completed'.
    seen = []

    def fake_request(text, deadline):
        seen.append((text, deadline))
        return "tapas_", None

    monkeypatch.setattr(app, "_dharmamitra_raw_output", fake_request)
    monkeypatch.setattr(app, "DHARMAMITRA_DEADLINE_SECS", 3.0)
    dm = {"tokens": [{"form": "agnim"}]}
    processed = {"padas": [
        {"pada": "agnim", "dharmamitra": {"padaccheda": ["agnim"], "words": []}},
        {"pada": "tapodhena", "dharmamitra": None},
    ]}

    started = time.monotonic()
    assert app.fill_missing_pada_readings(dm, processed) == 1

    assert [text for text, _ in seen] == ["tapodhena"]
    assert seen[0][1] == pytest.approx(started + 3.0, abs=0.5)
    # The answer is recorded in the raw document so an offline rebuild replays it without the network.
    assert dm["pada_followups"] == [
        {"pada": "tapodhena", "raw_output": "tapas_", "tokens": [{"form": "tapas"}]}
    ]


def test_failed_word_request_is_reported_and_leaves_the_pada_untagged(monkeypatch, capsys):
    monkeypatch.setattr(
        app, "_dharmamitra_raw_output", lambda text, deadline: (None, "Dharmamitra API request timed out")
    )
    dm = {"tokens": [{"form": "agnim"}]}
    processed = {"padas": [{"pada": "tapodhena", "dharmamitra": None}]}
    assert app.fill_missing_pada_readings(dm, processed) == 0
    assert "pada_followups" not in dm
    assert "word request for 'tapodhena' failed" in capsys.readouterr().err


def test_word_requests_stop_when_the_deadline_has_passed(monkeypatch, capsys):
    monkeypatch.setattr(app, "DHARMAMITRA_DEADLINE_SECS", 0.0)
    dm = {"tokens": [{"form": "agnim"}]}
    processed = {"padas": [{"pada": "tapodhena", "dharmamitra": None}]}
    assert app.fill_missing_pada_readings(dm, processed) == 0
    assert "ran out of time" in capsys.readouterr().err


def test_word_requests_are_skipped_when_the_verse_pass_gave_nothing(monkeypatch):
    # An engine that failed outright is reported as failed; patching individual padas would invent a
    # reading the API never gave.
    calls = []
    monkeypatch.setattr(app, "_dharmamitra_raw_output", lambda t, d: calls.append(t) or ("x_", None))
    processed = {"padas": [{"pada": "tapodhena", "dharmamitra": None}]}
    assert app.fill_missing_pada_readings({"error": "Dharmamitra API unavailable"}, processed) == 0
    assert app.fill_missing_pada_readings({"tokens": []}, processed) == 0
    assert calls == []


def test_word_request_limit_is_announced_when_it_bites(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(app, "_MAX_PADA_FOLLOWUPS", 2)
    monkeypatch.setattr(
        app, "_dharmamitra_raw_output", lambda t, d: seen.append(t) or ("tapas_", None)
    )
    dm = {"tokens": [{"form": "agnim"}]}
    processed = {"padas": [{"pada": f"w{i}", "dharmamitra": None} for i in range(5)]}
    assert app.fill_missing_pada_readings(dm, processed) == 2
    assert seen == ["w0", "w1"]
    assert "requesting only the first 2" in capsys.readouterr().err


def test_kosha_is_built_once_per_process(monkeypatch):
    builds = []

    class FakeKosha:
        def __init__(self, path):
            builds.append(path)

    monkeypatch.setattr(app, "_KOSHA_CACHE", None)
    monkeypatch.setattr(app, "_KOSHA_UNAVAILABLE", False)
    monkeypatch.setitem(sys.modules, "vidyut.kosha", SimpleNamespace(Kosha=FakeKosha))

    first = app.load_kosha()
    second = app.load_kosha()
    assert len(builds) == 1, "the ~200 MB kosha index must not be rebuilt per engine"
    assert first is second

# ---------------------------------------------------------------------------
# Output pair naming and the stdout contract (all engines stubbed)
# ---------------------------------------------------------------------------

CHANDA = {
    "vrtta": None,
    "candidates": ["anuṣṭubh"],
    "pada_count": 2,
    "classified_pada_count": 0,
    "aksharas_per_pada": [8, 8],
}


def _stub_engines(monkeypatch, chanda=None):
    """Replace every engine with an instant offline stub."""
    vidyut = {"kosha": [], "prakriya": {}, "meter": []}
    if chanda is not None:
        vidyut["chandas"] = chanda
    for name, impl in {
        "run_sanskrit_parser": lambda *a: {"word_decompositions": {}},
        "run_dharmamitra": lambda *a: {"tokens": []},
        "run_vidyut": lambda *a: dict(vidyut),
    }.items():
        monkeypatch.setattr(app, name, impl)


def _src(tmp_path, name="in.txt", text="agnim īḷe"):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return str(tmp_path / name)


def test_default_output_base_writes_the_pair_under_results(tmp_path, monkeypatch):
    _stub_engines(monkeypatch, chanda=CHANDA)
    src = _src(tmp_path, "raghuvamsha-1.1.txt")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["app.py", "shloka", "-i", src])
    assert app.main() == 0
    assert sorted(p.name for p in (tmp_path / "results").iterdir()) == [
        "raghuvamsha-1.1.raw.json",
        "raghuvamsha-1.1.result.json",
    ]


def test_stdin_input_names_the_pair_after_the_mode(tmp_path, monkeypatch):
    _stub_engines(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("agnim īḷe"))
    monkeypatch.setattr(sys, "argv", ["app.py", "pada", "-i", "-"])
    assert app.main() == 0
    assert sorted(p.name for p in (tmp_path / "results").iterdir()) == [
        "pada.raw.json",
        "pada.result.json",
    ]


def test_custom_base_creates_missing_directories_and_both_documents(
    tmp_path, monkeypatch
):
    _stub_engines(monkeypatch)
    src = _src(tmp_path)
    base = str(tmp_path / "deep" / "nested" / "analysis")
    monkeypatch.setattr(sys, "argv", ["app.py", "shloka", "-i", src, "-o", base])
    assert app.main() == 0
    written = sorted(p.name for p in (tmp_path / "deep" / "nested").iterdir())
    assert written == ["analysis.raw.json", "analysis.result.json"]


@pytest.mark.parametrize("suffix", ["", ".json", ".raw.json", ".result.json"])
def test_suffix_is_stripped_from_the_output_base(tmp_path, monkeypatch, suffix):
    _stub_engines(monkeypatch)
    src = _src(tmp_path)
    base = str(tmp_path / "analysis") + suffix
    monkeypatch.setattr(sys, "argv", ["app.py", "shloka", "-i", src, "-o", base])
    assert app.main() == 0
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".json") == [
        "analysis.raw.json",
        "analysis.result.json",
    ]


def test_both_documents_are_written_even_when_a_local_engine_fails(tmp_path, monkeypatch):
    _stub_engines(monkeypatch)
    monkeypatch.setattr(
        app, "run_vidyut", lambda *a: {"error": "Vidyut data directory not found"}
    )
    src = _src(tmp_path)
    base = str(tmp_path / "analysis")
    monkeypatch.setattr(sys, "argv", ["app.py", "shloka", "-i", src, "-o", base])
    assert app.main() == 1
    written = sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".json")
    assert written == ["analysis.raw.json", "analysis.result.json"]
    raw = json.loads(postprocess_analysis.raw_path(base).read_text(encoding="utf-8"))
    assert raw["engine_outputs"]["vidyut"] == {
        "error": "Vidyut data directory not found"
    }


def test_stdout_carries_exactly_input_and_chandas(tmp_path, monkeypatch, capsys):
    _stub_engines(monkeypatch, chanda=CHANDA)
    src = _src(tmp_path)
    base = str(tmp_path / "analysis")
    monkeypatch.setattr(sys, "argv", ["app.py", "shloka", "-i", src, "-o", base])
    assert app.main() == 0

    printed = json.loads(capsys.readouterr().out)
    result = json.loads(
        postprocess_analysis.result_path(base).read_text(encoding="utf-8")
    )
    assert list(printed) == ["input", "chandas"]
    assert printed["chandas"] == CHANDA == result["chandas"]
    assert list(printed["input"]) == ["devanagari", "iast"]
    assert printed["input"]["iast"] == result["input"]["iast"]


def test_stdout_never_carries_padas_or_engine_sections(tmp_path, monkeypatch, capsys):
    _stub_engines(monkeypatch, chanda=CHANDA)
    src = _src(tmp_path)
    base = str(tmp_path / "analysis")
    monkeypatch.setattr(sys, "argv", ["app.py", "shloka", "-i", src, "-o", base])
    assert app.main() == 0
    out = capsys.readouterr().out
    for marker in ("padas", "padaccheda", "engine_outputs", "word_decompositions"):
        assert marker not in out


def test_pada_mode_prints_null_chandas_on_stdout(tmp_path, monkeypatch, capsys):
    _stub_engines(monkeypatch)  # vidyut stub has no chanda summary at all
    src = _src(tmp_path)
    base = str(tmp_path / "analysis")
    monkeypatch.setattr(sys, "argv", ["app.py", "pada", "-i", src, "-o", base])
    assert app.main() == 0
    printed = json.loads(capsys.readouterr().out)
    assert list(printed) == ["input", "chandas"]
    assert printed["chandas"] is None
