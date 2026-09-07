# Value precision: targeting valuable data

Status: phases 0 (lean) and 1 landed; phases 2-4 proposed.
Detector: `document-values-detector-3` current.

**Naming.** This document was written before the P1 contract split in
`docs/internal/architecture.md` §7, which renamed everything off
"financial-value". Current-tense references below have been updated to the names
that exist today — `document-values-v1`, `engines/values/`, `detect_values`,
`score_values.py`, `render_values_overlay.py`, `ValuesOverlay`,
`tests/fixtures/values/`. Detector version strings naming a *measured historical
run* are left as written, because they are facts about a run that happened:
`financial-values-detector-N` is the same lineage as today's
`document-values-detector-N`, renamed and renumbered at the split.

## 1. The problem, measured

Baseline on `sample-document-corpus/financial-statements/apple 10k.pdf` (80 pages, native text layer,
`detect_values` over `extract_text_geometry`):

| Metric | Value |
|--------|-------|
| Values published | 3,078 |
| Per page | 38.5 |
| Kind split | 1,829 number · 979 date · 256 percent |
| Sitting in flowing prose (not an isolated island) | 1,779 (58%) |
| Bare 4-digit years published as dates | 684 (22%) |
| Bare integers, no separator/decimal/currency/percent | 866 (28%) |

Every published value is a click target in `ValuesOverlay`, so a false
positive is not a cosmetic problem — it is a transparent button sitting over
the user's text, competing with rectangle drawing and text selection.

### What the noise actually is

Sampled directly from the baseline run:

| Source text | Published as | Why it is noise |
|---|---|---|
| `Washington, D.C. 20549` | `20549` | ZIP code |
| `FORM 10-K` | `10` | Hyphen-joined identifier |
| `Commission File Number: 001-36743` | `001`, `36743` | Filing identifier, split in two |
| `94-2404110` | `94`, `2404110` | EIN, split in two |
| `(408) 996-1010` | `(408)` → **−408**, `996`, `1010` | Phone number; the area code is normalized as a negative |
| `SECTION 13 OR 15(d) ... ACT OF 1934` | `13`, `15`, `1934` | Statutory citation |
| `3:13 PM` | `3`, `13` | Clock time |
| `Apple Inc. \| 2025 Form 10-K \| 7` | `2025`, `7` | Running page footer, repeated on 80 pages |
| `Net 30` | `30` | Payment term |
| `(1)` `(2)` `(3)` at 60% of line glyph height | `−1`, `−2`, `−3` | Superscript footnote markers, normalized as negatives |
| `INVOICE # BPXINV-00550` | `00550` | Document number |

Two of these are worse than clutter: parenthesised area codes and footnote
markers are published with a *negative* `normalizedValue`, so linking one puts
a wrong number in a cell.

### The root causes

1. **Token boundaries are too loose.** `_NUMBER_RE` guards with `(?<![\w\d])`
   and `(?![\w\d])`. `-`, `/` and `:` are not `\w`, so any hyphen-joined or
   colon-joined identifier is shredded into its numeric pieces.
2. **Recognition is the only test.** `recognize_spans` decides shape; nothing
   downstream asks whether a well-shaped number is *worth* publishing.
   `_is_isolated_fragment` already computes the strongest cheap structural
   signal in the file, but it is used only to align wrapped dates.
3. **The bare-year date pattern fires unconditionally.** `(?:FY\s*)?(19|20)\d{2}`
   matches every year in every sentence, including statute years.
4. **Document-level structure is never consulted.** Running headers and footers,
   repeated on every page, are re-detected as fresh values 87 times.
5. **Table structure is computed and discarded.** `worker.py` runs
   `detect_tables` at line ~445 and `detect_values` at ~462. The table model —
   column bands, body vs. header rows, header labels, per-column periods — is
   already in scope and costs 5.5s for this document. `detect_values` does
   not receive it.

## 2. Decisions taken

- **Prose values count.** A number is valuable wherever it appears, provided it
  is a real quantity. `$3,253,431,000,000` of market value, `166,000` employees
  and `40% and 60%` of channels are all in flowing prose and all wanted.
  Filtering targets identifiers and structural artifacts, never location.
- **Suppressed, classified, and kept.** *(Revised after phase 1 shipped; the
  original decision was to drop suppressed spans entirely.)* A suppressed span is
  never a value and never a click target, but it is not thrown away either: each
  page carries a `noise` array holding the refused spans and the rule that
  refused each one. Structurally separate from `values`, so no consumer can
  mistake one for the other, and lean — id, kind, text, bounds, reason, nothing
  else. There is still no viewer-side confidence threshold; the detector alone
  decides what is a value.

  The cost is artifact size: +10% on the 10-K, +53% on a two-page invoice where
  a third of everything recognized is a product code. The benefit is that the
  classification is inspectable in the product rather than only in a script, and
  a tuning question can be answered from a real workbook instead of a re-run.
- **Value detection consumes table structure.** `detect_values` takes the
  `table-structure-v1` model as an optional second input.

## 3. Design

Three stages, each owning one decision, mirroring the shape `engines/table/`
already uses.

```
spans.py       what does this text look like?      (recognition + token integrity)
profile.py     what is this document made of?      (document-level pre-pass)
evidence.py    is this span worth publishing?      (suppress, then support)
detector.py    orchestration and the model envelope
```

### Stage 1 — Token integrity (`spans.py`)

Tighten the number boundary so a candidate is rejected when it is a fragment of
a larger joined token: the character immediately before is `-`, `/` or `:` with
an alphanumeric immediately before *that*, or the character immediately after is
`-`, `/` or `:` with an alphanumeric immediately after *that*. Adjacency is
required — `par value: 50,400,000` keeps its number because of the space.

Measured on the baseline: **245 kills (8%), zero of which carry a currency
symbol, comma group or percent sign.** This is the highest-yield, lowest-risk
change in the plan and it removes the two actively-wrong negatives above.

Date patterns already claim their spans first, so `2025-09-27` and `9/27/25`
are unaffected. Year *ranges* (`2024-2025`) are collateral and are covered by
the period-context rule in stage 3.

### Stage 2 — Document profile (`profile.py`, new)

A single pass over the geometry before detection, producing per-document facts
that no single line can know:

- **Running header/footer bands.** Group lines by (digit-normalized text, y
  rounded to 0.1) and mark any group appearing on ≥25% of pages, minimum 3.
  Digit normalization is what makes `Apple Inc. | 2025 Form 10-K | 7` collapse
  across pages despite the varying page number. Measured: **87 kills.**
- **Page-median glyph height**, for superscript detection. Footnote markers
  measure 0.0066–0.0074 against a page median of 0.0114 — a clean 0.6–0.65 ratio.
- **Page section hints** (deferred to phase 4): exhibit index, signature page,
  cover page.

### Stage 3 — Evidence (`evidence.py`, new)

A candidate publishes iff **no suppressor fires** and **at least one support
holds**.

**Suppressors** — any one kills:

| ID | Rule | 10-K kills |
|----|------|---|
| S1 | Fragment of a joined token (stage 1) | 255 |
| S2 | On a running header/footer line | 54 |
| S3 | Line carries a phone number in an unambiguous shape (`+CC …`, `(NNN) NNN-NNNN`) | 1 |
| S4 | An identifier cue precedes the span: phone, tel, fax, ZIP, postal, suite, P.O., ISIN, CUSIP, SEDOL, ticker, symbol, employer identification, file number, commission file, registration no. | 0 |
| S5 | Glyph height < 0.72 × page median (superscript / footnote marker) | 10 |
| S6 | Bare year in citation context: act, section, rule, item, part, exhibit, form, schedule, chapter, article, paragraph, regulation, pursuant, subtopic, topic, CIK within 25 chars before | 25 |
| S7 | Sits in an identifier column (table-aware — see stage 4) | phase 3 |

S3 was folded into S4 in the original draft. It earns its own rule because a phone
number often carries no cue word at all — `Cupertino, California 95014 (408) 996-1010` —
and the parenthesised area code is the case that publishes a wrong *negative*.
S4 scores zero on the 10-K only because S1 reaches those spans first; it costs
nothing and it is the rule that catches an unhyphenated identifier.

**Supports** — any one publishes:

| ID | Rule |
|----|------|
| E1 | Intrinsic financial marks: currency symbol or code, `%`, magnitude token, comma grouping, or a decimal fraction |
| E2 | Lands in a table body cell (table-aware) |
| E3 | Isolated island — the existing `_is_isolated_fragment`, promoted from wrapped-date helper to a first-class signal |
| E4 | Period or financial language immediately before: due, ended, ending, as of, fiscal, through, maturing, maturity, year(s), quarter, period, expiring, beginning, thereafter |
| E5 | A date with precision better than `year` |

A bare integer in flowing prose satisfies none of these and is dropped:
**337 kills.**

A note on what evidence *deliberately does not* include: an earlier draft
proposed treating 3-or-more decimal places as suspicious. The corpus refutes it —
144 such values, and they are coupon rates (`1.375%`, `3.050%`) and par value
(`$0.00001`). The rule was dropped before it was written.

### Stage 4 — Table structure

Proposed, not built: today's detector is
`detect_values(geometry, *, claims=(), diagnostics=None)` and takes no table
model. The proposal is that `worker.py` pass the already-computed
`table_structure` dict in alongside those. The detector depends on the
**contract shape** (`table-structure-v1`), not on `engines.table` — no import
across engines, consistent with contracts being the single source of truth.
`table_structure` stays optional and the detector degrades to stages 1–3 when
table detection failed or was skipped; it is an allowed-to-fail stage in the
worker and value detection must not become the thing that breaks when it does.

What it buys:

- **E2 — table cell membership.** 56% of baseline detections land inside a
  detected table. This rescues the values a prose-hostile rule would wrongly
  kill: bare integers in a quantity column, bare years in a column header, a
  maturity year in a debt schedule.
- **S6 — identifier columns.** The invoice corpus exposes the case E3 cannot
  reach: `31417, 31418, 31419…` is a column of isolated bare integers that pass
  the isolation test but are document numbers. A column whose cells are all
  separator-free integers, largely monotonic, carrying no currency anywhere in
  the column, and whose header label matches ID vocabulary (no., num, ref, id,
  code, item, account, invoice) is an identifier column and is suppressed
  wholesale.
- **Column-inherited currency and magnitude.** A column header reading
  `$ in millions` should stamp every cell below it, replacing today's
  page-wide `context_for_text` guess. This also directly serves idea #3.

Because a table column carries a `period` and the header carries labels, this
same wiring is the bridge to the Financial Document Analysis: attributing a
number to a row label and a period column is the next step on top of it.

### Confidence recalibration

The current ladder is lexical: 0.99 percent/currency, 0.94 comma, 0.82 decimal,
0.62 bare. It measures *shape*, which is now only one input. Rebase it on
evidence — how many independent supports hold, and whether the value sits in a
table cell — so the number means "how sure are we this is a real value" rather
than "how ornate is the text". Bump `DETECTOR_VERSION` to
`financial-values-detector-4`; the semantics change even though the schema does not.

## 4. Measured effect

Phase 1 as shipped, across two documents of different shapes:

| Document | Recognized | Published | Suppressed | Per page (before → after) |
|---|---|---|---|---|
| `apple 10k.pdf` (80pp) | 3,064 | 2,719 | 345 (11%) | 38.3 → 34.0 |
| `PDF with loooots of tables.pdf` (12pp) | 5,368 | 5,309 | 59 (1%) | 447 → 442 |

The scorer fails the run if any suppressed candidate carries a currency symbol,
comma grouping or percent sign. Across both documents exactly one does:
`3%` out of `16 2/3%`, where suppressing the misleading fragment is correct — the
value is the whole fraction, which the recognizer does not yet read.

What phase 1 does *not* reach, and phase 2 is aimed at: ZIP codes with no cue
word (`Washington, D.C. 20549`, `Narbonne, Aude, 11100`), street numbers,
payment terms (`Net 30`, `Due after 30 days`) and the 600 bare years still
published from narrative prose. These are all bare integers or bare years in
prose, which is exactly the support model's target.

## 5. Measurement first

Hard-drop means production output can no longer show what was rejected, so the
measurement tooling is a **prerequisite, not a follow-up**. Mirror the
table-detection convention exactly:

- **`scripts/score_values.py`** — currently a 30-line span oracle over 15
  synthetic strings. Extend it into a document scorer: precision/recall/F1
  against page goldens, broken out by kind and by drop reason, with
  `--write-report` producing a comparable JSON report. Keep the span oracle as
  a unit-level gate underneath it.
- **`scripts/render_values_overlay.py`** — add `--show-rejected`, drawing
  suppressed candidates in a distinct color annotated with the suppressor ID.
  It reads the model's own `noise`, so the script and the viewer show the same
  set by construction.
- **Goldens** under `src/python/tests/fixtures/values/`, page-level, spanning
  a 10-K page, an invoice, a cover page, an exhibit index and a footnote page.
  Hand-approved — the scorer never blesses its own output.

Establish the baseline report *before* any gate lands, so every phase is a
measured delta.

## 6. Phasing

| Phase | Work | Risk | Effect | State |
|---|---|---|---|---|
| 0 | Document scorer, `--show-rejected` overlay, per-reason diagnostics | none | none | **landed** (goldens still to come) |
| 1 | Token integrity + S1–S6 (no table dependency) | very low | −11% on the 10-K | **landed** as `financial-values-detector-4` |
| 2 | `evidence.py` support model, E1–E5 | medium — this is the gate that can lose real values | −5% on the filings, −2% on a CAFR, 0 elsewhere | **partly landed** as the `unsupported` refusal: numbers only |
| 3 | Table structure wiring: E2, S7, column currency/magnitude, confidence recalibration, `financial-values-detector-5` | medium | rescues recall lost in phase 2; enables tightening | proposed |
| 4 | Section classification (exhibit index, signature page, cover page) | low | residual | proposed |

Phase 0 landed in its lean form: rejections are counted per reason into the
worker's diagnostics, `render_values_overlay.py --show-rejected` draws every
suppressed candidate labelled with the reason it lost, and
`score_values.py <pdf>` produces a comparable per-document report while still
running the span oracle when given no arguments. Hand-approved page goldens are
still outstanding and remain a prerequisite for phase 2, where the risk of
losing real values is real.

Phase 1 is worth landing on its own — it is near-zero-risk and it fixes the two
wrong-sign bugs.

Phase 2 landed for **numbers only**, as the `unsupported` noise reason. E1
(intrinsic marks), E3 (the isolated island) and E4 (period language) are the
supports; a line of six words or more is what makes the surrounding text a
sentence rather than a row. E2 is deliberately absent: on the corpus CAFR only
314 of 772 values sit inside a detected table, so table membership as a
requirement would refuse the face of the statements, and the island test reaches
the same figures without the dependency. E5 is unbuilt because bare years are
out of scope — 443 survive on Apple and 302 on Quest, and separating a period
anchor from a statute year still needs the section classifier of phase 4.

The honest gap: `score_values.py`'s financial-mark gate cannot watch this
rule. An `unsupported` span carries no currency symbol, comma group or percent
sign by construction, so the assertion that has caught every other regression is
blind to this one. Until the page goldens exist, the only instruments are the
per-reason diagnostic count and the noise overlay, which now names the rule in
its hover tip.

## 7. Contract and layering impact

- **`contracts/document-values-v1.json`** — gains `PageValues.noise`, an array of
  `NoiseValue` (`id`, `kind`, `text`, `bounds`, `reason`). `reason` is a closed
  enum, so a new suppressor is added to the contract before it is implemented —
  which is the point. `detectorVersion` moved to `financial-values-detector-4` in phase 1
  because the published set changed materially; the `confidence` semantics change
  in phase 3 and take `-5` with them.
- **`contracts/webview-messages-v1.json`** — gains `set-value-noise-visible`,
  mirroring `set-values-visible`. Independent of it: the noise overlay draws
  nothing clickable, so both views can be on at once.
- **`contracts/python-worker-v1.json`** — no change. The worker already carries
  both artifacts; only the internal call gains an argument.
- **New diagnostics** in `worker.py` alongside `values_detected`: a count per
  suppressor ID, so a regression in the field is visible without re-running the
  scorer.
- **Viewer** — `ValuesOverlay` gains a second, independent visibility flag. The
  noise layer is `pointer-events: none` and renders `div`s rather than buttons,
  so it can never take a click from a value box or from rectangle drawing. Boxes
  are colour-coded per reason. Reachable from Settings -> Development ("Show
  value noise", persisted per user) and from the console as
  `__talliark.toggleValueNoise()`.
- **Hover tips** — both debug views explain what is under the cursor. Neither
  layer can report its own hover: noise takes no pointer events at all, and a
  value box is a click target whose native `title` would race a richer tip. So
  the overlay hit-tests the cursor and resolves the smallest box across whichever
  layers are showing. A noise tip gives the rule and what it objected to; a value
  tip gives the reading -- normalized value, currency, magnitude, date precision
  and order, segment count, confidence -- which is what makes a misreading
  visible (`03/04/2025` reports an ambiguous order and no derived date).

  A value tip cannot say *why* the span was kept, because the detector records no
  positive evidence for keeping one, only the rules that would have refused it.
  "Kept because no suppressor objected to it" is the whole truth today. When the
  phase 2 support model records which evidence carried a value, that reason
  replaces the sentence, and the wording lives in one place ready for it.
- **Shared component** — the tip itself is `HoverTip` in `packages/shared`,
  beside `Modal`, with its styles in `base.css`. It is deliberately neutral:
  the caller supplies a `resolve` answering "what is at this point" and the
  content to show, and `HoverTip` owns the element, placement and closing. It
  says nothing about diagnostics, so it also serves a user-facing hint; the
  technical wording here belongs to `describeValue` / `describeNoise`, which
  read the `document-values-v1` contract and never restate the detector's rules.

## 8. Open questions

- **Bare years in narrative prose.** 600 survive the conservative rule set.
  "During 2025, the Company repurchased…" is a period anchor and probably
  valuable for Reconcile; "as defined below … 2026" is not. This likely needs
  the section classifier from phase 4 rather than another lexical rule.
- **Vulgar fractions.** `16 2/3%` is recognized as `3%`. Phase 1 suppresses the
  fragment, which is right, but the value itself is still unread.
- **A page number alone in the margin.** Furniture detection needs a word to
  hold onto, so a footer that is only a page number is invisible to it. Nothing
  in the corpus has one; the fix, if one appears, is positional rather than
  lexical.
- **A statement header repeated down a continued schedule.** Once per page, at
  the same band, on a quarter of the document — a long schedule's repeated column
  header could still be read as furniture. The month-name exclusion covers the
  common shape (`September 27, 2025`), but a header carrying other words would
  not be caught. This is what the page goldens are for.
- **Ambiguous numeric dates.** `03/04/2025` publishes with
  `dateOrder: "ambiguous"` and no `normalizedValue`. Does a value that cannot be
  normalized belong in a click target at all, or should document-level order
  inference (majority vote across unambiguous dates in the same document)
  resolve it first?

## 9. What implementation changed about the design

Three things the corpus corrected once the gates were real. Recorded because each
was a plausible rule that would have shipped a bug.

**Page furniture does not live in the margins.** The first cut restricted the
running-header test to the top and bottom tenth of the page, which is what
furniture means on paper. Apple's footer normalizes to y 0.67–0.78 — its position
varies by more than a tenth of a page from one page to the next — so the margin
rule found none of it. The constraint was removed; the y band alone does the
positional work.

**Digit normalization convicts a column of figures.** Collapsing digit runs to
`#` is what lets `Apple Inc. | 2025 Form 10-K | 7` match itself across eighty
pages. It also turns every bare numeric cell into the same `#`, so on the
12-page report every figure in a band matched every other figure in that band
and 5,162 of 5,368 values — the entire document — were suppressed as furniture.
Two constraints fixed it: a normalized line must retain a word that is not a
month name or a function word, and furniture must appear **at most once per
page**. The second is what separates a running footer from a body row that
recurs because the report reuses a phrase (`50% Down`) several times a page.

The function-word exclusion earns its place twice over: it also keeps
`As of December 31, 2025` — a report's period caption, printed on every page —
out of the furniture set, so the period stays readable.

**A matched span can open on whitespace.** `_NUMBER_RE` admits leading space
after its optional opening parenthesis, so the span for `50,400,000` in
`par value: 50,400,000` began at the space and put the colon of `value:`
directly against it. `is_joined_fragment` now measures adjacency from the span's
first and last printing glyphs rather than from its offsets.

**A bracketed negative percentage closes before its sign.** `(4)%` puts the
closing parenthesis ahead of the `%`, where the number pattern expected it after.
The match ended at `(4)`, so the value published as the number −4 and the percent
sign was dropped; `(4)percent` was worse, publishing a positive 4. The pattern now
admits a closer in that position, guarded by a lookahead so it only claims the
parenthesis when a percentage follows and the ordinary `(1,234)` closer is
untouched. Corpus totals are unchanged — no document in `sample-document-corpus`
writes percentages this way, which is why the recognizer had not met it.

## 10. Recognition by token, and furniture by identity plus role

Two changes replace the machinery phase 1 shipped, after a wider corpus
(`sample-document-corpus/financial-statements`, five statements) showed how much of
it was tuned to one document.

**A value must claim whole tokens.** The recognizer matched a number anywhere and
a suppressor then removed the fragments; now a match has to cover whole
whitespace-delimited tokens, with only sentence punctuation left over. So
`123-456-7890`, `10-K`, `3:13`, `ASU 2024-03` and `#7` never become values at
all, while `$ 50.14`, `December 31, 2025`, `1.0 million` and `0.2 percent` still
parse across the spaces they contain. The `joined-token` suppressor is gone,
subsumed. Tokens refused this way are still published as noise, classified by
shape: `identifier`, `alphanumeric`, `partial-token`. The last of those is what
makes OCR fragmentation visible -- `1,2 34` publishes `34` and reports `1,2` --
rather than silently dropping a damaged figure.

**Furniture is identity plus role, and identity has two forms.** The absolute
0.1 y band is deleted: Apple's footer floats between y 0.31 and 0.90 because it
follows the end of the text rather than sitting at the foot of the sheet, and
the band scattered one 58-page pattern across six buckets so that only 27 of 58
footers were ever suppressed.

- *Labels* are found by skeleton -- the line with digit runs masked -- with each
  masked slot classified across pages as constant or page-tracking (value minus
  page index never changes). A slot that varies freely means the line is data.
  This catches `Apple Inc. | 2025 Form 10-K | 7` whole, all 58 of them.
- *Bare page numbers* cannot be found that way: a line that is only `41` has the
  same skeleton as every figure in the document, so it never forms a group. They
  are found instead by the offset they share -- subtract the page index and the
  same number comes back on page after page, for at least three consecutive
  pages. This is what finds the page numbers in `cafr1112bfs.pdf` (offset +41 on
  13 of 13 pages) and `RoyCarver` (offset -1 on 15 of 18), both of which
  previously yielded no furniture at all.

Role is stated as being outside the body -- above every ordinary line, or below
every one -- rather than as a fixed height. Repetition-based furniture is
limited to document labels and page numbers. Note headings are no longer
inferred as generic section heads: explicit note syntax establishes a canonical
document-level note catalogue, continuation headings attach to the same note,
and narrative references resolve back to it. Full heading/reference spans are
reported as `note-header` and `note-reference` noise respectively.

### What the corpus says

| Document | Page furniture | Note |
|---|---|---|
| `apple 10k.pdf` (80pp) | 122 | 58 footer years + 58 footer numbers + 6 exhibit numbers; was 27 |
| `disney 10-k.pdf` (128pp) | 121 | page numbers |
| `cafr1112bfs.pdf` (13pp) | 13 | offset +41; was 0 |
| `RoyCarver` (18pp) | 15 | offset -1; was 0 |
| `Sample-Financial-Statements-1` (3pp) | 0 | too short to establish a pattern |

No evidence-stage suppression on any document carries a currency symbol, comma
group or percent sign. The model validates against the contract on all of them.

### Two traps the corpus caught

**A year sequence looks exactly like a page number.** A statement's column
headers -- `2023` on one page, `2023 2024` on the next, `2024 2025` on the next
-- climb by one across consecutive pages and produce a constant offset, so the
offset vote claimed them and suppressed 44 period years on Apple. Two guards:
a value inside 1500-2200 is not a page number unless the document is long enough
to reach that page, and the sequence's lines must sit in the same place on every
page, which a column header moving with its table does not.

**A sparse page has no ordinary text.** The superscript rule compared a glyph
against its page's median height. RoyCarver's title page carries 63 glyphs in
two sizes, so the median landed on the *heading* font and the ordinary text below
it read as a footnote marker -- suppressing a real `April 30, 2014`. The baseline
now falls back to the document's median when a page has too few glyphs to be
representative.

The note catalogue recognizes integer, decimal and Roman-numeral identifiers.
Each occurrence retains its complete printed text and geometry; a reference
also records whether its description was printed or inherited from the
canonical heading.

## 11. Ordinals that number rather than measure

A third apparatus, alongside the note and item catalogues: the ordinal that
numbers a list item, a footnote, or the indicator pointing at one. It is the
last large source of published noise in a filing, and the worst-behaved,
because the recognizer reads a parenthesised integer as a negative. An exhibit
index publishes `3.1` through `10.53`; the footnotes under it publish `-1`
through `-63`; the indicators in the descriptions publish the same values a
second time.

### What refuses them

Not the token — `(1)` under a compensation table and `(1)` in a tax schedule are
the same six pixels, and one of them is a real loss of one. What separates them
is the **chain**: ordinals that step through a list in order, each leading
something. Three tests, and the corpus produced each one by breaking the version
without it.

| Test | What it kills that the others do not |
|---|---|
| The ordinal **steps** — last part +1 inside a group, or a new group opening at one | Columns that merely ascend: executive ages (`48`, `50`, `54`), invoice quantities (`10`, `15`, `100`), aircraft models (`737`, `747`, `767`), a tax column of `(30)`, `(80)`, `(121)`, `(623)` |
| Each member **leads prose** | A numeric cell whose right-hand neighbour is another figure. This is the test that separates an exhibit column from an option-price column, because in extracted geometry both are a narrow cell with something beside it |
| The chain **holds together** — one left edge across consecutive pages, or one passage for an inline enumeration | Unrelated markers on distant pages joining into one chain |

Shape then tells the two apparatuses apart. Parenthesised means footnote
apparatus, because that is how footnotes are set; anything else is an ordered
list. This is a convention rather than a proof, and it mislabels a
parenthesised list — Apple's `(1) All financial statements` in its exhibit index
reports as `footnote-marker`. Both are refused either way, so the cost is a
wrong word in a diagnostic tip rather than a wrong value in a cell.

An echo test was tried instead — call a chain footnotes only when its forms
appear again as indicators — and dropped. It classified correctly where
indicators are set inline and failed where they are set as superscript cells of
their own, which is Apple's and RoyCarver's house style, so it turned real
footnote blocks into lists. Shape gets the same answers with one fewer moving
part.

### A note with no siblings

The chain is evidence a footnote block gives about itself, and a block of one
gives none: Apple's segment note prints `China ⁽¹⁾` in a net-sales table and
again in a long-lived-assets table below it, both answered by a single
`(1) China includes Hong Kong and Taiwan.` The definition formed no chain, so
nothing refused it and it published as the value **−1** — the exact failure the
chain rule exists to prevent, reappearing wherever a note serves several rows
instead of several notes serving one each.

So a definition is established either way round: by the chain it belongs to, or
by an indicator pointing at it. That needed a fourth candidate class, because
the marks in question are not the trailing kind — geometry delivers a
superscript as its own cell, so `China ⁽¹⁾` arrives as the word in one cell and
the mark in another, leading nothing.

What makes the second route safe is what an indicator has to *be*, not how many
there are:

- **Raised** — alone in a cell and set below 0.72 of the page's ordinary line
  height, the same ratio `profile.py` uses for the same observation. A full-size
  bracketed number alone in a cell is a negative figure in a table column, and
  it stays a value.
- **Attached** — trailing the end of a word, which no figure in a column is.

Both classes are already refused today (as `superscript`, or by the token
rules), so reading them as indicators cannot lose a value; it only says
something more specific about spans that were losing anyway. Because the classes
carry the safety, the count does not: `MIN_INDICATORS` is 1. Requiring two was
tried and rejected — it costs 45 real definitions across the corpus (`(1) For
leased properties, represents the total leased space excluding sub-leased
space.`, `(1) Represents amortization of intangible assets.`) and buys nothing,
since a second mark is no harder to come by than the first for anything that
could have fooled the rule. The route is scoped to marks standing above the
definition on its own page, which is all a single definition can be sure of: a
footnote answers the table it follows.

### The recall guards

Two rules exist only to protect real values, and both were written after the
corpus caught the version without them:

- **An ordinal that continues into a quantity was never a marker.** Disney
  prints `(5)%`, a negative five percent, twenty-eight times. The first draft
  matched `(5)` inside it and suppressed a real value with a percent sign on it
  — precisely what `score_values.py`'s financial-mark gate exists to catch.
- **An indicator annotates a word.** Only a letter or a closing bracket may
  precede one, so `Article 5(4)` and a figure followed by a bracketed negative
  are both refused; an enumerator may also follow the punctuation that ends the
  clause before it. Sentence punctuation is trimmed before that test, because
  `Quest Sustainability Services, Inc. (20)` is an annotated word and the trim
  leaves a figure's last digit exposed, so `1,234 (56)` is still refused.
  Reference resolution is further bounded to four pages around its definitions,
  which is what keeps `(30)` on Quest's page 84 out of reach of the exhibit
  footnotes that end on page 58.

### Measured effect

Ten documents, `financial-values-detector-7` to `-8`:

| Document | Published before | After | Removed | list-marker | footnote-marker | footnote-reference |
|---|---|---|---|---|---|---|
| `quest 10k.pdf` (90pp) | 2,416 | 2,150 | 266 | 87 | 105 | 107 |
| `disney 10-k.pdf` (128pp) | 4,663 | 4,563 | 100 | 100 | 77 | 89 |
| `apple 10k.pdf` (80pp) | 2,514 | 2,407 | 107 | 73 | 40 | 9 |
| `Amazon_AR.pdf` (89pp) | 3,236 | 3,109 | 127 | 33 | 49 | 51 |
| `Boeing-2023-Annual-Report.pdf` (152pp) | 5,007 | 4,930 | 77 | 77 | 29 | 39 |
| `RoyCarver-2014-Rpt-Final.pdf` (18pp) | 479 | 472 | 7 | 0 | 7 | 0 |
| `cafr1112bfs.pdf`, `Sample-Financial-Statements-1.pdf`, `PDF with loooots of tables.pdf` | 5,998 | 5,998 | 0 | 0 | 0 | 0 |

The marker and reference counts exceed the removals because some of these spans
were already refused for another reason — a superscript indicator, a token
sharing a cell with a word — and now carry the more specific classification
instead. The three documents with no list or footnote apparatus are untouched,
which is the result that says the rules are reading structure rather than
guessing at small integers. No suppression on any of the ten carries a currency
symbol, comma group or percent sign.

### What it does not reach

- **A parenthesised list reports as footnote apparatus.** Documented above; a
  labelling cost, not a correctness one.
- **Alphabetic and Roman markers are not parsed.** `a)`, `(iv)` are never read
  as values, so refusing them would publish nothing. They do mean a chain of
  Arabic markers interleaved with them can break in two.
- **A tail that stops stepping falls out of its chain.** An exhibit index ending
  `97.1`, `101`, `104` keeps the chain through `97.1` and publishes the last
  two, because `101` neither advances `97.1` by one nor opens a group at one.
  Two spans per filing.
- **A raised mark is recognized by size, so a full-size one in its own cell is
  not.** An indicator set at body size, alone in a cell rather than attached to
  a word, is indistinguishable from a bracketed negative in a table column, and
  the column wins. Nothing in the corpus prints one.
