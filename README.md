# samskrta-multi-parser-raw

Unified multi-engine Sanskrit analyzer. Runs three independent engines on the same Devanagari input and produces structured JSON results under distinct top-level keys in a single output file.

## Overview

This tool analyzes Sanskrit text (single words or full shloka lines) through three parallel engines, each producing raw structured output without merging or filtering. The engines are:

| Engine | Source | Capabilities |
|--------|--------|-------------|
| `sanskrit_parser` | Local Python package | Sandhi splitting, morphological tags, vakya (sentence) parsing |
| `dharmamitra` | Remote API (dharmamitra.org) | Sandhi splitting with lemma morphosyntax tags |
| `vidyut` | Local Python package | Kosha dictionary lookup, dhatu/pratipadika prakriya (derivation), meter classification, recursive sandhi splitting |

Each engine runs independently. If one fails, its key contains `{"error": "..."}` and execution continues for the remaining engines.

## Architecture

```
app.py (CLI entry point — raw pass)
├── preprocess_devanagari()     # Strip classical punctuation (।, ॥)
├── devanagari_to_iast()        # Convert Devanagari → IAST
├── read_input()                # Read from file or stdin ('-')
├── run_sanskrit_parser()       # Local: sandhi + morphology + vakya
├── run_dharmamitra()           # Remote: API-based lemma tags
│   └── enrich_dharmamitra_lemmas()  # Adds vidyut kosha lemmas to DM tokens
├── run_vidyut()                # Local: kosha + prakriya + meter + sandhi
└── main()                      # Runs the engines, writes one JSON document

normalize.py (CLI entry point — normalized pass)
└── normalize_raw()             # Reads output-verbose.json → compact output.json
```

The two passes are separate commands: `app.py` never imports `normalize.py`, and
`normalize.py` reads only the `sanskrit_parser` and `dharmamitra` engines plus
the raw input (vidyut output is not part of the normalized document).

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
# Shloka mode — raw JSON goes to stdout by default
uv run python app.py shloka > output-verbose.json

# Pada mode (single-word analysis)
uv run python app.py pada > output-verbose.json

# Write the raw pass straight to a file instead of piping
uv run python app.py shloka -i my_shloka.txt -o output-verbose.json

# Read from stdin
echo "वागर्थाविव संपृक्तौ वागर्थप्रतिपत्तये" | uv run python app.py shloka > raw.json

# Second pass: compact, deduplicated reading
uv run python normalize.py -o output.json
```

## CLI Arguments

```
usage: app.py [-h] [-i INPUT] [-o OUTPUT] [-f {json,pretty}] {pada,shloka}

positional arguments:
  {pada,shloka}        Analysis mode: 'pada' for single-word, 'shloka' for full-line analysis

options:
  -h, --help           Show this help message
  -i, --input INPUT    Input file (use '-' for stdin); defaults to input.txt, then the
                       mode-specific file (see Input Files)
  -o, --output OUTPUT  Output file; '-' (default) prints JSON to stdout only
  -f, --format FORMAT  Output format: 'json' (compact) or 'pretty' (indented, default)
```

## Input Files

| File | Purpose |
|------|---------|
| `input.txt` | Default input, used whenever it exists and `-i` is absent |
| `shloka_input.txt` | Fallback for shloka mode, only when `input.txt` is missing |
| `pada_input.txt` | Fallback for pada mode, only when `input.txt` is missing |

Precedence: `-i FILE` → `input.txt` → the mode-specific file. `-i -` reads stdin.

Input files should contain Devanagari Sanskrit text. Classical punctuation (`।`, `॥`) is automatically stripped during preprocessing.

## Output Schema

The output is a JSON object with the following structure:

```json
{
  "input": {
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

### sanskrit_parser output

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

### dharmamitra output

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

### vidyut output

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
  ]
}
```

A pāda whose classification yields several akshara groups reports them joined by
` | ` in one `weight_pattern`, with `meter` set only when a vṛtta matched.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VIDYUT_DATA_DIR` | `./data-0.4.0` | Path to vidyut data directory containing kosha, prakriya, chandas, sandhi, and cheda subdirectories |
| `DHARMAMITRA_AUTH` | built-in demo credential | Value of the `Authorization` header sent to the Dharmamitra API |

## Error Isolation

Each engine runs independently. If one fails, its key contains `{"error": "..."}` and execution continues for the remaining engines. Common failure modes:

- **sanskrit_parser unavailable**: Package not installed or import error
- **Dharmamitra API unreachable**: `{"error": "Dharmamitra API request timed out"}`, `{"error": "Dharmamitra API unavailable: ..."}` (connection errors, after one retry) or `{"error": "Dharmamitra API returned non-JSON body: ..."}`
- **Vidyut data directory not found**: `VIDYUT_DATA_DIR` points to a non-existent directory (`{"error": "Vidyut data directory not found"}`)

A failed engine is also surfaced in the normalized document as an
`engine_errors` object, so a crash never looks like an empty analysis.

### Dharmamitra API quirks

- The API silently truncates its response after any line ending with trailing whitespace before a newline. `run_dharmamitra()` strips per-line whitespace before sending to work around this.
- Words the API cannot tag come back as empty underscore fields (`____iva_`); `_parse_tokens()` drops only those empty segments, so an untagged word simply yields no token for that pada. A pada with no Dharmamitra tokens appears as `"dharmamitra": null` in `output.json` rather than as an error.

## Example Output

```bash
$ uv run python app.py shloka -o output-verbose.json   # silent; the JSON is in the file
$ uv run python normalize.py -o output.json
Raw: 89,920 bytes → normalized: 7,103 bytes (92.1% smaller)
```

## Two-Output Architecture

The system produces two outputs:

### `output-verbose.json` (raw)
Complete raw output from all three engines. Used for:
- Debugging
- Investigating parser failures
- Developing new heuristics

Typically ~90 KB for a śloka.

### `output.json` (normalized)
Generated by running `uv run python normalize.py` on `output-verbose.json`.

Contains:
- `padaccheda`: each engine's full word sequence for the śloka as one pipe-joined line
- `padas`: one entry per pada **as written in the input**, keyed by engine
- Per-engine `words` with morphology: Dharmamitra tokens carry kosha `lemma`/`type`; sanskrit_parser forms carry `root`, `vibhakti`, `vacana`, `linga`
- `differences`: aligned regions where the two engines split a pada differently (only present when they disagree)

Typically ~7 KB for a śloka (~92% reduction). `output.json` is byte-stable **for a given
`output-verbose.json`**: SP split candidates and morphology groups are re-ranked
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

## Normalization Pipeline

```
raw engine outputs (output-verbose.json)
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
normalized output (padaccheda + engine-keyed padas)
```

This turns a ~90 KB raw document into a ~7 KB reading while preserving the
linguistic content of both comparison engines.

## Example Normalized Output

```json
{
  "mode": "shloka",
  "input": {"devanagari": "वागर्थाविव संपृक्तौ वागर्थप्रतिपत्तये ।\nजगतः पितरौ वन्दे पार्वतीपरमेश्वरौ ॥", "iast": "vāgarthāviva saṃpṛktau vāgarthapratipattaye \njagataḥ pitarau vande pārvatīparameśvarau"},
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
  ]
}
```

Devanagari appears only in `input.devanagari`; everything else is IAST. Each pada shows both engines' word splits side by side, with lemmas for every split token and the disagreement regions flagged explicitly.

## Determinism, Not Source Priority

The normalized layer compares exactly two engines — `dharmamitra` and
`sanskrit_parser` — and never merges them into a single verdict. Both readings
are kept side by side per pada, and the places where they disagree are listed in
that pada's `differences`. There is no cross-engine voting or fallback chain.

Determinism comes from ranking instead of insertion order: SP split candidates
and morphology groups are scored (`_morph_rank`, `_best_word_split`) so repeated
runs produce byte-identical `output.json` for the same raw input.

## Testing

```bash
uv run pytest -q          # offline, deterministic; never calls the Dharmamitra API
```

`tests/conftest.py` pins `VIDYUT_DATA_DIR` to the bundled `data-0.4.0/` and puts
the project root on `sys.path`. The suite covers both CLIs: pure helpers
(preprocessing, akshara counting, pada splitting, kosha classification, chain
scoring) and the whole of `normalize.py` against hand-written raw fixtures.

## License

MIT
