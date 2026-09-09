# Reconcile engine

| | |
| --- | --- |
| **Source** | `src/python/engines/reconcile/` |
| **Contract** | `contracts/reconcile-v1.json` |
| **Entry point** | `engines/reconcile/detector.py` |
| **Consumes** | The Reconcile-owned PDF version, its freshly produced artifacts, and private `FinancialTableScan` blocks |
| **Tests** | `src/python/tests/test_reconcile.py`; fixtures in `src/python/tests/fixtures/reconcile/` |

**Modules**

`detector.py`, `findings.py`, `nominate.py`, `structures.py`, `sums.py`

Reconcile and its financial-table input engine are on-demand; neither runs in
the cache build.

## 1. Purpose

Reconcile proves arithmetically whether totals printed in financial-statement
blocks equal the addends the document presents. It owns nomination, run search,
proof, findings, and the durable `reconcile-v1` envelope; it no longer owns table
recognition or value-to-cell membership.

## 2. Inputs and outputs

`detect_reconcile` invokes `engines.financial_table.detect_financial_tables`
after the same Reconcile worker job produces geometry, table, value and financial
models from the independent PDF snapshot. It consumes the returned
page-local blocks in page and reading order, evaluates every nominated total,
and publishes `reconcile-v1`.

The contract carries every examined block, not only exceptions. Every nominated
total has exactly one outcome--confirmed, break, or unresolved--and only findings
that can be restated as evidence-backed arithmetic are spoken. The source
records Reconcile document/version identity, geometry, and artifact versions for
currentness against that same snapshot; the Reconcile
detector version also includes the financial-table version because the sister is
a private implementation dependency rather than a separately stored artifact.

One dedicated workbook Custom XML part owns the named project. Setup fills the
`primary` slot with the current statement and `comparison-1` with the prior-year
statement; `comparison-2` remains reserved for another prior period. Each slot
has a document identity and one current version identity. Importing or copying
creates new identities and duplicates bytes rather than retaining an
ordinary-document reference. Completing setup scans the primary only. A
successful scan atomically replaces that version's analysis artifacts and
result. Failure or cancellation performs no storage write, so an earlier
successful result remains intact. Comparison analysis and version-history UI
are intentionally not implemented.

## 3. Types

- **Nomination** - structural evidence that a printed cell asserts a total;
  arithmetic is never allowed to create a nomination.
- **Run** - an ordered contiguous set of eligible addend cells above or beside a
  nominated total.
- **Confirmed** - an independently nominated total for which a permitted run
  ties exactly.
- **Break** - an independently nominated total with a plausible run whose sum
  differs from the printed total.
- **Unresolved** - a nominated total for which no permitted run supports a
  conclusion.
- **Finding** - a human-checkable sentence plus the total and addend spans that
  establish it.
- **Corroborated structure** - the same row relationship proven independently
  in parallel value columns.
- **Grid fallback** - a total reached through a general-grid cell view when that
  result is novel or stronger than the lattice result for the same span.

## 4. Algorithm

1. The financial-table sister engine builds lattice and grid-fallback blocks.
2. `structures.py` discovers row relationships by leave-one-out agreement across
   parallel columns. One column cannot supply evidence for its own conclusion.
3. `nominate.py` adds explicit label, total-column, ruling-above, and
   double-rule-below nominations.
4. `sums.py` walks permitted contiguous runs, protects already established
   subtotal blocks from double counting, preserves printed decimal specificity,
   and evaluates bounded sign-reversal variants.
5. `detector.py` repeats column resolution while newly confirmed subtotals add
   safe block boundaries, then applies the narrow cross-footing pass.
6. Lattice and grid totals are deduplicated by value span and axis. A grid result
   replaces only a weaker unresolved lattice duplicate.
7. `findings.py` publishes confirmed evidence silently and turns supported
   breaks or selected unresolved cases into reviewer-checkable sentences.

### 4.1 Substrate: the alignment lattice

The sum tree stands on the value-alignment lattice supplied by
`engines/financial_table/`; `table-structure-v1` is corroborating geometry, not a
prerequisite. `reconcile-v1` retains the historical `tables` property name, but
its entries are the statement blocks arithmetic actually examined. See the
[financial-table engine record](../financial-table/README.md) for construction,
tuning, and failure modes.

### 4.2 Why the substrate is not the detected grid

Three measured facts are why Reconcile consumes the sister's lattice rather than
depending on the published grid:

- **The cost was not small.** On the Apple 10-K, **157 of 1,147 number spans —
  14% — sit in no detected table cell**, including page 28, where 17 of 20
  numbers are invisible. Under a grid substrate that is arithmetic recall lost
  for reasons that have nothing to do with arithmetic, and a table-detection
  regression becomes an arithmetic regression.
- **The sum tree never needed a table.** It needs to know which figures line up
  vertically, which line up horizontally, in what order, and where a block ends.
  *A table exists here* is a strictly stronger claim than anything the
  arithmetic consumes.
- **The lattice and corroboration are load-bearing on each other.** A lattice is
  a looser substrate than a grid, and under label-based nomination that looseness
  would be reckless. Under parallel-column corroboration it costs almost nothing,
  because a wrongly-grouped column cannot tie in two independent columns — which
  is why the prototype produced zero false misses even at triple tolerance. The
  lattice without corroboration is less safe than the grid was; corroboration
  without the lattice leaves 14% of the document unreachable. Neither is sound
  alone. See [`corroboration.md`](corroboration.md).

The union policy remains: a cached grid contributes a fallback under the same
corroboration rules, and span identity removes duplicates. The ownership change
does not change that measured behavior; it prevents arithmetic orchestration
from becoming a second table detector.

## 5. Tuning and thresholds

Financial-table geometry thresholds, including the measured `0.005` right-edge
plateau, now live in the [sister engine record](../financial-table/README.md) §5.

**`sums.SKIP_CAPTION_ROWS`.** Stepping over caption rows was measured and
rejected: it buys 8 confirmations at the cost of 6 false breaks.

## 6. Failure modes

**Two structural models must stay coherent.** The lattice and the cached grid are
reconciled at scan time by code with no prior art in the repo. The
substrate-disagreement counter (§4.1) is the instrument for catching a drift
between them.

**Coincidental arithmetic exists.** A number may equal an arbitrary combination
of neighbours by chance. Structure must nominate before arithmetic runs, short
runs need corroboration, and sign reversal is bounded; loosening any of those
guards can turn recall gains into confident false ties.

**A fragment may confirm but not accuse.** When the financial-table input cannot
prove the full top of a block, an exact tie is still evidence but a mismatch is
an unresolved scan limit rather than a break in the document.

## 7. Tests and corpus

`test_reconcile.py` pins nomination independence, parallel-column
corroboration, subtotal block protection, decimal specificity, bounded sign
reversal, grid/lattice deduplication, findings, progress stages, and contract
validation. Candidate real-document goldens are never accepted automatically;
the whole-document scorer's hard bar is zero false ties.

The Apple scorer currently completes with 238 confirmations, zero breaks, and
55 unresolved totals. The ownership controls bump the composed version to
`reconcile-detector-4+financial-table-detector-2` without changing arithmetic
policy. That is a regression
observation rather than a hand-approved correctness golden.

## 8. Related documents

- [`sum-tree.md`](sum-tree.md) — the arithmetic layer: what is added, what is eligible, and what a tie is allowed to conclude. Governs its subject matter.
- [`corroboration.md`](corroboration.md) — nomination by agreement across parallel columns.
- [`../financial-table/README.md`](../financial-table/README.md) — statement
  block recognition upstream of arithmetic.
- [`../../architecture.md`](../../architecture.md) — scope tiers, the two-runtime line, and where Reconcile sits against the three cache-build engines.
- [`../../architecture.md`](../../architecture.md) §4 — why this engine runs on demand rather than in the cache build.
