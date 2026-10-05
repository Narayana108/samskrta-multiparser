"""Offline, deterministic tests for normalize.py.

Every fixture is a hand-written dict in the shape of the raw multi-engine
analysis output; all strings are IAST, matching the project's output
convention. No engine is ever invoked: Dharmamitra is a remote API and stays
untouched here.
"""

import pytest

import normalize


# ---------------------------------------------------------------------------
# _norm_anusvara
# ---------------------------------------------------------------------------

def test_norm_anusvara_collides_variant_spellings():
    assert normalize._norm_anusvara("saṃpṛktau") == "sampṛktau"
    assert normalize._norm_anusvara("sampṛktau") == "sampṛktau"


def test_norm_anusvara_rewrites_only_anusvara_and_is_idempotent():
    assert normalize._norm_anusvara("durlabhaṃ") == "durlabham"
    for word in ("vāgarthāviva", "havyaṃ", "saṃbodhana"):
        once = normalize._norm_anusvara(word)
        assert normalize._norm_anusvara(once) == once


# ---------------------------------------------------------------------------
# parse_sp_tag_group
# ---------------------------------------------------------------------------

def test_parse_full_tag_group():
    group = {
        "root": "vāgartha",
        "tags": ["bahuvacanam", "prathamāvibhaktiḥ", "puṃlliṅgam"],
    }
    assert normalize.parse_sp_tag_group(group) == {
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
    assert normalize.parse_sp_tag_group({"tags": [tag]}) == {"vibhakti": expected}


@pytest.mark.parametrize("tag, short", [
    ("ekavacanam", "eka"),
    ("dvivacanam", "dvi"),
    ("bahuvacanam", "bahu"),
])
def test_parse_vacana_tag(tag, short):
    assert normalize.parse_sp_tag_group({"tags": [tag]}) == {"vacana": short}


@pytest.mark.parametrize("linga", [
    "puṃlliṅgam",
    "strīliṅgam",
    "napuṃsakaliṅgam",
])
def test_parse_linga_tag_keeps_full_tag(linga):
    assert normalize.parse_sp_tag_group({"tags": [linga]}) == {"linga": linga}


def test_unrecognized_tags_survive_in_residual_list_stripped_and_ordered():
    group = {
        "root": "rāma",
        "tags": ["samāsapūrvapadanāmapadam", " bahuvacanam ", "\tkaviḥ", "aniṣṭa"],
    }
    parsed = normalize.parse_sp_tag_group(group)
    assert parsed["vacana"] == "bahu"
    assert parsed["tags"] == ["samāsapūrvapadanāmapadam", "kaviḥ", "aniṣṭa"]
    assert "vibhakti" not in parsed


def test_vibhakti_regex_is_anchored():
    # A near-miss tag must fall through to the residual list, not match.
    assert normalize.parse_sp_tag_group(
        {"tags": ["prathamāvibhaktiḥpurvam"]}
    ) == {"tags": ["prathamāvibhaktiḥpurvam"]}


def test_root_only_emitted_when_nonempty():
    assert normalize.parse_sp_tag_group({"root": "", "tags": ["ekavacanam"]}) == {
        "vacana": "eka"
    }
    assert normalize.parse_sp_tag_group({"root": "agni", "tags": []}) == {
        "root": "agni"
    }


def test_empty_group_returns_none():
    assert normalize.parse_sp_tag_group({"tags": []}) is None
    assert normalize.parse_sp_tag_group({}) is None


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
    assert normalize._morph_rank(_FULL_READING) > normalize._morph_rank(
        _COMPOUND_FRAGMENT
    )
    assert max([_COMPOUND_FRAGMENT, _FULL_READING], key=normalize._morph_rank) is (
        _FULL_READING
    )
    assert max([_FULL_READING, _COMPOUND_FRAGMENT], key=normalize._morph_rank) is (
        _FULL_READING
    )


def test_rank_prefers_vacana_then_fewer_residual_tags():
    case_and_number = {"vibhakti": "tṛtīyā", "vacana": "eka"}
    case_only = {"vibhakti": "tṛtīyā"}
    assert normalize._morph_rank(case_and_number) > normalize._morph_rank(case_only)

    lean = {"vibhakti": "tṛtīyā", "tags": ["kaviḥ"]}
    heavy = {"vibhakti": "tṛtīyā", "tags": ["kaviḥ", "aniṣṭa"]}
    assert normalize._morph_rank(lean) > normalize._morph_rank(heavy)


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
    assert normalize.collect_sp_morphology(sp_output) == {
        "vāgarthāviva": {
            "root": "vāgartha",
            "vacana": "bahu",
            "vibhakti": "prathamā",
            "linga": "puṃlliṅgam",
        },
        "vāgartha": {"root": "vāgartha", "tags": ["samāsapūrvapadanāmapadam"]},
    }


def test_collect_morphology_anusvara_collision_winner_is_order_independent():
    expected = {"sampṛktau": {
        "root": "vāgartha",
        "vacana": "bahu",
        "vibhakti": "prathamā",
        "linga": "puṃlliṅgam",
    }}
    forward = normalize.collect_sp_morphology(_nested_sp_raw(fragment_first=False))
    reverse = normalize.collect_sp_morphology(_nested_sp_raw(fragment_first=True))
    assert forward == expected  # full reading wins over the fragment
    assert reverse == forward   # not last-writer-wins


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
    assert normalize.collect_sp_decompositions(sp_output) == {
        "agnim": ["agni"],
        "mixed": ["upa", "x"],
    }


@pytest.mark.parametrize("sp_output", [{}, {"word_decompositions": None}])
def test_collect_decompositions_missing_input_gives_empty_dict(sp_output):
    assert normalize.collect_sp_decompositions(sp_output) == {}


def test_collect_decompositions_collision_prefers_more_parts():
    sp_output = {"word_decompositions": {
        "sampṛktau": ["sa", "pṛktau"],
        "saṃpṛktau": ["sampṛktau"],
    }}
    assert normalize.collect_sp_decompositions(sp_output) == {
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
    assert normalize.collect_sp_decompositions({"word_decompositions": pair}) == {
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
    assert normalize.collect_dm_tokens(dm_output) == [
        {"form": "agnim", "lemma": "agni", "kosha_type": "noun"},
        {"form": "vīra"},
    ]


@pytest.mark.parametrize("dm_output", [{}, {"tokens": None}, {"tokens": []}])
def test_collect_dm_tokens_missing_or_empty_tokens(dm_output):
    assert normalize.collect_dm_tokens(dm_output) == []


def test_group_dm_tokens_greedy_first_letter_split():
    words = ["havyaṃ", "pavakaḥ"]
    tokens = [{"form": "havyam"}, {"form": "hutaḥ"}, {"form": "kaviḥ"}]
    groups = normalize.group_dm_tokens_by_word(words, tokens)
    assert [[t["form"] for t in g] for g in groups] == [
        ["havyam", "hutaḥ"],
        ["kaviḥ"],
    ]


def test_group_dm_tokens_attaches_trailing_tokens_to_last_pada():
    tokens = [{"form": "agnim"}, {"form": "sūryaḥ"}]  # 's' matches no word letter
    groups = normalize.group_dm_tokens_by_word(["agniḥ"], tokens)
    assert [[t["form"] for t in g] for g in groups] == [["agnim", "sūryaḥ"]]


def test_group_dm_tokens_empty_inputs():
    empty_groups = normalize.group_dm_tokens_by_word(["agniḥ", "īḷe"], [])
    assert empty_groups == [[], []]
    # No pada to attach to: unmatched tokens are dropped, not fabricated.
    assert normalize.group_dm_tokens_by_word([], [{"form": "agnim"}]) == []


# ---------------------------------------------------------------------------
# _diff_regions
# ---------------------------------------------------------------------------

def test_diff_regions_equal_sequences_including_anusvara_variants():
    assert normalize._diff_regions(["dharmaḥ"], ["dharmaḥ"]) == []
    assert normalize._diff_regions(["saṃpṛktau"], ["sampṛktau"]) == []


def test_diff_regions_replace_region():
    assert normalize._diff_regions(
        ["agniḥ", "samiddhaḥ"], ["agniḥ", "samiddham"]
    ) == [{"sanskrit_parser": ["samiddhaḥ"], "dharmamitra": ["samiddham"]}]


def test_diff_regions_insert_and_delete_have_null_sides():
    assert normalize._diff_regions(["dharmaḥ"], ["dharmaḥ", "cāraḥ"]) == [
        {"sanskrit_parser": None, "dharmamitra": ["cāraḥ"]}
    ]
    assert normalize._diff_regions(["tapaḥ", "dhena"], []) == [
        {"sanskrit_parser": ["tapaḥ", "dhena"], "dharmamitra": None}
    ]


# ---------------------------------------------------------------------------
# _dm_word_entry / _sp_word_entry
# ---------------------------------------------------------------------------

def test_dm_word_entry_filters_keys():
    assert normalize._dm_word_entry({"form": "agnim"}) == {"form": "agnim"}
    assert normalize._dm_word_entry(
        {"form": "agnim", "lemma": "agni", "kosha_type": "noun"}
    ) == {"form": "agnim", "lemma": "agni", "type": "noun"}
    # Empty enrichment values are dropped.
    assert normalize._dm_word_entry(
        {"form": "agnim", "lemma": "", "kosha_type": ""}
    ) == {"form": "agnim"}


def test_sp_word_entry_looks_up_normalized_form_and_filters_keys():
    morph = {"sampṛktau": {
        "root": "vāgartha",
        "vibhakti": "prathamā",
        "vacana": "eka",
        "linga": "puṃlliṅgam",
        "tags": ["samāsapūrvapadanāmapadam"],
    }}
    assert normalize._sp_word_entry("saṃpṛktau", morph) == {
        "form": "saṃpṛktau",
        "root": "vāgartha",
        "vibhakti": "prathamā",
        "vacana": "eka",
        "linga": "puṃlliṅgam",
        "tags": ["samāsapūrvapadanāmapadam"],
    }
    assert normalize._sp_word_entry("īḷe", morph) == {"form": "īḷe"}
    # Falsy morphology fields are not copied.
    assert normalize._sp_word_entry(
        "agni", {"agni": {"root": "", "tags": [], "vacana": "eka"}}
    ) == {"form": "agni", "vacana": "eka"}


# ---------------------------------------------------------------------------
# build_padas / build_padaccheda
# ---------------------------------------------------------------------------

def _tapodhena_fixture():
    """One split pada where SP and DM disagree on the segmentation."""
    return (
        ["tapodhena"],
        {"tapodhena": ["tapaḥ", "dhena"]},
        {"tapaḥ": {"root": "tapas", "vibhakti": "ṣaṣṭhī", "vacana": "eka"}},
        [[{"form": "tapodhena", "lemma": "tapa"}]],
    )


def test_build_padas_full_entry_with_differences_when_dm_available():
    words, sp_decomp, sp_morph, dm_groups = _tapodhena_fixture()
    padas = normalize.build_padas(words, sp_decomp, sp_morph, dm_groups, True)
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
    padas = normalize.build_padas(words, sp_decomp, sp_morph, dm_groups, False)
    assert set(padas[0]) == {"pada", "dharmamitra", "sanskrit_parser"}
    assert "differences" not in padas[0]


def test_build_padas_empty_dm_group_with_dm_available_gives_null_side():
    padas = normalize.build_padas(
        ["tapodhena"], {"tapodhena": ["tapaḥ", "dhena"]}, {}, [[]], True
    )
    assert padas[0]["dharmamitra"] is None
    assert padas[0]["differences"] == [
        {"sanskrit_parser": ["tapaḥ", "dhena"], "dharmamitra": None}
    ]


def test_build_padas_agreeing_engines_get_no_differences_key():
    padas = normalize.build_padas(
        ["agnim"], {"agnim": ["agnim"]}, {}, [[{"form": "agnim"}]], True
    )
    assert "differences" not in padas[0]
    assert padas[0]["dharmamitra"]["padaccheda"] == ["agnim"]


def test_build_padaccheda_none_for_engines_without_parts():
    assert normalize.build_padaccheda([]) == {
        "dharmamitra": None,
        "sanskrit_parser": None,
    }
    padas = [{
        "pada": "x",
        "dharmamitra": None,
        "sanskrit_parser": {"padaccheda": [], "words": []},
    }]
    assert normalize.build_padaccheda(padas) == {
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
    assert normalize.build_padaccheda(padas) == {
        "dharmamitra": "agnim",
        "sanskrit_parser": "agni | īḷe",
    }


# ---------------------------------------------------------------------------
# normalize_raw
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
def test_normalize_raw_requires_string_input_iast(raw):
    with pytest.raises(ValueError) as excinfo:
        normalize.normalize_raw(raw)
    assert str(excinfo.value) == "raw output has no usable 'input.iast' string"


def _clean_raw() -> dict:
    return {
        "mode": "verbose",
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


def test_normalize_raw_clean_fixture_end_to_end():
    normalized = normalize.normalize_raw(_clean_raw())
    assert normalized == {
        "mode": "verbose",
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


def test_normalize_raw_records_engine_error_and_keeps_other_engine():
    raw = _clean_raw()
    raw["engine_outputs"] = {
        "sanskrit_parser": raw["engine_outputs"]["sanskrit_parser"],
        "dharmamitra": {"error": "boom"},
    }
    normalized = normalize.normalize_raw(raw)
    assert normalized["engine_errors"] == {"dharmamitra": "boom"}
    # The surviving engine is still processed; DM side is empty everywhere.
    assert normalized["padaccheda"]["sanskrit_parser"] == "agni | īḷe"
    assert all(p["dharmamitra"] is None for p in normalized["padas"])
    # No DM tokens at all -> dm_available False -> no differences fabricated.
    assert all("differences" not in p for p in normalized["padas"])


def test_normalize_raw_error_key_coerced_to_str_and_both_engines_recorded():
    raw = {
        "input": {"iast": "agnim"},
        "engine_outputs": {
            "sanskrit_parser": {"error": "sp failed"},
            "dharmamitra": {"error": 7},
        },
    }
    normalized = normalize.normalize_raw(raw)
    assert normalized["engine_errors"] == {
        "sanskrit_parser": "sp failed",
        "dharmamitra": "7",
    }


def test_normalize_raw_without_engine_outputs_at_all():
    raw = {"input": {"iast": "agnim īḷe"}}
    normalized = normalize.normalize_raw(raw)
    assert normalized["mode"] is None  # 'mode' passed through from raw
    assert normalized["padaccheda"] == {
        "dharmamitra": None,
        "sanskrit_parser": "agnim | īḷe",
    }
    assert [p["pada"] for p in normalized["padas"]] == ["agnim", "īḷe"]
    assert all(p["dharmamitra"] is None for p in normalized["padas"])
    assert all("differences" not in p for p in normalized["padas"])
    assert "engine_errors" not in normalized
