# samskrta-multiparser

[github.com/Narayana108/samskrta-multiparser](https://github.com/Narayana108/samskrta-multiparser)

Unified multi-engine Sanskrit analyzer. Runs three independent engines — [sanskrit_parser](https://github.com/kmadathil/sanskrit_parser), [Dharmamitra](https://dharmamitra.org) and [vidyut](https://github.com/ambuda-org/vidyut) — on the same input (Devanagari, or any romanization vidyut's lipi can detect: IAST, SLP1, Harvard-Kyoto, ITRANS) and writes a pair of JSON documents under one base name: `<base>.raw.json`, holding everything each engine produced, and `<base>.result.json`, the condensed word-by-word reading.

**Further reading:** [DOCUMENTATION.md](DOCUMENTATION.md) — architecture, the library quirks that shaped the code, and maintenance notes · [ACCURACY.md](ACCURACY.md) — every measured accuracy figure (metre names, word readings, word boundaries) against published sources.

### Engine versions this project runs on

| Package | Version (`uv.lock` today) | What it provides |
|---|---|---|
| [`sanskrit-parser`](https://pypi.org/project/sanskrit-parser/) | **0.2.6** | sandhi splitting, per-pada morphological tags (pulls in `sqlalchemy` 2.0.52) |
| [`vidyut`](https://pypi.org/project/vidyut/) | **0.4.0**, with the matching [`data-0.4.0`](https://github.com/ambuda-org/vidyut) dataset bundled in this repo | kosha dictionary, dhatu/pratipadika prakriya, chandas (meter), lipi transliteration |
| [`indic-transliteration`](https://pypi.org/project/indic-transliteration/) | **2.3.82** | SLP1 ↔ IAST ↔ Devanagari conversion |
| `requests` | `>=2.34.2` | the Dharmamitra HTTP call (no package — remote API) |

Only `vidyut>=0.4.0` and `requests>=2.34.2` are pinned in `pyproject.toml`; `sanskrit-parser` and `indic-transliteration` float, so a fresh `uv sync` can resolve newer builds. That matters because the postprocessor matches upstream tag spellings — see [DOCUMENTATION.md §8 item 3](DOCUMENTATION.md).

## Overview

This tool analyzes Sanskrit text (single words or full shloka lines) through three parallel engines, each producing raw structured output without merging or filtering. The engines are:

| Engine | Source | Capabilities |
|--------|--------|-------------|
| [`sanskrit_parser`](https://github.com/kmadathil/sanskrit_parser) | Local Python package | Sandhi splitting (one pada-line at a time), morphological tags |
| [`dharmamitra`](https://dharmamitra.org) | Remote HTTP API | Independent unsandhiing; the response is underscore-separated surface forms |
| [`vidyut`](https://github.com/ambuda-org/vidyut) | Local Python package | Kosha dictionary lookup, dhatu/pratipadika prakriya (derivation), meter classification, recursive sandhi splitting |

Each engine runs independently: if one fails its key holds `{"error": "..."}` and the others still run. Dharmamitra is a remote service, so an unreachable API only costs that section — a `Warning:` line on stderr and exit code 0. `sanskrit_parser` and `vidyut` are local dependencies: when either cannot run at all the same error object is recorded, an `Error:` line goes to stderr, and the process exits 1.

## Why Three Engines

The engines overlap on purpose; none of them is good at everything.

- **Sandhi splitting — sanskrit_parser.** `run_sanskrit_parser()` uses two calls on `Parser(output_encoding=sanscript.DEVANAGARI)` from [sanskrit_parser](https://github.com/kmadathil/sanskrit_parser):
  - `parser.split(line, limit=5)` — up to five candidate unsandhied readings of each line (ten in pada mode). Every item of every candidate is then tagged with `parser.sandhi_analyzer.getMorphologicalTags(item, tmap=True)`, which is where the root and the vibhakti / vacana / linga tags come from.
  - Splitting is driven **one pada-line at a time**, never on the whole input: the graph search is superlinear in the number of words it spans, so handing sanskrit_parser a multi-pāda string explodes its candidate pool. Above `SAMSKRTA_MIN_PARALLEL_LINES` lines (default 8) that per-line work runs in a process pool — vidyut's Rust bindings never release the GIL and sanskrit_parser is pure Python, so threads cannot speed any of it up. `SAMSKRTA_WORKERS` sizes the pool (default 2, ~350 MB resident each; `1` forces the single-process path).
  - `parser.split(word, limit=10)` on each word by itself — `_best_word_split()` ranks those candidates instead of taking the first, because candidate order changes between processes. With the vidyut kosha loaded (`load_kosha()`, cached) the keys are: every part exactly attested in the dictionary; then fewest parts; then every part having standalone morphology; then scarcest-part rarity; then longest shortest part; sorted parts last for byte-stability. These per-word readings are what `<base>.result.json` prints under `padaccheda.sanskrit_parser`, and their quality is measured — see [What each engine cannot do](#what-each-engine-cannot-do-and-what-this-tool-does-about-it).
- **An independent second opinion — Dharmamitra.** `run_dharmamitra()` POSTs the IAST text to `https://dharmamitra.org/api/tagging/` (`mode="unsandhied-lemma-morphosyntax"`; its two attempts share one `DHARMAMITRA_TIMEOUT_SECS` deadline, default 20 s) and reads `results[0]`: in practice that is an underscore-separated sequence of **surface forms** — `kva_sūrya_prabhavaḥ_vaṃśaḥ_…` — so the API's contribution is its own unsandhiing, nothing else. The request runs on a background thread, so its network wait overlaps both local engines instead of adding to them. Nothing is merged: `<base>.result.json` keeps both word sequences side by side under `padaccheda` and lists every region where they disagree under `differences`, so the reader — not a scoring heuristic — decides which split to accept for a given pada.
- **Everything else — vidyut.** `run_vidyut()` uses four [vidyut](https://github.com/ambuda-org/vidyut) modules against the local `data-0.4.0/` trees:
  - `vidyut.kosha.Kosha(data-0.4.0/kosha)` — an FST dictionary queried with `kosha.get(slp1)`; `kosha_lookup()` adds a stem fallback (strip up to three trailing SLP1 characters) for surface forms the keys miss, and `enrich_dharmamitra_lemmas()` re-queries it (with pause-spelling fixes: `…c → …k`, `…j → …g`, `…ś → …ṣ`) to attach lemmas to Dharmamitra's tokens. `_kosha_entry_info` classifies each hit — repr contains `Tinanta` → tīnantāḥ, else `entry.is_avyaya` → avyayam, else sūnantāḥ — and keeps the lemma. A word that hits gets up to eight deduplicated `grammatical_entries`, formatted by `_format_pada_entry_json`: pratipadika / artha / linga / vibhakti / vacana for nominal forms; dhatu / gaṇa / prayoga / lakāra / puruṣa / vacana for finite verbs.
  - `vidyut.prakriya.Vyakarana().derive(...)` — derivation steps from `prakriya.history` (sūtra code, source, terms, which terms changed) for two stem kinds only: `Dhatu.mula(upadeśa, gaṇa)` yields the krdanta derivation plus three fixed tīnanta samples (lat / laṭ / loṭ × madhyama × ekavacana, kartari prayoga), and `Pratipadika.basic(lemma)` covers up to five nominal stems. **There is no prakriya for avyayas** — vidyut derives nāma and ākhyāta only, so an avyaya gets its kosha label and nothing further.
  - `vidyut.chandas.Chandas(data-0.4.0/chandas/meters.tsv)` — `classify()` runs on one pāda at a time (`_split_into_padas` cuts a line where the cumulative akshara count reaches its midpoint), giving the matched vṛtta (`match.padya`) and every akshara's weight (`match.aksharas`). `_summarize_chandas()` names the verse only when all padas agree; meters.tsv holds 145 vṛttas and not the classical anuṣṭubh, which is why `vrtta` stays null for a plain śloka while `aksharas_per_pada: [8, 8, 8, 8]` still shows its shape.
  - `vidyut.lipi` — `detect()` names the input script and `transliterate()` moves between Devanagari, IAST and SLP1 for everything else in the pipeline.

vidyut does ship a sandhi splitter — `Splitter.from_csv(data-0.4.0/sandhi/rules.csv)` — and `run_vidyut()` uses it through `_is_quality_split()`, which calls `splitter.split_at(word, i)` at every position and keeps only splits whose parts are ≥ 4 characters long, have at least two kosha entries each, and have at least one non-derived entry per part. `recursive_split()` then walks the surviving pairs depth-first (`max_depth=2`, at most four parts) and `_chain_score` ranks the chains (more kosha-attested parts, fewer parts). Unfiltered splitting runs well past the real word boundary, which is why those `sandhi_splits` stay in `<base>.raw.json` as a third opinion; the pada-by-pada comparison in `<base>.result.json` is between sanskrit_parser and Dharmamitra, and vidyut's contribution there is the `chandas` summary.

### What each engine cannot do, and what this tool does about it

The split of merit is Dharmamitra's, but only for sandhi: it reads the whole sentence, so it knows that `मामेकं` is `mām + ekam`. It is also the least reliable source in the pipeline, which is why nothing is merged.

- **Dharmamitra — right boundaries, wrong words.** Remote only (both attempts share one `DHARMAMITRA_TIMEOUT_SECS` deadline, default 20 s; an offline run records `{"error": …}` and the other engines still produce documents). Its answer is a flat underscore-separated stream with no pada marks, and it is uneven: on a whole-verse request it can skip an opening compound entirely, prepend a word that is not in the verse (`mā` before Gītā 2.47's first pāda), or pad one answer out with words from elsewhere (`ṛta | iva | vāsāṃsi` for वासांसि). Mitigation: tokens are attached to padas by a coverage DP — each token must rebuild the slice of the word it is filed under, and a token that rebuilds none is reported in `dharmamitra_unmatched` instead of being guessed at (`group_dm_tokens_by_word`, DOCUMENTATION §8) — so one over-long stream no longer shifts every later pada. Any pada its verse-level pass left empty gets exactly one word-level request of its own; those answers are recorded in the raw document as `pada_followups` and labelled `"request": "pada"` in the reading, because they arrive without sentence context. Its readings still contain base stems instead of surface forms (`vāc` for वाक्), its own misreadings (`jagantaḥ` for जगतः) and tokens no sandhi rule produces (`upahāsya | tām` for उपहास्यताम्): the answer is kept side by side with `differences`, never merged; lemmas are filled locally from the vidyut kosha (DOCUMENTATION §4); wherever it serves as a test reference its splits are corrected against published padaccheda tables first.
- **sanskrit_parser — a good generator with no context.** It inspects one word at a time, so samāsa stays whole (`yathākālaprabodhinām`, `prāṃśulabhye`) and it sometimes cuts inside a joined form (`māme | akam` for माम् एकम्). Mitigations: all ten candidates are ranked by dictionary attestation plus a transparent-compound gate that reads long attested compounds as their members (`sūryaprabhavas` → `sūrya | prabhavaḥ`) rather than taken in library order (deterministic within one process, accuracy-floored across processes); `parser.split()` returns `None`, not `[]`, when it finds no split at all — guarded with `or []` after that crashed the raw pass on an avagraha token. Its combinatorial vakya (sentence) parse is **not run**: it cost more than every other engine combined and produced a graph for one pāda in nine verses, so the component was removed rather than tuned.
- **vidyut — dictionary, derivation, meter; weak sandhi.** Its splitter is the weakest generator measured: reference-consistent chains for 33 of the 63 padas it covered (measured on the earlier 64-pada fixture), against sanskrit_parser's pool containing a reference-consistent split for 127 of today's 147. `_is_quality_split()` suppresses noise by demanding parts ≥ 4 characters with ≥ 2 kosha entries — and with them real words such as `iva`, `yā`, `tu`. So vidyut's splits stay in `<base>.raw.json` as a third opinion and never enter the pada-by-pada comparison. Its other gaps are documented where they occur: no prakriya for avyayas, no anuṣṭubh in meters.tsv (`vrtta: null`, shape still visible as `aksharas_per_pada`), and `Splitter.split_at(word, i)` takes a **byte** offset — passing a Python character index mis-splits non-ASCII words, so `rūpāṇi` came back as `rū | pāṇi`.
- **Meter names are a table problem, not a scanning problem.** vidyut counts the verse correctly — `aksharas_per_pada` matches the published grid on all sixteen pinned verses — but its metre table (145 rows, all `vrtta`) holds no jāti metre, so अनुष्टुप् (Raghuvaṃśa 1.1–1.7, Gītā 2.47 / 18.66) and उपजातिः (Gītā 2.22 / 11.15) can never be named: it offers short vṛtta prefixes such as `mṛgī` or `vasumatī`, or nothing at all. What *was* ours: the classifier matches gaṇa **prefixes**, so an 11-akshara pāda is reported under the 12-akshara name `indravaṃśā`, and one such impossible name used to veto a verse the other three pādas had named correctly. `_summarize_chandas` now checks every name against the length its own pattern declares (`_meter_lengths` reads meters.tsv, where `|` only groups the pattern), keeps an impossible name in `candidates` while excluding it from the vote, says so on stderr, and lets pādas vidyut abstains on leave the vote too (a pāda-final syllable is free in length; its patterns are not). That recovers इन्द्रवज्रा for Gītā 15.5 / 15.15 and स्रग्धरा / मालिनी for Śākuntala 1.1 / 1.7 / 1.18 — five named verses where before none were. `tests/data/meter_truth.json` plus `tests/test_meter_accuracy.py` keep the published छन्दः of every pinned verse next to vidyut's answer and warn, naming the engine and its documented limit, wherever the truth cannot be reached.
- **sanskrit_parser offers several case readings for one word, and all of them are shown.** Of 546 pada-words in the fixtures, 432 carry more than one distinct reading: to an analyser without context `वसाम्सि` is prathamā *and* dvitīyā *and* saṃbodhana. Keeping exactly one (which earlier builds did) produced a vocative epidemic that was our bug, not the engine's. `_morph_rank` now chooses only the **primary** reading — grammatical case order, an `avyayam` reading above a bare compound marker such as `samāsapūrvapadanāmapadam`, which is why `yathā` / `tathā` read as indeclinables — and every other reading is published under `"alternates"`. Dharmamitrā's kosha `type` remains the trusted classifier beside it.
- **Avagraha (`ऽ`) is left inside the token.** Three variants were tried on Raghuvaṃśa 1.4 (`वंशेऽस्मिन्पूर्वसूरिभिः`): keeping it, deleting it, and restoring the elided `अ`. Deleting strands `स्मिन्` and makes sanskrit_parser return nothing; restoring adds a syllable that vidyut then counts, so the meter came back as `[17, 8, 8]` instead of `[8, 8, 8, 8]`. With the avagraha untouched, sanskrit_parser splits the fused token correctly by itself: `vaṃśe | asmin | pūrvasūribhis`.

**Measured splitting quality.** `tests/data/sandhi_truth.json` holds 147 padas of the sixteen pinned verses split the way a reader splits them: for nine verses Dharmamitra's boundaries hand-corrected against published padaccheda tables, for seven its reading checked word-for-word against the पदच्छेदः printed for that very verse (transparent compounds listed as their members). Against it `_best_word_split()` reproduces the reading for **103/147** padas; morphology-only ranking — the fallback when no kosha is available — manages 92/147, and sanskrit_parser's candidate pool contains a reference-consistent split for 127/147, which bounds any ranking. `tests/test_sandhi_accuracy.py` pins a floor of 101 and prints every missed pada on failure. The remaining misses are context problems (samāsa, case government) that no per-word method can solve — that is the role Dharmamitra plays in the output. Chasing Dharmamitra's own splits was measured and rejected: on the nine hand-corrected verses it agrees with the reference on only **22/64** padas (it answers `jagataḥ` as `jagatas | ca`, invents `upahāsya | tām`, reports base stems instead of surface forms), so matching it would lower the score, not raise it. Metre was tried too, and is reported but never used for splitting — pāda-edge alignment moved one pick (44/64 → 44/64), fitting an anuṣṭubh guru-laghu pattern cost five (44/64 → 39/64) and the caesura rule cost nine on the Raghuvaṃśa rows (32/46 → 23/46) — figures measured on the earlier 64-pada fixture, because vidyut scans weights straight across word boundaries and its table has no anuṣṭubh row at all.

## Architecture

```
app.py (CLI entry point — raw pass)
├── detect_script()             # Name the script vidyut lipi detects
├── to_devanagari()             # Canonicalize any detected script to Devanagari
├── preprocess_input()          # Separators → spaces, whitespace runs collapsed
├── devanagari_to_iast()        # Convert Devanagari → IAST
├── read_input()                # Read from file or stdin ('-')
├── run_sanskrit_parser()       # Local: sandhi + morphology, per pada-line (pooled when long)
├── run_dharmamitra()           # Remote: independent unsandhiing (surface forms only)
│   └── enrich_dharmamitra_lemmas()  # Adds vidyut kosha lemmas to DM tokens
├── run_vidyut()                # Local: kosha + prakriya + meter + sandhi
│   └── _summarize_chandas()    # Verse-level vṛtta summary from pāda matches
└── main()                      # Runs the engines, writes both output documents

postprocess_analysis.py (second pass; also a standalone CLI)
├── postprocess()               # '<base>.raw.json' → '<base>.result.json'
└── output_base(), raw_path(), result_path(), write_document()   # naming helpers
```

`app.py` runs both passes in one command: once the engines finish it calls
`postprocess_analysis.postprocess()` and writes the reading document next to the raw
one. That module is stdlib-only, imports nothing from `app.py`, and can still be run by
itself to re-process an existing raw document. For the word-by-word comparison
`postprocess()` reads the `sanskrit_parser` and `dharmamitra` engines plus the raw
input, and it carries vidyut's verse-level `chandas` summary across; vidyut's per-word
kosha/prakriya detail stays in the raw document only.

Within vidyut, recursive compound sandhi splitting uses DFS over the kosha and
sandhi rules, with quality filtering to prevent spurious splits.

## Prerequisites

- **Python 3.10+**
- **[uv](https://docs.astral.sh/uv/)** for dependency management (or `pip`)
- **Vidyut data directory** (`data-0.4.0/`) containing kosha, prakriya, chandas, sandhi, and cheda subdirectories

### Install dependencies

```bash
git clone git@github.com:Narayana108/samskrta-multiparser.git
cd samskrta-multiparser
uv sync
```

Or with pip:

```bash
pip install sanskrit-parser indic-transliteration "vidyut>=0.4.0" requests
```

## Quick Start

```bash
# Shloka mode — with no -i the single .txt file in input/ is used, and both documents
# land under results/, named after that file
uv run python app.py shloka          # input/bhagavad_gita-2.47.txt → results/bhagavad_gita-2.47.{raw,result}.json

# Pick the base yourself; missing directories are created, a trailing '.json' is stripped
uv run python app.py shloka -i my_shloka.txt -o results/my_shloka

# Pada mode (single-word analysis)
uv run python app.py pada            # input/pada_input.txt → results/pada_input.{raw,result}.json

# IAST input analyzes exactly like the Devanagari one
uv run python app.py shloka -i my_shloka.iast.txt -o results/my_shloka

# Read from stdin — with no file name the pair is named after the mode
echo "वागर्थाविव संपृक्तौ वागर्थप्रतिपत्तये" | uv run python app.py shloka

# Second pass alone, on an existing raw document (offline, instant)
uv run python postprocess_analysis.py -o results/my_shloka
```

stdout carries only the result document's top-level `input` and `chandas` objects — no
padas, no engine data, no logs. Warnings, errors and size summaries go to stderr.

## CLI Arguments

```
usage: app.py [-h] [-i INPUT] [-o OUTPUT] [-f {json,pretty}] {pada,shloka}

positional arguments:
  {pada,shloka}        Analysis mode: 'pada' for single-word, 'shloka' for full-line analysis

options:
  -h, --help           Show this help message
  -i, --input INPUT    Input file (use '-' for stdin); default: the single .txt file
                       in input/ (see Input Files)
  -o, --output OUTPUT  Output base path; writes '<base>.raw.json' and
                       '<base>.result.json' (a trailing '.json' is stripped).
                       Default: results/<input stem>
  -f, --format FORMAT  Output format: 'json' (compact) or 'pretty' (indented, default)
```

## Input Files

There is exactly one place an implicit input may live: `input/`. The directory ships with nothing but
`.gitkeep` and everything else in it is gitignored, so a verse you type there can never be committed by
accident, and its stem names both output documents.

Precedence: `-i FILE` → the single `.txt` file in `input/`. If `input/` holds more than one, the run
stops and lists them rather than guessing; if it holds none, the run says so and points at `-i`. The
root-level files earlier releases fell back to (`input.txt`, `shloka_input.txt`, `pada_input.txt`) are
gone — they sat outside `input/`, their names matched nothing else in the project, and a mode-specific
name was chosen by the CLI argument instead of by what you actually put on disk. `-i -` reads stdin; with
no file name at all the pair is named after the mode (`results/shloka.*`). The pinned test corpus lives
separately under `tests/data/` (see Testing).

Input files may hold Devanagari or any romanization vidyut lipi detects; the text is canonicalized to Devanagari before analysis and both working scripts are recorded under `input` (the script the user typed is an input detail, not part of the analysis). Preprocessing first closes a **hyphenated line break** — printed editions split one word across the pāda junction (`… विहाय जीर्णान्य्-` / `अन्यानि …`), and handing an engine half of that word leaves it a dangling virama it cannot decompose, so the two halves are joined before anything else happens. It then turns every separator — dandas (`।` `॥`), ASCII pipes, dots, commas, hyphens inside a line, slashes — *and* every digit into a space: pasted verse numbers such as `॥ 66॥` or Devanagari `॥६६॥` disappear before any engine sees the text. Runs of whitespace inside each line collapse to one, so `vāc-artha`, `vāc artha.` and `vāc  artha` analyze identically; line structure is preserved and only the padding around a line is trimmed.

## Output Schema

The output is a JSON object with the following structure:

```json
{
  "input": {
    "devanagari": "वागर्थाविव संपृक्तौ...",
    "iast": "vāgarthāviva saṃpṛktau..."
  },
  "engine_outputs": {
    "sanskrit_parser": { ... },
    "dharmamitra": { ... },
    "vidyut": { ... }
  }
}
```

### [`sanskrit_parser`](https://github.com/kmadathil/sanskrit_parser) output

```json
{
  "mode": "pada" | "shloka",
  "input": "Devanagari text",
  "sandhi_splits": [
    {
      "line_index": 0,
      "split_index": 0,
      "split": ["vāgarthās", "viva", "sampṛktau", ...],
      "items": [
        {
          "pada": "vāgarthās",
          "morphological_tags": [
            {"root": "vāgartha", "tags": ["bahuvacanam", "prathamāvibhaktiḥ", "puṃlliṅgam"]}
          ]
        }
      ]
    }
  ],
  "word_decompositions": {
    "saṃpṛktau": [["sam", "pṛktau"]]
  },
  "word_morphology": [
    {"pada": "vāgartha", "morphological_tags": [{"root": "vāgartha", "tags": ["bahuvacanam", "prathamāvibhaktiḥ", "puṃlliṅgam"]]}
  ]
}
```

`line_index` names the pada-line a candidate came from — splitting is driven one line at a time,
never on the whole input. `word_morphology` holds the tags of the words the per-word ranking
actually chose: `sandhi_splits` only covers whatever whole-line candidates the library sampled, so
a chosen word can be missing there and would otherwise lose its root, case and number in the
reading document.

### [`dharmamitra`](https://dharmamitra.org) output

```json
{
  "api_endpoint": "https://dharmamitra.org/api/tagging/",
  "mode": "unsandhied-lemma-morphosyntax",
  "input_lines": ["vāgarthāviva saṃpṛktau vāgarthapratipattaye", "jagataḥ pitarau vande pārvatīparameśvarau"],
  "raw_output": "vāc_arthau_iva_saṃpṛktau_vāc_artha_pratipattaye_jagantaḥ_pitarau_vande_pārvatī_parameśvarau_",
  "tokens": [
    {"form": "vāc", "lemma": ["vac", "vāc"], "kosha_type": "sūnantāḥ"},
    {"form": "arthau", "lemma": ["artha", "arthi"], "kosha_type": "sūnantāḥ"}
  ]
}
```

`lemma` is a list when the vidyut kosha has several stems for that surface form;
a token with no kosha match keeps only `form`.

### [`vidyut`](https://github.com/ambuda-org/vidyut) output

```json
{
  "kosha": [
    {
      "iast": "vāk",
      "punctuation": "",
      "is_compound": false,
      "grammatical_entries": [
        {
          "type": "sūnantāḥ",
          "pratipadika": "vāc",
          "artha": "vācanam",
          "linga": "strīliṅgam",
          "vibhakti": "prathamā",
          "vacana": "dvivacanam"
        }
      ],
      "is_verb": false
    },
    {
      "iast": "saṃpṛktau",
      "is_compound": true,
      "sandhi_splits": [
        "sam + pṛktau",
        "sam + pra + kta"
      ]
    }
  ],
  "prakriya": {
    "dhatus": [
      {
        "dhatu": "vac",
        "krdantas": [
          {"step": 1, "sutra": "3.1.1", "source": "krt", "terms_iast": "kta", "changed_iast": ["kta"]}
        ],
        "tinantas": [
          {"label": "laṭ/madhyama/eka", "form": "vakti"},
          {"label": "laṅ/madhyama/eka", "form": "vakta"},
          {"label": "loṭ/madhyama/eka", "form": "vakṣati"}
        ]
      }
    ],
    "pratipadikas": [
      {
        "lemma": "vāc",
        "steps": [
          {"step": 1, "sutra": "1.1.1", "source": "pratyaya", "terms_iast": "sup", "changed_iast": ["sup"]}
        ]
      }
    ]
  },
  "meter": [
    {
      "line_iast": "vāgarthāviva saṃpṛktau vāgarthapratipattaye",
      "padas": [
        {
          "iast": "vāgarthāviva saṃpṛktau",
          "meter": "madalekhā",
          "akshara_count": 8,
          "weight_pattern": "GGGLLGGG"
        },
        {
          "iast": "vāgarthapratipattaye",
          "meter": "śuddhavirāṭ",
          "akshara_count": 8,
          "weight_pattern": "GGGLLGLG"
        }
      ]
    }
  ],
  "chandas": {
    "vrtta": null,
    "candidates": ["madalekhā", "śuddhavirāṭ"],
    "pada_count": 4,
    "classified_pada_count": 2,
    "aksharas_per_pada": [8, 8, 8, 8]
  }
}
```

A pāda whose classification yields several akshara groups reports them joined by
` | ` in one `weight_pattern`, with `meter` set only when a vṛtta matched.

`chandas` summarizes those pāda matches for the whole verse: `vrtta` names a meter
only when every pāda classifies to the same one, otherwise it stays `null` and
vidyut's own suggestions appear in `candidates`. vidyut's bundled catalogue
(`data-0.4.0/chandas/meters.tsv`, 145 vṛttas) contains no anuṣṭubh/śloka pattern, so
a classical śloka reports `null` there while `aksharas_per_pada: [8, 8, 8, 8]` shows
its real shape. A line is cut into pādas only when its akshara total is even and
above 12; shorter or odd lines go to the classifier whole (DOCUMENTATION.md §8).

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VIDYUT_DATA_DIR` | `./data-0.4.0` | Path to vidyut data directory containing kosha, prakriya, chandas, sandhi, and cheda subdirectories |
| `DHARMAMITRA_AUTH` | built-in demo credential | Value of the `Authorization` header sent to the Dharmamitra API |
| `DHARMAMITRA_TIMEOUT_SECS` | `20` | Total wall-clock budget for the Dharmamitra request, shared by both attempts |
| `SAMSKRTA_WORKERS` | auto (`min(2, cpu_count)`) | Process-pool size for the per-line sanskrit_parser work; `1` disables the pool |
| `SAMSKRTA_MIN_PARALLEL_LINES` | `8` | Pada-lines an input must reach before the pool is used at all |

## Performance

Measured on a 32-core box with `uv run python app.py shloka -f pretty`: one śloka end to end **~3 s** (interpreter and model load dominate); the sixteen-verse corpus in one run — 46 pada-lines — takes **~22 s** with the two-worker pool and ~53 s single-process. Where that time goes:

- sanskrit_parser dominates — ≈0.24 s of sandhi graph search per pada-line and ≈70 ms of kosha ranking per word. The process pool divides exactly this work, which is why it only starts above `SAMSKRTA_MIN_PARALLEL_LINES`: a single śloka has two lines and gains nothing from it.
- vidyut costs milliseconds, not seconds — its share of a corpus run is well under a second. Its kosha FST is built **once per process** by `load_kosha()` behind a lock, and shared by the dictionary lookups, the Dharmamitra lemma enrichment and every pool worker — building it three times per run was pure waste.
- The Dharmamitra round-trip runs on a background thread underneath the local engines, so a slow API adds at most `DHARMAMITRA_TIMEOUT_SECS` to the wall clock instead of stacking on top of it; a fast one costs nothing.

A pool worker holds its own Parser (~130 MB) and kosha index (~220 MB), which is why two is the default — on a memory-tight machine set `SAMSKRTA_WORKERS=1` rather than accept swapping.

## Error Handling

Each engine runs independently. If one fails, its key contains `{"error": "..."}` and execution continues for the remaining engines. Common failure modes:

- **sanskrit_parser unavailable**: Package not installed or import error — the run exits 1
- **Dharmamitra API unreachable**: `{"error": "Dharmamitra API request timed out after 20.0s"}`, `{"error": "Dharmamitra API unavailable: ..."}` (connection errors; both attempts share the one `DHARMAMITRA_TIMEOUT_SECS` deadline) or `{"error": "Dharmamitra API returned non-JSON body: ..."}` — a warning only, the run still exits 0
- **Vidyut data directory not found**: `VIDYUT_DATA_DIR` points to a non-existent directory (`{"error": "Vidyut data directory not found"}`) — the run exits 1

A failed engine is also surfaced in the processed document as an
`engine_errors` object, so a crash never looks like an empty analysis. Every failure
gets one stderr line: `Warning:` for Dharmamitra, `Error:` for the two local engines.

### Dharmamitra API quirks

- The API silently truncates its response after any line ending with trailing whitespace before a newline. `run_dharmamitra()` strips per-line whitespace before sending to work around this.
- Words the API cannot tag come back as empty underscore fields (`____iva_`); `_parse_tokens()` drops only those empty segments, so an untagged word simply yields no token for that pada. A pada with no Dharmamitra tokens appears as `"dharmamitra": null` in `<base>.result.json` rather than as an error.

## Example Output

```bash
$ uv run python app.py shloka -i tests/data/raghuvamsha-1.1.txt -o results/raghuvamsha-1.1
{
  "input": { "devanagari": "वागर्थाविव …", "iast": "vāgarthāviva …" },
  "chandas": { "vrtta": null, "candidates": ["madalekhā", "śuddhavirāṭ"], … }
}                                    # stdout: exactly these two objects
$ ls results/
raghuvamsha-1.1.raw.json  raghuvamsha-1.1.result.json

$ uv run python postprocess_analysis.py -o results/raghuvamsha-1.1    # second pass only
Raw: 85,903 bytes → processed: 7,361 bytes (91.4% smaller)            # stderr

## Two-Output Architecture

The system writes two documents under one base path — by default `results/<input stem>`:

### `<base>.raw.json` (raw)
Complete raw output from all three engines. Used for:
- Debugging
- Investigating parser failures
- Developing new heuristics

Typically ~90 KB for a śloka.

### `<base>.result.json` (processed)
Written by the same `app.py` run, or regenerated on its own with
`uv run python postprocess_analysis.py -o BASE`.

Contains:
- `padaccheda`: each engine's full word sequence for the śloka as one pipe-joined line
- `padas`: one entry per pada **as written in the input**, keyed by engine
- Per-engine `words` with morphology: Dharmamitra tokens carry kosha `lemma`/`type`; sanskrit_parser forms carry `root`, `vibhakti`, `vacana`, `linga`, plus `"alternates"` — every other reading the engine offered for that form, omitted when it offered only one. A Dharmamitra side that came from a word-level request instead of the verse pass carries `"request": "pada"`
- `chandas`: vidyut's verse-level meter summary (`vrtta`, `candidates`, per-pāda akshara counts). A name whose declared length contradicts its pāda stays in `candidates` but cannot decide `vrtta`; `vrtta: null` usually means vidyut's table has no metre for that shape at all (DOCUMENTATION §10)
- `differences`: aligned regions where the two engines split a pada differently (only present when they disagree)
- `dharmamitra_unmatched`: Dharmamitra tokens from the verse pass that rebuild no word of the verse — reported, never filed under a pada (key omitted when empty)

Typically ~7 KB for a śloka (~91% reduction). `<base>.result.json` is byte-stable **for a
given `<base>.raw.json`**: SP split candidates and morphology groups are re-ranked
deterministically, never taken in first-seen order. The raw pass itself is not
reproducible — sanskrit_parser enumerates candidate splits in an
unspecified order; see [DOCUMENTATION.md](DOCUMENTATION.md) §3.

```json
{
  "pada": "vāgarthapratipattaye",
  "dharmamitra": {
    "padaccheda": ["vāc", "artha", "pratipattaye"],
    "words": [
      {"form": "vāc", "lemma": ["vac", "vāc"], "type": "sūnantāḥ"},
      {"form": "artha", "lemma": "artha", "type": "sūnantāḥ"},
      {"form": "pratipattaye", "lemma": "pratipat", "type": "sūnantāḥ"}
    ]
  },
  "sanskrit_parser": {
    "padaccheda": ["vāgartha", "pratipattaye"],
    "words": [
      {"form": "vāgartha", "root": "vāgartha", "vibhakti": "saṃbodhana", "vacana": "eka", "linga": "puṃlliṅgam", "alternates": [{"root": "vāgartha", "linga": "puṃlliṅgam", "tags": ["samāsapūrvapadanāmapadam"]}]},
      {"form": "pratipattaye", "root": "pratipatti", "vibhakti": "caturthī", "vacana": "eka", "linga": "strīliṅgam"}
    ]
  },
  "differences": [
    {"sanskrit_parser": ["vāgartha"], "dharmamitra": ["vāc", "artha"]}
  ]
}
```

- `dharmamitra` is `null` when neither the verse-level request nor that pada's own word request produced any token for it.
- A lemma **list** means the vidyut kosha has several stems matching that surface form (genuine ambiguity, not a guess). No lemma key means the kosha had no match.
- `differences` regions come from `difflib` alignment; agreeing parts (`pratipattaye` above) are omitted and a `null` side marks a split-count mismatch.
- All comparison is anusvara-normalized: `saṃpṛktau` and `sampṛktau` count as equal.

## Processing Pipeline

```
raw engine outputs (<base>.raw.json)
    ↓
sanskrit_parser per-word split ranking (_best_word_split, app.py)
    ↓
Dharmamitra token lemma enrichment via vidyut kosha (app.py)
    ↓
word-level Dharmamitra requests for padas the verse pass left empty,
appended to the raw document as pada_followups (fill_missing_pada_readings, app.py)
    ↓
DM token grouping under input padas — coverage DP over anusvara-normalized IAST
(group_dm_tokens_by_word), then gap fill from pada_followups (build_padas)
    ↓
SP morphology collection + ranking across all sandhi splits
    ↓
difflib region diff between engines
    ↓
vidyut chandas summary copied to the verse level (_summarize_chandas, app.py)
    ↓
result document (<base>.result.json: padaccheda + engine-keyed padas + chandas
                [+ dharmamitra_unmatched when a token rebuilds no word])
```

This turns a ~90 KB raw document into a ~7 KB reading while preserving the
linguistic content of both comparison engines.

## Example Processed Output

```json
{
  "input": {"devanagari": "वागर्थाविव संपृक्तौ वागर्थप्रतिपत्तये\nजगतः पितरौ वन्दे पार्वतीपरमेश्वरौ", "iast": "vāgarthāviva saṃpṛktau vāgarthapratipattaye\njagataḥ pitarau vande pārvatīparameśvarau"},
  "padaccheda": {
    "dharmamitra": "vāc | arthau | iva | saṃpṛktau | vāc | artha | pratipattaye | jagantaḥ | pitarau | vande | pārvatī | parameśvarau",
    "sanskrit_parser": "vāgartha | āviva | sampṛktau | vāgartha | pratipattaye | jagatas | pitarau | vande | pārvatī | parameśvarau"
  },
  "padas": [
    {
      "pada": "vāgarthāviva",
      "dharmamitra": {"padaccheda": ["vāc", "arthau", "iva"], "words": [{"form": "vāc", "lemma": ["vac", "vāc"], "type": "sūnantāḥ"}, {"form": "arthau", "lemma": ["artha", "arthi"], "type": "sūnantāḥ"}, {"form": "iva", "lemma": "iva", "type": "avyayam"}]},
      "sanskrit_parser": {"padaccheda": ["vāgartha", "āviva"], "words": [{"form": "vāgartha", "root": "vāgartha", "vibhakti": "saṃbodhana", "vacana": "eka", "linga": "puṃlliṅgam", "alternates": [{"root": "vāgartha", "linga": "puṃlliṅgam", "tags": ["samāsapūrvapadanāmapadam"]}]}, {"form": "āviva", "root": "av", "vacana": "dvi", "tags": ["liṭ", "parasmaipadam", "prāthamikaḥ", "uttamapuruṣaḥ"]}]},
      "differences": [{"sanskrit_parser": ["vāgartha", "āviva"], "dharmamitra": ["vāc", "arthau", "iva"]}]
    }
  ],
  "chandas": {"vrtta": null, "candidates": ["madalekhā", "śuddhavirāṭ"], "pada_count": 4, "classified_pada_count": 2, "aksharas_per_pada": [8, 8, 8, 8]}
}
```

Devanagari appears only in `input.devanagari`; everything else is IAST. Both fields hold the *cleaned* text, so dandas and verse numbers never reach an engine or a pada list — an IAST file yields the same pair of fields with the same analysis. Each pada shows both engines' word splits side by side, with lemmas for every split token and the disagreement regions flagged explicitly.

## Determinism, Not Source Priority

The processed layer compares exactly two engines — `dharmamitra` and
`sanskrit_parser` — and never merges them into a single verdict. Both readings
are kept side by side per pada, and the places where they disagree are listed in
that pada's `differences`. There is no cross-engine voting or fallback chain; from
vidyut only the verse-level `chandas` summary is carried over.

Determinism comes from ranking instead of insertion order: SP split candidates
and morphology groups are scored (`_morph_rank`, `_best_word_split`) so repeated
runs produce byte-identical `<base>.result.json` for the same raw input.

## Testing

```bash
uv run pytest -q                        # offline, deterministic; never calls the Dharmamitra API
SAMSKRTA_LIVE_GOLDEN=1 uv run pytest -q tests/test_golden_outputs.py   # real engines again (~90 s, sixteen verses)
```

`tests/conftest.py` pins `VIDYUT_DATA_DIR` to the bundled `data-0.4.0/` and puts
the project root on `sys.path`. The suite covers both CLIs: pure helpers
(preprocessing, script detection, akshara counting, pada splitting, kosha
classification, chain scoring), the exit-code policy with every engine stubbed out
(a local engine down → 1, Dharmamitra alone down → 0), output-base naming (default
`results/<stem>`, created directories, stripped suffixes) and the stdout contract
(`input` + `chandas` only), plus the whole of `postprocess_analysis.py` against
sixteen verses — Raghuvaṃśa 1.1–1.7, Abhijñānaśākuntala 1.1, 1.7 and 1.18, Bhagavad Gītā 2.22, 2.47, 11.15, 15.5, 15.15 and 18.66 — live in `tests/data/`, each with an IAST twin that must canonicalize to exactly the same text, and each with a **pinned output pair** under `tests/data/results/`: `<stem>.raw.json` (what the engines produced) and `<stem>.result.json` (the reading document it postprocesses into). They cover an eight-akshara pāda (anuṣṭubh), eleven-akshara jagatī pādas, a fifteen-akshara mālinī and a twenty-one-akshara sragdharā, avagraha elisions (`वंशेऽस्मिन्`, `मा ते सङ्गोऽस्त्वकर्मणि`) and printed hyphens at pāda junctions.

`tests/test_golden_outputs.py` checks offline that `postprocess()` turns each pinned raw document
into exactly its pinned reading document — including identical bytes after re-serialization — and
that both documents satisfy per-verse invariants: pada count, metre shape (`aksharas_per_pada`,
vṛtta candidates), the exact padaccheda strings, and the specific splits where the two compared
engines disagree (Raghuvaṃśa 1.1's `vāc | arthau` against `vāgarthās`; Gītā 18.66's `mām | ekam`
against `māme | akam`). With `SAMSKRTA_LIVE_GOLDEN=1` it also regenerates every pair from the
fixture: Dharmamitra and vidyut must come back byte-identically, as must the reading document's
Dharmamitra column, pada sequence and metre summary, while the sanskrit_parser side is left free to
vary (see DOCUMENTATION.md §3).

Splitting quality is tested separately. `tests/data/sandhi_truth.json` records how a reader splits 147 padas of those verses (per-group provenance is spelled out in the test module's docstring); `tests/test_sandhi_accuracy.py` asserts thirteen curated cases always (a finite verb must survive whole, an elided conjunction must be separated, attested words must not dissolve into fragments, and long compounds must be read as their members) and, under the same `SAMSKRTA_LIVE_GOLDEN=1` switch, scores all 147 against `_best_word_split()` with a pinned floor of 101. A pada counts as correct when the part count agrees and each part pairs with an expected part sharing a kosha lemma stem — spelling is not compared, because sandhi changes it and the splitter writes word-final visarga as `s`, anusvara as `m`.

Metre accuracy is tested the same way. `tests/data/meter_truth.json` records the published छन्दः of each pinned verse — Devanagari name, IAST spelling, aksharas per pāda, pāda count and source URL (sanskritsahitya.org, cross-checked against its own data repository) — and `tests/test_meter_accuracy.py` runs offline. It asserts the scanned shape equals every published grid, that the five verses whose metre vidyut's table can hold do carry it (`indravajrā`, `sragdharā`, `malinī`), and that an impossible candidate such as the 12-akshara `indravaṃśā` stays visible in `candidates` without vetoing इन्द्रवज्रा. It then prints published truth next to best effort for all sixteen verses; the eleven anuṣṭubh and उपजातिः verses are pinned as `vrtta: null` and each emits a warning naming vidyut and its documented limit (145 vṛtta patterns, no jāti metre), because that gap is upstream and cannot be closed in our code.

Word readings are measured the same way. `tests/data/morphology_truth.json` records, for nine forms from these verses, what the word actually is *in that verse* (published पदच्छेदः plus standard grammar — BG 18.66 `mām ekaṃ śaraṇaṃ vraja`, Raghuvaṃśa 1.1 `jagataḥ pitarau vande`, …), and `tests/test_morphology_accuracy.py` runs offline against the committed result documents. Measured: **sanskrit_parser offers the published reading for all nine** — as primary or inside `alternates` — while our context-free ranking chooses it for **four** (`pitarau`, `deva`, `navāni`, `avyayam`). The other five (`vande`, `jagataḥ`, `asti`, `vraja`, `śucaḥ`) need the sentence to decide, so each warns with the limit that explains it instead of failing. Two ranking rules were tried and rejected on those numbers: finite-verb readings first changes 20 primaries across the sixteen verses (≈8 right, ≈12 wrong — `navāni` read as √nu, `deva` as an imperative), and preferring kosha-attested stems changes 15 (≈3 right, ≈10 wrong). Neither is a gain, so `_morph_rank` stays as it is and every other reading travels in `alternates`.

Regenerate a golden after an intentional change:

```bash
uv run python app.py shloka -f pretty -i tests/data/<stem>.txt -o tests/data/results/<stem>
```

## License

MIT
