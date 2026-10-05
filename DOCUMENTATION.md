# Documentation

Deep reference for `samskrta-multi-parser-raw`. The [README](README.md) covers
usage; this file covers *why* the code is shaped the way it is, the library
quirks that forced specific workarounds, and what to watch when maintaining it.

## 1. Architecture

Two independent command-line passes over the same Devanagari input:

```
Devanagari text
      │
 app.py ──────────────────────────────► output-verbose.json   (raw pass)
 │   ├── run_sanskrit_parser()   local
 │   ├── run_dharmamitra()       remote HTTP (+ kosha lemma enrichment)
 │   └── run_vidyut()            local
      │
 normalize.py ───────────────────────► output.json            (reading pass)
```

`app.py` never imports `normalize.py`; `normalize.py` consumes the raw JSON and
touches only two engines (`sanskrit_parser`, `dharmamitra`) plus `input`. The
split exists because the raw dumps are ~90 KB of engine-specific noise while the
normalized reading is ~7 KB; keeping both makes parser disagreements auditable.

### Error isolation

Every engine call is wrapped so a failure becomes data, never a crash:

| Failure | Shape in raw output |
|---|---|
| Engine import missing | `{"error": "sanskrit_parser unavailable: ..."}` |
| Dharmamitra unreachable / timeout / bad body | `{"error": "Dharmamitra API request timed out"}`, `{"error": "Dharmamitra API unavailable: ..."}`, `{"error": "Dharmamitra API returned non-JSON body: ..."}` |
| Unexpected response shape | `{"error": "Dharmamitra API response shape unexpected: ..."}` |
| Vidyut data missing | `{"error": "Vidyut data directory not found"}` |
| Vidyut library init failure | `{"error": "Vidyut initialization failed: <exc>"}` |

Two consequences worth knowing:

- **`engine_errors` in `output.json`.** `normalize_raw` scans the engine dicts for
  an `error` key and emits `"engine_errors": {engine: message}` only when at least
  one engine failed. Without it, a crashed engine is indistinguishable from a text
  that genuinely produced no analysis.
- **The vakya watchdog.** sanskrit_parser's vakya (dependency) parsing can spin on
  long pādas, so each parse runs under `signal.alarm(VAKYA_TIMEOUT_SECS)` (5 s). A
  timeout records `"vakya_error"` on that split instead of a graph. The alarm is
  best-effort: it cannot interrupt native parser code (see §8).

Library chatter that would otherwise pollute the pipeline is reported through
`_warn_once(site, exc)`: one line per distinct site to **stderr**, so stdout stays
valid JSON while swallowed library errors remain visible. This replaced several
bare `except Exception: pass` blocks in the vidyut prakriya and sandhi paths.

## 2. Transliteration pipeline

Three scripts are in play; every conversion is explicit about which one it uses.

| Script | Used by | Notes |
|---|---|---|
| Devanagari | user input, `input.devanagari` | the only place Devanagari survives into any output |
| SLP1 | vidyut (kosha, sandhi, prakriya, chandas), sanskrit_parser internals | one code point per vowel: `A`=ā, `E`=ai, `O`=au, `f`=ṛ, `x`=ḷ |
| IAST | all emitted text, Dharmamitra request/response | Unicode with combining diacritics — **not** ASCII |

Conversion helpers in `app.py`:

- `devanagari_to_iast()` — vidyut lipi, used for the top-level `input.iast` and
  for building Dharmamitra requests.
- `_slp1_to_iast()` — sanscript SLP1→IAST for sanskrit_parser output; passes
  already-Devanagari strings through untouched (the parser returns mixed scripts).
- `_slp1_to_iast_vidyut()` — vidyut lipi SLP1→IAST for the vidyut engine. It first
  strips Vedic accent markers (`~`, `\`, `'` — udātta/anudātta and svarita are written
  as two-code-point pairs) that vidyut embeds in dhatu upadeśas; lipi would otherwise
  emit stray combining signs (this was a real output bug: `√vad` printed as `vadim॒̐`).
- `_convert_devanagari_to_iast()` — recursive dict/list walker applied to the
  sanskrit_parser and vidyut subtrees in `main()`, catching any Devanagari that
  survived engine-specific formatting.

Where a *display* name is needed for a dhatu, prefer `DhatuEntry.clean_text`
(accent-free dictionary spelling) over `dhatu.aupadeshika`; the latter must keep
its accent marks because `Dhatu.mula()` expects them.

### Akshara counting

Meter analysis needs syllable counts, and character counts are wrong
(conjuncts share a vowel; anusvāra/visarga carry none). SLP1's one-vowel-per-code
point property makes the correct count trivial: `_count_aksharas` counts
characters in `_VOWELS = "aAiIuUfFxXoOeE"`. That feeds `_split_into_padas`, which
cuts a verse line where cumulative aksharas reach half the line, so vidyut's
per-pāda classifier sees true 8+8 (or 11/12-syllable) halves instead of whole
lines. Lines with fewer than three meaningful tokens or ≤ 12 aksharas pass
through unsplit.

## 3. Determinism

The normalized pass is reproducible: the same `output-verbose.json` always yields a
byte-identical `output.json`. The rules that buy that stability:

- **Ranked, never first-seen.** SP split candidates go through `_best_word_split`;
  morphology groups through `_morph_rank` (a complete case reading outranks a
  fragment such as one tagged only `samāsapūrvapadanāmapadam`). Vidyut split chains
  are scored by `_chain_score` = (kosha-attested parts, −len(chain)).
- **Anusvara-normalized keys.** `saṃpṛktau` and `sampṛktau` must collide onto one
  key; `normalize._norm_anusvara` does that, and the *key* is normalized at
  collection time so collisions resolve by rank rather than by dict insertion
  order.
- **Sorted iteration where a library's order is unspecified.** vidyut/sanskrit_parser
  return dicts whose traversal order can shift between runs; `collect_sp_decompositions`
  iterates `sorted(items)` and resolves duplicate keys with an explicit rule
  (`(-len(cand), cand)`).
- **Dedup by full content.** Verb readings in the vidyut kosha section are keyed on
  the whole formatted entry (`json.dumps(..., sort_keys=True)`) because verbs carry
  `dhatu`/`lakara`/`purusha` where nouns carry `pratipadika`/`linga`/`vibhakti`; a
  partial key silently collapsed every verb reading of a root into one.

**What is *not* reproducible.** The raw pass re-hits the network and lets
sanskrit_parser enumerate candidate splits and vakya parses in an order it does not
specify, so two `app.py shloka` runs differ in `engine_outputs.sanskrit_parser`
(observed: 5 different split candidates between runs, and `vakya_parses` appearing or
not depending on which parse crossed the 5 s watchdog). The dharmamitra and vidyut
subtrees were byte-identical across those runs. Because normalize re-ranks rather than
traverses, that noise usually cancels — but when sanskrit_parser proposes a genuinely
different candidate set, `output.json` legitimately changes with it. Pin the raw file
(and hence the normalized one) if you need archival reproducibility.

## 4. Lemma provenance (Dharmamitra tokens)

The Dharmamitra API returns surface forms only — no lemmas. `enrich_dharmamitra_lemmas`
fills them from the **local vidyut kosha**, so lemma claims in `output.json` are
vidyut's, not the API's.

Lookup path: IAST form → Devanagari → SLP1 via *sanscript* (not vidyut lipi:
lipi's Iast→Slp1 yields `arTau`, while the kosha keys inflected forms as `arTO`).
Then, in order: exact key; key minus final visarga; pause-normalized spelling
(a stem ending in `c/j/ś` surfaces as `k/g/ṣ`, so `vAc` misses and `vAk` hits lemma
`vac`); finally a 1–3 character stem strip guarded by a shared three-character
prefix, without which homographic roots win (`kosha.get('arTa')` returns `arTi`).

Ambiguity is reported, not resolved: `"lemma": ["artha", "arthi"]` means several
stems match the surface form. A token with no kosha match keeps only `form`, and a
pada whose Dharmamitra tokens all lack lemmas simply has no lemma keys — that is a
documented miss (e.g. `jagantaḥ`), not an error.

## 5. Dharmamitra API quirks

- **Trailing whitespace truncates the response.** Any input line ending with
  whitespace before a newline silently cuts the response there, so tokens for later
  pādas vanish (symptom: DM tokens cover only the first pāda). `run_dharmamitra`
  strips per-line whitespace before POSTing.
- **The `mode` parameter is ignored** by the endpoint; the request asks for
  unsandhied lemma-morphosyntax output and gets it regardless of what the field says.
- **Untagable words come back as empty underscore fields** (`____iva_`);
  `_parse_tokens` drops only those empty segments, so an untagable word produces no
  token rather than a bogus one.
- Requests retry once (two attempts, 1 s apart) before reporting `unavailable`; the
  timeout path returns immediately with its own error string.
- The bundled `Authorization` header is the public demo credential, overridable via
  `DHARMAMITRA_AUTH`. It is committed on purpose so a fresh clone runs out of the box;
  see §8 before pointing this at anything sensitive.

## 6. Data layout

```
data-0.4.0/            # VIDYUT_DATA_DIR (default ./data-0.4.0)
├── kosha/             # inflected SLP1 keys → PadaEntry objects (~1M stems; never index per run)
├── prakriya/          # derivation data used by vidyut.prakriya.Vyakarana
├── chandas/meters.tsv # meter definitions for Chandas.classify
├── sandhi/rules.csv   # rules for Splitter.from_csv (split_at takes a BYTE offset)
└── cheda/             # present in the archive; this project does not read it
```

`Kosha.get` keys are *inflected* SLP1 forms (`vAk`, `arTO`, `jagataH`), which is why
the pause/stem normalization of §4 exists.

## 7. Development workflow

```bash
uv sync                                   # runtime deps + pytest (dev group)
uv run python app.py shloka -o output-verbose.json   # ~20 s; hits the DM API
uv run python normalize.py -o output.json            # offline, instant
uv run pytest -q                          # offline suite
```

- `tests/conftest.py` pins `VIDYUT_DATA_DIR` to the bundled data and puts the project
  root on `sys.path`. Tests are deterministic and never call the Dharmamitra API;
  kosha-backed tests use a module-scoped fixture over the local data.
- Both passes write to stdout by default (`-o -`). Piping is therefore explicit:
  `uv run python app.py shloka > output-verbose.json`. normalize.py prints its size
  summary to stderr so stdout redirection stays clean.
- On import, sanskrit_parser sets its own logger to DEBUG and attaches a stderr
  handler; suppression must be applied *after* importing it, otherwise every run
  emits ~180 MB of debug noise into whatever stream you redirected.

## 8. Known issues and maintenance notes

Ordered by how likely they are to bite:

1. **Committed demo credential.** `API_HEADERS["Authorization"]` falls back to the
   public Dharmamitra demo account so the tool runs unconfigured. Rotate/override with
   `DHARMAMITRA_AUTH`; do not reuse this repo's header for a private account.
2. **SIGALRM is POSIX-only and native-blind.** The vakya watchdog cannot interrupt C
   extensions (vidyut, sanskrit_parser internals) that never return to the Python
   interpreter, and it does nothing on Windows. Treat 5 s as a guardrail, not a bound.
3. **Unpinned upstreams vs hard-coded tag spellings.** `normalize.py` matches IAST tag
   strings (`prathamāvibhaktiḥ`, `bahuvacanam`, `puṃlliṅgam`) and the residual tag
   `samāsapūrvapadanāmapadam`. `pyproject.toml` pins only `vidyut>=0.4.0`;
   `sanskrit-parser` and `indic-transliteration` float, so a wording change upstream
   silently degrades morphology extraction to empty fields rather than failing loudly.
   Pin them before relying on the normalized output in a pipeline.
4. **`_norm_anusvara` is duplicated** between `app.py` and `normalize.py`. Deliberate:
   normalize.py stays stdlib-only so it can be run against any raw JSON without
   importing the heavy engine stack. Change one, remember the other.
5. **Retry policy is minimal.** One retry for Dharmamitra connection errors; no backoff,
   no caching, no resume. Re-running a shloka costs ~20 s and re-hits the API.
6. **Meter naming depends on splitting quality.** `_split_into_padas` is an akshara-midpoint
   heuristic: it handles anuṣṭubh-style 8+8 lines, but a line whose word boundaries do not
   straddle the midpoint (or a jagatī/triṣṭubh line) can still be cut in the wrong place,
   which changes the classified vṛtta. The classifier itself is vidyut's; only the split is ours.
7. **Recursive sandhi splitting is bounded by heuristics.** `_is_quality_split` demands both
   parts ≥ 4 characters with ≥ 2 kosha entries and at least one standalone (non-`Krdanta`,
   non-`Tinanta`) entry; `recursive_split` caps depth 2 / 4 parts in `run_vidyut`. Loosening
   the thresholds re-admits spurious splits where short strings match verb roots.
8. **Vidyut kosha section cost.** `run_vidyut` performs one FST lookup per candidate part per
   token; that loop dominates runtime of the vidyut engine. `_kosha_entry_info` /
   `_kosha_info_from_entries` exist so a single lookup feeds count, standalone test and lemma —
   do not reintroduce separate lookups per predicate.
