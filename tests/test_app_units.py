"""Offline unit tests for the pure helpers in ``app.py``.

Nothing here opens a socket or drives the CLI: the Dharmamitra HTTP path is never
imported, the kosha-backed cases read only the local vidyut data directory pinned
by ``tests/conftest.py``, and every assertion compares literal values (sets are
sorted before comparison where order is unspecified).
"""

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import app


# ---------------------------------------------------------------------------
# Preprocessing and input reading
# ---------------------------------------------------------------------------


def test_preprocess_strips_dandas_and_trims():
    assert app.preprocess_devanagari("  श्रीरामः।  ") == "श्रीरामः"
    # Dandas vanish without inserting a space; surrounding blanks are trimmed.
    assert app.preprocess_devanagari("अ। ब॥\n\n") == "अ ब"


def test_preprocess_is_idempotent_and_keeps_other_punctuation():
    once = app.preprocess_devanagari("नमो|भगवते॥ [line]")
    assert once == "नमो|भगवते [line]"
    assert app.preprocess_devanagari(once) == once


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


def test_split_anustubh_line_at_akshara_midpoint():
    # 5 + 3 | 8 aksharas: the cut lands where the running total reaches half (8).
    line = "vAgarTAviva saMpfktO vAgarTapratipattaye"
    assert app._split_into_padas(line) == [
        "vAgarTAviva saMpfktO",
        "vAgarTapratipattaye",
    ]


def test_split_attaches_trailing_punctuation_to_first_half():
    line = "vAgarTAviva saMpfktO . vAgarTapratipattaye"
    assert app._split_into_padas(line) == [
        "vAgarTAviva saMpfktO .",
        "vAgarTapratipattaye",
    ]


def test_split_preserves_every_token():
    line = "vAgarTAviva saMpfktO vAgarTapratipattaye .."
    halves = app._split_into_padas(line)
    assert halves == ["vAgarTAviva saMpfktO", "vAgarTapratipattaye .."]
    assert " ".join(halves).split() == line.split()


def test_split_cut_follows_aksharas_not_characters():
    # 7 + 8 | 3 aksharas: a character-count proxy would cut inside the long
    # second token, the akshara midpoint cuts after it.
    line = "dUrUpavaktrAmhasA sahasrakIrNanAshanaH paramaM"
    assert app._split_into_padas(line) == [
        "dUrUpavaktrAmhasA sahasrakIrNanAshanaH",
        "paramaM",
    ]


def test_split_cut_lands_after_four_of_six_tokens():
    # 1 + 3 + 2 + 3 | 4 + 1 aksharas (total 14, half 7).
    line = "te jagatyAM guNA jAgarTI bhAvayanti ca"
    assert app._split_into_padas(line) == [
        "te jagatyAM guNA jAgarTI",
        "bhAvayanti ca",
    ]


@pytest.mark.parametrize(
    "line",
    [
        "",  # nothing to split
        "..",  # punctuation only: no meaningful token at all
        "gacChati deva",  # two meaningful tokens
        "dUrUpavaktrAmhasA sahasrakIrNanAshanaH .",  # two meaningful, 15 aksharas
        "dUrUpavaktrAmhasA devaH jAgarTI",  # three meaningful, exactly 12 aksharas
    ],
)
def test_split_passes_through_lines_that_are_too_short(line):
    assert app._split_into_padas(line) == [line]


def test_split_drops_empty_second_half():
    # 1 + 1 + 12 aksharas: only the last token crosses the midpoint, so there is
    # nothing left for a second half and no empty string is emitted.
    line = "sa ca dUrUpavaktrAmhasAkIrNanAshanaH"
    assert app._split_into_padas(line) == [line]


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
