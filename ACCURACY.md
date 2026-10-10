# Measured accuracy

Every claim about what this tool gets right lives here, with the reference it was checked against. Nothing in these
tables comes from our own engines: metre names come from the printed editions ([sanskritsahitya.org](https://www.sanskritsahitya.org),
cross-checked against [sanskritsahitya-com/data](https://github.com/sanskritsahitya-com/data)), word grammar from those
editions' पदच्छेदः plus standard Pāṇinian analysis, and word boundaries from the editions' padaccheda tables.

Measured **2026-10-10** on `sanskrit-parser 0.2.6`, `vidyut 0.4.0` + bundled `data-0.4.0`,
`indic-transliteration 2.3.82`. Fixtures: [`tests/data/meter_truth.json`](tests/data/meter_truth.json) (16 verses),
[`tests/data/morphology_truth.json`](tests/data/morphology_truth.json) (9 forms),
[`tests/data/sandhi_truth.json`](tests/data/sandhi_truth.json) (147 padas). Reproduce with the commands in §5.

## 1. Metre: shape right on every verse, names limited by vidyut's table

| Verse | Published छन्दः | Aksharas/pāda | Our `chandas.vrtta` | vidyut candidates | Pādas vidyut scanned | Named? |
|---|---|---|---|---|---|---|
| abhijnaana_shakuntala-1.1 | स्रग्धरा (sragdharā) | [21, 21, 21, 21] | `sragdharā` | sragdharā | 3/4 | yes |
| abhijnaana_shakuntala-1.18 | मालिनी (mālinī) | [15, 15, 15, 15] | `malinī` | malinī | 3/4 | yes |
| abhijnaana_shakuntala-1.7 | स्रग्धरा (sragdharā) | [21, 21, 21, 21] | `sragdharā` | sragdharā | 2/4 | yes |
| bhagavad_gita-11.15 | उपजातिः (upajāti) | [11, 11, 11, 11] | `—` | indravajrā, indravaṃśā, upendravajrā | 4/4 | no row in vidyut |
| bhagavad_gita-2.22 | उपजातिः (upajāti) | [11, 11, 11, 11] | `—` | indravajrā, indravaṃśā, upendravajrā, vaṃśastha | 4/4 | no row in vidyut |
| bhagavad_gita-15.5 | इन्द्रवज्रा (indravajrā) | [11, 11, 11, 11] | `indravajrā` | indravajrā, indravaṃśā | 4/4 | yes |
| bhagavad_gita-18.66 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] | `—` | mṛgī | 1/4 | no row in vidyut |
| bhagavad_gita-2.22 | उपजातिः (upजाति) | [11, 11, 11, 11] | `—` | indravajrā, indravaṃśā, upendravajrā, vaṃśastha | 4/4 | no row in vidyut |
| bhagavad_gita-2.47 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] | `—` | candralekhā, vasumatī | 2/4 | no row in vidyut |
| raghuvamsha-1.1 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] | `—` | madalekhā, śuddhavirāṭ | 2/4 | no row in vidyut |
| raghuvamsha-1.2 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] | `—` | jaloddhatagati | 1/4 | no row in vidyut |
| raghuvamsha-1.3 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] | `—` | vasumatī | 1/4 | no row in vidyut |
| raghuvamsha-1.4 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] | `—` | none | 0/4 | no row in vidyut |
| raghuvamsha-1.5 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] | `—` | none | 0/4 | no row in vidyut |
| raghuvamsha-1.6 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] | `—` | upasthita | 1/4 | no row in vidyut |
| raghuvamsha-1.7 | अनुष्टुप् (anuṣṭubh) | [8, 8, 8, 8] | `—` | none | 0/4 | no row in vidyut |

- **Counting is correct: 16/16.** `pada_count` and `aksharas_per_pada` equal the published grid on every verse —
  `[8,8,8,8]` for the nine anuṣṭubh pādas, `[11,11,11,11]` for the four jagatī-family verses, `[21…]`, `[15…]` for the
  Śākuntala metres. That counting is vidyut's scanner (`Chandas.classify().aksharas`).
- **Naming: 5/16.** इन्द्रवज्रा (Gītā 15.5, 15.15), स्रग्धरा (Śākuntala 1.1, 1.7) and मालिनी (Śākuntala 1.18, printed by
  vidyut as `malinī`) come back identical to the edition. The other eleven are pinned as `vrtta: null` on purpose.

### Why an 8-akshara pāda is not called anuṣṭubh — this is vidyut's data, not our code

Verified against vidyut's own repository and the bundled dataset on 2026-10-10:

| Check | Result |
|---|---|
| `grep -rin "anuSTub\|triSTub\|jAgatI\|upajAti"` over the whole vidyut repo (Rust sources, Python bindings, all data files) | **no metre hit at all** — only unrelated Pāṇinian rows (`ganapatha.rs:2226 "jagatI"` is a stem in the gaṇapāṭha; `sutrapatha.tsv:1864` is a sūtra) |
| `cut -f2 data-0.4.0/chandas/meters.tsv \| sort \| uniq -c` | **145 rows, all `vrtta`; 0 rows of kind `jati`** |
| jāti metres in code (`vidyut-chandas/src/chandas.rs:98-121`) | exactly seven hard-coded — `vEtAlIyam, upagIti, AryAgIti, gIti, udgIti, Aupacchandasikam, AryA`; none is anuṣṭubh/triṣṭubh/jagatī/upajāti |
| How a name is chosen (`VrttaPada::try_match`) | gaṇa **prefix** matching over the pāda's weight string; declared length is not enforced, so an 11-akshara pāda can be reported as `indravaṃśā` (which declares 12) |

So the pipeline is: vidyut counts correctly → vidyut matches a gaṇa prefix against its 145-row vṛtta table → that name
is wrong or absent. Our code never renames anything. `_summarize_chandas` (`app.py`) only *vetoes*: it reads each
candidate's declared akshara count out of `meters.tsv` and demotes any candidate whose length contradicts the pāda
vidyut just scanned, publishes a name only when all classified pādas agree, keeps every vidyut suggestion in
`candidates`, and prints the contradiction on stderr. That is what turned 0 named verses into 5 — the remaining gap is
data that does not exist upstream.

Two ways to close it later (both are decisions, not patches to this summary): an **upstream PR** adding jāti rows /
anuṣṭubh-triṣṭubh entries to `meters.tsv`, or a metre table shipped in this project. Until then the eleven verses stay
`vrtta: null` with their real shape in `aksharas_per_pada`.

## 2. Word grammar: the engine offers everything, ranking chooses four of nine

| Form | Verse | Reference reading | Our primary | Alternates kept | Verdict |
|---|---|---|---|---|---|
| `vande` | raghuvamsha-1.1 | vand, eka, laṭ/uttamapuruṣaḥ/ātmanepadam | vandā, prathamā, dvi, strīliṅgam | 13 | visible, not primary |
| `jagatas` | raghuvamsha-1.1 | jagat, ṣaṣṭhī, eka, napuṃsakaliṅgam | jagat, prathamā, bahu, strīliṅgam | 11 | visible, not primary |
| `pitarau` | raghuvamsha-1.1 | pitṛ, prathamā, dvi, puṃlliṅgam | pitṛ, prathamā, dvi, puṃlliṅgam | 2 | **primary** |
| `asti` | raghuvamsha-1.4 | as, eka, laṭ/prathamapuruṣaḥ | asta, saṃbodhana, eka, strīliṅgam | 4 | visible, not primary |
| `deva` | bhagavad_gita-11.15 | deva, saṃbodhana, eka, puṃlliṅgam | deva, saṃbodhana, eka, puṃlliṅgam | 5 | **primary** |
| `navāni` | bhagavad_gita-2.22 | nava#1, prathamā, bahu, napuṃsakaliṅgam | nava#1, prathamā, bahu, napuṃsakaliṅgam | 7 | **primary** |
| `avyayam` | bhagavad_gita-15.5 | avyaya#1, dvitīyā, eka | avyaya#1, prathamā, eka, napuṃsakaliṅgam | 6 | **primary** |
| `vraja` | bhagavad_gita-18.66 | vraj, eka, loṭ/madhyamapuruṣaḥ | vraja, saṃbodhana, eka, puṃlliṅgam | 4 | visible, not primary |
| `śucas` | bhagavad_gita-18.66 | śuc, eka, madhyamapuruṣaḥ | śuc#2, prathamā, bahu, strīliṅgam | 26 | visible, not primary |

**Offered by sanskrit_parser: 9/9. Made primary by our ranking: 4/9.** The reference reading is never lost — it is in
the published `alternates` for all nine. Case, number and gender are properties of the *sentence*; sanskrit_parser
analyses one pada at a time and returns every reading Pāṇini allows for those letters (27 for `śucas`, 14 for `vande`,
12 for `jagatas`). No ordering of context-free readings can recover them.

Across the whole sixteen-verse corpus: **546 pada-words carry morphology and 432 of them have more than one distinct
reading.** Ambiguity is the normal case, not an exception.

### Ranking rules tried on this fixture and rejected (measured, so nobody re-tries them blind)

| Rule | Effect over the 16 verses | Verdict |
|---|---|---|
| Finite verb above nominal reading | changes 20 primaries: ≈8 better (`vande`, `vraja`, `asti`, `yāti`), ≈12 worse (`navāni`→√nu, `deva`→imperative of √dev, `avyayam`→√vyā, `ajanma`, `āsam`) | rejected — net loss |
| Prefer readings whose stem is exactly attested in the vidyut kosha | changes 15 primaries: ≈3 better, ≈10 worse (`anu`→dvitīyā napuṃsaka) | rejected — net loss |

## 3. Word boundaries (sandhi): 103/147, with a hard pool ceiling of 127

The thirteen padas below are chosen for the distinct failure modes the ranking exists to fix; they run offline in ~5 s:

| Pada | Reference parts | Our current split | Match |
|---|---|---|---|
| `mokṣayiṣyāmi` | mokṣayiṣyāmi | `mokṣayiṣyāmi` | match |
| `gamiṣyāmyupahāsyatām` | gamiṣyāmi \| upahāsyatām | `gamiṣyāmi \| upahāsyatām` | match |
| `haviryā` | havis \| yā | `havis \| yā` | match |
| `prapannastanubhiravatu` | prapanna \| tanubhiḥ \| avatu | `prapannas \| tanubhis \| avatu` | match |
| `sūtrasyevāsti` | sūtrasya \| iva \| asti | `sūtrasya \| iva \| asti` | match |
| `sarvadharmānparityajya` | sarva \| dharmān \| parityajya | `sarva \| dharmān \| parityajya` | match |
| `saṃpṛktau` | saṃpṛktau | `sampṛktau` | match |
| `saṅgo'stvakarmaṇi` | saṅgaḥ \| astu \| akarmaṇi | `saṅgas \| astu \| akarmaṇi` | match |
| `jīrṇānyanyāni` | jīrṇāni \| anyāni | `jīrṇāni \| anyāni` | match |
| `vāmanaḥ` | vāmanaḥ | `vāmanas` | match |
| `sūryaprabhavo` | sūrya \| prabhavaḥ | `sūrya \| prabhavas` | match |
| `vajrasamutkīrṇe` | vajra \| samutkīrṇe | `vajra \| samutkīrṇe` | match |
| `saṃbhṛtārthānāṃ` | saṃbhṛta \| arthānām | `sambhṛtā \| arthānām` | match |

Parts are compared by **vidyut kosha lemma stem**, never by spelling: sandhi changes spellings (`vāc`/`vāk`) and
sanskrit_parser writes final visarga as `s`, anusvara as `m` — hence `prapannas | tanubhis` scoring against
`prapanna | tanubhiḥ`.

| Measure | Score | Meaning |
|---|---|---|
| Full corpus, current ranking (run of 2026-10-10) | **103/147** | what the tool picks today |
| Same code, other processes the same day | 99–100/147 | `parser.split(limit=10)` enumerates candidates in an unspecified order: ±4 drift between processes |
| Ceiling of any ranking | **127/147** | for 20 padas no reference-consistent split exists inside the candidate pool at all |
| Ranking by morphology only (old behaviour) | 92/147 | why dictionary attestation is the primary key |
| Gated test floor (`MIN_MATCHES`) | 101 | fails on a real regression, survives drift |

### Split-ranking changes measured and rejected on 2026-10-10

All variants were scored over identical cached candidate pools, so the deltas are ranking-only:

| Change | Score (same process as `current` = 99) | Curated-pada failures introduced | Verdict |
|---|---|---|---|
| Deep-split gate fix (a deeper split may beat a *shorter* one only if it beats the shortest fully attested one) | 100 (+`darbhairardhāvalīḍhaiḥ`, +`devāṃstava`, −`pārvatīparameśvarau`) | none | rejected — +1 inside ±4 noise, and it breaks a compound read correctly today |
| Prefer splits containing whole indeclinables (`iva`, `api`, …) above the part-count key | 46/147 (limit 10), 37/147 (limit 20) | 10 of 13 curated padas, e.g. `haviryā`→`ha \| vi \| ryā`, `saṃpṛktau`→`sam \| pṛktau` | rejected — catastrophic: more parts means more chances to contain an avyaya fragment |
| Candidate pool `limit 10 → 20` (6.7 → 11.2 candidates per pada) | 99/147, unchanged from limit 10 in the same comparison | none | rejected — no accuracy gain measured; costs roughly double the splitting time |

## 4. Which engine is authoritative for which field

| Field | Engine we publish | Known upstream limit |
|---|---|---|
| Akshara counts, pāda count | vidyut `chandas` | correct on all 16 verses; ṛ/ṝ are not counted as vowels (`sounds.rs:4`), so a pāda containing ṛ is miscounted by vidyut and by our `_count_aksharas` in step |
| Metre name | vidyut `chandas` | 145 vṛtta rows, no jāti rows, no anuṣṭubh/triṣṭubh → 11/16 verses unnamed (§1) |
| Word boundaries | sanskrit_parser pool + vidyut kosha ranking | ceiling 127/147; samāsa resolution and case government need context we do not have |
| Root, stem, dictionary attestation | vidyut kosha (`load_kosha`) | surface keys only; pause-normalised lookups needed for `vāk`→`vac` |
| Vibhakti / vacana / liṅga / lakāra tags | sanskrit_parser | per-pada, context-free: 432 of 546 forms arrive ambiguous (§2) |
| Sentence-level reading (padaccheda) | Dharmamitrā (remote) | returns surface forms with no tags; skips or invents tokens (`jagantaḥ`, a stray `mā`); never used as the reference key |

## 5. Reproducing these numbers

```bash
uv run pytest -q tests/test_meter_accuracy.py        # offline: prints truth vs best effort for all 16 verses
uv run pytest -q tests/test_morphology_accuracy.py   # offline: 9/9 offered, 4/9 chosen, warnings name the limit
SAMSKRTA_LIVE_GOLDEN=1 uv run pytest -q tests/test_sandhi_accuracy.py   # ~50 s live; prints every missed pada
```

`test_meter_accuracy.py` currently passes with **11 warnings** (one per verse vidyut cannot name), `test_morphology_accuracy.py`
with **5 warnings** (`vande`, `jagatas`, `asti`, `vraja`, `śucaḥ`). Those warnings are the point of the tests: an engine
limit is reported, never hidden. See [DOCUMENTATION.md](DOCUMENTATION.md) §9 (splitting), §10 (metre) and §11 (word
readings) for how each fixture was built and which further rules were ruled out.
