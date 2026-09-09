# Architecture: scope tiers and two runtimes

The record of what is built and why it is shaped this way: the tier split, the
line between the cache build and the Reconcile scan, the span categories, and
what P1 changed. It spans all five engines rather than any one of them; the
per-engine records are in [`engines/`](engines/README.md).

Everything here describes code that exists. Work that is intended but not
built is not recorded here.

Companion: [`engines/values/value-precision.md`](engines/values/value-precision.md),
the record of detector precision work.

## 1. What is actually wrong

`financial_values` names a kind of *number* and has grown to hold logic scoped to a
kind of *document*. Recognition of a dollar amount, the note catalogue, and the
Item catalogue sit in one module behind one contract, and they do not apply to
the same documents or cost the same to compute.

Two consequences, both live today:

- **The apparatus a document lacks is indistinguishable from apparatus that
  failed.** A compilation or review report has notes and no `Item 1A` anywhere.
  The item catalogue is self-anchoring, so on those documents it correctly finds
  nothing — but nothing in the model says whether it found nothing because the
  document has no items or because detection missed them. That distinction is
  invisible today and it is what the checks will stand on.
- **The cache build pays for the filing apparatus on every document.** The
  scan budget is soft — a minute for a whole document is fine, ten minutes is
  not — so the problem is not that seconds are scarce. It is that the cost falls
  on the wrong documents: OCR runs constantly and almost never on a financial
  statement, yet every run carries machinery only a filing uses, and the
  expensive work Reconcile will need has nowhere to go but into that same path.

## 2. Decisions

| | Decision |
|---|---|
| **D1** | Two tiers by document scope: values (any PDF), and financial documents (statements and filings alike). What a document lacks is detected, not gated. |
| **D2** | Two runtimes by cost. The line between them is **not** the tier line — see §4. |
| **D3** | Clickability is uniform across documents. A span kind that is clickable on one document is clickable on all of them. |
| **D4** | Classification may be probabilistic; verification must be arithmetic. |
| **D5** | Nothing leaves the machine at runtime. Everything a scan asserts is derived from the PDF itself. |
<!-- XBRL SUSPENDED: every scan detail must be derived from the PDF itself. Kept for reference only; do not build on it without a deliberate decision to reintroduce XBRL. -->
<!-- was: | **D5** | Nothing leaves the machine at runtime. XBRL is a build-time asset only. | -->
| **D6** | Span identity is content-addressed before Reconcile ships, not after. |
| **D7** | The cache-build engines are `engines/values/`, `engines/financial/`, and `engines/table/`. The on-demand path first interprets their neutral artifacts in `engines/financial_table/`, then proves arithmetic in `engines/reconcile/`. |
| **D8** | The rename off "financial-value" reaches everything in one pass — contracts, Python, viewer TypeScript, CSS, webview messages, id prefixes. |
| **D9** | The cache build emits values and structure as two artifacts, so the viewer draws tier 1 without waiting on tier 2 and a non-financial document carries no structure payload. |

D4 is the one that governs Reconcile's design. An auditor will accept a
suggested tag they confirm or correct. They will not accept "the model thinks
this total is wrong" — but they will accept "these five rows sum to 12,345, the
total row reads 12,354, and the difference of 9 is a digit transposition."
Every exception Reconcile raises must be restatable as arithmetic over
identified spans.

## 3. The tier split

| Tier | Applies to | Owns | Modules today |
|---|---|---|---|
| **1 — values** | any PDF | number / percent / date recognition, page furniture, superscripts, phone and identifier context, ordinal apparatus (list markers, footnote markers, indicators), currency and scale context | `spans`, `profile`, `context`, `lists`, `evidence` |
| **2 — financial documents** | audited GAAP, compilations, reviews, CAFRs, 10-K / 10-Q / 8-K | heading mechanics, note and item catalogues, statement-table cell membership and label meaning, reconciliation and checks | `financial/{headings,notes,items}`, `financial_table/{detector,cells,lattice,labels}`, `reconcile/*` |

### 3.1 The table-recognition boundary

The general table engine owns visible structure that should mean the same thing
on an invoice, laboratory report, form, or financial statement. The
financial-table engine owns a private interpretation of that structure under
financial presentation conventions. Reconcile owns conclusions reached by
addition.

The dependency is one-way:

```text
table-structure-v1 + document-values-v1 + text geometry
                         │
                         ▼
                 financial_table
                 (private blocks)
                         │
                         ▼
                    Reconcile
                 (reconcile-v1)
```

The general engine never reads document class or imports either on-demand
engine. The financial-table engine never rewrites `table-structure-v1`, and it
does not decide arithmetic outcomes. The complete ownership matrix, including
currency markers, dates, dashes, rulings, repeated headers, and partial statement
recovery, is in
[`engines/financial-table/README.md` §1.1](engines/financial-table/README.md#11-ownership-boundary).

The boundary is executable. `table.handoff` gives private analysis a frozen
copy, a fresh page layout and the grid's token-placement operation; the sister
engine cannot import layout or grid implementation modules directly. Each grid
fallback pins its source table id and geometry digest, and detection fails
closed if page correspondence, fallback cardinality, geometry, provenance, or
the original artifact changes. `scripts/audit_table_engine_drift.py` records
structured cross-engine disagreements across the corpus. Those disagreements
are review signals rather than failures, because making one recognizer the
other's oracle would create exactly the drift the split prevents.

`headings.py` is tier 2. Its mechanics are stated generically — identifier
shapes, following a heading across the lines it wrapped onto, continuation
markers, the description printed beside a citation — but the grammar they
implement is the note and item grammar, and nothing outside a financial
statement or a filing asks for it. Notes and items share it, which is one of
the reasons they share a tier.

Heading *recognition* still runs in the cache build, for the reason §4 gives —
an unclassified `Note 7` publishes as the value 7 and becomes a click target.
What belongs to Reconcile is what the headings mean: that a document uses one
note convention, that notes run in sequence without gaps, that a reference
resolves to a note that exists. Those are assertions (§6.4).

Statements and filings are one tier rather than two because the difference
between them is **what a document happens to contain, not what code should run
on it**. A 10-K is a filing that contains financial statements; a compilation is
the statements without the wrapper. Each apparatus already anchors itself — the
item catalogue needs a contents row or a body heading before it publishes
anything, and an identifier absent from the catalogue is never published as a
reference — so running it on a document with no items finds nothing, which is
the correct answer. Gating that behind a tier buys a little time and costs a
branch.

What the collapse does require is that **absence be recorded, not merely
produced**. Tier 2 publishes what it looked for and what it found, so a document
with no contents page is known to have none. Two things depend on it: check
applicability (§6.4), where a check with no subject must be skipped rather than
failed, and a `documentClass` — filing, statement, neither — published as
metadata. Nothing branches on the class; the Reconcile window needs it to say what
it thinks it is looking at, and the scorer needs it to break the corpus out by
document family, which is the fix for the "tuned to one document" failure
recorded in `docs/internal/engines/values/value-precision.md` §10.

### Contracts

`financial-values-v1` splits. There is no backward-compatibility requirement, so this
is close to free now and expensive once Reconcile has users.

| Contract | Holds | Produced by |
|---|---|---|
| `document-values-v1` | values, references, noise, page and document context | cache build |
| `financial-structure-v1` | documentClass, notes, noteReferences, items, itemReferences, and what was looked for but absent | cache build |
| `reconcile-v1` | statements, tagged facts, periods, exceptions, dispositions | Reconcile runtime |

The Reconcile webview consumes `reconcile-v1` and nothing else.
`engines/financial_table/` deliberately has no contract: its statement blocks
exist only between Python packages during one scan, so no second public table
model can disagree with `table-structure-v1`.

## 4. The two runtimes

The tier axis says *which documents* a rule applies to. The runtime axis says
*what we can afford to compute during a cache build*. They are orthogonal, and
conflating them would push cheap note recognition out of the cache build for no
reason.

The line is:

> **Classifying a span belongs to the cache build. Asserting something about the
> document belongs to Reconcile.**

A note heading must be classified during the cache build, or `Note 7` publishes
as the value 7 and becomes a click target. Whether the notes run 1 through 14
without a gap is an assertion, and nobody needs the answer until they open
Reconcile.

| | Cache build | Reconcile scan |
|---|---|---|
| Trigger | ordinary OCR | user runs Reconcile for the workspace primary |
| Budget | soft — **a minute for a whole document is fine, ten minutes is not** | generous; the user is waiting on a deliberate action |
| Runs on | every document | financial statements only |
| Produces | `document-values-v1`, `financial-structure-v1`, and experimental `table-structure-v1` when enabled | a complete independent snapshot: geometry, tables, values, financial structure, private financial-table blocks, then `reconcile-v1` |
| Contains | document-neutral table geometry, value recognition, ordinal apparatus, note and item recognition, catalogue assembly | financial table interpretation, statement classification, reconciliation, tagging, checks, exception dispositions |

Two consequences worth stating before anything is built:

- **Reconcile is a workbook-scoped workspace, not an ordinary document cache.**
  A named project owns a current statement in its primary slot and a prior-year
  statement in its first comparison slot, each with one current version, in a
  dedicated Custom XML part. Import or copy duplicates the PDF bytes and assigns
  Reconcile document and version ids. Ordinary rename, deletion, OCR, and
  settings changes cannot change those snapshots. The second comparison role is
  reserved for another prior period. Only the current statement is analysed;
  comparison analysis and version history are not exposed yet.
- **Published table structure is experimental during ordinary OCR.** The
  user-scoped setting defaults off. Table-cell recovery stays unconditional
  because it improves source text geometry; only general table publication and
  detector-driven viewer controls, notices, suggestions, snapping, and metadata
  enrichment are gated. Manual table rectangles and grids never consult the gate.
  Reconcile always forces general table detection on inside its own one-job scan.

- **The soft ceiling is a guardrail, not a design constraint.**
  `docs/internal/engines/values/value-precision.md` records table detection at 5.5s on the 80-page 10-K,
  so the build has real headroom and nothing currently in it has to move for
  cost alone. What the split protects against is Reconcile's work —
  reconciliation over every numeric column, tagging every row of every statement
  — landing in the path that runs on every scanned invoice.
- **A truncated table model no longer limits the scan.** Detection carries a
  time budget and publishes `truncated` when it stops early, and for a while the
  scan answered that by re-running detection unbudgeted and privately. It no
  longer does: `engines/financial_table/` builds a value-alignment lattice and
  reads the cached grid only where that grid is confident, then Reconcile proves
  arithmetic over those blocks. A truncated model costs corroboration, never
  arithmetic recall. See
  [`engines/financial-table/README.md`](engines/financial-table/README.md) §4.

## 5. Span categories replace the value / noise binary

D3 settles a question `docs/internal/engines/values/value-precision.md` left as "the detector alone
decides what is a value": user-facing behaviour must not vary by document type.
That rules out promoting identifiers on invoices and suppressing them on
filings. But the underlying need is real — an auditor sampling invoices wants
to snip the invoice number, and today it is refused as `identifier`.

The resolution is a third category rather than a per-document policy. What the
detector publishes becomes:

| Category | What it is | Clickable | Examples |
|---|---|---|---|
| **value** | a measured quantity | yes | `$1,234`, `5.2%`, `December 31, 2025` |
| **reference** | a printed datum that identifies rather than measures | yes | invoice and PO numbers, account and check numbers, EIN, phone, note and item citations |
| **noise** | an artifact of setting the page | never | running headers and footers, page numbers, list and footnote markers, superscript indicators, partial tokens |

Clickability is then uniform by construction: it follows the category, and the
category follows the span, not the document. Whether the reference layer is
drawn can be a single global setting, the same on every document.

This also repairs a real loss. `identifier` and `identifier-context` currently
refuse exactly the spans that make an invoice worth snipping in a workpaper.
Moving them from noise to reference recovers them without weakening any rule
that protects a filing.

The reason enum splits along the same line: recognition reasons and evidence
reasons keep their present meaning inside `noise`, while `identifier`,
`identifier-context`, `phone-context`, `note-reference` and `item-reference`
become reference *kinds* rather than refusals.

## 6. Reconcile

What Reconcile does with the arithmetic is
[`engines/reconcile/sum-tree.md`](engines/reconcile/sum-tree.md); how it nominates
a total is [`engines/reconcile/corroboration.md`](engines/reconcile/corroboration.md).
Statement-table recognition immediately upstream is documented in
[`engines/financial-table/README.md`](engines/financial-table/README.md). This
section covers only what Reconcile needs from the tiers above it.

### 6.1 Statement classification

Decide what each table is — balance sheet, income statement, cash flow, equity,
or a note schedule — from its caption and the fingerprint of its row labels
against per-statement vocabularies. Everything downstream leans on it: `Cash` in
a balance sheet, at the foot of a cash flow, and in a segment note are three
different facts, and the enclosing statement separates them almost for free. It
also supplies the structure each statement is checked against.

### 6.2 Tagging

Tagging is the part of the plan with the least certainty, so it is staged with a
measured decision point rather than a choice made up front. What makes the
staging safe: Reconcile confirms tags in the UI, so an unmatched label is a
productivity cost, never a correctness one.

| Stage | Method | Cost | Move on when |
|---|---|---|---|
| **A** | Label normalization plus exact and near-exact match against a hand-built lexicon | days; a dictionary and a normalizer | always start here; measure coverage on the corpus |
<!-- XBRL SUSPENDED: every scan detail must be derived from the PDF itself. Kept for reference only; do not build on it without a deliberate decision to reintroduce XBRL. -->
<!-- was seeded from the us-gaap taxonomy's own standard and documentation labels. -->
| **B** | Token-overlap scoring with ties broken by structural priors — statement type, position in the sum tree, sign, instant vs duration | a week or so; still deterministic, no training | only if A's coverage leaves a tail worth the work |
| **C** | A fitted ranker over hand-designed features, or local embeddings for unseen labels | data plumbing and installer size, not modelling | only if B's residual is large and repetitive |

Stage C is where the real costs live and they are not the model: a fitted ranker
needs labelled data (§7), and embeddings mean bundling roughly 100MB and a
runtime into a VSTO installer, with "why" degrading from a lexicon entry to a
similarity score. Do not skip to it.

<!-- XBRL SUSPENDED: every scan detail must be derived from the PDF itself. Kept for reference only; do not build on it without a deliberate decision to reintroduce XBRL. -->
<!--
Two things the taxonomy supplies that are worth having regardless of stage:
every element carries a **balance type** (debit / credit), which is the
directionality check for free, and a **period type**
(instant vs duration), which is the distinction `datePrecision` cannot express.
"Cash at September 27, 2025" and "revenue for the year ended September 27, 2025"
carry the same printed date and are not the same kind of fact, so periods are
modelled as intervals, not points.
-->

Periods are modelled as intervals, not points: "Cash at September 27, 2025"
and "revenue for the year ended September 27, 2025" carry the same printed date
and are not the same kind of fact, a distinction `datePrecision` cannot express.

## 7. P1, in detail — as landed


Three changes that would each rewrite the same schema and the same viewer
consumption path, done as one so that surface is crossed once.

**Contracts, first.** `financial-values-v1` becomes `document-values-v1` (values,
references, noise, context) and `financial-structure-v1` (documentClass, notes, items,
their references, and what each apparatus looked for and did not find).
`python-worker-v1` gains the second artifact. `webview-messages-v1` renames its
financial-values messages and gains the reference-layer visibility flag alongside the
existing noise one.

**Python.** `engines/financial_values/` becomes `engines/values/` (`spans`, `profile`,
`context`, `lists`, `evidence`) and `engines/financial/` (`headings`, `notes`,
`items`). The worker calls them in that order and publishes two artifacts.

**Categories.** Today's binary becomes value / reference / noise (§5).
`identifier`, `identifier-context`, `phone-context`, `note-reference` and
`item-reference` move out of noise and become reference kinds; everything else
in the reason enum stays a refusal. Clickability follows the category on every
document.

**Identity.** Ids are content-addressed: a value or reference is keyed by a
hash over its page index, normalized text and bounds rounded to three decimals;
a note or item is keyed by its normalized identifier, which is already stable by
construction (`financial-item-3` becomes `item-1A`). The honest limit is that this is
stable across *detector* versions, which is what dispositions need, and not
across a re-OCR that moves geometry — re-anchoring after a geometry rebuild is a
separate problem and is listed in §9.

**Scoring.** `score_fs_values.py` and `render_fs_values_overlay.py` rename with
the rest. The gate that no suppressed span carries a currency symbol, comma
group or percent sign now covers a smaller set, because identifiers left noise —
so it needs a sibling assertion that no span carrying those marks was
categorized as a reference either. Without that, the categories could quietly
launder a real value out of the value set.

## 8. What P1 changed about the design

**Headings and contents rows left noise entirely.** They were published twice --
once as a full-text noise span, once as a catalogue occurrence. In the split the
occurrence is the only home for the printed text, and `document-values-v1` now
carries just the integer inside a heading, refused so it cannot be read as a
figure. The overlay's join changed with it: a refusal is matched to its
catalogue entry by containment rather than by identical text and bounds.

**Derived ids turned out to be what joins the two artifacts.** The structure
tier resolves a citation before the value tier publishes it, so the two would
have had to agree on an identifier they could not pass between them. Because the
id is a hash of page, text and place, both arrive at the same one independently:
`spanId` needs no lookup table and no ordering guarantee. Dispositions were the
reason to content-address; this is the reason it had to happen in P1 rather than
after it.

**The recognizer's own token refusals became references, not noise.**
`alphanumeric` and `identifier` are shapes like `BPXINV-00550` -- which is the
invoice number, the thing an auditor samples. The noise reason enum fell from
sixteen entries to ten and seven reference kinds took their place, and a token
refused on a line carrying a printed phone number now says `phone` rather than
the generic kind, because a refused token never reaches the classification stage
where that cue would otherwise have been read.

**The scorer needed a second gate.** Its existing one fails a run if any
*suppressed* span carries a currency symbol, comma group or percent sign. With
identifiers moved out of noise that gate covers a smaller set, so a value
misdirected into the reference layer would pass unnoticed. The sibling gate
watches references for a currency symbol or percent sign -- but not a comma,
which an identifier is full of -- and exempts citations, which quote whatever
their sentence quotes.
