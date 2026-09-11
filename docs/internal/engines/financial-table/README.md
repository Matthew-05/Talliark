# Financial-table engine

| | |
| --- | --- |
| **Source** | `src/python/engines/financial_table/` |
| **Contract** | None; private Python analysis types consumed by Reconcile |
| **Entry point** | `engines/financial_table/detector.py` |
| **Consumes** | `text-geometry-v1`, `document-values-v1`, `table-structure-v1` |
| **Tests** | `src/python/tests/test_financial_table.py`, `test_table_engine_drift.py`, `test_reconcile.py` |

This sister engine runs only during an explicit Reconcile scan. It does not run
in the cache build and does not publish a second table model.

## 1. Purpose

The financial-table engine interprets a conservative general table scan as the
statement-oriented blocks needed for arithmetic. It may recover an alignment
lattice where no general table was published, but that private inference never
changes `table-structure-v1` or becomes visible to general table consumers.

The boundary is semantic rather than historical. Geometry rules useful on any
document--wrapped cells, repeated headers, marker attachment, table-versus-prose
scoring--stay in `engines/table/`. Period-column meaning, accounting dashes,
subtotal rulings, and value-to-statement-cell membership live here.

### 1.1 Ownership boundary

The rule in one sentence is:

> The general engine describes what the page visibly arranges; the
> financial-table engine interprets financial conventions; Reconcile decides
> what the arithmetic proves.

| Concern | General `table` scan | `financial_table` sister | Reconcile |
| --- | --- | --- | --- |
| Runtime | Experimental ordinary OCR, and always inside a Reconcile scan | Explicit Reconcile scan only | Explicit Reconcile scan only |
| Evidence | Text/rule/image geometry and primitive token shapes | General tables, values, geometry, financial conventions | Financial cells, independent structural signals, decimal arithmetic |
| Regions | Decide whether a visible region is a table and publish its bounds | Recover private statement blocks even when the general scan declined or fragmented them | Never decide that a table exists |
| Rows and columns | Fit generic rows, columns, wrapped cells, section rows, and repeated headers | Align financial values by row centre and last-digit edge; join multi-panel label columns | Walk nominated rows and columns as candidate sum runs |
| Numbers | Keep a currency/sign/percent marker physically attached to its number | Interpret an accounting dash as zero and preserve printed decimal specificity | Add values, test bounded sign conventions, and diagnose differences |
| Headers | Detect and transcribe header bands, groups, dates, and repeated schemas | Interpret fiscal periods, total columns, rates, averages, margins, and other non-additive measures | Use those meanings to constrain nomination and cross-footing |
| Rulings | Detect lines and publish their geometry without assigning accounting meaning | Associate single/double accounting rules with statement rows and suppress that reading on fully ruled grids | Decide whether a rule is sufficient independent evidence to nominate a total |
| Confidence | Accept or reject a general table for all consumers | Record lattice/grid provenance and disagreements; never rewrite general confidence | Publish confirmed, break, or unresolved arithmetic outcomes |
| Durable output | `table-structure-v1` | None; private `FinancialTableScan` | `reconcile-v1` |

Concrete boundary examples:

- Keeping `$` and `1,234` inside the same visible cell is **general**. Treating
  `—` in that value column as the number zero is **financial-table**.
- Detecting `2025` as text in a header is **general**. Deciding it is one column
  of a comparative fiscal statement is **financial-table**.
- Detecting an underline is **general**. Associating it with the row above or
  below under accounting presentation conventions is **financial-table**.
- Recognizing a repeated header and splitting two visible tables is **general**.
  Reassembling partial statement blocks privately for footing is
  **financial-table**.
- Saying that three rows occupy aligned cells is recognition. Saying they sum to
  a printed total is **Reconcile**.

The dependency direction is enforced: `table` imports neither
`financial_table` nor `reconcile`, and `financial_table` imports no Reconcile
module. Reconcile may depend on both upstream engines. The financial-table
package reads general internals only through `engines.table.handoff`; importing
`table.layout` or `table.grid` directly is a test failure.

When ownership is uncertain, start the rule in the private financial-table
engine. Promote it to the general engine only when it can be stated without a
financial lexicon, document classification, issuer/form name, or arithmetic,
and when non-financial fixtures demonstrate that the behavior is desirable
there too. A rule that needs addition belongs in Reconcile, never in either
recognizer.

## 2. Inputs and outputs

`detect_financial_tables` reads the complete text geometry and values models plus
the cached general table model. The general model is read-only corroboration and
may be absent or truncated. `table.handoff` rebuilds the page layout and freezes
a recursive copy of every accepted table before this engine sees it. The source
artifact's content digest is checked again after detection.

The result is a versioned `FinancialTableScan`, a private in-memory object
containing one `FinancialTablePage` per geometry page. Each page carries:

- `lattice_blocks`: statement blocks inferred from aligned value spans;
- `composed_lattice_fallbacks`: fragments privately reassembled where one
  accepted general table independently says they belong to the same statement;
- `grid_fallbacks`: cell joins over the general tables published on that page;
- `disagreements`: structured cases where lattice numeric alignments fall
  outside a corroborating general grid, one general value column has no lattice
  alignment, or one general column contains multiple lattice alignments.

No cross-runtime consumer may depend on these types. Reconcile publishes the
durable result under `reconcile-v1` after arithmetic evaluates the blocks.
Worker diagnostics record the engine version and the lattice/grid block counts
so recognition changes can be measured separately from arithmetic outcomes.

## 3. Types

- **Financial table scan** - every private statement-table view built for one
  Reconcile invocation.
- **Financial table page** - the ordered lattice and grid views for one page.
- **Lattice block** - rows clustered by vertical centre and numeric columns
  clustered by the right edge of their last digit.
- **Composed lattice fallback** - two or more lattice fragments mapped to one
  general table and rebuilt as one arithmetic view. It is exact-only and must
  be corroborated across independent value columns before Reconcile admits it.
- **Grid fallback** - financial cells obtained by intersecting a published
  general grid with text and value geometry.
- **Financial cell** - printed cell text plus optional normalized number,
  statement row label, span id, and ruling semantics.
- **Header semantics** - whether a printed column header denotes a period, a
  total, or a non-additive measure.
- **Provenance** - `lattice`, `lattice+table`, or `table`, identifying which
  structural evidence produced the block.
- **General lineage** - source general table id and a digest over its bounds,
  columns, rows, header and rulings. Every grid fallback carries both; a
  corroborated lattice carries them when it borrows general evidence.
- **Boundary disagreement** - a typed review record describing two different
  numeric-column partitions. It is diagnostic evidence, never permission to
  rewrite either partition.

## 4. Algorithm

1. `table.handoff` builds a fresh document-neutral `PageLayout`, recursively
   freezes copied general tables, and records the source artifact digest.
2. `detector.py` indexes values by page and consumes only that supported
   handoff.
3. `lattice.py` clusters number spans by row centre, cuts vertical blocks at
   pitch/prose fences, and clusters numeric columns by last-digit right edge.
4. A general table overlapping at least 25% of the lattice block lends printed
   header labels, bounds, and horizontal rulings. It is corroboration, never a
   prerequisite.
5. One-row and multi-row value fragments mapped to the same general table are
   composed into a fallback view. A one-row fragment must contain at least two
   figures; a fragment immediately above or below the detected body may join
   only within the row/line-height adjacency window. Reconcile admits only
   exact totals whose row/sign structure also agrees in independent columns, so
   removing a caption boundary can recover proof but cannot create a break or
   enlarge the Not checked list.
5b. Adjacent detected tables that a statement split in two -- a balance sheet
   the general detector closed at *Commitments and contingencies* or
   *Stockholders' equity:* -- are additionally merged into one statement region
   and rebuilt whole. The region carries no general-table lineage, publishes as
   `lattice` provenance, and is admitted under the same exact-only composed
   rule, so it can prove the grand total whose addends straddle the split
   without changing the general grid or the ordinary blocks.
6. `cells.py` recovers accounting dashes omitted by the values model, joins
   values to rows and columns, chooses the nearest label column to the left, and
   marks single rules above and double rules below candidate subtotal rows.
7. Every published general table is independently converted to a grid fallback.
   Reconcile later admits only fallback totals that are novel or stronger than
   their lattice duplicate.
8. Source ids, geometry digests, bounds, row counts and column counts are
   checked against every fallback. Lattice provenance is checked against its
   source lineage, and the original general artifact digest must be unchanged.
9. Numeric-column partition disagreements are recorded in detail. Neither view
   is selected as the universal winner.
10. `labels.py` interprets the printed labels without changing them. `Net` is a
    subtractive result column, while useful-life, rate, average, percentage and
    per-unit columns are non-additive metadata. It also identifies opening
    states, carried results, equity movements, allocation components, and the
    narrow Gross result phrases used by Reconcile's unresolved admission.
    Arithmetic nomination and proof remain downstream in `engines/reconcile/`.

## 5. Tuning and thresholds

| Setting | Value | Reason and effect |
| --- | ---: | --- |
| Right-edge tolerance | `0.005` | Plateau begins here on the Apple sweep; smaller values lose negative numbers whose closing parenthesis extends past the last digit. |
| Minimum block rows | `3` | Two isolated aligned figures are not enough structure for a footing block. |
| Composed fragments | at least `2` fragments and `3` total rows | Composition repairs a split statement rather than replacing ordinary block detection. |
| One-row composed fragment | at least `2` figures | Recovers a wrapped opening row without treating isolated page furniture as a footing block. |
| Composed adjacency | `max(row_gap, 2 × line_height)` | Allows a first data row classified into a header band to retain its table lineage while keeping unrelated nearby schedules separate. |
| Statement-region merge | `max(3 × row_gap, 8 × line_height)`, and `50%` horizontal overlap | Rejoins two detected tables a statement split at a caption band (the balance sheet's two sides) without merging schedules set further apart or side by side. |
| Maximum row gap | `max(row_gap, pitch × 2.35)` | Separates vertically independent schedules while retaining ordinary statement spacing. |
| General-table overlap | `25%` of lattice area | Enough shared area to borrow headers and rulings without requiring equal bounds. |
| Rule-grid suppression | rules above more than `50%` of rows | A fully ruled grid does not use a rule above as a subtotal signal. |
| Double-rule gap | `0.0004` to `0.006` | Distinguishes one accounting double rule from a thick stroke or unrelated rules. |
| Rule reach | `1.25 ×` row text height | Associates a rule with its printed row without lending it across the next row. |

## 6. Failure modes

- Right-edge clustering assumes figures are right aligned. Centred numeric
  columns can split or merge incorrectly; the engine then fails primarily by
  losing recall because Reconcile still requires independent structural evidence.
- A page with values but no recognized `number` spans cannot form a lattice.
- General-table header mistakes can lend incorrect column labels, though values
  and arithmetic do not depend on those labels for membership.
- Lattice cells and the block's parallel `row_labels` array are populated from
  the same nearest-left printed label. Statement-wide interpretation reads the
  array, while evidence quotes the cell; a regression that lets them diverge is
  pinned directly by `test_financial_table.py`.
- Closely stacked schedules with no prose or pitch fence can form one lattice
  block. Caption rows constrain what Reconcile may accuse after such a merge.
- Captions and wrapped first rows can split one visible statement into several
  lattice fragments. Composition repairs only fragments with the same general
  table lineage, and its exact-plus-parallel-column admission rule intentionally
  leaves single-column cases unresolved.
- Grid and lattice views can disagree. The detector counts those disagreements
  and retains their page, table, block, column band and lattice edges rather
  than silently choosing one as universally authoritative.
- A programming error that mutates the public model, loses one-to-one fallback
  lineage, changes copied grid geometry, or mislabels provenance fails the scan
  closed with `FinancialTableInvariantError`.

## 7. Tests and corpus

`test_financial_table.py` verifies byte-for-byte source stability, recursive
handoff immutability, exact grid-fallback lineage and geometry, structured
disagreement reporting, recovery without general tables, and the one-way import
boundary. `test_table_engine_drift.py` pins the report policy: metric changes
are visible but only ownership-invariant failures fail the report.
`test_reconcile.py` exercises cell joins, accounting dashes, label columns,
period and total semantics, ruling interpretation, lattice-only refusal, and the
union/deduplication policy.

`scripts/audit_table_engine_drift.py` scans supplied PDFs, or the whole local
financial-statement corpus by default. It records counts, provenance, unmatched
views and structured disagreements and can show deltas from `--baseline`.
Count deltas do not fail the command because neither engine is an oracle for the
other. General table goldens and Reconcile's approved arithmetic goldens remain
the correctness gates; the drift report is the cross-engine review surface.

The Apple whole-document scorer is the current real-statement regression sweep
and must retain zero breaks while this engine changes. Layout-family fixtures,
not issuer phrases, are the unit-test boundary. A general-engine promotion also
requires a non-financial positive or negative fixture showing that the same
visual rule is desirable outside statements.

## 8. Related documents

- [`../table/README.md`](../table/README.md) - conservative general recognition
  and the public table artifact.
- [`../reconcile/README.md`](../reconcile/README.md) - arithmetic consumer of the
  private blocks.
- [`../reconcile/sum-tree.md`](../reconcile/sum-tree.md) - proof and refusal
  rules downstream of recognition.
- [`../../architecture.md`](../../architecture.md) - cache-build and on-demand
  runtime boundary.
