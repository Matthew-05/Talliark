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

## 2. Inputs and outputs

The detector reads character geometry from `text-geometry-v1` and inspects the PDF
page for vector and raster ruling evidence. Coordinates are normalized to the
displayed page after rotation. Every output page preserves its zero-based
`pageIndex`; tables are ordered top-to-bottom and left-to-right and conform to
`table-structure-v1`.

Published row and column bands cover the full detected region. A consumer may
reconstruct cells by intersecting text geometry with those bands. Header rows are
also present in `rows` and are labelled with `kind: "header"`. A candidate below
the acceptance threshold is not published.

## 3. Types

- **Visual line** - characters sharing a baseline, tokenized into whitespace
  islands.
- **Logical row** - one anchor line plus any wrapped continuation lines assigned
  to the same record.
- **Candidate** - a generous possible table region supported by whitespace,
  rulings, or both.
- **Grid hypothesis** - fitted column boundaries and logical rows for a candidate.
- **Section row** - a label band inside a table that carries no values.
- **Spanning label** - a leading band that qualifies several columns and is stored
  as a caption rather than a table row.
- **Graphic** - chart-like vector area, curve or diagonal evidence. Page canvas,
  white panels, row shading and embedded raster images are not graphics merely by
  occupying area; a raster image becomes graphic evidence only when it contains
  a sustained diagonal plotted series.

## 4. Algorithm

1. `layout.py` converts characters into classified tokens, visual lines and page
   body metrics, and marks running prose.
2. `rulings.py` extracts vector and raster rules with their real extents. It
   ignores near-page background rectangles and white fills. Raster image coverage
   remains neutral; a small Hough-style vote records only long diagonal series as
   chart evidence.
3. `candidates.py` proposes independent ruled and whitespace regions and combines
   evidence where their bounds agree.
4. `grid.py` votes for persistent whitespace corridors, builds logical rows and
   iterates until columns and rows settle. It aligns boundaries to actual cells,
   coalesces empty bands again, and after header detection removes columns that
   only a centred title occupied.
5. `refine.py` splits schema changes, repeated headers and prose interruptions;
   merges adjacent fragments only when their columns agree; strips spanning
   labels; and deduplicates overlapping proposals.
6. `headers.py` recognizes period bands, stacked titles and complete all-word
   headers from the fitted cell matrix.
7. `scoring.py` measures alignment, repetition, typing, spacing, headers, numeric
   content, rulings, graphics and negative layouts. Two parallel columns that are
   predominantly running prose are rejected even when their first line was
   inferred to be a header. A coherent ruled grid can override that ambiguity.
8. `redesign.py` publishes accepted candidates as `table-detector-3`.

## 5. Tuning and thresholds

| Setting | Value | Reason and effect |
| --- | ---: | --- |
| Acceptance confidence | `0.50` | Separates small but repeated tables from aligned lists and prose negatives. |
| Prose words per side | `4` | Requires sentence-like content in both columns; a short jurisdiction or code column remains table evidence. |
| Prose numeric allowance | `2` | Years and small counts occur inside prose and must not disable the prose test. |
| Dominant prose share | `0.60` | One short paragraph tail may differ; most rows must still exhibit parallel prose before the full penalty applies. |
| Ruled override | ruling feature `>= 0.50` | At least a small coherent set of intersections establishes cells independently of text flow. |
| Page backdrop extent | `90%` on both axes | A rectangle covering nearly the whole page is canvas rather than figure content. |
| Visually white fill | every component `>= 0.95` | A white paint operation contributes no visible graphic area. |
| Minimum graphic extent | `0.04` on both axes | Thin shading bands and furniture are not chart regions. |
| Raster series span | `60%` of image width | Text strokes do not traverse a figure; plotted series do. |
| Raster series votes | `30%` of image width at `30%` density | Preserves dashed plots while rejecting scattered text and scanned grid furniture. |
| Maximum rule thickness | `2.5 pt` | Thicker rectangles are fills rather than ruling lines. |
| Column support | `35%` of weighted anchors | Keeps minority gaps from inventing columns while allowing sparse tables. |
| Maximum ordinary crossing | `15%` | A persistent boundary may not cut through ordinary cell text. |
| Spanning-header crossing | `40%` | The first tolerant vote allows centred and spanning headers to cross provisional boundaries. |
| Grid iterations | `3` | Columns and wrapped-row membership settle within three passes on the regression corpus. |
| Refinement depth | `4` | Bounds recursive splitting while allowing stacked independent tables to separate. |
| Default document budget | `300,000 ms` | Production remains bounded; diagnostic and reconcile callers may disable the budget explicitly. |

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
analysis, post-alignment column coalescing, section-row handling and scoring.

The Quest 10-K corpus goldens record pages 3 and 4 as negative editorial layouts,
page 49 as three independent tables with centred multi-line headers, and page 64
as a striped financial statement. The Apple 10-K and scanned form/table goldens
exercise different publishers and raster inputs. Corpus goldens are visually
approved and are never regenerated as an oracle from detector output.

## 8. Related documents

- [`../../../value-types.md`](../../../value-types.md) - the vocabulary used by
  the value tier whose geometry often corroborates table columns.
- [`../reconcile/README.md`](../reconcile/README.md) - the main analytical
  consumer. Reconcile uses table structure as corroboration rather than as the
  sole arithmetic substrate.
- [`../../architecture.md`](../../architecture.md) - runtime and contract
  boundaries shared by all engines.
