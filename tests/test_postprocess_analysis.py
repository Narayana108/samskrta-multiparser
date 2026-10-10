"""Offline, deterministic tests for postprocess_analysis.py.

Every fixture is a hand-written dict in the shape of the raw multi-engine
analysis output; all strings are IAST, matching the project's output
convention. No engine is ever invoked: Dharmamitra is a remote API and stays
untouched here.
"""

import json
import sys

import pytest

import postprocess_analysis


# ---------------------------------------------------------------------------
# _norm_anusvara
# ---------------------------------------------------------------------------

def test_norm_anusvara_collides_variant_spellings():
    assert postprocess_analysis._norm_anusvara("saṃpṛktau") == "sampṛktau"
    assert postprocess_analysis._norm_anusvara("sampṛktau") == "sampṛktau"


def test_norm_anusvara_rewrites_only_anusvara_and_is_idempotent():
    assert postprocess_analysis._norm_anusvara("durlabhaṃ") == "durlabham"
    for word in ("vāgarthāviva", "havyaṃ", "saṃbodhana"):
        once = postprocess_analysis._norm_anusvara(word)
        assert postprocess_analysis._norm_anusvara(once) == once


# ---------------------------------------------------------------------------
# parse_sp_tag_group
# ---------------------------------------------------------------------------

def test_parse_full_tag_group():
    group = {
        "root": "vāgartha",
        "tags": ["bahuvacanam", "prathamāvibhaktiḥ", "puṃlliṅgam"],
    }
    assert postprocess_analysis.parse_sp_tag_group(group) == {
        "root": "vāgartha",
        "vacana": "bahu",
        "vibhakti": "prathamā",
        "linga": "puṃlliṅgam",
    }


@pytest.mark.parametrize("tag, expected", [
    ("prathamāvibhaktiḥ", "prathamā"),
    ("dvitīyāvibhaktiḥ", "dvitīyā"),
    ("tṛtīyāvibhakti", "tṛtīyā"),  # trailing visarga is optional
    ("caturthīvibhaktiḥ", "caturthī"),
    ("pañcamīvibhaktiḥ", "pañcamī"),
    ("ṣaṣṭhīvibhaktiḥ", "ṣaṣṭhī"),
    ("saptamīvibhaktiḥ", "saptamī"),
    ("saṃbodhanavibhaktiḥ", "saṃbodhana"),
])
def test_parse_vibhakti_tag(tag, expected):
    assert postprocess_analysis.parse_sp_tag_group({"tags": [tag]}) == {"vibhakti": expected}


@pytest.mark.parametrize("tag, short", [
    ("ekavacanam", "eka"),
    ("dvivacanam", "dvi"),
    ("bahuvacanam", "bahu"),
])
def test_parse_vacana_tag(tag, short):
    assert postprocess_analysis.parse_sp_tag_group({"tags": [tag]}) == {"vacana": short}


@pytest.mark.parametrize("linga", [
    "puṃlliṅgam",
    "strīliṅgam",
    "napuṃsakaliṅgam",
])
def test_parse_linga_tag_keeps_full_tag(linga):
    assert postprocess_analysis.parse_sp_tag_group({"tags": [linga]}) == {"linga": linga}


def test_unrecognized_tags_survive_in_residual_list_stripped_and_ordered():
    group = {
        "root": "rāma",
        "tags": ["samāsapūrvapadanāmapadam", " bahuvacanam ", "\tkaviḥ", "aniṣṭa"],
    }
    parsed = postprocess_analysis.parse_sp_tag_group(group)
    assert parsed["vacana"] == "bahu"
    assert parsed["tags"] == ["samāsapūrvapadanāmapadam", "kaviḥ", "aniṣṭa"]
    assert "vibhakti" not in parsed


def test_vibhakti_regex_is_anchored():
    # A near-miss tag must fall through to the residual list, not match.
    assert postprocess_analysis.parse_sp_tag_group(
        {"tags": ["prathamāvibhaktiḥpurvam"]}
    ) == {"tags": ["prathamāvibhaktiḥpurvam"]}


def test_root_only_emitted_when_nonempty():
    assert postprocess_analysis.parse_sp_tag_group({"root": "", "tags": ["ekavacanam"]}) == {
        "vacana": "eka"
    }
    assert postprocess_analysis.parse_sp_tag_group({"root": "agni", "tags": []}) == {
        "root": "agni"
    }


def test_empty_group_returns_none():
    assert postprocess_analysis.parse_sp_tag_group({"tags": []}) is None
    assert postprocess_analysis.parse_sp_tag_group({}) is None


# ---------------------------------------------------------------------------
# _morph_rank
# ---------------------------------------------------------------------------

_FULL_READING = {
    "root": "vāgartha",
    "vibhakti": "prathamā",
    "vacana": "bahu",
    "linga": "puṃlliṅgam",
}
_COMPOUND_FRAGMENT = {"tags": ["samāsapūrvapadanāmapadam"]}


def test_full_case_reading_beats_compound_marker_in_both_orders():
    assert postprocess_analysis._morph_rank(_FULL_READING) > postprocess_analysis._morph_rank(
        _COMPOUND_FRAGMENT
    )
    assert max([_COMPOUND_FRAGMENT, _FULL_READING], key=postprocess_analysis._morph_rank) is (
        _FULL_READING
    )
    assert max([_FULL_READING, _COMPOUND_FRAGMENT], key=postprocess_analysis._morph_rank) is (
        _FULL_READING
    )


def test_rank_prefers_vacana_then_fewer_residual_tags():
    case_and_number = {"vibhakti": "tṛtīyā", "vacana": "eka"}
    case_only = {"vibhakti": "tṛtīyā"}
    assert postprocess_analysis._morph_rank(case_and_number) > postprocess_analysis._morph_rank(case_only)

    lean = {"vibhakti": "tṛtīyā", "tags": ["kaviḥ"]}
    heavy = {"vibhakti": "tṛtīyā", "tags": ["kaviḥ", "aniṣṭa"]}
    assert postprocess_analysis._morph_rank(lean) > postprocess_analysis._morph_rank(heavy)


def test_rank_prefers_an_avyaya_reading_over_a_compound_marker():
    """'avyayam' says what the word is; a compound marker only says where it sits."""
    indeclinable = {"root": "yathā", "tags": ["avyayam", "saṃyojakaḥ"]}
    fragment = {"root": "yathā", "tags": ["samāsapūrvapadanāmapadam"]}
    assert postprocess_analysis._morph_rank(indeclinable) > postprocess_analysis._morph_rank(fragment)
    assert max([fragment, indeclinable], key=postprocess_analysis._morph_rank) is indeclinable


# ---------------------------------------------------------------------------
# collect_sp_morphology
# ---------------------------------------------------------------------------

def _nested_sp_raw(fragment_first: bool) -> dict:
    """Raw sanskrit_parser output with one pada spelled two anusvara ways.

    Args:
        fragment_first: whether the bare compound-marker reading is inserted
            before the full case reading (candidate order must not matter).
    """
    full = {"pada": "saṃpṛktau", "morphological_tags": [{
        "root": "vāgartha",
        "tags": ["bahuvacanam", "prathamāvibhaktiḥ", "puṃlliṅgam"],
    }]}
    fragment = {"pada": "sampṛktau", "morphological_tags": [{
        "root": "vāgartha",
        "tags": ["samāsapūrvapadanāmapadam"],
    }]}
    nodes = [fragment, full] if fragment_first else [full, fragment]
    return {
        "sandhi_splits": [
            {"splits": [nodes, [{"error": "split failed"}]]},
        ],
    }


def test_collect_morphology_recurses_through_nested_splits_and_errors():
    sp_output = {
        "sandhi_splits": [
            {"splits": [[
                {"pada": "vāgarthāviva", "morphological_tags": [{
                    "root": "vāgartha",
                    "tags": ["bahuvacanam", "prathamāvibhaktiḥ", "puṃlliṅgam"],
                }]},
            ]]},
            [{"error": "no parse"}],
            {"pada": "vāgartha", "morphological_tags": [{
                "root": "vāgartha",
                "tags": ["samāsapūrvapadanāmapadam"],
            }]},
        ],
    }
    assert postprocess_analysis.collect_sp_morphology(sp_output) == {
        "vāgarthāviva": [{
            "root": "vāgartha",
            "vacana": "bahu",
            "vibhakti": "prathamā",
            "linga": "puṃlliṅgam",
        }],
        "vāgartha": [{"root": "vāgartha", "tags": ["samāsapūrvapadanāmapadam"]}],
    }


def test_collect_morphology_anusvara_collision_winner_is_order_independent():
    expected = {"sampṛktau": [
        {
            "root": "vāgartha",
            "vacana": "bahu",
            "vibhakti": "prathamā",
            "linga": "puṃlliṅgam",
        },
        {"root": "vāgartha", "tags": ["samāsapūrvapadanāmapadam"]},
    ]}
    forward = postprocess_analysis.collect_sp_morphology(_nested_sp_raw(fragment_first=False))
    reverse = postprocess_analysis.collect_sp_morphology(_nested_sp_raw(fragment_first=True))
    assert forward == expected  # the full case reading ranks above the fragment
    assert reverse == forward   # not last-writer-wins


def test_collect_morphology_covers_words_only_found_in_word_morphology():
    """A word the per-word ranking chose may appear in no sampled whole-line split.

    Its tags arrive through `word_morphology`; without that source the reading document shows the
    bare form with no root, case or number — and which words lose them changes every run.
    """
    sp_output = {
        "sandhi_splits": [{"items": [{
            "pada": "sarva",
            "morphological_tags": [
                {"root": "sarva", "tags": ["ekavacanam", "prathamāvibhaktiḥ", "puṃlliṅgam"]},
            ],
        }]}],
        "word_morphology": [{
            "pada": "pāpebhyas",
            "morphological_tags": [
                {"root": "pāpa", "tags": ["samāsapūrvapadanāmapadam"]},
                {"root": "pāpa", "tags": ["bahuvacanam", "pañcamīvibhaktiḥ", "puṃlliṅgam"]},
            ],
        }],
    }
    assert postprocess_analysis.collect_sp_morphology(sp_output) == {
        "sarva": [{"root": "sarva", "vacana": "eka", "vibhakti": "prathamā", "linga": "puṃlliṅgam"}],
        "pāpebhyas": [
            {"root": "pāpa", "vacana": "bahu", "vibhakti": "pañcamī", "linga": "puṃlliṅgam"},
            {"root": "pāpa", "tags": ["samāsapūrvapadanāmapadam"]},
        ],
    }


def test_collect_morphology_keeps_every_case_reading_with_the_best_one_first():
    """An ambiguous form is never collapsed to a single guess.

    sanskrit_parser lists vāsāṃsi as nominative, accusative and vocative plural at once. The old
    alphabetical tie-break published only 'saṃbodhana' — 103 golden padas showed the vocative alone
    while their raw output also offered prathamā/dvitīyā — so the reading document looked like a
    parser that misreads every neuter plural. Case order now decides the primary field, and the
    alternatives stay visible as `alternates`.
    """
    sp_output = {"word_morphology": [{
        "pada": "vāsāṃsi",
        "morphological_tags": [
            {"root": "vāsas", "tags": ["bahuvacanam", "saṃbodhanavibhaktiḥ", "napuṃsakaliṅgam"]},
            {"root": "vāsas", "tags": ["bahuvacanam", "dvitīyāvibhaktiḥ", "napuṃsakaliṅgam"]},
            {"root": "vāsas", "tags": ["bahuvacanam", "prathamāvibhaktiḥ", "napuṃsakaliṅgam"]},
        ],
    }]}
    morph = postprocess_analysis.collect_sp_morphology(sp_output)
    assert len(morph) == 1  # keyed by the anusvara-normalized form, not the surface spelling
    readings = next(iter(morph.values()))
    assert [r["vibhakti"] for r in readings] == ["prathamā", "dvitīyā", "saṃbodhana"]
    entry = postprocess_analysis._sp_word_entry("vāsāṃsi", morph)
    assert entry["vibhakti"] == "prathamā"
    assert [r["vibhakti"] for r in entry["alternates"]] == ["dvitīyā", "saṃbodhana"]


def test_collect_morphology_demotes_a_root_the_dictionary_does_not_record():
    """A reading built on a stem the kosha never lists for that form loses to one it does.

    This is the ranking rule measured over the sixteen pinned verses: 8 of 273 published primaries move,
    and 6 of them become what the printed editions read — 'asti' becomes √as "is" instead of an invented
    vocative *asta*, and with it 'hi', 'yathāvidhi', 'prakṛti', 'sanni' and 'yāti' in saṃyāti. The
    unattested reading here is the *fuller* one (case, number and gender), so completeness alone used to
    win; the `#1` homophony marker must not hide an attested root either.
    """
    sp_output = {"word_morphology": [{
        "pada": "asti",
        "morphological_tags": [
            {"root": "asta", "tags": ["ekavacanam", "saṃbodhanavibhaktiḥ", "strīliṅgam"]},
            {"root": "as#1", "tags": ["laṭ", "prathamapuruṣaḥ", "ekavacanam"]},
        ],
    }]}

    def attest(form):
        assert form == "asti"
        return {"as"}  # vidyut records √as for this form, and no stem 'asta'

    readings = postprocess_analysis.collect_sp_morphology(sp_output, attest)["asti"]
    assert [r.get("root") for r in readings] == ["as#1", "asta"]  # demoted, never dropped


def test_collect_morphology_without_a_dictionary_is_byte_for_byte_the_old_ranking():
    """Forms the kosha says nothing about keep exactly the order they had."""
    sp_output = {"word_morphology": [{
        "pada": "vastā",
        "morphological_tags": [
            {"root": "vas#1", "tags": ["ekavacanam", "prathamāvibhaktiḥ", "strīliṅgam"]},
            {"root": "vas", "tags": ["bahuvacanam", "tṛtīyāvibhaktiḥ"]},
        ],
    }]}
    no_signal = postprocess_analysis.collect_sp_morphology(sp_output, lambda form: set())
    assert no_signal == postprocess_analysis.collect_sp_morphology(sp_output)
    assert [r.get("root") for r in no_signal["vastā"]] == ["vas#1", "vas"]


def test_attestation_is_exact_membership_never_a_prefix():
    """vidyut records short homographs, so a prefix of a stem proves nothing.

    The kosha lists 'ah' and 'aha' for the form अहम्; matching those as prefixes would have blessed the
    truncations that made the rule misfire ('vas' standing in for the instrumental plural *vasu*, 'vand'
    for *vandā*). Homophony markers are stripped before the comparison.
    """
    assert postprocess_analysis._root_attested({"root": "vandā"}, {"vand"}) is False
    assert postprocess_analysis._root_attested({"root": "vandā"}, {"vandA"}) is True
    assert postprocess_analysis._root_attested({"root": "as#1"}, {"as"}) is True
    assert postprocess_analysis._root_attested({"root": None}, {"as"}) is False
    assert postprocess_analysis.kosha_attest(None) is None  # no kosha data: no lookup, old ranking

# ---------------------------------------------------------------------------
# collect_sp_decompositions
# ---------------------------------------------------------------------------

def test_collect_decompositions_filters_bad_entries():
    sp_output = {"word_decompositions": {
        "agnim": ["agni"],
        "not-a-list": "īḷe",
        "all-non-str": [None, 5],
        "mixed": ["upa", "x", None],
    }}
    assert postprocess_analysis.collect_sp_decompositions(sp_output) == {
        "agnim": ["agni"],
        "mixed": ["upa", "x"],
    }


@pytest.mark.parametrize("sp_output", [{}, {"word_decompositions": None}])
def test_collect_decompositions_missing_input_gives_empty_dict(sp_output):
    assert postprocess_analysis.collect_sp_decompositions(sp_output) == {}


def test_collect_decompositions_collision_prefers_more_parts():
    sp_output = {"word_decompositions": {
        "sampṛktau": ["sa", "pṛktau"],
        "saṃpṛktau": ["sampṛktau"],
    }}
    assert postprocess_analysis.collect_sp_decompositions(sp_output) == {
        "sampṛktau": ["sa", "pṛktau"]
    }


@pytest.mark.parametrize("keys_first", [True, False])
def test_collect_decompositions_collision_tie_break_is_lexicographic(keys_first):
    # Same part count: the lexicographically smaller list wins no matter which
    # spelling appears first in the literal (iteration is over sorted items).
    pair = {
        "samy": ["p", "q"],
        "saṃy": ["a", "z"],
    }
    if not keys_first:
        pair = {"saṃy": ["a", "z"], "samy": ["p", "q"]}
    assert postprocess_analysis.collect_sp_decompositions({"word_decompositions": pair}) == {
        "samy": ["a", "z"]
    }


# ---------------------------------------------------------------------------
# collect_dm_tokens / group_dm_tokens_by_word
# ---------------------------------------------------------------------------

def test_collect_dm_tokens_drops_unusable_entries():
    dm_output = {"tokens": [
        {"form": "agnim", "lemma": "agni", "kosha_type": "noun"},
        {"lemma": "no-form"},
        {"form": ""},
        {"form": 42},
        None,
        {"form": "vīra"},
    ]}
    assert postprocess_analysis.collect_dm_tokens(dm_output) == [
        {"form": "agnim", "lemma": "agni", "kosha_type": "noun"},
        {"form": "vīra"},
    ]


@pytest.mark.parametrize("dm_output", [{}, {"tokens": None}, {"tokens": []}])
def test_collect_dm_tokens_missing_or_empty_tokens(dm_output):
    assert postprocess_analysis.collect_dm_tokens(dm_output) == []


def test_group_dm_tokens_keeps_each_token_on_the_word_it_rebuilds():
    # The greedy first-letter walk this replaces gave 'grīvābhaṅgābhirāmaṃ' the following pada's
    # tokens because the word contains an 'm', and left that pada empty.
    words = ["grīvābhaṅgābhirāmaṃ", "muhuranupatati"]
    tokens = [
        {"form": "grīvā"}, {"form": "bhaṅga"}, {"form": "abhirāmam"},
        {"form": "muhur"}, {"form": "anupatati"},
    ]
    groups, unmatched = postprocess_analysis.group_dm_tokens_by_word(words, tokens)
    assert [[t["form"] for t in g] for g in groups] == [
        ["grīvā", "bhaṅga", "abhirāmam"],
        ["muhur", "anupatati"],
    ]
    assert unmatched == []


def test_group_dm_tokens_reports_a_token_that_rebuilds_no_word():
    # Dharmamitrā put a 'mā' in front of the first pada of Bhagavadgītā 2.47, where it belongs to no
    # word; it is reported instead of being glued onto the pada whose other tokens already fit.
    words = ["karmaṇyevādhikāraste", "mā"]
    tokens = [
        {"form": "mā"}, {"form": "karmaṇi"}, {"form": "eva"},
        {"form": "adhikāraḥ"}, {"form": "te"}, {"form": "mā"},
    ]
    groups, unmatched = postprocess_analysis.group_dm_tokens_by_word(words, tokens)
    assert [[t["form"] for t in g] for g in groups] == [
        ["karmaṇi", "eva", "adhikāraḥ", "te"],
        ["mā"],
    ]
    assert [t["form"] for t in unmatched] == ["mā"]


def test_group_dm_tokens_gives_a_skipped_word_an_empty_group():
    groups, unmatched = postprocess_analysis.group_dm_tokens_by_word(
        ["agniḥ", "īḷe"], [{"form": "agnim"}]
    )
    assert [[t["form"] for t in g] for g in groups] == [["agnim"], []]
    assert unmatched == []


def test_group_dm_tokens_empty_inputs():
    groups, unmatched = postprocess_analysis.group_dm_tokens_by_word(["agniḥ", "īḷe"], [])
    assert (groups, unmatched) == ([[], []], [])
    # No pada to attach a token to: it is reported as unmatched, never invented onto a word.
    groups, unmatched = postprocess_analysis.group_dm_tokens_by_word([], [{"form": "agnim"}])
    assert (groups, unmatched) == ([], [{"form": "agnim"}])


# ---------------------------------------------------------------------------
# collect_dm_word_requests
# ---------------------------------------------------------------------------

def test_collect_word_requests_keeps_only_the_run_that_rebuilds_the_pada():
    # A single-word request for 'vāsāṃsi' came back padded with a phrase Dharmamitrā recognised.
    dm_output = {"pada_followups": [
        {"pada": "vāsāṃsi", "tokens": [{"form": "ṛta"}, {"form": "iva"}, {"form": "vāsāṃsi"}]},
    ]}
    answers = postprocess_analysis.collect_dm_word_requests(dm_output)
    assert [sorted(t["form"] for t in v) for v in answers.values()] == [["vāsāṃsi"]]


def test_collect_word_requests_drops_answers_that_rebuild_nothing_or_are_unusable():
    dm_output = {"pada_followups": [
        {"pada": "grīvābhaṅgābhirāmaṃ", "tokens": [{"form": "muhur"}, {"form": "anupatati"}]},
        {"pada": "syandane", "tokens": []},
        {"pada": None, "tokens": [{"form": "śarīra"}]},
        {"tokens": [{"form": "prayāti"}]},
        "not a mapping",
    ]}
    assert postprocess_analysis.collect_dm_word_requests(dm_output) == {}


# ---------------------------------------------------------------------------
# _diff_regions
# ---------------------------------------------------------------------------

def test_diff_regions_equal_sequences_including_anusvara_variants():
    assert postprocess_analysis._diff_regions(["dharmaḥ"], ["dharmaḥ"]) == []
    assert postprocess_analysis._diff_regions(["saṃpṛktau"], ["sampṛktau"]) == []


def test_diff_regions_replace_region():
    assert postprocess_analysis._diff_regions(
        ["agniḥ", "samiddhaḥ"], ["agniḥ", "samiddham"]
    ) == [{"sanskrit_parser": ["samiddhaḥ"], "dharmamitra": ["samiddham"]}]


def test_diff_regions_insert_and_delete_have_null_sides():
    assert postprocess_analysis._diff_regions(["dharmaḥ"], ["dharmaḥ", "cāraḥ"]) == [
        {"sanskrit_parser": None, "dharmamitra": ["cāraḥ"]}
    ]
    assert postprocess_analysis._diff_regions(["tapaḥ", "dhena"], []) == [
        {"sanskrit_parser": ["tapaḥ", "dhena"], "dharmamitra": None}
    ]


# ---------------------------------------------------------------------------
# _dm_word_entry / _sp_word_entry
# ---------------------------------------------------------------------------

def test_dm_word_entry_filters_keys():
    assert postprocess_analysis._dm_word_entry({"form": "agnim"}) == {"form": "agnim"}
    assert postprocess_analysis._dm_word_entry(
        {"form": "agnim", "lemma": "agni", "kosha_type": "noun"}
    ) == {"form": "agnim", "lemma": "agni", "type": "noun"}
    # Empty enrichment values are dropped.
    assert postprocess_analysis._dm_word_entry(
        {"form": "agnim", "lemma": "", "kosha_type": ""}
    ) == {"form": "agnim"}


def test_sp_word_entry_looks_up_normalized_form_and_filters_keys():
    morph = {"sampṛktau": [{
        "root": "vāgartha",
        "vibhakti": "prathamā",
        "vacana": "eka",
        "linga": "puṃlliṅgam",
        "tags": ["samāsapūrvapadanāmapadam"],
    }]}
    assert postprocess_analysis._sp_word_entry("saṃpṛktau", morph) == {
        "form": "saṃpṛktau",
        "root": "vāgartha",
        "vibhakti": "prathamā",
        "vacana": "eka",
        "linga": "puṃlliṅgam",
        "tags": ["samāsapūrvapadanāmapadam"],
    }
    assert postprocess_analysis._sp_word_entry("īḷe", morph) == {"form": "īḷe"}
    # Falsy morphology fields are not copied.
    assert postprocess_analysis._sp_word_entry(
        "agni", {"agni": [{"root": "", "tags": [], "vacana": "eka"}]}
    ) == {"form": "agni", "vacana": "eka"}


# ---------------------------------------------------------------------------
# build_padas / build_padaccheda
# ---------------------------------------------------------------------------

def _tapodhena_fixture():
    """One split pada where SP and DM disagree on the segmentation."""
    return (
        ["tapodhena"],
        {"tapodhena": ["tapaḥ", "dhena"]},
        {"tapaḥ": [{"root": "tapas", "vibhakti": "ṣaṣṭhī", "vacana": "eka"}]},
        [[{"form": "tapodhena", "lemma": "tapa"}]],
    )


def test_build_padas_full_entry_with_differences_when_dm_available():
    words, sp_decomp, sp_morph, dm_groups = _tapodhena_fixture()
    padas = postprocess_analysis.build_padas(words, sp_decomp, sp_morph, dm_groups, True)
    assert padas == [{
        "pada": "tapodhena",
        "dharmamitra": {
            "padaccheda": ["tapodhena"],
            "words": [{"form": "tapodhena", "lemma": "tapa"}],
        },
        "sanskrit_parser": {
            "padaccheda": ["tapaḥ", "dhena"],
            "words": [
                {"form": "tapaḥ", "root": "tapas", "vibhakti": "ṣaṣṭhī", "vacana": "eka"},
                {"form": "dhena"},
            ],
        },
        "differences": [
            {"sanskrit_parser": ["tapaḥ", "dhena"], "dharmamitra": ["tapodhena"]}
        ],
    }]


def test_build_padas_dm_available_false_omits_differences_entirely():
    words, sp_decomp, sp_morph, dm_groups = _tapodhena_fixture()
    padas = postprocess_analysis.build_padas(words, sp_decomp, sp_morph, dm_groups, False)
    assert set(padas[0]) == {"pada", "dharmamitra", "sanskrit_parser"}
    assert "differences" not in padas[0]


def test_build_padas_empty_dm_group_with_dm_available_gives_null_side():
    padas = postprocess_analysis.build_padas(
        ["tapodhena"], {"tapodhena": ["tapaḥ", "dhena"]}, {}, [[]], True
    )
    assert padas[0]["dharmamitra"] is None
    assert padas[0]["differences"] == [
        {"sanskrit_parser": ["tapaḥ", "dhena"], "dharmamitra": None}
    ]


def test_build_padas_agreeing_engines_get_no_differences_key():
    padas = postprocess_analysis.build_padas(
        ["agnim"], {"agnim": ["agnim"]}, {}, [[{"form": "agnim"}]], True
    )
    assert "differences" not in padas[0]
    assert padas[0]["dharmamitra"]["padaccheda"] == ["agnim"]


def test_build_padas_fills_an_empty_side_from_a_word_request_and_labels_it():
    padas = postprocess_analysis.build_padas(
        ["tapodhena", "agnim"],
        {"tapodhena": ["tapaḥ", "dhena"]},
        {},
        [[], [{"form": "agnim"}]],
        True,
        {"tapodhena": [{"form": "tapas", "lemma": "tapas"}]},
    )
    assert padas[0]["dharmamitra"] == {
        "padaccheda": ["tapas"],
        "words": [{"form": "tapas", "lemma": "tapas"}],
        "request": "pada",
    }
    # The extra reading changes the comparison as well, so the disagreement is still recorded.
    assert padas[0]["differences"] == [
        {"sanskrit_parser": ["tapaḥ", "dhena"], "dharmamitra": ["tapas"]}
    ]
    assert "request" not in padas[1]["dharmamitra"]


def test_build_padas_verse_tokens_beat_a_word_request():
    # A word-level answer never overwrites what the verse-level stream already attributed.
    padas = postprocess_analysis.build_padas(
        ["agnim"], {"agnim": ["agnim"]}, {}, [[{"form": "agnim"}]], True,
        {"agnim": [{"form": "havyam"}]},
    )
    assert padas[0]["dharmamitra"]["padaccheda"] == ["agnim"]
    assert "request" not in padas[0]["dharmamitra"]


def test_build_padas_without_an_answer_keeps_the_null_side():
    padas = postprocess_analysis.build_padas(
        ["tapodhena"], {"tapodhena": ["tapaḥ", "dhena"]}, {}, [[]], True, {}
    )
    assert padas[0]["dharmamitra"] is None



def test_build_padaccheda_none_for_engines_without_parts():
    assert postprocess_analysis.build_padaccheda([]) == {
        "dharmamitra": None,
        "sanskrit_parser": None,
    }
    padas = [{
        "pada": "x",
        "dharmamitra": None,
        "sanskrit_parser": {"padaccheda": [], "words": []},
    }]
    assert postprocess_analysis.build_padaccheda(padas) == {
        "dharmamitra": None,
        "sanskrit_parser": None,
    }


def test_build_padaccheda_joins_parts_with_pipe():
    padas = [
        {
            "pada": "agnim",
            "dharmamitra": {"padaccheda": ["agnim"], "words": []},
            "sanskrit_parser": {"padaccheda": ["agni"], "words": []},
        },
        {
            "pada": "īḷe",
            "dharmamitra": None,
            "sanskrit_parser": {"padaccheda": ["īḷe"], "words": []},
        },
    ]
    assert postprocess_analysis.build_padaccheda(padas) == {
        "dharmamitra": "agnim",
        "sanskrit_parser": "agni | īḷe",
    }


# ---------------------------------------------------------------------------
# postprocess
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    {},
    {"input": None},
    {"input": {}},
    {"input": {"devanagari": "अग्निम्"}},
    {"input": "agnim"},
    {"input": {"iast": None}},
    {"input": {"iast": 42}},
    {"input": {"iast": ""}},
    {"input": {"iast": "   "}},
    {"input": {"iast": "\n \t"}},
])
def test_postprocess_requires_string_input_iast(raw):
    with pytest.raises(ValueError) as excinfo:
        postprocess_analysis.postprocess(raw)
    assert str(excinfo.value) == "raw output has no usable 'input.iast' string"


def _clean_raw() -> dict:
    return {
        "mode": "verbose",  # a stray key in the raw document; the reading must not carry it
        "input": {"iast": "agnim īḷe"},
        "engine_outputs": {
            "sanskrit_parser": {
                "word_decompositions": {"agnim": ["agni"]},
                "sandhi_splits": [{
                    "pada": "agnim",
                    "morphological_tags": [{
                        "root": "agni",
                        "tags": ["dvitīyāvibhaktiḥ", "ekavacanam", "puṃlliṅgam"],
                    }],
                }],
            },
            "dharmamitra": {
                "tokens": [{"form": "agnim", "lemma": "agni", "kosha_type": "noun"}]
            },
        },
    }


def test_postprocess_clean_fixture_end_to_end():
    normalized = postprocess_analysis.postprocess(_clean_raw())
    assert normalized == {
        "input": {"iast": "agnim īḷe"},
        "padaccheda": {"dharmamitra": "agnim", "sanskrit_parser": "agni | īḷe"},
        "padas": [
            {
                "pada": "agnim",
                "dharmamitra": {
                    "padaccheda": ["agnim"],
                    "words": [{"form": "agnim", "lemma": "agni", "type": "noun"}],
                },
                "sanskrit_parser": {
                    "padaccheda": ["agni"],
                    "words": [{"form": "agni"}],
                },
                "differences": [
                    {"sanskrit_parser": ["agni"], "dharmamitra": ["agnim"]}
                ],
            },
            {
                # DM produced no token for 'īḷe' although it had tokens overall:
                # a real disagreement, so the null-side difference shows up.
                "pada": "īḷe",
                "dharmamitra": None,
                "sanskrit_parser": {
                    "padaccheda": ["īḷe"],
                    "words": [{"form": "īḷe"}],
                },
                "differences": [
                    {"sanskrit_parser": ["īḷe"], "dharmamitra": None}
                ],
            },
        ],
    }
    assert "engine_errors" not in normalized


def test_postprocess_records_engine_error_and_keeps_other_engine():
    raw = _clean_raw()
    raw["engine_outputs"] = {
        "sanskrit_parser": raw["engine_outputs"]["sanskrit_parser"],
        "dharmamitra": {"error": "boom"},
    }
    normalized = postprocess_analysis.postprocess(raw)
    assert normalized["engine_errors"] == {"dharmamitra": "boom"}
    # The surviving engine is still processed; DM side is empty everywhere.
    assert normalized["padaccheda"]["sanskrit_parser"] == "agni | īḷe"
    assert all(p["dharmamitra"] is None for p in normalized["padas"])
    # No DM tokens at all -> dm_available False -> no differences fabricated.
    assert all("differences" not in p for p in normalized["padas"])


def test_postprocess_error_key_coerced_to_str_and_both_engines_recorded():
    raw = {
        "input": {"iast": "agnim"},
        "engine_outputs": {
            "sanskrit_parser": {"error": "sp failed"},
            "dharmamitra": {"error": 7},
        },
    }
    normalized = postprocess_analysis.postprocess(raw)
    assert normalized["engine_errors"] == {
        "sanskrit_parser": "sp failed",
        "dharmamitra": "7",
    }


def test_postprocess_without_engine_outputs_at_all():
    raw = {"input": {"iast": "agnim īḷe"}}
    normalized = postprocess_analysis.postprocess(raw)
    assert "mode" not in normalized, "the reading document has no mode field"
    assert normalized["padaccheda"] == {
        "dharmamitra": None,
        "sanskrit_parser": "agnim | īḷe",
    }
    assert [p["pada"] for p in normalized["padas"]] == ["agnim", "īḷe"]
    assert all(p["dharmamitra"] is None for p in normalized["padas"])
    assert all("differences" not in p for p in normalized["padas"])
    assert "engine_errors" not in normalized


def test_postprocess_passes_vidyut_chandas_summary_through():
    raw = _clean_raw()
    chanda = {
        "vrtta": None,
        "candidates": ["sragdharā"],
        "pada_count": 4,
        "classified_pada_count": 3,
        "aksharas_per_pada": [21, 21, 21, 21],
    }
    raw["engine_outputs"]["vidyut"] = {"chandas": chanda, "meter": []}
    assert postprocess_analysis.postprocess(raw)["chandas"] == chanda


def test_postprocess_records_vidyut_error_and_omits_chandas():
    raw = _clean_raw()
    raw["engine_outputs"]["vidyut"] = {"error": "Vidyut data directory not found"}
    processed = postprocess_analysis.postprocess(raw)
    assert processed["engine_errors"]["vidyut"] == "Vidyut data directory not found"
    assert "chandas" not in processed


def test_postprocess_reports_a_token_that_rebuilds_no_word():
    raw = _clean_raw()
    # Dharmamitrā put an extra 'mā' ahead of the verse; it rebuilds no pada here, so it is named in the
    # document instead of being filed under a word or dropped.
    raw["engine_outputs"]["dharmamitra"]["tokens"].insert(0, {"form": "mā"})
    normalized = postprocess_analysis.postprocess(raw)
    assert normalized["dharmamitra_unmatched"] == ["mā"]
    assert list(normalized) == ["input", "padaccheda", "padas", "dharmamitra_unmatched"]
    # The pada columns are untouched by the extra token.
    assert [p["dharmamitra"]["padaccheda"] if p["dharmamitra"] else None for p in normalized["padas"]] == [
        ["agnim"], None
    ]


def test_postprocess_omits_the_unmatched_key_when_every_token_is_used():
    assert "dharmamitra_unmatched" not in postprocess_analysis.postprocess(_clean_raw())


def test_postprocess_notes_a_word_request_whose_answer_rebuilds_nothing(capsys):
    raw = _clean_raw()
    # 'ila' covers too little of 'īḷe' to be believed, so the recorded request is reported as unused.
    raw["engine_outputs"]["dharmamitra"]["pada_followups"] = [
        {"pada": "īḷe", "raw_output": "ila_", "tokens": [{"form": "ila"}]},
    ]
    normalized = postprocess_analysis.postprocess(raw)
    err = capsys.readouterr().err
    assert err.startswith("Note: Dharmamitrā's word-level answers rebuilt none of these padas")
    assert "īḷe" in err
    assert normalized["padas"][1]["dharmamitra"] is None


# ---------------------------------------------------------------------------
# CLI: output base naming, directory creation, stdout contract
# ---------------------------------------------------------------------------

def _run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ("postprocess_analysis.py",) + argv)
    return postprocess_analysis.main()


@pytest.mark.parametrize("suffix", ["", ".json", ".raw.json", ".result.json"])
def test_output_base_forms_all_name_the_same_pair(tmp_path, monkeypatch, suffix):
    base = tmp_path / "nested" / "analysis"
    postprocess_analysis.write_document(
        postprocess_analysis.raw_path(str(base)), _clean_raw()
    )
    assert _run_cli(monkeypatch, "-o", str(base) + suffix) == 0
    result = json.loads(
        (tmp_path / "nested" / "analysis.result.json").read_text(encoding="utf-8")
    )
    assert [p["pada"] for p in result["padas"]] == ["agnim", "īḷe"]


def test_cli_derives_the_result_sibling_from_the_raw_file(tmp_path, monkeypatch):
    base = tmp_path / "analysis"
    raw_file = postprocess_analysis.raw_path(str(base))
    postprocess_analysis.write_document(raw_file, _clean_raw())
    assert _run_cli(monkeypatch, "-i", str(raw_file)) == 0
    assert postprocess_analysis.result_path(str(base)).exists()


def test_cli_creates_missing_output_directories(tmp_path, monkeypatch):
    base = tmp_path / "a" / "b" / "c" / "analysis"
    postprocess_analysis.write_document(
        postprocess_analysis.raw_path(str(base)), _clean_raw()
    )
    assert _run_cli(monkeypatch, "-o", str(base)) == 0
    assert postprocess_analysis.result_path(str(base)).exists()


def test_cli_stdout_is_only_input_and_chandas(tmp_path, monkeypatch, capsys):
    base = tmp_path / "analysis"
    raw = _clean_raw()
    chanda = {
        "vrtta": None,
        "candidates": ["anuṣṭubh"],
        "pada_count": 2,
        "classified_pada_count": 0,
        "aksharas_per_pada": [8, 8],
    }
    raw["engine_outputs"]["vidyut"] = {"chandas": chanda}
    postprocess_analysis.write_document(postprocess_analysis.raw_path(str(base)), raw)

    assert _run_cli(monkeypatch, "-o", str(base)) == 0
    printed = json.loads(capsys.readouterr().out)
    assert list(printed) == ["input", "chandas"]
    assert printed["chandas"] == chanda
    assert printed["input"] == {"iast": "agnim īḷe"}


def test_cli_never_prints_padas_or_engine_sections(tmp_path, monkeypatch, capsys):
    base = tmp_path / "analysis"
    postprocess_analysis.write_document(
        postprocess_analysis.raw_path(str(base)), _clean_raw()
    )
    assert _run_cli(monkeypatch, "-o", str(base)) == 0
    out = capsys.readouterr().out
    for marker in ("padas", "padaccheda", "engine_outputs", "differences"):
        assert marker not in out


def test_cli_requires_a_base_or_a_raw_document(monkeypatch, capsys):
    assert _run_cli(monkeypatch) == 1
    assert "give an output base" in capsys.readouterr().err


def test_cli_reports_a_missing_raw_document(tmp_path, monkeypatch, capsys):
    base = tmp_path / "gone"
    assert _run_cli(monkeypatch, "-o", str(base)) == 1
    assert f"'{postprocess_analysis.raw_path(str(base))}' not found" in capsys.readouterr().err
