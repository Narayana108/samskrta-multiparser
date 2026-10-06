# samskrta-multiparser

[github.com/Narayana108/samskrta-multiparser](https://github.com/Narayana108/samskrta-multiparser)

Unified multi-engine Sanskrit analyzer. Runs three independent engines — [sanskrit_parser](https://github.com/kmadathil/sanskrit_parser), [Dharmamitra](https://dharmamitra.org) and [vidyut](https://github.com/ambuda-org/vidyut) — on the same input (Devanagari, or any romanization vidyut's lipi can detect: IAST, SLP1, Harvard-Kyoto, ITRANS) and writes a pair of JSON documents under one base name: `<base>.raw.json`, holding everything each engine produced, and `<base>.result.json`, the condensed word-by-word reading.

## Overview

This tool analyzes Sanskrit text (single words or full shloka lines) through three parallel engines, each producing raw structured output without merging or filtering. The engines are:

| Engine | Source | Capabilities |
|--------|--------|-------------|
| [`sanskrit_parser`](https://github.com/kmadathil/sanskrit_parser) | Local Python package | Sandhi splitting, morphological tags, vakya (sentence) parsing |
| [`dharmamitra`](https://dharmamitra.org) | Remote HTTP API | Independent unsandhiing; the response is underscore-separated surface forms |
| [`vidyut`](https://github.com/ambuda-org/vidyut) | Local Python package | Kosha dictionary lookup, dhatu/pratipadika prakriya (derivation), meter classification, recursive sandhi splitting |

Each engine runs independently: if one fails its key holds `{"error": "..."}` and the others still run. Dharmamitra is a remote service, so an unreachable API only costs that section — a `Warning:` line on stderr and exit code 0. `sanskrit_parser` and `vidyut` are local dependencies: when either cannot run at all the same error object is recorded, an `Error:` line goes to stderr, and the process exits 1.

## Why Three Engines

The engines overlap on purpose; none of them is good at everything.

- **Sandhi splitting — sanskrit_parser.** `run_sanskrit_parser()` uses three calls on `Parser(output_encoding=sanscript.DEVANAGARI)` from [sanskrit_parser](https://github.com/kmadathil/sanskrit_parser):
  - `parser.split(line, limit=5)` — up to five candidate unsandhied readings of each line (ten in pada mode). Every item of every candidate is then tagged with `parser.sandhi_analyzer.getMorphologicalTags(item, tmap=True)`, which is where the root and the vibhakti / vacana / linga tags come from.
  - `split.parse(limit=3)` on each candidate — the vakya (sentence) graph: for every pada its root and tags, plus its predecessor and the sambandha label. Best-effort only: it is combinatorial, so it runs under a `SIGALRM` watchdog (`VAKYA_TIMEOUT_SECS = 5`) and records `vakya_error` instead of failing the engine.
  - `parser.split(word, limit=10)` on each word by itself — `_best_word_split()` ranks those candidates instead of taking the first, because candidate order changes between processes. With the vidyut kosha loaded (`load_kosha()`, cached) the keys are: every part exactly attested in the dictionary; then fewest parts; then every part having standalone morphology; then scarcest-part rarity; then longest shortest part; sorted parts last for byte-stability. These per-word readings are what `<base>.result.json` prints under `padaccheda.sanskrit_parser`, and their quality is measured — see [What each engine cannot do](#what-each-engine-cannot-do-and-what-this-tool-does-about-it).
- **An independent second opinion — Dharmamitra.** `run_dharmamitra()` POSTs the IAST text to `https://dharmamitra.org/api/tagging/` (`mode="unsandhied-lemma-morphosyntax"`, one retry, 30 s timeout) and reads `results[0]`: in practice that is an underscore-separated sequence of **surface forms** — `kva_sūrya_prabhavaḥ_vaṃśaḥ_…` — so the API's contribution is its own unsandhiing, nothing else. Nothing is merged: `<base>.result.json` keeps both word sequences side by side under `padaccheda` and lists every region where they disagree under `differences`, so the reader — not a scoring heuristic — decides which split to accept for a given pada.
- **Everything else — vidyut.** `run_vidyut()` uses four [vidyut](https://github.com/ambuda-org/vidyut) modules against the local `data-0.4.0/` trees:
  - `vidyut.kosha.Kosha(data-0.4.0/kosha)` — an FST dictionary queried with `kosha.get(slp1)`; `kosha_lookup()` adds a stem fallback (strip up to three trailing SLP1 characters) for surface forms the keys miss, and `enrich_dharmamitra_lemmas()` re-queries it (with pause-spelling fixes: `…c → …k`, `…j → …g`, `…ś → …ṣ`) to attach lemmas to Dharmamitra's tokens. `_kosha_entry_info` classifies each hit — repr contains `Tinanta` → tīnantāḥ, else `entry.is_avyaya` → avyayam, else sūnantāḥ — and keeps the lemma. A word that hits gets up to eight deduplicated `grammatical_entries`, formatted by `_format_pada_entry_json`: pratipadika / artha / linga / vibhakti / vacana for nominal forms; dhatu / gaṇa / prayoga / lakāra / puruṣa / vacana for finite verbs.
  - `vidyut.prakriya.Vyakarana().derive(...)` — derivation steps from `prakriya.history` (sūtra code, source, terms, which terms changed) for two stem kinds only: `Dhatu.mula(upadeśa, gaṇa)` yields the krdanta derivation plus three fixed tīnanta samples (lat / laṭ / loṭ × madhyama × ekavacana, kartari prayoga), and `Pratipadika.basic(lemma)` covers up to five nominal stems. **There is no prakriya for avyayas** — vidyut derives nāma and ākhyāta only, so an avyaya gets its kosha label and nothing further.
  - `vidyut.chandas.Chandas(data-0.4.0/chandas/meters.tsv)` — `classify()` runs on one pāda at a time (`_split_into_padas` cuts a line where the cumulative akshara count reaches its midpoint), giving the matched vṛtta (`match.padya`) and every akshara's weight (`match.aksharas`). `_summarize_chandas()` names the verse only when all padas agree; meters.tsv holds 145 vṛttas and not the classical anuṣṭubh, which is why `vrtta` stays null for a plain śloka while `aksharas_per_pada: [8, 8, 8, 8]` still shows its shape.
  - `vidyut.lipi` — `detect()` names the input script and `transliterate()` moves between Devanagari, IAST and SLP1 for everything else in the pipeline.

vidyut does ship a sandhi splitter — `Splitter.from_csv(data-0.4.0/sandhi/rules.csv)` — and `run_vidyut()` uses it through `_is_quality_split()`, which calls `splitter.split_at(word, i)` at every position and keeps only splits whose parts are ≥ 4 characters long, have at least two kosha entries each, and have at least one non-derived entry per part. `recursive_split()` then walks the surviving pairs depth-first (`max_depth=2`, at most four parts) and `_chain_score` ranks the chains (more kosha-attested parts, fewer parts). Unfiltered splitting runs well past the real word boundary, which is why those `sandhi_splits` stay in `<base>.raw.json` as a third opinion; the pada-by-pada comparison in `<base>.result.json` is between sanskrit_parser and Dharmamitra, and vidyut's contribution there is the `chandas` summary.

### What each engine cannot do, and what this tool does about it

The split of merit is Dharmamitra's, but only for sandhi: it reads the whole sentence, so it knows that `मामेकं` is `mām + ekam`. It is also the least reliable source in the pipeline, which is why nothing is merged.

- **Dharmamitra — right boundaries, wrong words.** Remote only (30 s timeout, one retry; an offline run records `{"error": …}` and the other engines still produce documents). Its answer is a flat underscore-separated stream with no pada marks: when it cuts one word into more tokens than the verse has padas, every later alignment shifts by one, which shows up as a null side or `—` column in `<base>.result.json` (Raghuvaṃśa 1.2 `mohāduḍupenāsmi`, 1.3 `gamiṣyāmyupahāsyatām`). Its readings contain base stems instead of surface forms (`vāc` for वाक्), its own misreadings (`jagantaḥ` for जगतः) and tokens no sandhi rule produces (`upahāsya | tām` for उपहास्यताम्). Mitigation: side-by-side columns plus `differences`, never a merged answer; lemmas are filled locally from the vidyut kosha (DOCUMENTATION §4); wherever it serves as a test reference its splits are hand-corrected against published padaccheda tables first.
- **sanskrit_parser — a good generator with no context.** It inspects one word at a time, so samāsa stays whole (`yathākālaprabodhinām`, `prāṃśulabhye`) and it sometimes cuts inside a joined form (`māme | akam` for माम् एकम्). Mitigations: all ten candidates are ranked by dictionary attestation plus a transparent-compound gate that reads long attested compounds as their members (`sūryaprabhavas` → `sūrya | prabhavaḥ`) rather than taken in library order (deterministic within one process, accuracy-floored across processes); `parser.split()` returns `None`, not `[]`, when it finds no split at all — guarded with `or []` after that crashed the raw pass on an avagraha token; the combinatorial vakya parse runs under a 5 s `SIGALRM` watchdog and records `vakya_error`.
- **vidyut — dictionary, derivation, meter; weak sandhi.** Its splitter is the weakest generator measured: reference-consistent chains for 33 of 63 curated padas, against sanskrit_parser's pool containing one for 56 of 64. `_is_quality_split()` suppresses noise by demanding parts ≥ 4 characters with ≥ 2 kosha entries — and with them real words such as `iva`, `yā`, `tu`. So vidyut's splits stay in `<base>.raw.json` as a third opinion and never enter the pada-by-pada comparison. Its other gaps are documented where they occur: no prakriya for avyayas, no anuṣṭubh in meters.tsv (`vrtta: null`, shape still visible as `aksharas_per_pada`), and `Splitter.split_at(word, i)` takes a **byte** offset — passing a Python character index mis-splits non-ASCII words, so `rūpāṇi` came back as `rū | pāṇi`.
- **Avagraha (`ऽ`) is left inside the token.** Three variants were tried on Raghuvaṃśa 1.4 (`वंशेऽस्मिन्पूर्वसूरिभिः`): keeping it, deleting it, and restoring the elided `अ`. Deleting strands `स्मिन्` and makes sanskrit_parser return nothing; restoring adds a syllable that vidyut then counts, so the meter came back as `[17, 8, 8]` instead of `[8, 8, 8, 8]`. With the avagraha untouched, sanskrit_parser splits the fused token correctly by itself: `vaṃśe | asmin | pūrvasūribhis`.

**Measured splitting quality.** `tests/data/sandhi_truth.json` holds 64 padas of the pinned verses split the way a reader splits them (Dharmamitra's boundaries, corrected against the published Raghuvaṃśa padaccheda). Against it `_best_word_split()` reproduces the reading for **44/64** padas; morphology-only ranking — the fallback when no kosha is available — manages 36/64, and sanskrit_parser's candidate pool contains a reference-consistent split for 56/64, which bounds any ranking. `tests/test_sandhi_accuracy.py` pins a floor of 42 and prints every missed pada on failure. The remaining misses are context problems (samāsa, case government) that no per-word method can solve — that is the role Dharmamitra plays in the output. Chasing Dharmamitra's own splits was measured and rejected: it agrees with that reference on only **22/64** padas itself (it answers `jagataḥ` as `jagatas | ca`, invents `upahāsya | tām`, and reports base stems instead of surface forms), so matching it would lower the score, not raise it. Metre was tried too, and is reported but never used for splitting: pāda-edge alignment moved one pick (44/64 → 44/64), fitting an anuṣṭubh guru-laghu pattern cost five (44/64 → 39/64) and the caesura rule cost nine on the Raghuvaṃśa rows (32/46 → 23/46), because vidyut scans weights straight across word boundaries and its table has no anuṣṭubh row at all.

## Architecture

```
app.py (CLI entry point — raw pass)
├── detect_script()             # Name the script vidyut lipi detects
├── to_devanagari()             # Canonicalize any detected script to Devanagari
├── preprocess_input()          # Separators → spaces, whitespace runs collapsed
├── devanagari_to_iast()        # Convert Devanagari → IAST
├── read_input()                # Read from file or stdin ('-')
├── run_sanskrit_parser()       # Local: sandhi + morphology + vakya
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
# Shloka mode — both documents land under results/, named after the input file
uv run python app.py shloka          # → results/shloka_input.raw.json + .result.json

# Pick the base yourself; missing directories are created, a trailing '.json' is stripped
uv run python app.py shloka -i my_shloka.txt -o results/my_shloka

# Pada mode (single-word analysis)
uv run python app.py pada            # → results/pada_input.{raw,result}.json

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
  -i, --input INPUT    Input file (use '-' for stdin); defaults to input.txt, then the
                       mode-specific file (see Input Files)
  -o, --output OUTPUT  Output base path; writes '<base>.raw.json' and
                       '<base>.result.json' (a trailing '.json' is stripped).
                       Default: results/<input stem>
  -f, --format FORMAT  Output format: 'json' (compact) or 'pretty' (indented, default)
```

## Input Files

| File | Purpose |
|------|---------|
| `input.txt` | Default input, used whenever it exists and `-i` is absent |
| `shloka_input.txt` | Fallback for shloka mode, only when `input.txt` is missing |
| `pada_input.txt` | Fallback for pada mode, only when `input.txt` is missing |

Precedence: `-i FILE` → `input.txt` → the mode-specific file. `-i -` reads stdin.

Input files may hold Devanagari or any romanization vidyut lipi detects; the text is canonicalized to Devanagari before analysis and both working scripts are recorded under `input` (the script the user typed is an input detail, not part of the analysis). Preprocessing then turns every separator — dandas (`।` `॥`), ASCII pipes, dots, commas, hyphens, slashes — *and* every digit into a space: pasted verse numbers such as `॥ 66॥` or Devanagari `॥६६॥` disappear before any engine sees the text. Runs of whitespace inside each line collapse to one, so `vāc-artha`, `vāc artha.` and `vāc  artha` analyze identically; line structure is preserved and only the padding around a line is trimmed.

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
      "split_index": 0,
      "split": ["vāgarthās", "viva", "sampṛktau", ...],
      "items": [
        {
          "pada": "vāgarthās",
          "morphological_tags": [
            {"root": "vāgartha", "tags": ["bahuvacanam", "prathamāvibhaktiḥ", "puṃlliṅgam"]}
          ]
        }
      ],
      "vakya_parses": [
        {
          "parse_index": 0,
          "cost": 12.5,
          "graph": [
            {"pada": "vāc", "root": "vac", "tags": [...], "predecessor": {...}, "sambandha": "..."}
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

`vakya_parses[].graph` is omitted for a pāda whose vakya parse exceeded the
5-second watchdog; that split then carries `vakya_error` instead. `word_morphology` holds the tags
of the words the per-word ranking actually chose: `sandhi_splits` only covers whatever whole-line
candidates the library sampled, so a chosen word can be missing there and would otherwise lose its
root, case and number in the reading document.

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

## Error Handling

Each engine runs independently. If one fails, its key contains `{"error": "..."}` and execution continues for the remaining engines. Common failure modes:

- **sanskrit_parser unavailable**: Package not installed or import error — the run exits 1
- **Dharmamitra API unreachable**: `{"error": "Dharmamitra API request timed out"}`, `{"error": "Dharmamitra API unavailable: ..."}` (connection errors, after one retry) or `{"error": "Dharmamitra API returned non-JSON body: ..."}` — a warning only, the run still exits 0
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
- Per-engine `words` with morphology: Dharmamitra tokens carry kosha `lemma`/`type`; sanskrit_parser forms carry `root`, `vibhakti`, `vacana`, `linga`
- `chandas`: vidyut's verse-level meter summary (`vrtta`, `candidates`, per-pāda akshara counts)
- `differences`: aligned regions where the two engines split a pada differently (only present when they disagree)

Typically ~7 KB for a śloka (~91% reduction). `<base>.result.json` is byte-stable **for a
given `<base>.raw.json`**: SP split candidates and morphology groups are re-ranked
deterministically, never taken in first-seen order. The raw pass itself is not
reproducible — sanskrit_parser enumerates candidate splits and vakya parses in an
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
      {"form": "vāgartha", "root": "vāgartha", "vibhakti": "saṃbodhana", "vacana": "eka", "linga": "puṃlliṅgam"},
      {"form": "pratipattaye", "root": "pratipatti", "vibhakti": "caturthī", "vacana": "eka", "linga": "strīliṅgam"}
    ]
  },
  "differences": [
    {"sanskrit_parser": ["vāgartha"], "dharmamitra": ["vāc", "artha"]}
  ]
}
```

- `dharmamitra` is `null` when the API returned no tokens for that pada.
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
DM token grouping under input padas (IAST matching, anusvara-normalized)
    ↓
SP morphology collection + ranking across all sandhi splits
    ↓
difflib region diff between engines
    ↓
vidyut chandas summary copied to the verse level (_summarize_chandas, app.py)
    ↓
result document (<base>.result.json: padaccheda + engine-keyed padas + chandas)
```

This turns a ~90 KB raw document into a ~7 KB reading while preserving the
linguistic content of both comparison engines.

## Example Processed Output

```json
{
  "input": {"devanagari": "वागर्थाविव संपृक्तौ वागर्थप्रतिपत्तये\nजगतः पितरौ वन्दे पार्वतीपरमेश्वरौ", "iast": "vāgarthāviva saṃpṛktau vāgarthapratipattaye\njagataḥ pitarau vande pārvatīparameśvarau"},
  "padaccheda": {
    "dharmamitra": "vāc | arthau | iva | saṃpṛktau | vāc | artha | pratipattaye | jagantaḥ | pitarau | vande | pārvatī | parameśvarau",
    "sanskrit_parser": "vāgarthās | viva | sampṛktau | vāgartha | pratipattaye | jagatas | pitarau | vande | pārvatī | parameśvarau"
  },
  "padas": [
    {
      "pada": "vāgarthāviva",
      "dharmamitra": {"padaccheda": ["vāc", "arthau", "iva"], "words": [{"form": "vāc", "lemma": ["vac", "vāc"], "type": "sūnantāḥ"}, {"form": "arthau", "lemma": ["artha", "arthi"], "type": "sūnantāḥ"}, {"form": "iva", "lemma": "iva", "type": "avyayam"}]},
      "sanskrit_parser": {"padaccheda": ["vāgarthās", "viva"], "words": [{"form": "vāgarthās", "root": "vāgartha", "vibhakti": "prathamā", "vacana": "bahu", "linga": "puṃlliṅgam"}, {"form": "viva", "root": "viva", "vibhakti": "saṃbodhana", "vacana": "eka", "linga": "napuṃsakaliṅgam"}]},
      "differences": [{"sanskrit_parser": ["vāgarthās", "viva"], "dharmamitra": ["vāc", "arthau", "iva"]}]
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
SAMSKRTA_LIVE_GOLDEN=1 uv run pytest -q tests/test_golden_outputs.py   # real engines again (~6 min, nine verses)
```

`tests/conftest.py` pins `VIDYUT_DATA_DIR` to the bundled `data-0.4.0/` and puts
the project root on `sys.path`. The suite covers both CLIs: pure helpers
(preprocessing, script detection, akshara counting, pada splitting, kosha
classification, chain scoring), the exit-code policy with every engine stubbed out
(a local engine down → 1, Dharmamitra alone down → 0), output-base naming (default
`results/<stem>`, created directories, stripped suffixes) and the stdout contract
(`input` + `chandas` only), plus the whole of `postprocess_analysis.py` against
Nine verses — Raghuvaṃśa 1.1–1.7, Abhijñānaśākuntala 1.1, Bhagavad Gītā 18.66 — live in `tests/data/`, each with an IAST twin that must canonicalize to exactly the same text, and each with a **pinned output pair** under `tests/data/results/`: `<stem>.raw.json` (what the engines produced) and `<stem>.result.json` (the reading document it postprocesses into).

`tests/test_golden_outputs.py` checks offline that `postprocess()` turns each pinned raw document
into exactly its pinned reading document — including identical bytes after re-serialization — and
that both documents satisfy per-verse invariants: pada count, metre shape (`aksharas_per_pada`,
vṛtta candidates), the exact padaccheda strings, and the specific splits where the two compared
engines disagree (Raghuvaṃśa 1.1's `vāc | arthau` against `vāgarthās`; Gītā 18.66's `mām | ekam`
against `māme | akam`). With `SAMSKRTA_LIVE_GOLDEN=1` it also regenerates every pair from the
fixture: Dharmamitra and vidyut must come back byte-identically, as must the reading document's
Dharmamitra column, pada sequence and metre summary, while the sanskrit_parser side is left free to
vary (see DOCUMENTATION.md §3).

Splitting quality is tested separately. `tests/data/sandhi_truth.json` records how a reader splits 64 padas of those verses; `tests/test_sandhi_accuracy.py` asserts eleven curated cases always (a finite verb must survive whole, an elided conjunction must be separated, attested words must not dissolve into fragments, and three long compounds must be read as their members) and, under the same `SAMSKRTA_LIVE_GOLDEN=1` switch, scores all 64 against `_best_word_split()` with a pinned floor of 42. A pada counts as correct when the part count agrees and each part pairs with an expected part sharing a kosha lemma stem — spelling is not compared, because sandhi changes it and the splitter writes word-final visarga as `s`, anusvara as `m`.

Regenerate a golden after an intentional change:

```bash
uv run python app.py shloka -f pretty -i tests/data/<stem>.txt -o tests/data/results/<stem>
```

## License

MIT
