# samskrta-multiparser

Analyse a Sanskrit verse with three independent engines — **Dharmamitrā** (HTTP API),
**sanskrit_parser** and **vidyut** (both local) — and write two JSON documents under one base name:
`<base>.raw.json`, the untouched engine output, and `<base>.result.json`, a pada-by-pada reading that lines their
answers up side by side. Nothing is merged into a consensus: where the engines disagree, the document says so.

How it works, every engine quirk we work around, and the full output schemas: [DOCUMENTATION.md](DOCUMENTATION.md).

## Known limits — read before trusting an answer

Measured on sixteen pinned verses against published padaccheda and metre tables; every figure and its provenance is in
[ACCURACY.md](ACCURACY.md).

- **Word boundaries: 104 of 147 padas** match the published split exactly. Of the rest, ⚠️20 are pool-limited — no
  reference-consistent split exists in what sanskrit_parser proposed for that line — and ❌23 are ranking-limited: the
  right split is in the pool but our ranking does not choose it today. The ceiling with this candidate set is 127 — live
  passes measure 126–128, because sanskrit_parser's own candidate pool drifts per process (§9).
- **Metre is named for exactly 5 of the 16 verses.** vidyut scans every verse correctly (`aksharas_per_pada` matches
  the published grid on all sixteen) but its 145-row table contains no jāti metre, so अनुष्टुप् and उपजातिः can never be
  named; it reports lookalike vṛtta names or `null`. Naming them is a metre-table problem, not a scanning problem (§10).
- **Word readings: the published first reading appears in 10 of 15 checked forms.** The other five are carried in
  `alternates`, ranked; per-pada analysis cannot settle them without sentence context (§11).
- **Dharmamitrā needs network access** and ships a public demo credential. Offline it degrades to
  `{"error": …}` in its own section and the run still exits 0 with a warning.
- **Every one of these is said out loud on stderr.** Nothing in this tool suppresses an engine error or limit.

## Install and run

```bash
uv sync                                          # runtime deps + pytest; vidyut ships abi3 wheels, no Rust needed
echo "अग्निमीले पर्वतशयनस्य नित्यं" > input/agne.txt
uv run samskrta shloka                           # → results/agne.raw.json  and  results/agne.result.json
uv run samskrta-postprocess -o results/agne      # re-derive the reading document, offline, no network
```

`samskrta` and `samskrta-postprocess` are the console entry points declared in `pyproject.toml`; they wrap `app.py`
and `postprocess_analysis.py`, so `uv run python app.py …` keeps working. One śloka takes ~2–3 s, dominated by the
Dharmamitrā round trip: vidyut's analysis costs milliseconds.

## Engines and versions

| Engine | Bound in `pyproject.toml` | Installed today | Role here |
|---|---|---|---|
| [`sanskrit-parser`](https://github.com/kitavani/sanskrit-parser) | `>=0.2.6,<0.3` | 0.2.6 | sandhi candidate generator + morphology; one word at a time, no context |
| [`vidyut`](https://github.com/ambuda-org/vidyut) | `>=0.4.0,<0.5` (+ `data-0.4.0`) | 0.4.0 | dictionary (kosha), prakriya derivations, chandas metre scanning; weakest splitter measured |
| [`indic-transliteration`](https://github.com/indic-transliteration/indic_transliteration_py) | `>=2.3.82,<3` | 2.3.82 | SLP1 → IAST for sanskrit_parser output (its own pinned `sanskrit-kg` data) |
| [`requests`](https://github.com/psf/requests) | `>=2.34.2,<3` | 2.34.2 | Dharmamitrā HTTP; pulls in `sqlalchemy` transitively via sanskrit-parser (unbounded, harmless) |

Raising a bound is an accuracy change, not housekeeping: the tag strings this tool matches come from those
libraries ([DOCUMENTATION.md §8 item 3](DOCUMENTATION.md)). Every output document records the exact versions that
produced it in its `meta` block.

## Command line

```bash
uv run samskrta [shloka|pada] [-i FILE|-] [-o BASE]
uv run samskrta-postprocess (-i RAW.json | -o BASE) [--no-dictionary]
```

- Modes differ only in the default input: `shloka` reads verse lines, `pada` one pada per line. Both accept any script —
  Devanagari, IAST or others (vidyut lipi detects and converts); separators (`। ॥ | , ; : - _` and ASCII punctuation)
  and digits become spaces, so a pasted verse number like `॥ 66॥` never reaches an engine. The avagraha `ऽ` is left
  exactly where it stands (§8 item 10).
- Input with no `-i`: the single `.txt` file in `input/`; several → listed and refused, none → error. `-i -` reads stdin.
- Output: `-o BASE` writes `BASE.raw.json` + `BASE.result.json`; with no `-o` the base is `results/<input stem>`
  (`results/shloka` / `results/pada` for stdin). Parent directories are created; a trailing `.json`, `.raw` or
  `.result` on the base is stripped, so feeding a generated file back in reproduces its sibling pair.
- `--no-dictionary` skips the kosha re-ranking of word readings — reproducible without vidyut's data files, at the cost
  of the ranking described in ACCURACY.md §2.

## Exit codes

| Code | `samskrta` | `samskrta-postprocess` |
|---|---|---|
| 0 | analysis complete (a Dharmamitrā failure only degrades its own section, with a `Warning:` on stderr) | reading document written |
| 1 | input problem: unreadable `-i`, ambiguous/empty `input/`, or the reading document could not be built from the raw one | any failure |
| 2 | a *local* engine (sanskrit_parser or vidyut) could not run at all — both documents, error objects included, are written first | |
| 3 | an output file could not be written (`OSError`) — only the two analysis documents are covered by this code | |

stdout carries every library warning — in Python's own format, nothing filtered — followed by the reading document's
top-level `input` and `chandas` objects; the metre caveat travels inside `chandas`, which is why it lives in the JSON
instead of flickering past on the console. A warning line may therefore precede the JSON: parse from the first `{` if
you pipe. The tool's own notes (`Warning:` for a degraded section, the provenance mismatch note, an incomplete run) stay
on stderr with the libraries' log noise. There is no `--quiet`, deliberately: those warning lines are the point of the
tool ([DOCUMENTATION.md §7](DOCUMENTATION.md)).

## The two documents

- **`<base>.raw.json`** — what each engine said, before any merging: full sanskrit_parser output (including every split
  candidate it offered), Dharmamitrā's token stream with locally-filled lemmas and the per-pada follow-up requests,
  vidyut's sandhi chains plus kosha readings, prakriya derivations and `chandas` per pāda. It opens with the provenance
  block, so any figure can be traced back to the engines that produced it:

```json
"meta": { "tool_version": "1.0.0",
          "engines": {"sanskrit_parser": "0.2.6", "vidyut": "0.4.0", "indic_transliteration": "2.3.82"},
          "vidyut_data": "data-0.4.0" }
```

  It is not reproducible: it re-hits the network and lets sanskrit_parser enumerate candidates in an order it does not
  specify (§3).
- **`<base>.result.json`** — one entry per pada with each engine's split side by side, a `differences` field naming the
  disagreements, chosen-word morphology, and vidyut's metre summary with a `note` on what its counts are worth and what
  its name is not. Reproducible from any raw file, offline and byte-stable. It carries no provenance block of its own: a
  reading inherits the engines recorded in its raw pair, and re-processing a raw document written by other versions
  prints a one-line note on stderr rather than passing silently.

Full field-by-field schemas, with example JSON for each engine and for the reading document:
[DOCUMENTATION.md §12](DOCUMENTATION.md).

## Environment variables

| Variable | Default | Effect |
|---|---|---|
| `VIDYUT_DATA_DIR` | `./data-0.4.0` | vidyut's data directory (kosha, prakriya, chandas, sandhi rules) |
| `DHARMAMITRA_AUTH` | the package's public demo credential | Authorization value for the API; set your own before anything sensitive |
| `DHARMAMITRA_TIMEOUT_SECS` | `20` | one shared deadline across both attempts per request |
| `SAMSKRTA_WORKERS` | auto: `min(2, cpu_count)` | processes used to analyze pada-lines in parallel; each holds its own Parser (~130 MB) and kosha FST (~220 MB) |
| `SAMSKRTA_MIN_PARALLEL_LINES` | `8` | inputs shorter than this never spawn a pool (spawn costs ~1 s per worker) |

Measured on a 32-core box over the sixteen pinned verses (46 pada-lines): ~22 s with two workers against ~53 s single,
≈0.24 s of sandhi work per pada-line and ≈70 ms of kosha ranking per word; vidyut's own analysis costs milliseconds on
top.

## Tests

```bash
uv run pytest -q                    # offline: 449 passed, 34 skipped — no network, deterministic
SAMSKRTA_LIVE_GOLDEN=1 uv run pytest -q tests/test_golden_outputs.py \
    tests/test_sandhi_accuracy.py tests/test_tools.py   # 157 passed in ~200 s; needs network + both engines
```

The offline half proves the reading pass still reproduces each pinned `<stem>.result.json` from its pinned raw file and
scores splitting/ranking against curated truth; the gated half re-runs all three engines. Goldens are regenerated, never
hand-edited: `uv run samskrta shloka -f pretty -i tests/data/<stem>.txt -o tests/data/results/<stem>`. A ranking-only
change needs no engine run — regenerate each reading document from its pinned raw file (~0.5 s each).

Every number in [ACCURACY.md](ACCURACY.md) has a committed tool that reproduces it: `tools/meter_audit.py`,
`tools/morphology_lab.py`, `tools/morphology_ranks.py`, `tools/sandhi_ceiling.py` and the ranking-rule lab
`tools/sandhi_lab.py`. They import the tests' own scoring helpers, so a tool can never report something the suite would
disagree with.

## Where to read more

[DOCUMENTATION.md](DOCUMENTATION.md) — §1 architecture · §2 transliteration and preprocessing · §3 determinism ·
§4 lemma provenance · §5 Dharmamitrā API quirks · §6 data layout · §7 development workflow, exit codes, logging policy ·
§8 known issues (19 of them, ordered by how likely they are to bite) · §9 how splitting quality is measured ·
§10 metre naming · §11 word-reading ranking · §12 output document schemas.

[ACCURACY.md](ACCURACY.md) — the scoreboard per area, what was tried and rejected with its numbers, and which engine
limit explains each remaining error.

## License

MIT.
