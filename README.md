# samskrta-multi-parser-raw

Unified multi-engine Sanskrit analyzer. Runs three independent engines — [sanskrit_parser](https://github.com/kmadathil/sanskrit_parser), [Dharmamitra](https://dharmamitra.org) and [vidyut](https://github.com/ambuda-org/vidyut) — on the same input (Devanagari, or any romanization vidyut's lipi can detect: IAST, SLP1, Harvard-Kyoto, ITRANS) and writes a pair of JSON documents under one base name: `<base>.raw.json`, holding everything each engine produced, and `<base>.result.json`, the condensed word-by-word reading.

## Overview

This tool analyzes Sanskrit text (single words or full shloka lines) through three parallel engines, each producing raw structured output without merging or filtering. The engines are:

| Engine | Source | Capabilities |
|--------|--------|-------------|
| [`sanskrit_parser`](https://github.com/kmadathil/sanskrit_parser) | Local Python package | Sandhi splitting, morphological tags, vakya (sentence) parsing |
| [`dharmamitra`](https://dharmamitra.org) | Remote HTTP API | Sandhi splitting with lemma morphosyntax tags |
| [`vidyut`](https://github.com/ambuda-org/vidyut) | Local Python package | Kosha dictionary lookup, dhatu/pratipadika prakriya (derivation), meter classification, recursive sandhi splitting |

Each engine runs independently: if one fails its key holds `{"error": "..."}` and the others still run. Dharmamitra is a remote service, so an unreachable API only costs that section — a `Warning:` line on stderr and exit code 0. `sanskrit_parser` and `vidyut` are local dependencies: when either cannot run at all the same error object is recorded, an `Error:` line goes to stderr, and the process exits 1.

## Why Three Engines

The engines overlap on purpose; none of them is good at everything.

- **Sandhi splitting — sanskrit_parser.** This is the splitter the reading document trusts. It proposes candidate splits for each pada, `app.py` ranks them (`_best_word_split`) so a complete case reading beats a fragment, and the morphological tags (root, vibhakti, vacana, linga) come from the same parse; it can also attempt a full vakya (sentence) parse of the pāda.
- **An independent second opinion — Dharmamitra.** The remote tagging API unsandhies the same text through an entirely different route and returns surface forms with lemma and kosha-type tags. Nothing is merged: `<base>.result.json` keeps both word sequences side by side under `padaccheda` and lists every region where they disagree under `differences`, so the reader — not a scoring heuristic — decides which split to accept for a given pada.
- **Everything else — vidyut.** [vidyut](https://github.com/ambuda-org/vidyut) carries the rest of the grammar: kosha lookup (lemma plus `sūnantāḥ` / `tīnantāḥ` / `avyayam` classification), dhatu and pratipadika prakriya — the step-by-step derivation of an inflected form from its stem, krdantas included — and chandas: per-pāda syllable counts plus the verse-level vṛtta candidates. Its kosha also supplies the lemmas attached to Dharmamitra's tokens (`enrich_dharmamitra_lemmas`), because that API returns surface forms only.

vidyut does ship a sandhi splitter, and `run_vidyut()` uses it — but its DFS over sandhi rules and the kosha has to be filtered hard before it is usable (`_is_quality_split`: both parts ≥ 4 aksharas, at least two kosha entries each, at least one non-derived entry per part; chains then scored by `_chain_score`), because unfiltered splitting runs well past the real word boundary. Its `sandhi_splits` therefore stay in `<base>.raw.json` as a third opinion for debugging and heuristic work; the pada-by-pada comparison in `<base>.result.json` is between sanskrit_parser and Dharmamitra, and vidyut's contribution there is the `chandas` summary.

## Architecture

```
app.py (CLI entry point — raw pass)
├── detect_script()             # Name the script vidyut lipi detects
├── to_devanagari()             # Canonicalize any detected script to Devanagari
├── preprocess_input()          # Separators → spaces, whitespace runs collapsed
├── devanagari_to_iast()        # Convert Devanagari → IAST
├── read_input()                # Read from file or stdin ('-')
├── run_sanskrit_parser()       # Local: sandhi + morphology + vakya
├── run_dharmamitra()           # Remote: API-based lemma tags
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
cd samskrta-multi-parser-raw
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

Input files may hold Devanagari or any romanization vidyut lipi detects; the detected script is recorded as `input.script` and the text is canonicalized to Devanagari before analysis. Preprocessing then turns every separator — dandas (`।` `॥`), ASCII pipes, dots, commas, hyphens, slashes — into a space and collapses runs of whitespace inside each line, so `vāc-artha`, `vāc artha.` and `vāc  artha` analyze identically. Line structure is preserved; only the padding around a line is trimmed.

## Output Schema

The output is a JSON object with the following structure:

```json
{
  "input": {
    "script": "Devanagari",
    "devanagari": "वागर्थाविव संपृक्तौ...",
    "iast": "vāgarthāviva saṃpṛktau..."
  },
  "mode": "pada" | "shloka",
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
  }
}
```

`vakya_parses[].graph` is omitted for a pāda whose vakya parse exceeded the
5-second watchdog; that split then carries `vakya_error` instead.

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
  "input": { "script": "Devanagari", "devanagari": "वागर्थाविव …", "iast": "vāgarthāviva …" },
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
  "mode": "shloka",
  "input": {"script": "Devanagari", "devanagari": "वागर्थाविव संपृक्तौ वागर्थप्रतिपत्तये ।\nजगतः पितरौ वन्दे पार्वतीपरमेश्वरौ ॥", "iast": "vāgarthāviva saṃpṛktau vāgarthapratipattaye \njagataḥ pitarau vande pārvatīparameśvarau"},
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

Devanagari appears only in `input.devanagari`; everything else is IAST. `input.script` records which script the user actually typed — an IAST file yields `"script": "Iast"` with the same analysis. Each pada shows both engines' word splits side by side, with lemmas for every split token and the disagreement regions flagged explicitly.

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
uv run pytest -q          # offline, deterministic; never calls the Dharmamitra API
```

`tests/conftest.py` pins `VIDYUT_DATA_DIR` to the bundled `data-0.4.0/` and puts
the project root on `sys.path`. The suite covers both CLIs: pure helpers
(preprocessing, script detection, akshara counting, pada splitting, kosha
classification, chain scoring), the exit-code policy with every engine stubbed out
(a local engine down → 1, Dharmamitra alone down → 0), output-base naming (default
`results/<stem>`, created directories, stripped suffixes) and the stdout contract
(`input` + `chandas` only), plus the whole of `postprocess_analysis.py` against
hand-written raw fixtures. Three real verses (Raghuvaṃśa 1.1/1.2,
Abhijñānaśākuntala 1.1) live in `tests/data/`, each with an IAST twin that must
preprocess to exactly the same text.

## License

MIT
