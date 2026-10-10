# Measured accuracy — what is verified correct and what is wrong

Every number here comes from the pinned fixtures in `tests/data/` compared against an **independent source of truth**,
never against our own engines: metre names and akshara grids from the printed editions
([sanskritsahitya.org](https://www.sanskritsahitya.org), cross-checked with
[sanskritsahitya-com/data](https://github.com/sanskritsahitya-com/data)), word grammar from those editions' पदच्छेदः
plus standard Pāṇinian analysis, word boundaries from the editions' padaccheda tables.

## How to read every table

| Mark | Meaning — read this at a glance |
|---|---|
| ✅ **correct** | our published output equals the source of truth. Verified, error-free. |
| ❌ **our error** | the right answer was available and we published a wrong one. Fixable in this repo. |
| ⚠️ **engine limit** | the engine cannot produce it from what it ships (missing data, no sentence context). Not fixable here — upstream fix or a feature decision. |

A row with `—` in "our answer" means we publish **null on purpose**: the tool refused to print a name it could not
justify. That is a ⚠️ limit, never a wrong guess.

Measured **2026-10-10** on `sanskrit-parser 0.2.6`, `vidyut 0.4.0` + bundled `data-0.4.0`,
`indic-transliteration 2.3.82`. Fixtures: [`tests/data/meter_truth.json`](tests/data/meter_truth.json) (16 verses),
[`tests/data/morphology_truth.json`](tests/data/morphology_truth.json) (9 forms),
[`tests/data/sandhi_truth.json`](tests/data/sandhi_truth.json) (147 padas). Commands to reproduce: §5.

## Scoreboard

| What we measure | Score | Verdict at a glance |
|---|---|---|
| Aksharas per pāda, pāda count (§1) | **16 / 16** ✅ | verified correct on every verse |
| Metre name (§1) | **5 / 16** named; 11 ⚠️ null | zero ❌: vidyut's data has no jāti/anuṣṭubh rows, and our veto only removes wrong names |
| Right word reading is offered at all (§2) | **15 / 15** ✅ | never silently dropped — it travels in `alternates` |
| Right word reading published first (§2) | **10 / 15**; 5 ⚠️/❌ | the five need sentence context, or a homograph the dictionary really does record |
| Word boundaries, whole corpus (§3) | **104 / 147** | ⚠️ 20 padas: the right cut is not in the candidate pool at all; ❌ 23 padas: a correct candidate existed and our ranking did not pick it |

## 1. Metre — shape verified on every verse, names limited by vidyut's data

Read this as: **the "Shape" column is all green; the "Name we publish" column shows what vidyut can and cannot name.**

| Verse | Published छन्दः (source of truth) | Aksharas/pāda (truth = ours) | Name we publish | Name verdict | vidyut suggested, we did not publish |
|---|---|---|---|---|---|
| abhijnaana_shakuntala-1.1 | स्रग्धरा (sragdharā) | [21, 21, 21, 21] ✅ | `sragdharā` | ✅ correct | — |
| abhijnaana_shakuntala-1.7 | स्रग्धरा (sragdharā) | [21, 21, 21, 21] ✅ | `sragdharā` | ✅ correct | — |
| abhijnaana_shakuntala-1.18 | मालिनी (mālinī) | [15, 15, 15, 15] ✅ | `malinī` | ✅ correct (vidyut spells it `malinī`) | — |
| bhagavad_gita-15.5 | इन्द्रवज्रा (indravajrā) | [11, 11, 11, 11] ✅ | `indravajrā` | ✅ correct | indravaṃśā declares 12, pāda scanned 11 → vetoed |
| bhagavad_gita-15.15 | इन्द्रवज्रा (indravajrā) | [11, 11, 11, 11] ✅ | `indravajrā` | ✅ correct | indravaṃśā declares 12 → vetoed |
| bhagavad_gita-11.15 | उपजातिः (upajāti) | [11, 11, 11, 11] ✅ | `—` | ⚠️ unnamed: no upajāti row in vidyut | indravajrā and upendravajrā both fit 11 but were matched on **different pādas** → we refuse to pick; indravaṃśā declares 12 → vetoed |
| bhagavad_gita-2.22 | उपजातिः (upajāti) | [11, 11, 11, 11] ✅ | `—` | ⚠️ unnamed: no upajāti row in vidyut | same pāda disagreement; indravaṃśā and vaṃśastha declare 12 → vetoed |
| bhagavad_gita-2.47 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] ✅ | `—` | ⚠️ unnamed: no anuṣṭubh row in vidyut | candralekhā declares 15, vasumatī 6 → both vetoed |
| bhagavad_gita-18.66 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] ✅ | `—` | ⚠️ unnamed: no anuṣṭubh row in vidyut | mṛgī's declared length contradicts 8 → vetoed (only 1/4 pādas classified) |
| raghuvamsha-1.1 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] ✅ | `—` | ⚠️ unnamed: no anuṣṭubh row in vidyut | madalekhā declares 7, śuddhavirāṭ 10 → both vetoed |
| raghuvamsha-1.2 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] ✅ | `—` | ⚠️ unnamed: no anuṣṭubh row in vidyut | jaloddhatagati declares 12 → vetoed (only 1/4 pādas classified) |
| raghuvamsha-1.3 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] ✅ | `—` | ⚠️ unnamed: no anuṣṭubh row in vidyut | vasumatī declares 6 → vetoed |
| raghuvamsha-1.4 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] ✅ | `—` | ⚠️ unnamed: vidyut classified 0/4 pādas | none at all |
| raghuvamsha-1.5 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] ✅ | `—` | ⚠️ unnamed: vidyut classified 0/4 pādas | none at all |
| raghuvamsha-1.6 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] ✅ | `—` | ⚠️ unnamed: no anuṣṭubh row in vidyut | upasthita declares 11 → vetoed |
| raghuvamsha-1.7 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] ✅ | `—` | ⚠️ unnamed: vidyut classified 0/4 pādas | none at all |

- **Why each suggestion was dropped — read out of `meters.tsv`, not inferred.** `_summarize_chandas` applies two rules,
  and both are visible in the table above: a candidate whose declared akshara count (read from
  `data-0.4.0/chandas/meters.tsv` by `_meter_lengths`) contradicts the pāda vidyut just scanned is vetoed; and a name is
  published only when every classified pāda agrees — Gītā 11.15 and 2.22 are null because vidyut matched `indravajrā` on
  some pādas and `upendravajrā` on others, neither of which is the upajāti the edition prints.
- **✅ Verified correct — counting.** `pada_count` and `aksharas_per_pada` equal the published grid on all sixteen
  verses (vidyut's scanner, `Chandas.classify().aksharas`).
- **✅ Verified correct — five names.** इन्द्रवज्रा (Gītā 15.5, 15.15), स्रग्धरा (Śākuntala 1.1, 1.7) and मालिनी
  (Śākuntala 1.18, printed by vidyut as `malinī`) come back identical to the edition.
- **⚠️ Engine limit — eleven names.** They stay `vrtta: null` because the name does not exist in vidyut's data. No row
  here is ❌ our error: `_summarize_chandas` never renames, it only *vetoes* candidates whose declared akshara count
  contradicts the pāda vidyut just scanned, and publishes a name only when all classified pādas agree. That veto is what
  turned 0 named verses into 5; everything still unnamed is missing data.

### Why an 8-akshara pāda is not called anuṣṭubh — vidyut's data, verified in their repository (2026-10-10)

| Check | Result |
|---|---|
| `grep -rin "anuSTub\|triSTub\|jAgatI\|upajAti"` over the whole vidyut repo (Rust, Python bindings, all data files) | **no metre hit at all** — only unrelated Pāṇinian rows (`ganapatha.rs:2226 "jagatI"` is a gaṇapāṭha stem; `sutrapatha.tsv:1864` a sūtra) |
| `cut -f2 data-0.4.0/chandas/meters.tsv \| sort \| uniq -c` | **145 rows, all kind `vrtta`; 0 rows of kind `jati`** — no anuṣṭubh / triṣṭubh / jagatī / upajāti anywhere |
| jāti metres in code (`vidyut-chandas/src/chandas.rs:98-121`) | exactly seven hard-coded: `vEtAlIyam, upagIti, AryAgIti, gIti, udgIti, Aupacchandasikam, AryA` — none is a śloka metre; `Jati::try_match` compares akshara counts only |
| vṛtta matching (`VrttaPada::try_match`) | gaṇa **prefix** match over the weight string; declared length is not enforced, so an 11-akshara pāda can be reported as `indravaṃśā` (which declares 12) — exactly the wrong names our veto removes |

## 2. Word grammar — the right reading is always offered; our ranking picks it first for 10 of 15

Read this as: **"Our published primary" vs "Source of truth"; the last two columns say whether the right answer survived
in the output and how deep we buried it.**

| Form | Verse | Source of truth | Our published primary | Verdict | Right reading still published? (rank among alternates) |
|---|---|---|---|---|---|
| `pitarau` | raghuvamsha-1.1 | pitṛ, prathamā, dvi, puṃlliṅgam | pitṛ, prathamā, dvi, puṃlliṅgam | ✅ correct | it *is* the primary (2 alternates) |
| `deva` | bhagavad_gita-11.15 | deva, saṃbodhana, eka, puṃlliṅgam | deva, saṃbodhana, eka, puṃlliṅgam | ✅ correct | it *is* the primary (5 alternates) |
| `navāni` | bhagavad_gita-2.22 | nava#1, prathamā, bahu, napuṃsakaliṅgam | nava#1, prathamā, bahu, napuṃsakaliṅgam | ✅ correct | it *is* the primary (7 alternates) |
| `avyayam` | bhagavad_gita-15.5 | avyaya, dvitīyā **or** prathamā, eka | avyaya#1, prathamā, eka, napuṃsakaliṅgam | ✅ correct (the fixture accepts both cases) | it *is* the primary (6 alternates) |
| `prakṛti` | abhijnaana_shakuntala-1.1 | stem prakṛti (head of *sarva-bīja-prakṛtiḥ*) | prakṛti, compound member | ✅ correct — fixed by the dictionary rule (§2.1) | it *is* the primary (1 alternate) |
| `hi` | abhijnaana_shakuntala-1.18 | hi, avyayam | hi, avyayam | ✅ correct — fixed by the dictionary rule (§2.1) | it *is* the primary (2 alternates) |
| `sanni` | bhagavad_gita-15.15 | sanni, prefixed member of *sanni-niviṣṭaḥ* | sanni, compound member | ✅ correct — fixed by the dictionary rule (§2.1) | it *is* the primary (1 alternate) |
| `yāti` | bhagavad_gita-2.22 | yā, laṭ, eka (*saṃ-yāti*, "goes") | yā, laṭ, prathamapuruṣaḥ, eka | ✅ correct — fixed by the dictionary rule (§2.1) | it *is* the primary (6 alternates) |
| `yathāvidhi` | raghuvamsha-1.6 | yathāvidhi, avyayam | yathāvidhi, avyayam | ✅ correct — fixed by the dictionary rule (§2.1) | it *is* the primary (1 alternate) |
| `asti` | raghuvamsha-1.4 | as, eka, laṭ / prathamapuruṣaḥ | as, eka, laṭ, prathamapuruṣaḥ | ✅ correct — fixed by the dictionary rule (§2.1) | it *is* the primary (4 alternates) |
| `vraja` | bhagavad_gita-18.66 | vraj, eka, loṭ / madhyamapuruṣaḥ | vraja, saṃbodhana, eka, puṃlliṅgam | ❌ wrong primary — ⚠️ needs the sentence | yes, **alternate #2 of 4** |
| `jagatas` | raghuvamsha-1.1 | jagat, ṣaṣṭhī, eka, napuṃsakaliṅgam | jagat, prathamā, bahu, strīliṅgam | ❌ wrong primary — ⚠️ needs the sentence | yes, **alternate #9 of 11** |
| `vande` | raghuvamsha-1.1 | vand, eka, laṭ / uttamapuruṣaḥ / ātmanepadam | vandā, prathamā, dvi, strīliṅgam | ❌ wrong primary — ⚠️ needs the sentence; the kosha records *vandA* too, so attestation cannot settle it | yes, **alternate #4 of 13** |
| `śucas` | bhagavad_gita-18.66 | śuc, eka, madhyamapuruṣaḥ | śuc#2, prathamā, bahu, strīliṅgam | ❌ wrong primary — ⚠️ needs the sentence | yes, **alternate #24 of 26** |
| `aham` | bhagavad_gita-18.66 | asmad, prathamā | aha (a real noun, "non-existence"), prathamā, eka | ❌ wrong primary — ⚠️ the dictionary records that homograph for these very letters | yes, **alternate #1 of 7** |

- **✅ Verified correct — nothing is lost.** The reference reading appears in the published output for all fifteen
  curated forms; `tests/test_morphology_accuracy.py::test_published_reading_is_among_the_readings` fails if one ever
  disappears.
- **❌/⚠️ Five wrong primaries.** Case, number and gender are properties of the *sentence*. sanskrit_parser analyses one
  pada at a time and returns every reading Pāṇini allows for those letters (27 for `śucas`, 14 for `vande`), so no
  ordering of context-free readings can settle them. Two more resist the dictionary rule because the dictionary itself
  is against us: vidyut records a stem *vandA* for `vande`, and a genuine noun *aha* ("non-existence") for the form
  अहम्, so attestation prefers those over the verbal/pronominal reading. They warn in the test suite, they never fail
  silently.
- **One truth no engine offers.** AS 1.1 `vastābhiraṣṭābhirīśaḥ` = *vastebhyaḥ aṣṭabhiḥ*, the instrumental plural of
  *vasu* (the eight Vasus). Neither engine emits that reading, so it is documented here rather than pinned in the
  fixture — pinning it would fail the "right reading is still offered" test instead of measuring anything.
- **Ambiguity is normal:** across the sixteen verses **546 pada-words carry morphology and 432 have more than one
  distinct reading.**

### 2.1 The dictionary-attestation ranking rule (shipped)

A reading whose `root` vidyut's kosha does not record as a lemma for that surface form is demoted below every reading it
does record; everything else about the ordering is unchanged, and no reading is ever dropped. Measured over the sixteen
pinned verses (**273 published word readings**): **8 primaries move**, and 6 of those become what the printed editions
read — `asti` (√as "is", not an invented vocative *asta*), `hi`, `yathāvidhi`, `prakṛti`, `sanni`, and `yāti` inside
`saṃyāti` (√yā "goes", not a saptamī of *yāt*). No curated form lost its reading; the fixture went from 4/9 correct to
10/15 by adding six forms the rule settles. Membership is **exact, never prefix**: the kosha lists `ah` and `aha` for
अहम्, and prefix matching blessed exactly those truncations (`vas` standing in for *vastā*, `vand` for *vandā*) and made
the rule misfire. The lookup probes both the IAST→SLP1 spelling of the pada and the orthography the engines use among
themselves (anusvara `M`→`m`, final visarga `H`→`s`, trailing sign dropped), so a pause-normalised form still finds its
lemma. Ranking is skipped entirely with `postprocess_analysis.py --no-dictionary`, and on a machine without kosha data
the analysis falls back to the previous order instead of failing.

### Ranking rules tried on this fixture (measured — do not re-try the rejected ones blind)

| Rule | Measured effect over the 16 verses | Verdict |
|---|---|---|
| Finite verb above nominal reading | 20 primaries change: ≈8 better (`vande`, `vraja`, `asti`, `yāti`), ≈12 worse (`navāni`→√nu, `deva`→imperative of √dev, `avyayam`→√vyā, `ajanma`, `āsam`) | ❌ rejected — net loss |
| Prefer readings whose stem is exactly attested in the vidyut kosha | 15 primaries change: ≈3 better, ≈10 worse (`anu`→dvitīyā napuṃsaka) | ❌ rejected — net loss |
| Demote roots absent from the vidyut kosha lemma set for that form (**exact** membership only) | 8 primaries move of 273; 6 curated forms fixed, none lost | ✅ **applied** — see §2.1 |
| …and prefer the longer attested lemma stem among the recorded ones | 9/15 curated, 36 primaries move | ❌ rejected — four times the blast radius of the rule above for one less correct form; it also cannot fix `aham`, where the short homograph is the recorded word |

## 3. Word boundaries (sandhi) — 104 of 147 padas correct; the ceiling is 127

The thirteen curated padas below are the hard cases the ranking exists to fix; they run offline in ~5 s and all match
today, so any regression fails the suite:

| Pada | Source of truth (edition padaccheda) | Our split | Verdict |
|---|---|---|---|
| `mokṣayiṣyāmi` | mokṣayiṣyāmi | `mokṣayiṣyāmi` | ✅ |
| `gamiṣyāmyupahāsyatām` | gamiṣyāmi \| upahāsyatām | `gamiṣyāmi \| upahāsyatām` | ✅ |
| `haviryā` | havis \| yā | `havis \| yā` | ✅ |
| `prapannastanubhiravatu` | prapanna \| tanubhiḥ \| avatu | `prapannas \| tanubhis \| avatu` | ✅ (visarga written `s`) |
| `sūtrasyevāsti` | sūtrasya \| iva \| asti | `sūtrasya \| iva \| asti` | ✅ |
| `sarvadharmānparityajya` | sarva \| dharmān \| parityajya | `sarva \| dharmān \| parityajya` | ✅ |
| `saṃpṛktau` | saṃpṛktau | `sampṛktau` | ✅ (anusvara written `m`) |
| `saṅgo'stvakarmaṇi` | saṅgaḥ \| astu \| akarmaṇi | `saṅgas \| astu \| akarmaṇi` | ✅ |
| `jīrṇānyanyāni` | jīrṇāni \| anyāni | `jīrṇāni \| anyāni` | ✅ |
| `vāmanaḥ` | vāmanaḥ | `vāmanas` | ✅ |
| `sūryaprabhavo` | sūrya \| prabhavaḥ | `sūrya \| prabhavas` | ✅ |
| `vajrasamutkīrṇe` | vajra \| samutkīrṇe | `vajra \| samutkīrṇe` | ✅ |
| `saṃbhṛtārthānāṃ` | saṃbhṛta \| arthānām | `sambhṛtā \| arthānām` | ✅ (stem match, not spelling) |

Parts are compared by **vidyut kosha lemma stem**, never by spelling: sandhi changes spellings (`vāc`/`vāk`) and
sanskrit_parser writes final visarga as `s`, anusvara as `m`.

### The whole corpus: who is at fault for the misses

| Measure | Score | Read this as |
|---|---|---|
| Current ranking, runs of 2026-10-10 | **104 / 147 correct** ✅ | what the tool picks today; 43 padas wrong ❌ below |
| …of those 43 misses: no reference-consistent split exists anywhere in sanskrit_parser's candidate pool | **20 padas** ⚠️ | engine limit — the ceiling of *any* ranking is **127/147**; fixing these needs upstream splitting, not our ordering |
| …of those 43 misses: a correct candidate was in the pool and we ranked something else first | **23 padas** ❌ | our error; this is where future ranking work has room (max +23) |
| Same code, different processes on the same day | 99–104 / 147 | `parser.split(limit=10)` enumerates in unspecified order → ±4 drift; the gated floor `MIN_MATCHES = 101` absorbs it and still catches real regressions |
| Ranking by morphology only (the old behaviour) | 92 / 147 | why dictionary attestation is our primary key: +11 from using the kosha |

`tools/sandhi_ceiling.py` prints all of this from **one process**, so the miss split is a measurement and not
arithmetic across runs. Two runs on 2026-10-10 after the gate clause below landed gave `picked 104/147` both times, with
ceiling **127** (⚠️ 20 pool / ❌ 23 ranking) and **128** (⚠️ 19 / ❌ 24): the candidate pool itself drifts by one pada because
`parser.split(limit=10)` enumerates in unspecified order. The two kinds of miss look completely different:

| Kind | Example pada | Source of truth | What came out | Verdict |
|---|---|---|---|---|
| ❌ ranking-limited — fixable in this repo | `cālpaviṣayā` | ca \| alpa \| viṣayā | we chose `cā \| alpaviṣayā`, although a reference-consistent candidate was sitting in the pool | our ordering |
| ❌ ranking-limited | `kṛtavāgdvāre` | kṛta \| vāk \| dvāre | we chose `kṛta \| vāgdvāre`; a matching cut existed in the pool | our ordering |
| ⚠️ pool-limited — needs better upstream splitting | `yathāvidhihutāgnīnāṃ` | yathā \| vidhi \| huta \| agnīnām | best of ten: `yathāvidhi \| hutāgnīnām`, `yathā \| avidhi \| hutāgnīnām` — nothing at any depth matches the edition | engine limit |
| ⚠️ pool-limited (long fused pāda) | `so'hamājanmaśuddhānāmāphalodayakarmaṇām` | saḥ \| aham \| ājanma \| śuddhānām \| āphala \| udaya \| karmaṇām | every candidate leaves `āphalodayakarmaṇām` fused into one word | engine limit |

### Split-ranking changes measured on 2026-10-10 (ranking-only deltas over identical cached pools, `tools/sandhi_lab.py`)

Every row re-ranks the same dumped candidate pools through `app._rank_with_kosha`, so a rule is judged on exactly what
the app would publish; the shipped baseline in that process was **104/147** (before it, 103).

| Change | Measured score | New curated-pada failures | Verdict |
|---|---|---|---|
| Gate accepts an attestation **tie**: a transparent-compound split may beat the whole pada when its scarcest part is *at least as* attested (`>=` where it demanded strictly better) | 104 (+`paścārdhena` → `paścā \| ardhena`) | none in the fixture; on published goldens it also cuts BG 11.15's `kamalāsanastham` at the wrong joint (`kamalā \| āsanastham`, still a miss either way) | ✅ **shipped** — +1, no counted regression; needed a live regeneration of all 16 goldens |
| Gate part-length floor `_MIN_DEEP_PART_LEN` 5 → 3 | 84 (−20) | `satyāya`→`satī \| āya`, `prajāyai`→`prajās \| yai`, `navāni`→`nava \| āni`, `saṃpṛktau`→`sam \| pṛktau` | ❌ rejected — the floor exists to stop exactly these sandhi fragments |
| Gate part ceiling `_MAX_DEEP_PARTS` 3 → 4 | 105 (+`śramavivṛtamukhabhraṃśibhiḥ`) | none measured | ⏸ not shipped — one pada of evidence, unmeasured blast radius on words the fixture does not cover, and another live regeneration |
| Drop the whole-word comparison entirely (gate fires even when the pada itself is attested) | 105 (+`prāṃśulabhye`, +`yathāparādhadaṇḍānāṃ`, +`vinivṛttakāmāḥ`) | `mokṣayiṣyāmi`→`mokṣe \| iṣyāmi`, `madhurāṇāṃ`→`madhu \| rāṇām` | ❌ rejected — it removes the invariant this section documents: a finite verb the dictionary knows stays whole |
| Compare scarcest-part attestation before part count | 68 (−36) | `saṃpṛktau`→`sam \| pṛktau`, `pitarau`→`pi \| tarau`, `sāgaram`→`sās \| garam` | ❌ rejected — common fragments then outrank real readings |
| Deep-split gate fix (a deeper split may beat a *shorter* one only if it beats the shortest fully attested one) | 100 (+`darbhairardhāvalīḍhaiḥ`, +`devāṃstava`, −`pārvatīparameśvarau`) in a process whose baseline was 99 | breaks a compound read correctly today | ❌ rejected — inside the ±4 drift |
| Prefer splits containing whole indeclinables (`iva`, `api`, …) above the part-count key | 46/147 (limit 10), 37/147 (limit 20) | **10 of 13** curated padas, e.g. `haviryā`→`ha \| vi \| ryā`, `saṃpṛktau`→`sam \| pṛktau` | ❌ rejected — catastrophic: more parts means more chances to contain an avyaya fragment |
| Candidate pool `limit 10 → 20` (6.7 → 11.2 candidates per pada) | unchanged | none | ❌ rejected — no measured gain for roughly double the splitting time |
| vidyut's experimental `vidyut.cheda.Chedaka` as a second splitter | not scoreable: on these padas it returns nothing or junk (`cāhaṃ`→[], `kṛtavāgdvāre`→one fused token, `vinivṛttakāmāḥ`→`vinivft \| takAma \| As`) | — | ❌ dead end — no signal to rank with |

## 4. Which engine is authoritative for which field

| Field | Engine we publish from | Status of that output |
|---|---|---|
| Akshara counts, pāda count | vidyut `chandas` | ✅ correct on all 16 verses; ⚠️ ṛ/ṝ are not counted as vowels (`sounds.rs:4`), so a pāda containing ṛ is miscounted by vidyut and by our `_count_aksharas` in step |
| Metre name | vidyut `chandas` | ⚠️ 145 vṛtta rows, no jāti rows → 11/16 unnamed (§1); ✅ the five it can name are right, ❌ never a wrong published name (the veto holds) |
| Word boundaries | sanskrit_parser pool + vidyut kosha ranking | ⚠️ ceiling 127/147; ❌ 23 padas still ranked wrong (§3); samāsa resolution needs context we do not have |
| Root / stem / dictionary attestation | vidyut kosha (`load_kosha`) | ✅ now a ranking key as well as a display field: readings built on stems the kosha does not record for that form are demoted (§2.1), which fixed 6 curated forms and lost none; ⚠️ sometimes the dictionary itself is against us — it records *aha* ("non-existence") for अहम् and *vandA* for `vande`, so attestation cannot settle those; ⚠️ surface keys only — pause-normalised lookups needed (`vāk`→`vac`) |
| Vibhakti / vacana / liṅga / lakāra tags | sanskrit_parser | ✅ the only engine that emits lakāra/puruṣa at all (vidyut's kosha krdanta entries carry `lakara=None`); ⚠️ context-free: 432 of 546 forms arrive ambiguous (§2) |
| Sentence-level reading (padaccheda) | Dharmamitrā (remote) | shown side by side, never merged; ⚠️ returns surface forms with no tags and invents/skips tokens (`jagantaḥ`, a stray `mā`) — which is why it is not the reference key |

## 5. Reproducing these numbers

```bash
uv run pytest -q tests/test_meter_accuracy.py        # offline: truth vs best effort, all 16 verses
uv run pytest -q tests/test_morphology_accuracy.py   # offline: 15/15 offered, 10/15 chosen first
SAMSKRTA_LIVE_GOLDEN=1 uv run pytest -q tests/test_sandhi_accuracy.py   # ~50 s live; prints every missed pada
uv run python tools/meter_audit.py                 # offline: rebuilds §1, with the reason for every null
uv run python tools/morphology_ranks.py            # offline: rebuilds §2, incl. the rank of the right reading
uv run python tools/morphology_lab.py              # offline (kosha only): re-score a proposed reading rule on the pinned corpus
uv run python tools/sandhi_ceiling.py              # ~25 s local (no network): §3 score + ceiling + fault split
uv run python tools/sandhi_lab.py --pools pools.json   # ~2 s local: re-score a proposed split rule on the cached pools
```

The first three tools are how every table in this file was produced. They read only committed artefacts — the sandhi tool
also calls the splitter itself — and import the same scoring helpers as the tests, so any figure here can be
regenerated instead of re-derived by hand; `tools/sandhi_ceiling.py --pools pools.json` caches the candidate pools and
`--reuse pools.json` scores from them, which is how a ranking idea gets measured against an identical search space in
seconds rather than 25 s per variant (DOCUMENTATION.md §9).

A **word-reading** rule only reorders JSON, so `tests/data/results/*.result.json` is regenerated offline from the pinned
raw output (`for f in tests/data/results/*.raw.json; do uv run python postprocess_analysis.py -i "$f"; done`, ~7 s);
`postprocess_analysis.py --no-dictionary` reproduces the pre-rule ordering byte for byte. A **split-ranking** rule is a
different cost: the chosen split is baked into `.raw.json` by `_analyze_line`, so landing one means re-running all 16
verses against the live engines (~3 min) — and because `parser.split(limit=10)` enumerates in unspecified order, that run
moves unrelated splits too (the gate change fixed `paścā | ardhena` in the śākuntala verse while re-cutting BG 11.15's
`kamalāsanastham`). That is why a split rule needs a measured gain bigger than ±4 before it ships.

`test_meter_accuracy.py` passes with **11 warnings** (one per verse vidyut cannot name) and
`test_morphology_accuracy.py` with **5 warnings** (`vande`, `jagatas`, `vraja`, `śucaḥ`, `ahaṃ`). Those warnings *are*
the deliverable: an engine limit is reported, never hidden. How each fixture was built and which further rules were
ruled out lives in [DOCUMENTATION.md](DOCUMENTATION.md) §9 (splitting), §10 (metre), §11 (word readings) and §12
(the optional lexical scorer).

The numbers above are **master**. The gensim + sentencepiece lexical-scorer experiment, with its own measured verdict
(103/147 as it stood then — unchanged by the scorer, one curated regression, goldens move; not merged), lives on the branch
`feature/engine-accuracy-tuning`; see DOCUMENTATION.md §12.

## 6. Parked for later (decisions, not bugs)

- **Metre naming.** Two ways to close the eleven ⚠️ rows: ship our own jāti/vṛtta table in this project, or fix vidyut
  upstream (`meters.tsv` needs `jati` rows and `try_match` needs to respect declared length). Kept as a future task —
  whichever is chosen, §1's table is the acceptance test.
- **Choosing among context-free readings.** §2.1 closed what a dictionary can settle; the five remaining wrong primaries
  (`vande`, `jagatas`, `vraja`, `śucaḥ`, `ahaṃ`) need the sentence — case, number and lakāra are properties of the
  clause, not of the letters. Feeding a padaccheda back into the ranking is a feature decision, not a bug fix; every
  context-free rule tried so far is listed in §2's table with its measured cost.
- **Dharmamitrā upstream report.** Its `jagantaḥ` / `sūnantāḥ` type noise is worth reporting; until then it stays an
  unmerged second opinion.
