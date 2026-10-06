# Documentation

Deep reference for `samskrta-multi-parser-raw`. The [README](README.md) covers
usage; this file covers *why* the code is shaped the way it is, the library
quirks that forced specific workarounds, and what to watch when maintaining it.

## 1. Architecture

One command-line pass over text in any script vidyut lipi detects (Devanagari, IAST,
SLP1, Harvard-Kyoto, ITRANS) writes a **pair** of documents under one base name; the
reading pass can also be re-run on its own over an existing raw document:

```
any-script text
      │  detect_script() → name; to_devanagari() → Devanagari; preprocess_input() → separators as spaces
      ▼
 app.py ──────────────────────────────► <base>.raw.json             (engine dump)
 │   ├── run_sanskrit_parser()   local  (fatal when unavailable)
 │   ├── run_dharmamitra()       remote HTTP, + kosha lemma enrichment (tolerated)
 │   └── run_vidyut()            local  (fatal when unavailable; emits chandas)
      │  postprocess_analysis.postprocess() — called in-process by main()
      ▼
 <base>.result.json                                               (reading document)

 postprocess_analysis.py -o BASE   → rewrites the reading pass alone, offline
```

`app.py` imports `postprocess_analysis` and calls its `postprocess()`, so a single run
produces both documents. That module is stdlib-only, imports nothing back, and stays
available as a standalone command for re-processing an existing raw file. The reading
pass compares the two splitting engines (`sanskrit_parser`, `dharmamitra`) word by word
and copies vidyut's verse-level `chandas` summary across; everything else vidyut
produces stays in the raw document. The split exists because the raw dumps are ~90 KB of
engine-specific noise while the reading document is ~7 KB; keeping both makes parser
disagreements auditable.

### Error isolation

Every engine call is wrapped so a failure becomes data, never a crash:

| Failure | Shape in raw output |
|---|---|
| Engine import missing | `{"error": "sanskrit_parser unavailable: ..."}` |
| Dharmamitra unreachable / timeout / bad body | `{"error": "Dharmamitra API request timed out"}`, `{"error": "Dharmamitra API unavailable: ..."}`, `{"error": "Dharmamitra API returned non-JSON body: ..."}` |
| Unexpected response shape | `{"error": "Dharmamitra API response shape unexpected: ..."}` |
| Vidyut data missing | `{"error": "Vidyut data directory not found"}` |
| Vidyut library init failure | `{"error": "Vidyut initialization failed: <exc>"}` |

Three consequences worth knowing:

- **Exit codes, not just error objects.** A Dharmamitra failure is tolerated — one
  `Warning:` line on stderr and exit code 0, because the remote API is outside our
  control and only its section degrades. When a *local* engine cannot run at all the
  run fails: `_run_local_engine` normalizes both shapes an engine can fail in (a raised
  exception, and vidyut's habit of *returning* `{"error": "Vidyut data directory not found"}`)
  into one error object, prints `Error: <engine>: …`, and `main()` exits 1 after writing
  both documents. A run that silently loses an engine is worse than a failing one.
- **`engine_errors` in `<base>.result.json`.** `postprocess()` scans all three
  engine dicts for an `error` key and emits `"engine_errors": {engine: message}` only
  when at least one engine failed. Without it, a crashed engine is indistinguishable
  from a text that genuinely produced no analysis.
- **The vakya watchdog.** sanskrit_parser's vakya (dependency) parsing can spin on
  long pādas, so each parse runs under `signal.alarm(VAKYA_TIMEOUT_SECS)` (5 s). A
  timeout records `"vakya_error"` on that split instead of a graph. The alarm is
  best-effort: it cannot interrupt native parser code (see §8).

Library chatter that would otherwise pollute the pipeline is reported through
`_warn_once(site, exc)`: one line per distinct site to **stderr**, so the `input` +
`chandas` summary on stdout stays parseable while swallowed library errors remain
visible. This replaced several bare `except Exception: pass` blocks in the vidyut
prakriya and sandhi paths.

## 2. Transliteration pipeline

Three scripts are in play; every conversion is explicit about which one it uses.
Input may arrive in any of them — `detect_script()` asks vidyut lipi which, and the
answer is recorded verbatim as `input.script` (`"Devanagari"`, `"Iast"`, `"Slp1"`, …).

| Script | Used by | Notes |
|---|---|---|
| Devanagari | canonical internal form, `input.devanagari` | every script is transliterated to it before analysis; the only place Devanagari survives into any output |
| SLP1 | vidyut (kosha, sandhi, prakriya, chandas), sanskrit_parser internals | one code point per vowel: `A`=ā, `E`=ai, `O`=au, `f`=ṛ, `x`=ḷ |
| IAST | all emitted text, Dharmamitra request/response | Unicode with combining diacritics — **not** ASCII |

Input normalization happens once, in this order:

- `detect_script(text)` — lipi's detector; returns the scheme name as a string. Note
  that `detect(iast_text) is Scheme.Iast` is False (the function hands back a member
  whose `.name` you compare), and an ASCII-only string such as `"agnim ile"` detects
  as `"Devanagari"`, which is harmless because transliterating it to Devanagari is a
  no-op round trip.
- `to_devanagari(text)` — Devanagari input passes through byte-identically; anything
  else goes through `lipi.transliterate(text, scheme, Scheme.Devanagari)`. lipi does
  **not** transliterate literal separators: a `|` typed inside IAST text stays a `|`.
- `preprocess_input(devanagari)` — every character of `string.punctuation` plus the
  dandas `।॥` becomes a space, then whitespace runs are collapsed per line. This is
  what makes `vāc-artha`, `vāc artha.` and `vāc  artha` analyze identically; it also
  means an avagraha written as `'` simply disappears. Newlines survive so verse
  structure is kept, and only the padding around a line is trimmed. Running it twice
  changes nothing (idempotent).

What gets *recorded* differs from what gets *analyzed*: `input.devanagari` holds the
canonicalized user text with its separators verbatim, while `input.iast` and every
engine receive the cleaned text. A comma- and hyphen-riddled IAST input therefore shows
its punctuation back in `input.devanagari` yet produces byte-identical dharmamitra and
vidyut subtrees (verified on Raghuvaṃśa 1.2).

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
characters in `_VOWELS = "aAiIuUfFxXoOeE"`. That feeds `_split_into_padas`, which cuts
a verse line where cumulative aksharas reach half the line, so vidyut's per-pāda
classifier sees true 8+8 (or 11/12-syllable) halves instead of whole lines. The cut is
made at an exact akshara position even if that lands inside a written token — sandhi
joins words across pāda boundaries, and the halves feed only the classifier, never the
word list. Lines whose total aksharas are odd or ≤ 12, and lines with fewer than three
tokens, pass through unsplit rather than being cut at a false boundary.

## 3. Determinism

The reading pass is reproducible: the same `<base>.raw.json` always yields a
byte-identical `<base>.result.json`. The rules that buy that stability:

- **Ranked, never first-seen.** SP split candidates go through `_best_word_split`;
  morphology groups through `_morph_rank` (a complete case reading outranks a
  fragment such as one tagged only `samāsapūrvapadanāmapadam`). Vidyut split chains
  are scored by `_chain_score` = (kosha-attested parts, −len(chain)).
- **Anusvara-normalized keys.** `saṃpṛktau` and `sampṛktau` must collide onto one
  key; `postprocess_analysis._norm_anusvara` does that, and the *key* is normalized at
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
subtrees were byte-identical across those runs. Because postprocess re-ranks rather than
traverses, that noise usually cancels — but when sanskrit_parser proposes a genuinely
different candidate set, `<base>.result.json` legitimately changes with it. Pin the raw
document (and hence the reading one) if you need archival reproducibility.

## 4. Lemma provenance (Dharmamitra tokens)

The Dharmamitra API returns surface forms only — no lemmas. `enrich_dharmamitra_lemmas`
fills them from the **local vidyut kosha**, so lemma claims in `<base>.result.json`
are vidyut's, not the API's.

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

`chandas/meters.tsv` holds 145 vṛttas whose third column is a `/`-separated list of pāda
patterns; `Chandas.classify(slp1)` exposes only `.padya` (the matched name or `None`) and
`.aksharas`, while `vrttas`/`jatis` are attributes, not methods. The catalogue has **no
anuṣṭubh/śloka entry**, so a classical śloka's pādas match lookalikes instead: Raghuvaṃśa
1.1 yields `madalekhā` and `śuddhavirāṭ` for two pādas and nothing for the other two.
`_summarize_chandas` reports that honestly — `vrtta: null`, both names in `candidates`, the
real shape in `aksharas_per_pada: [8, 8, 8, 8]`. Do not "fix" the null by promoting a
candidate; vidyut's data simply does not know the śloka vṛtta.

## 7. Development workflow

```bash
uv sync                                   # runtime deps + pytest (dev group)
uv run python app.py shloka             # ~20 s; hits the DM API → results/shloka_input.{raw,result}.json
uv run python postprocess_analysis.py -o results/shloka_input   # offline, instant
uv run pytest -q                        # offline suite
```

- `tests/conftest.py` pins `VIDYUT_DATA_DIR` to the bundled data and puts the project
  root on `sys.path`. Tests are deterministic and never call the Dharmamitra API;
  kosha-backed tests use a module-scoped fixture over the local data.
- Output naming: `-o BASE` writes `BASE.raw.json` and `BASE.result.json`; with no `-o`
  the base is `results/<input stem>` (`results/shloka` / `results/pada` for stdin
  input). Parent directories are created on demand, and a trailing `.json`, `.raw` or
  `.result` on the base is stripped — feeding a generated file back into `-o`/`-i`
  reproduces its sibling pair.
- stdout carries only the reading document's top-level `input` and `chandas` objects,
  re-read from the file that was just written; warnings, errors and the postprocessor's
  size summary all go to stderr. Piping an analysis run therefore yields a small JSON
  object, never the full dump.
- Exit codes: `app.py` exits 1 when a local engine (sanskrit_parser, vidyut) could not
  run at all — both documents, error objects included, are written first; an offline
  Dharmamitra API only produces a warning and exit 0.
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
3. **Unpinned upstreams vs hard-coded tag spellings.** `postprocess_analysis.py` matches IAST tag
   strings (`prathamāvibhaktiḥ`, `bahuvacanam`, `puṃlliṅgam`) and the residual tag
   `samāsapūrvapadanāmapadam`. `pyproject.toml` pins only `vidyut>=0.4.0`;
   `sanskrit-parser` and `indic-transliteration` float, so a wording change upstream
   silently degrades morphology extraction to empty fields rather than failing loudly.
   Pin them before relying on the processed output in a pipeline.
4. **`_norm_anusvara` is duplicated** between `app.py` and `postprocess_analysis.py`. Deliberate:
   the postprocessor stays stdlib-only so it can be run against any raw JSON without
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
