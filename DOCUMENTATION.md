# Documentation

Deep reference for `samskrta-multiparser`. The [README](README.md) covers
usage; this file covers *why* the code is shaped the way it is, the library
quirks that forced specific workarounds, and what to watch when maintaining it.

## 1. Architecture

One command-line pass over text in any script vidyut lipi detects (Devanagari, IAST,
SLP1, Harvard-Kyoto, ITRANS) writes a **pair** of documents under one base name; the
reading pass can also be re-run on its own over an existing raw document:

```
any-script text
      │  detect_script() → name; to_devanagari() → Devanagari; preprocess_input() → separators and digits as spaces
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
| Dharmamitra unreachable / timeout / bad body | `{"error": "Dharmamitra API request timed out after <N>s"}`, `{"error": "Dharmamitra API unavailable: ..."}`, `{"error": "Dharmamitra API returned non-JSON body: ..."}` |
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
- **One bounded remote deadline.** Both Dharmamitra attempts share a single monotonic budget
  (`DHARMAMITRA_TIMEOUT_SECS`, default 20 s): the second attempt gets only the time the first one
  left, and there is no sleep between them. The request also runs on a background thread, so its
  network wait overlaps the local engines instead of stacking onto them (see below).

**Engine scheduling.** `main()` submits the Dharmamitra request to a one-worker thread pool first,
then runs sanskrit_parser and vidyut on the main thread, then joins the remote result. Inside
`run_sanskrit_parser`, an input of `SAMSKRTA_MIN_PARALLEL_LINES` pada-lines or more runs its
per-line analysis in a `concurrent.futures.ProcessPoolExecutor` (default 2 workers;
`SAMSKRTA_WORKERS`), created with the **spawn** start method because forking here would copy a
process that already has the Dharmamitra thread alive. Threads are no help for this work: vidyut's
pyo3 bindings never release the GIL and sanskrit_parser holds it throughout, so only processes run
it faster. The vidyut kosha is built once per process by `load_kosha()` behind a lock — three call
sites share it (dictionary lookups, Dharmamitra lemma enrichment, every pool worker).

Library chatter that would otherwise pollute the pipeline is reported through
`_warn_once(site, exc)`: one line per distinct site to **stderr**, so the `input` +
`chandas` summary on stdout stays parseable while swallowed library errors remain
visible. This replaced several bare `except Exception: pass` blocks in the vidyut
prakriya and sandhi paths.

## 2. Transliteration pipeline

Three scripts are in play; every conversion is explicit about which one it uses.
Input may arrive in any of them — `detect_script()` asks vidyut lipi which, but the answer only drives the
conversion: the documents record `input.devanagari` and `input.iast`, never the script name.

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

- **Ranked, never first-seen.** SP split candidates go through `_best_word_split`, which ranks all ten with the vidyut kosha in hand: exact dictionary attestation of every part first (this is what keeps `mokṣayiṣyāmi` one word and rejects fragments such as `ava | tu`), then fewest parts, then standalone morphology for every part, then rarity of the scarcest part, then longest shortest part, then the sorted list. Without a kosha it falls back to the older morphology-first keys. Morphology groups go through `_morph_rank` (a complete case reading outranks a fragment such as one tagged only `samāsapūrvapadanāmapadam`). Vidyut split chains are scored by `_chain_score` = (kosha-attested parts, −len(chain)).
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
- **Parallel lines, ordered merge.** Long inputs analyze their pada-lines in a process pool;
  `pool.map` hands results back in input order and the engine concatenates them line by line, so
  the pooled document equals the single-process one (verified on nine verses: identical reading
  documents, identical per-word decompositions).

**What is *not* reproducible.** The raw pass re-hits the network and lets
sanskrit_parser enumerate candidate splits in an order it does not specify, so two `app.py shloka`
runs differ in `engine_outputs.sanskrit_parser` (observed: 5 different split candidates between
runs). The dharmamitra and vidyut
subtrees were byte-identical across those runs. Because postprocess re-ranks rather than
traverses, that noise usually cancels — but when sanskrit_parser proposes a genuinely
different candidate set, `<base>.result.json` legitimately changes with it. Pin the raw
document (and hence the reading one) if you need archival reproducibility.

The pinned pairs in `tests/data/results/` (§7) exist because of this. Re-running Bhagavad Gītā 18.66 used to reproduce the dharmamitra and vidyut subtrees byte-for-byte while sanskrit_parser's tied per-word ranking put `mokṣe | iṣi | āmi` where the stored document had `mokṣe | iṣyā | āmi`. Dictionary-validated ranking removed that particular tie — every candidate that cut the finite verb now loses to the whole attested word, so the splitter column is stable in practice. The live golden test still compares only the Dharmamitra and vidyut subtrees plus the reading document's Dharmamitra column, pada sequence and metre summary: the *candidate set* `parser.split()` enumerates for a line is not specified to be stable, so pinning the splitter column would test sanskrit_parser's internals rather than this tool.

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
- **The response carries no tags.** The request asks for
  `mode="unsandhied-lemma-morphosyntax"`, but `results[0]` arrives as an underscore-separated list of
  unsandhied **surface forms** (`kva_sūrya_prabhavaḥ_vaṃśaḥ_…`). No lemma or morphosyntax tags come back,
  which is exactly why §4 fills lemmas from the local vidyut kosha.
- **Untagable words come back as empty underscore fields** (`____iva_`);
  `_parse_tokens` drops only those empty segments, so an untagable word produces no
  token rather than a bogus one.
- Requests make two attempts that share one `DHARMAMITRA_TIMEOUT_SECS` budget (default 20 s) before
  reporting `unavailable`; the second attempt receives only the remaining time.
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
uv run python app.py shloka             # ~2-3 s for one śloka; hits the DM API → results/shloka_input.{raw,result}.json
uv run python postprocess_analysis.py -o results/shloka_input   # offline, instant
uv run pytest -q                        # offline suite
```

- `tests/conftest.py` pins `VIDYUT_DATA_DIR` to the bundled data and puts the project
  root on `sys.path`. Tests are deterministic and never call the Dharmamitra API;
  kosha-backed tests use a module-scoped fixture over the local data.
- Each test verse has a pinned pair in `tests/data/results/` (`<stem>.raw.json`,
  `<stem>.result.json`, pretty-printed so diffs stay reviewable). Regenerate one after an
  intentional change with
  `uv run python app.py shloka -f pretty -i tests/data/<stem>.txt -o tests/data/results/<stem>`.
  The offline half of `tests/test_golden_outputs.py` then proves `postprocess()` still reproduces
  the reading document exactly; the live half re-runs all three engines, needs network access, and
  is skipped unless `SAMSKRTA_LIVE_GOLDEN=1`.
- `tests/test_sandhi_accuracy.py` scores splitting against the curated padaccheda of §9. Eight
  curated padas run offline (kosha + sanskrit_parser, ~3 s); the corpus-wide score needs both engines
  and runs under the same `SAMSKRTA_LIVE_GOLDEN=1` switch (~10 s). It is the test to consult before
  touching `_best_word_split`, `_is_standalone_word` or `load_kosha`.
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
2. **The pool trades memory and startup for wall time.** Each worker builds its own `Parser`
   (~130 MB) and kosha FST (~220 MB), so the default of two workers adds ~700 MB resident on long
   inputs — set `SAMSKRTA_WORKERS=1` instead of swapping. Spawn also costs about a second per
   worker, which is why `_sp_worker_count` refuses to pool short inputs at all.
3. **Unpinned upstreams vs hard-coded tag spellings.** `postprocess_analysis.py` matches IAST tag
   strings (`prathamāvibhaktiḥ`, `bahuvacanam`, `puṃlliṅgam`) and the residual tag
   `samāsapūrvapadanāmapadam`. `pyproject.toml` pins only `vidyut>=0.4.0`;
   `sanskrit-parser` and `indic-transliteration` float, so a wording change upstream
   silently degrades morphology extraction to empty fields rather than failing loudly.
   Pin them before relying on the processed output in a pipeline.
4. **`_norm_anusvara` is duplicated** between `app.py` and `postprocess_analysis.py`. Deliberate:
   the postprocessor stays stdlib-only so it can be run against any raw JSON without
   importing the heavy engine stack. Change one, remember the other.
5. **Retry policy is minimal.** Two Dharmamitra attempts share one `DHARMAMITRA_TIMEOUT_SECS`
   budget; no backoff, no caching, no resume. Re-running a śloka costs ~2-3 s and re-hits the API.
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

9. **`parser.split()` returns `None`, not an empty list,** when it finds no split for a word at all.
   `_best_word_split` guards this with `or []`; without the guard the raw pass died with
   `TypeError: 'NoneType' object is not iterable` (observed on a Raghuvaṃśa 1.4 token whose
   avagraha had been replaced by a space).
10. **Avagraha (`ऽ`) is deliberately left inside the token.** Three treatments were tried on
    `वंशेऽस्मिन्पूर्वसूरिभिः`: leave it (sanskrit_parser returns `vaṃśe | asmin | pūrvasūribhis`),
    delete it (strands `स्मिन्`, and `split()` then returns `None`), or restore the elided `अ`
    (words fine, but vidyut counts the extra syllable and 1.4 reported aksharas `[17, 8, 8]`
    instead of `[8, 8, 8, 8]`). Only the first keeps both splitting and metre correct, so
    `_SEPARATORS` does not touch `ऽ`.
11. **Chosen-word morphology must be recorded per word, not harvested from line splits.**
    `parser.split(line, limit=5)` samples whole-line candidates and their order/member set change
    between processes. Reading-document word analyses are keyed by the forms the per-word ranking chose
    in `word_decompositions`, which those sampled candidates need not contain — so before
    `word_morphology` existed, 17 of 116 chosen words (`pāpebhyas`, `āsam`, `udra`, …) came back as a
    bare `"form"` with no root/case/number, and *which* words lost them changed on every regeneration.
    `_best_word_split_with_items` returns the winning split's word objects precisely so
    `run_sanskrit_parser` can tag them; `tests/test_golden_outputs.py::test_every_chosen_word_carries_an_analysis`
    fails if that link is broken again.

12. **vidyut's metre layer is coarser than it looks, in three ways that matter here.** (a) Its
    scanner does not treat SLP1 `R`/`RR` (ṛ ṝ) as vowels — `AC = "aAiIuUfFxXeEoO"` in
    `vidyut-chandas/src/sounds.rs:4`, and `vidyut-sandhi/src/sounds.rs:24` has the same set — so
    `saṃpṛktau` scans as four aksharas (`saM pa Rkta u`) instead of two, and a pāda containing ṛ gets
    both its weight string and its length wrong. Our `_count_aksharas` uses the same set on purpose:
    `akshara_count` is defined as "what vidyut classified", so the two stay consistent; changing one
    without the other would make `chandas` contradict itself. (b) Guru-ness ignores position: every
    anusvara/visarga-final syllable is heavy and nothing else in a cluster counts, which is why
    raghuvaṃśa 1.1's first pāda scans `GGGLLGGG` where the traditional reading is `– – – u u – – –`.
    (c) The bundled table carries yatis (`|`) that `VrttaPada::try_match` parses and then never uses,
    has no anuṣṭubh/triṣṭubh row and no jāti rows at all. Treat `weight_pattern` as vidyut's scan of
    the text, not as a scansion of the verse.

## 9. Splitting quality: how it is measured

Per-word sandhi splitting has no oracle inside the tool, so one was built outside it.

- **Reference.** `tests/data/sandhi_truth.json`: 64 rows of `{stem, pada, devanagari, parts}`, one
  per pada of the nine pinned verses, split as a reader splits it. Provenance: Dharmamitra's
  boundaries first (it has sentence context), then hand correction against the published padaccheda
  tables for Raghuvaṃśa 1.1–1.7 — its output contains base stems (`vāc` where the pada reads वाक्),
  its own misreadings (`jagantaḥ` for जगतः) and invented tokens (`upahāsya | tām`), so it is a start
  point, never the answer key.
- **Metric.** A pada matches when the part count agrees *and* the parts pair one-to-one such that
  each pair shares at least one kosha lemma stem (SLP1). Spellings are not compared: sandhi changes
  them (`vāc`/`vāk`) and sanskrit_parser writes word-final visarga as `s`, anusvara as `m`. Because
  the engines disagree with the reference about exactly that ending, `lemma_stems()` looks the form
  up twice — once as written, once folded (`M→m`, `H→s`, trailing `s`/`r` dropped) — so
  `sarva | pāpebhyas` scores against `sarva | pāpebhyaḥ` instead of being called a wrong split. Plain
  stem-set equality was rejected as too permissive (an unattested fragment falls back to itself, so
  garbage sets collide), and folding surface forms onto stems before comparison as too brittle.
- **Measured, on that fixture.** vidyut's `recursive_split` chains contained a reference-consistent
  reading for 33 of the 63 padas they covered; sanskrit_parser's ten-candidate pool contains one for
  **56/64** — the ceiling any ranking can reach. Ranking by morphology alone reaches **36/64**;
  dictionary-validated ranking (`_best_word_split` with `load_kosha()`) reaches **44/64**, of which
  the transparent-compound gate (`_MAX_DEEP_PARTS`, `_MIN_DEEP_PART_LEN`) contributes three. The gated
  test floors the score at 42 so that tie-breaking drift between processes cannot fail it while any
  real regression does.
- **Rankings that were tried and lost.** Reconstructability under `Sandhi.join` as a primary key:
  39/64. Summed kosha frequency of the parts: 10-22/64. "Prefer more parts whenever all are
  attested": 8/64 — it cuts `jagatas` into `ja | ga | tas`. A kosha-pruned recursive segmentation
  generator built on sanskrit_parser's own split rules lifts the pool from 56 to 58 but changes no
  pick, and costs 12 s per corpus pass; it is not shipped. The gate above is the only deeper-splitting
  rule that paid: +3 padas over all nine verses for exactly five changed words, all compounds read as
  their members.
- **Metre was tried as a splitter input and lost.** Three keys were measured over this fixture.
  (a) *Pāda-edge alignment*: a line of `2n` aksharas ends a pāda at `n`, so a split that breaks a word
  there is metrically coherent. As an extra key it changed exactly one pick out of 64 and scored
  **44/64 → 44/64**; as the first key it also scored 44 but only by moving `so'hamājanma…` from one
  wrong reading to another. The reason is coverage: of the 64 curated padas, exactly two span more
  than one pāda (`so'hamājanmaśuddhānāmāphalodayakarmaṇām`, `āsamudrakṣitīśānāmānākarathavartmanām`),
  so the constraint is satisfied by 63/64 reference splits and discriminates almost nothing. Worse, it
  is not even true of the reference: the first of those two padas has its word boundaries at aksharas
  1, 3, 6, 9, 12, 15 — none at the pāda edge 8 (the anuṣṭubh pathya caesura falls after 4 or 6/7 of a
  *half*, not at the midpoint our `_split_into_padas` assumes).
  (b) *Guru-laghu pattern fit*: score each candidate by how few positions its weight string contradicts
  an anuṣṭubh `LLLLGGLL?`. That scored **44/64 → 39/64**, five breaks and no fixes. Word cuts do change
  vidyut's weights — it marks every anusvara/visarga-final syllable heavy unconditionally, so `māmekaṃ`
  reads `GGG` fused and `GGL` split (`vidyut-chandas/src/akshara.rs:105-118`) — but that is a modelling
  artefact of the scanner, not metre, and ranking on it prefers junk such as `gṛhamedhinā | ām`. Note
  also that vidyut's own table cannot supply either pattern: `data-0.4.0/chandas/meters.tsv` has 145 rows, all
  `vrtta`, with **no anuṣṭubh/triṣṭubh entry and no jāti rows at all** (so āryā is unclassifiable too).
  The table does mark caesurae with `|` — parsed into `VrttaPada::yati` and then never consulted by
  `try_match` — which is why every plain śloka in the corpus reports `vrtta: null`. Inside one pāda a
  weight pattern cannot rank word order at all, since weights are scanned straight across word
  boundaries. (c) *Caesura alignment* — the one metrical rule that does constrain word order inside a
  pāda, preferring a word break after akshara 4 or 6 of an eight-syllable pāda — was measured on the 46
  raghuvaṃśa rows alone: **32/46 → 23/46**. Nor can it be kept as a final tie-breaker: keys 1-6 of
  `_rank_with_kosha` leave a tie on just two padas of the whole fixture, and no pāda edge separates
  either. Metre therefore stays a reported property (`chandas`, per-pāda `weight_pattern`) and never an
  input to `_best_word_split`; `vidyut-sandhi` contains no reference to chandas either, so there is no
  upstream metre-aware splitter to borrow.
- **What the misses are.** Almost all remaining ones need context: samāsa resolution
  (`yathākālaprabodhinām`, `prāṃśulabhye` stay whole), case government across a pada, and a handful
  where sandhi joined two words so that the join is indistinguishable from an inflection
  (`māme | akam`). No per-word method fixes these; they are why Dharmamitra's column stays in
  `<base>.result.json` next to the offline one instead of being scored away. Re-ranking toward
  Dharmamitra was measured too: its reading is present in sanskrit_parser's candidate pool for only
  11 of the 60 padas it answers, and it agrees with the curated reference on just **22/64** itself,
  so "split like Dharmamitra" would cost accuracy rather than buy it.
- **Re-measuring.** `SAMSKRTA_LIVE_GOLDEN=1 uv run pytest -q tests/test_sandhi_accuracy.py` prints
  every missed pada by name on failure. Rule changes can be re-scored in seconds by generating each
  pada's candidate pool once and re-running the ranking over it; the pool, not the ranking, is the
  expensive part. When a verse is added to the corpus, regenerate its golden pair with the command in
  §7 rather than editing JSON by hand.
