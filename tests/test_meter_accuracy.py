"""Meter accuracy: the published छन्दः next to vidyut's best effort.

The reference is ``tests/data/meter_truth.json`` — for each of the sixteen pinned verses, the metre
printed by sanskritsahitya.org ('छन्दः <name> [<aksharas/pāda>: <gaṇas>]' plus its छन्दोविश्लेषणम्
grid), cross-checked against that site's own data repository. Nothing here re-derives the truth from
the engines; the point is to keep the published reading visible beside what our tools produce, and to
say out loud which misses are ours and which belong to vidyut.

Measured on 2026-10-09 against the committed result documents:

* The *shape* we report is right everywhere: `pada_count` and `aksharas_per_pada` match the published
  grids for all sixteen verses (8·8·8·8 for the śloka of Raghuvaṃśa I and the Gītā, 11·11·11·11 for
  BG 2.22 / 11.15 / 15.5 / 15.15, 21 and 15 for the Śākuntala metres). Counting aksharas is vidyut's
  scanning step, and it agrees with the published grids.
* The *name* is right wherever vidyut's table can express it: इन्द्रवज्रा (BG 15.5, 15.15), स्रग्धरा
  (Śākuntala 1.1, 1.7) and मालिनी (Śākuntala 1.18) — five verses. BG 15.5 / 15.15 only pass because
  `_summarize_chandas` refuses to let a name whose declared length contradicts the pāda it was given
  decide the verse: vidyut's classifier matches gaṇa prefixes, so it also offers the 12-akshara
  indravaṃśā for an 11-akshara pāda. That candidate stays visible in `candidates`.
* The name is missing for the eleven remaining verses, and that is a limit of vidyut's table rather
  than something our code can fix: data-0.4.0/chandas/meters.tsv holds 145 vṛtta patterns and no jāti
  entry at all, so anuṣṭubh (Raghuvaṃśa 1.1–1.7, BG 2.47, BG 18.66) and upajāti (BG 2.22, BG 11.15)
  cannot be named — vidyut instead offers short vṛtta prefixes (mṛgī, vasumatī, candralekhā,
  madalekhā, śuddhavirāṭ, jaloddhatagati, upasthita) or classifies nothing at all. Those verses are
  pinned as `vrtta: null` so that the gap is visible in the fixtures; if vidyut ever ships jāti metres
  this test fails and the pins get re-measured.

Metre names come from vidyut's own SLP1 → IAST table, which writes मालिनी as 'malinī'; the fixture
records that spelling beside the published one instead of us quietly rewriting it.
"""

import json
from pathlib import Path
import warnings

import pytest

DATA_DIR = Path(__file__).resolve().parent / "data"

TRUTH = {
    stem: row
    for stem, row in json.loads((DATA_DIR / "meter_truth.json").read_text(encoding="utf-8")).items()
    if not stem.startswith("_")
}

# Verses whose metre vidyut's 145-pattern vṛtta table can express at all. The rest are jāti metres
# (anuṣṭubh, upajāti) that the table does not contain; see the module docstring.
NAMEABLE_STEMS = sorted(stem for stem, row in TRUTH.items() if row["vidyut_can_name_it"])
JATI_STEMS = sorted(stem for stem, row in TRUTH.items() if not row["vidyut_can_name_it"])

# Measured 2026-10-09: every nameable verse is named correctly; the jāti verses cannot be.
MIN_NAMED_CORRECTLY = 5


def _chandas(stem: str) -> dict:
    """Return the committed result document's chandas block for one pinned verse."""
    path = DATA_DIR / "results" / f"{stem}.result.json"
    return json.loads(path.read_text(encoding="utf-8"))["chandas"]


def _expected_name(stem: str) -> str:
    """The spelling vidyut's own transliteration table produces for the published metre."""
    row = TRUTH[stem]
    return row.get("vidyut_spelling", row["chandas_iast"])


# ---------------------------------------------------------------------------
# Shape: akshara counts are the part we do get right
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stem", sorted(TRUTH))
def test_scanned_shape_matches_the_published_grid(stem):
    """Pāda count and per-pāda akshara counts must equal the published छन्दोविश्लेषणम् grid."""
    chandas = _chandas(stem)
    row = TRUTH[stem]
    assert chandas["pada_count"] == row["pada_count"], stem
    assert chandas["aksharas_per_pada"] == row["aksharas_per_pada"], (
        f"{stem}: vidyut scanned {chandas['aksharas_per_pada']}, "
        f"the published grid is {row['aksharas_per_pada']} ({row['url']})"
    )


# ---------------------------------------------------------------------------
# Name: what vidyut can name, and the length check that lets it be named
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stem", NAMEABLE_STEMS)
def test_metre_vidyut_can_name_is_named(stem):
    """The five verses whose metre is in vidyut's table must come out with that name."""
    chandas = _chandas(stem)
    assert chandas["vrtta"] == _expected_name(stem), (
        f"{stem}: expected {TRUTH[stem]['chandas_devanagari']} "
        f"({TRUTH[stem]['url']}), vidyut offered vrtta={chandas['vrtta']!r} "
        f"candidates={chandas['candidates']}"
    )


def test_impossible_candidate_does_not_veto_the_indravajra_reading():
    """BG 15.5 / 15.15 are इन्द्रवज्रा; vidyut also misnames a pāda with the 12-akshara indravaṃśā.

    The wrong name must stay visible in `candidates` while the length-aware vote still settles on
    indravajrā — that is the regression this pin exists for.
    """
    for stem in ("bhagavad_gita-15.5", "bhagavad_gita-15.15"):
        chandas = _chandas(stem)
        assert chandas["vrtta"] == "indravajrā", stem
        assert "indravaṃśā" in chandas["candidates"], (
            f"{stem}: vidyut's impossible 12-akshara candidate disappeared: {chandas['candidates']}"
        )


# ---------------------------------------------------------------------------
# The upstream gap, kept visible instead of hidden
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stem", JATI_STEMS)
def test_jati_metres_stay_unnamed_by_vidyut(stem):
    """anuṣṭubh and upajāti are absent from meters.tsv, so `vrtta` must stay null.

    Pinning the miss is deliberate: it keeps a limit of the tool in the fixtures where a reader (and
    the next person to bump vidyut) can see it, rather than letting us claim a metre we cannot derive.
    """
    chandas = _chandas(stem)
    row = TRUTH[stem]
    if chandas["vrtta"] is not None:
        pytest.fail(
            f"{stem}: vidyut now names {chandas['vrtta']!r}; the pinned 'cannot name anuṣṭubh/"
            f"upajāti' expectation needs re-measuring against {row['url']}"
        )
    warnings.warn(
        f"{stem}: published metre is {row['chandas_devanagari']} ({row['chandas_iast']}, "
        f"{row['aksharas_per_pada']} aksharas — {row['url']}), but vidyut's chandas table holds only "
        f"145 vṛtta patterns and no jāti metre, so it can only offer {chandas['candidates'] or 'nothing'}; "
        "expected, cannot be changed in our code without a metre table of our own",
        stacklevel=1,
    )


def test_meter_report_prints_truth_next_to_best_effort():
    """One readable truth-vs-best-effort table for the whole pinned corpus."""
    named = 0
    lines = [f"{'verse':26} {'published':28} {'vidyut vrtta':14} {'scanned':20} verdict"]
    for stem in sorted(TRUTH):
        row, chandas = TRUTH[stem], _chandas(stem)
        published = f"{row['chandas_devanagari']} {row['chandas_iast']}"
        got = chandas["vrtta"] or "—"
        if got == _expected_name(stem):
            named += 1
            verdict = "named"
        elif row["vidyut_can_name_it"]:
            verdict = "MISSED by us/vidyut"
        else:
            verdict = "jāti metre absent from vidyut's table"
        lines.append(
            f"{stem:26} {published:28} {got:14} "
            f"{str(chandas['aksharas_per_pada']):20} {verdict}"
        )
    print("\n".join(lines))
    assert named >= MIN_NAMED_CORRECTLY, (
        f"only {named}/{len(TRUTH)} verses carry the published metre name; "
        f"{[s for s in NAMEABLE_STEMS if _chandas(s)['vrtta'] != _expected_name(s)]} are nameable "
        "and were missed"
    )
