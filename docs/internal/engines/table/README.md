# Table engine

| | |
| --- | --- |
| **Source** | `src/python/engines/table/` |
| **Contract** | `contracts/table-structure-v1.json` |
| **Entry point** | `engines/table/detector.py` |
| **Consumes** | `text-geometry-v1`, PDF vector drawings and raster rulings |
| **Tests** | `src/python/tests/test_table_structure.py`, `test_table_detector.py`, `test_table_pipeline.py`, `test_table_overlay.py`, `test_table_benchmark.py`; fixtures in `src/python/tests/fixtures/tables/` |

## 1. Purpose

The table engine finds table-shaped regions on any document and publishes their
normalized bounds, columns, logical rows, header bands, confidence and supporting
evidence. It is document-neutral: financial statements, invoices, forms, scanned
pages and spreadsheet screenshots all travel through the same detector.

It does not interpret a row as a subtotal, a dash as accounting zero, or a
column as a fiscal period. The on-demand financial-table sister engine reads the
general artifact as corroboration and owns those meanings.

The canonical ownership matrix and concrete borderline examples live in
[`financial-table/README.md` §1.1](../financial-table/README.md#11-ownership-boundary).
Two constraints are enforced in tests: this package may not import
`engines.financial_table` or `engines.reconcile`, and it may not branch on a
document's financial classification. A generally useful visual invariant may
have a financial example in its test without becoming financial semantics.

Private downstream analysis reads this engine through `handoff.py`. That module
creates a recursively immutable copy of the public tables, rebuilds page layout,
and exposes the same token-placement operation used by the grid fitter. It is a
stable internal Python seam, not a second serialized contract.

## 2. Inputs and outputs

The detector reads character geometry from `text-geometry-v1` and inspects the PDF
page for vector and raster ruling evidence. Coordinates are normalized to the
displayed page after rotation. Every output page preserves its zero-based
`pageIndex`; tables are ordered top-to-bottom and left-to-right and conform to
`table-structure-v1`.

Published row and column bands cover the table itself. A consumer may reconstruct
cells by intersecting text geometry with those bands. Header rows are also
present in `rows` and are labelled with `kind: "header"`. A label above the table
that spans several columns is excluded from both the rows and table bounds. It is
published in `header.groups` with its text geometry and inclusive final-column
range, while `header.labels` remains one label per individual column. A candidate
below the acceptance threshold is not published.

## 3. Types

- **Visual line** - characters sharing a baseline, tokenized into whitespace
  islands.
- **Logical row** - one anchor line plus any wrapped continuation lines assigned
  to the same record.
- **Candidate** - a generous possible table region supported by whitespace,
  rulings, or both.
- **Grid hypothesis** - fitted column boundaries and logical rows for a candidate.
- **Section row** - a label band inside a table that carries no values.
- **Header group** - a horizontally merged label above the table, stored with its
  source bounds and the range of final columns it governs rather than as a row or
  an individual column label.
- **Graphic** - chart-like vector area, curve or diagonal evidence. Page canvas,
  white panels, row shading and embedded raster images are not graphics merely by
  occupying area; a raster image becomes graphic evidence only when it contains
  a sustained diagonal plotted series.

## 4. Algorithm

1. `layout.py` converts characters into classified tokens, visual lines and page
   body metrics, and marks running prose. A wide gutter only exempts a line from
   prose when the right-hand island is short; two substantial islands are the
   synchronized columns of an editorial layout. Currency signs attached to an
   amount remain part of the numeric token rather than becoming symbols.
2. `rulings.py` extracts vector and raster rules with their real extents. It
   ignores near-page background rectangles and white fills. Raster image coverage
   remains neutral; a small Hough-style vote records only long diagonal series as
   chart evidence. Projected raster lines are traced inside a narrow drift
   corridor and retain their individual extents. Each must be thin, locally
   contrasted and connected to at least two perpendicular rules; broad or
   low-contrast photographic bands therefore cannot manufacture a grid. Before
   the optional cell-aware OCR pass, raster candidates must be substantially
   visible on the page and have the light surface of a document scan; this keeps
   cropped spread art and photographic edges out of Tesseract.
3. `candidates.py` proposes independent ruled and whitespace regions and combines
   evidence where their bounds agree.
4. `grid.py` votes for persistent whitespace corridors, builds logical rows and
   iterates until columns and rows settle. It aligns boundaries to actual cells,
   coalesces empty bands again, and after header detection removes columns that
   only a centred title occupied.
5. `refine.py` splits schema changes, repeated headers and prose interruptions;
   trims page furniture before a complete internal header when a marked gap and
   a repeated multi-value body schema independently establish the table start;
   scopes rule evidence to each resulting fragment, downgrading one that no
   longer contains its own lattice to independently repeated whitespace evidence;
   merges adjacent fragments only when their columns agree; recognizes and
   removes horizontal header groups while retaining their geometry and column
   coverage; and deduplicates overlapping proposals.
6. `headers.py` recognizes period bands, stacked titles and complete all-word
   headers from the fitted cell matrix.
7. `scoring.py` measures alignment, repetition, typing, spacing, headers, numeric
   content, rulings, graphics and negative layouts. Every adjacent column pair is
   checked for predominantly running prose, including inferred header rows, so an
   extra provisional boundary cannot bypass the penalty. An inferred header earns
   positive credit only when body values or rule intersections corroborate it. A
   coherent ruled grid can override the prose ambiguity.
8. `redesign.py` maps retained header-group coverage onto the final columns and
   publishes accepted candidates as `table-detector-7`.
9. `handoff.py` isolates accepted tables for downstream analysis and fingerprints
   the source geometry. It makes no detection or financial interpretation
   decision.

## 5. Tuning and thresholds

| Setting | Value | Reason and effect |
| --- | ---: | --- |
| Acceptance confidence | `0.50` | Separates small but repeated tables from aligned lists and prose negatives. |
| Prose words per side | `4` | Requires sentence-like content in both columns; a short jurisdiction or code column remains table evidence. |
| Prose numeric allowance | `2` | Years and small counts occur inside prose and must not disable the prose test. |
| Dominant prose share | `0.50` in any adjacent column pair | Half the fitted rows, including a would-be header, establish a synchronized editorial layout; smaller shares retain a proportional penalty. |
| Marker-led prose | `2` trailing words | A numbered or bulleted gutter remains list structure when its sentence embeds measurements; a parenthesized amount beside a numeric cell remains eligible table data. |
| Accounting zero placeholders | `-`, `‐`, `‒`, `–`, `—`, `―` | A floated currency marker attaches to a dash exactly as it attaches to a numeric amount, allowing the otherwise-empty marker column to collapse. |
| Ruled override | ruling feature `>= 0.50` | At least a small coherent set of intersections establishes cells independently of text flow. |
| Page backdrop extent | `90%` on both axes | A rectangle covering nearly the whole page is canvas rather than figure content. |
| Visually white fill | every component `>= 0.95` | A white paint operation contributes no visible graphic area. |
| Minimum graphic extent | `0.04` on both axes | Thin shading bands and furniture are not chart regions. |
| Raster series span | `60%` of image width | Text strokes do not traverse a figure; plotted series do. |
| Raster series votes | `30%` of image width at `30%` density | Preserves dashed plots while rejecting scattered text and scanned grid furniture. |
| Maximum rule thickness | `2.5 pt` | Thicker rectangles are fills rather than ruling lines. |
| Raster rule darkness | median luminance `<= 225` | Very pale image structure is not positive ruling evidence. |
| Raster rule contrast | `18` luminance levels against an adjacent strip | A projected photo band must differ locally from its surroundings before it can become a rule; one clean side is sufficient when an annotation hugs the other. |
| Raster rule span | vertical `16%`, horizontal `30%` of the raster | Mirrors the minimum local-strip support required to nominate a page-level raster grid. |
| Raster rule continuity | `55%` sampled trace coverage | Tolerates text crossings and raster gaps without accepting scattered nearby ink. |
| Raster rule connectivity | at least `2` perpendicular intersections | A line participates in a cell lattice rather than merely sharing the page with unrelated edges. |
| Column support | `35%` of weighted anchors | Keeps minority gaps from inventing columns while allowing sparse tables. |
| Maximum ordinary crossing | `15%` | A persistent boundary may not cut through ordinary cell text. |
| Spanning-header crossing | `40%` | The first tolerant vote allows centred and spanning headers to cross provisional boundaries. |
| Grid iterations | `3` | Columns and wrapped-row membership settle within three passes on the regression corpus. |
| Refinement depth | `4` | Bounds recursive splitting while allowing stacked independent tables to separate. |
| Internal-header trim | first cell labelled, at least `max(3, columns - 1)` labelled cells, and `2` matching multi-value rows within the next `5` | A complete header plus a repeated body schema establishes a table start independently of nearby mastheads; the marked-gap and attached-group checks protect stacked headers. |
| Default document budget | `300,000 ms` | Production remains bounded; diagnostic and reconcile callers may disable the budget explicitly. |
| Cell-recovery image visibility | `80%` of the placed image | Rejects spread artwork and other placements whose apparent area is mostly clipped off the current page. |
| Cell-recovery light surface | `35%` of an autocontrasted `256 px` thumbnail at level `>= 220` | A ruled scan is predominantly paper; continuous-tone photography can contain long false rulings and is both inaccurate and pathologically expensive to OCR cell by cell. |
| Sparse recovery enlargement | preferred `8×`, maximum `24,000 px` and `60,000,000 px²` | Keeps normal low-resolution regions legible without constructing a Tesseract raster large enough to abort the optional recovery pass. |

Scoring uses positive weights for alignment (`0.20`), multi-column occupancy
(`0.15`), type stability (`0.10`), spacing (`0.08`), header evidence (`0.12`),
rulings (`0.08`), numeric content (`0.10`), rule/grid agreement (`0.05`) and size
(`0.12`). Penalties cover list markers (`0.55`), parallel prose (`0.45`), narrow
marker layouts (`0.30`), chart graphics (`0.50`), empty grids (`0.25`), irregular
density (`0.20`), unstable schemas (`0.30`) and unrepeated two-line blocks
(`0.35`).

## 6. Failure modes

- An unruled two-column table whose body consists mainly of long prose in both
  cells is geometrically indistinguishable from editorial columns and is declined
  conservatively unless independent ruling evidence exists.
- A raster-only bar chart with no diagonal series receives no penalty merely for
  being an image. It must be rejected by its lack of repeated cell structure;
  unusually table-like charts can therefore remain ambiguous.
- Poor OCR can merge adjacent values, erase gutters or move glyphs enough to
  change both candidate bounds and content-addressed row structure.
- Optional sparse page-text OCR reduces its enlargement to remain inside the
  recorded raster budget. If that local recognition still fails, table-cell
  recovery continues and reports `page_text_regions_failed` rather than
  aborting the document.
- Cell-aware OCR deliberately declines dark or continuous-tone raster tables.
  Their background violates the light-document assumption used by cell cleanup;
  direct page OCR remains authoritative for those pages.
- Centred multi-line titles can initially create header-only columns. The
  post-header coalescing pass removes columns empty throughout the body, but a
  genuinely sparse optional column cannot be removed safely.
- An unpunctuated section label can resemble a wrapped row label. Flush-left
  capitalized titles and short labelled rows are kept separate; unusual wrapping
  conventions may still require manual correction.

## 7. Tests and corpus

Synthetic fixtures cover whitespace and ruled tables, stacked schemas, wrapped
cells, currency markers, introductory prose, charts, lists and aligned prose.
Unit tests separately pin ruling extraction, page-background neutrality, header
analysis, internal-header preamble trimming, post-alignment column coalescing,
section-row handling and scoring.
Boundary tests also prevent financial or Reconcile imports and prevent the
financial-table sister from bypassing `handoff.py` to couple itself to layout or
grid implementation modules.

The Quest 10-K corpus goldens record pages 3 and 4 as negative editorial layouts,
page 49 as three independent tables with centred multi-line headers, page 50 as a
numbered-footnote negative beside a genuine equity-compensation table, and page
64 as a striped financial statement. The Apple 10-K and scanned form/table
goldens exercise different publishers and raster inputs. Corpus goldens are
visually approved and are never regenerated as an oracle from detector output.

## 8. Related documents

- [`../../../value-types.md`](../../../value-types.md) - the vocabulary used by
  the value tier whose geometry often corroborates table columns.
- [`../reconcile/README.md`](../reconcile/README.md) - the main analytical
  consumer of the sister engine's statement blocks.
- [`../financial-table/README.md`](../financial-table/README.md) - private
  financial interpretation layered over this engine's public, conservative
  geometry.
- [`../../architecture.md`](../../architecture.md) - runtime and contract
  boundaries shared by all engines.
