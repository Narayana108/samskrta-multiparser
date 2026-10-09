"""Morphology accuracy: the published reading next to what sanskrit_parser actually chose.

The reference is ``tests/data/morphology_truth.json`` — nine forms from the pinned verses whose
value in *that verse* follows from the published पदच्छेदः and standard grammar (BG 18.66
mām ekaṃ śaraṇaṃ vraja, Raghuvaṃśa 1.1 jagataḥ pitarau vande, …). Nothing here is derived from our
engines; the point is to keep the right answer visible beside the reading we publish.

Measured on 2026-10-09 against the committed result documents:

* The engine *offers* the published reading for all nine forms — it is either the primary reading or
  one of the `alternates` that ``postprocess_analysis`` keeps visible. So these misses are never
  "the tool does not know"; they are ranking decisions.
* Our published primary is right for four of them (pitarau, deva, navāni, avyayam). The other five
  (vande, jagataḥ, asti, vraja, śucaḥ) need the sentence to pick: sanskrit_parser analyses each pada
  on its own and offers every reading the grammar allows — for śucas it offered twenty-seven. No
  ordering of context-free readings can settle them.

Two ranking rules were tried against this fixture and rejected on the numbers, so that nobody re-tries
them blind: putting finite-verb readings first changes the primary of 20 words across the sixteen
verses (about 8 improvements — vande, vraja, asti, yāti — and about 12 regressions such as navāni read
as √nu and deva as an imperative of √dev); preferring readings whose stem is exactly attested in the
vidyut kosha changes 15 (about 3 improvements, about 10 regressions). Both are net losses, so the
primary stays where ``_morph_rank`` puts it and every other reading travels in `alternates`.

The four forms our ranking does get right are asserted as primary; the five context-dependent ones only
have to be present among the readings we publish, and each warns with the upstream limit that explains
why it cannot be chosen.
"""

import json
from pathlib import Path
import warnings

import pytest

DATA_DIR = Path(__file__).resolve().parent / "data"

ROWS = json.loads((DATA_DIR / "morphology_truth.json").read_text(encoding="utf-8"))["rows"]
BY_ID = {f"{row['verse']}/{row['form']}": row for row in ROWS}

# Forms whose reading our context-free ranking picks correctly today; the rest carry a `limit`.
CONTEXT_FREE_OK = sorted(key for key, row in BY_ID.items() if "limit" not in row)
CONTEXT_DEPENDENT = sorted(key for key, row in BY_ID.items() if "limit" in row)


def _published_readings(stem: str, form: str):
    """Return (primary, alternates) that the committed result document publishes for one form."""
    path = DATA_DIR / "results" / f"{stem}.result.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    for pada in doc["padas"]:
        for word in (pada.get("sanskrit_parser") or {}).get("words", []):
            if word.get("form") == form:
                primary = {k: v for k, v in word.items() if k != "alternates"}
                return primary, word.get("alternates", [])
    raise AssertionError(f"{stem}: no published reading for {form!r}")


def _matches(reading: dict, expected: dict) -> bool:
    """Check one engine reading against one reference reading (fields equal, tags a superset)."""
    for key, value in expected.items():
        if key == "tags":
            if not set(value) <= set(reading.get("tags", [])):
                return False
        elif reading.get(key) != value:
            return False
    return True


# ---------------------------------------------------------------------------
# What the tool can do: the published reading is among the readings we publish
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(BY_ID))
def test_published_reading_is_among_the_readings(key):
    """The reference reading must appear as primary or in `alternates` — never silently dropped."""
    row = BY_ID[key]
    primary, alternates = _published_readings(row["verse"], row["form"])
    offered = [primary, *alternates]
    if not any(_matches(got, want) for got in offered for want in row["expected"]):
        pytest.fail(
            f"{key}: expected one of {row['expected']} ({row['source']}); sanskrit_parser published "
            f"primary={primary} plus {len(alternates)} alternates"
        )


# ---------------------------------------------------------------------------
# What we get right on our own, and what the tool's context-free ranking cannot do
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", CONTEXT_FREE_OK)
def test_primary_reading_is_the_published_one(key):
    """Forms whose reading does not depend on the sentence must come out as primary."""
    row = BY_ID[key]
    primary, alternates = _published_readings(row["verse"], row["form"])
    assert any(_matches(primary, want) for want in row["expected"]), (
        f"{key}: published primary={primary} but the verse reads {row['expected']} ({row['source']}); "
        f"alternates were {alternates}"
    )


@pytest.mark.parametrize("key", CONTEXT_DEPENDENT)
def test_context_dependent_reading_stays_visible(key):
    """The five forms no per-pada analysis can settle: keep the miss visible, do not hide it.

    These warn rather than fail because the reading we publish is one the grammar does allow for those
    letters; only the sentence decides which. The `limit` text in the fixture names that limit.
    """
    row = BY_ID[key]
    primary, alternates = _published_readings(row["verse"], row["form"])
    if any(_matches(primary, want) for want in row["expected"]):
        pytest.fail(
            f"{key}: the context-dependent reading is now primary; re-measure whether a ranking rule "
            f"can be pinned here instead of warned about"
        )
    warnings.warn(
        f"{key}: verse reads {row['expected']} ({row['source']}); we publish primary={primary} and keep "
        f"the right reading among {len(alternates)} alternates — expected: {row['limit']}",
        stacklevel=1,
    )


def test_accuracy_summary(capsys):
    """Print the score this file exists to show: offered by the tool vs chosen by our ranking."""
    offered = correct = 0
    for key, row in BY_ID.items():
        primary, alternates = _published_readings(row["verse"], row["form"])
        offered += any(_matches(g, w) for g in [primary, *alternates] for w in row["expected"])
        hit = any(_matches(primary, w) for w in row["expected"])
        correct += hit
        print(f"{key:44s} primary={'ok' if hit else 'see alternates'}")
    print(f"reference forms: {len(BY_ID)}  offered by sanskrit_parser: {offered}  chosen by our ranking: {correct}")
    assert offered == len(BY_ID), "the engine stopped offering a reference reading"
    assert correct >= len(CONTEXT_FREE_OK), (
        f"our ranking lost one of the {len(CONTEXT_FREE_OK)} forms it used to choose correctly"
    )
