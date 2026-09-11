# Reconcile engine

| | |
| --- | --- |
| **Source** | `src/python/engines/reconcile/` |
| **Contract** | `contracts/reconcile-v1.json` |
| **Entry point** | `engines/reconcile/detector.py` |
| **Consumes** | The Reconcile-owned PDF version, its freshly produced artifacts, and private `FinancialTableScan` blocks |
| **Tests** | `src/python/tests/test_reconcile.py`; fixtures in `src/python/tests/fixtures/reconcile/` |

**Modules**

`detector.py`, `findings.py`, `nominate.py`, `propagate.py`, `structures.py`,
`sums.py`

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
page-local blocks in page and reading order, evaluates every internal candidate,
and publishes the candidates that survive the statement-aware admission policy
as `reconcile-v1`.

The contract carries every examined block, not only exceptions. Every admitted
total has exactly one outcome--confirmed, break, or unresolved--and only findings
that can be restated as evidence-backed arithmetic are spoken. Geometry-only
recognizer probes and rows that financial-statement context identifies as an
opening state, carried result, allocation, movement, or non-additive measure are
kept in diagnostics when unresolved rather than presented as Not checked. The source
records Reconcile document/version identity, geometry, and artifact versions for
currentness against that same snapshot; the Reconcile
detector version also includes the financial-table version because the sister is
a private implementation dependency rather than a separately stored artifact.

One dedicated workbook Custom XML part owns the named project. The `primary`
slot holds the current statement and is the only versioned slot: it carries a
document identity and one current version identity, and a scan's geometry,
table, value, financial and Reconcile models are stored against that version,
with currentness checked against its detector versions and geometry. Each
comparison slot (`comparison-1`, and `comparison-2` reserved for another prior
period) holds an unversioned prior-period snapshot, and re-adding a statement
for a period replaces that slot's snapshot wholesale rather than creating a
version. A comparison can be scanned like the primary: the same OCR analysis
job stores the same five artifacts on the slot, and because the slot is
unversioned the stored scan is current from the instant it lands — there is
nothing for it to go stale against. Importing or copying duplicates bytes
rather than retaining an ordinary-document reference. Completing setup scans
the primary only; a comparison is scanned on demand from its row in the home
list, and replacing a scanned comparison warns that its stored result will be
replaced with it. A
successful scan atomically replaces that slot's analysis artifacts and
result. Failure or cancellation performs no storage write, so an earlier
successful result remains intact. Version-history UI is intentionally not
implemented.

## 3. Types

- **Internal candidate** - a cell proposed by structural recognition for
  arithmetic evaluation. Arithmetic is never allowed to create one.
- **Admitted total** - a candidate credible enough to enter the durable,
  user-facing tree after its arithmetic outcome and statement context are known.
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
- **Composed lattice fallback** - fragments assigned to one general table and
  reassembled privately; only exact, independently parallel-supported results
  are eligible, so the broader view contributes neither breaks nor unresolved
  totals.

## 4. Algorithm

1. The financial-table sister engine builds ordinary lattice blocks, composed
   lattice fallbacks, and grid fallbacks.
2. `structures.py` discovers row relationships by leave-one-out agreement across
   parallel columns. Independence is computed from the perspective of the cell
   being judged, so a proportional column cannot borrow the representative it
   was collapsed into as corroboration. Zero and dash positions do not create a
   different signed-row pattern.
3. `nominate.py` adds explicit label, total-column, ruling-above, and
   double-rule-below nominations.
4. `sums.py` walks permitted contiguous runs twice -- bounded, stopping at the
   first blank or caption, and then crossing every gap inside the block -- and
   collapses any subtotal a run holds together with its own addends. It protects
   already established subtotal blocks from double counting, preserves printed
   decimal specificity, and evaluates bounded sign-reversal variants. A crossing
   or collapsed reading may confirm and may never accuse.
5. `detector.py` repeats column resolution while newly confirmed subtotals add
   safe block boundaries, with a row-count-derived convergence guard rather than
   a fixed nesting-depth ceiling. Each pass, `propagate.py` spends the structure
   a confirmed total proved: the same row is re-tried in the columns that did
   not resolve, and an unresolved total standing in a confirmed total's addend
   row may use the corroborated floor. It then applies the narrow cross-footing
   pass.
6. A post-arithmetic admission pass keeps every confirmation and break. An
   unresolved candidate remains only when the document itself credibly asserts
   a footing relationship; rule-only recognizer probes, opening states, carried
   results, peer cash-flow summaries, equity movements, allocation components,
   component uses of Net, and high-confidence non-additive columns remain
   diagnostic.
7. Composed lattice results are admitted only when they tie exactly and the same
   row/sign structure ties in at least two non-proportional columns. They never
   enlarge Not checked. Lattice, composed, and grid totals are then deduplicated
   by value span and axis. Exact confirmation outranks a break, a break outranks
   unresolved, and substrate order settles equal outcomes.
8. `findings.py` publishes confirmed evidence silently and turns supported
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

**`structures.MAX_HYPOTHESES_PER_BLOCK`.** The parallel-column search examines
at most 5,000 row spans per block. It visits a given span length across the
whole block before trying longer spans, so the cap reduces maximum look-back
evenly instead of making later totals unreachable. Diagnostics publish the
number of truncated blocks; all eight current corpus documents report zero.

**`nominate.MIN_REPEATED_CROSS_ROWS = 2`.** A direct two-addend cross-foot is
published only when another row repeats the same result column, addend columns,
and reversed-sign positions. Financial statements commonly print two-column
Total and gross-less-accumulated Net schedules; an isolated pair remains too
coincidental to publish.

**Unresolved admission is deliberately asymmetric.** `detector.py` applies it
only after arithmetic has returned `unresolved`; a confirmed total or supported
break cannot be hidden by label semantics. A label is interpreted from the
whole financial block, not from an issuer, form, page number, or fixture. The
diagnostics report both the total withheld count and counts by reason.

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
an unresolved scan limit rather than a break in the document. Caption, blank,
non-value, mixed-decimal, and unresolved-subtotal stops are all incomplete
boundaries for this purpose.

**A confirmed subtotal owns its whole interval.** A parent candidate may consume
the subtotal or its component rows, never the subtotal together with even part
of that interval. Arithmetic-only duplicate detection cannot see the partial
case; the established block top supplies the structural boundary.

**Nested ownership propagates to the deepest leaf.** When a parent consumes a
confirmed subtotal, its own top is the top of that subtotal's established block,
not merely the subtotal row. This keeps a balance-sheet grand total from mixing
a nested net asset with part of the asset block it already owns.

**The two sides of a balance sheet are separate blocks.** `Total assets` is a
hard statement-semantic boundary before `Total liabilities and equity` (and the
stockholders'-equity spelling). The equality of those two grand totals is the
accounting equation, not permission to add Total assets into the liability side.

**Not checked is an admitted assertion, not a recognizer trace.** A single rule
may be worth testing because an exact tie can confirm it, but a rule-only miss
does not mean the row should foot. Opening balances seed rollforwards; Net income
is carried into cash flow, comprehensive-income, EPS, and equity schedules;
period movements are components of equity; and rate, average, useful-life,
per-share, and similar columns are non-additive. Publishing those misses made
the user review the recognizer rather than the statement. They remain counted
by diagnostics so loss of recognition is still visible to development tooling.

## 7. Tests and corpus

`test_reconcile.py` has 101 tests pinning nomination independence, relative
column independence, zero-insensitive signed patterns, subtotal block
protection and deepest-top propagation, balance-sheet side boundaries,
incomplete-run safety, printed-rounding classification, bounded sign reversal,
repeated two-addend cross-foots, unresolved admission, composed/grid/lattice
precedence, findings, progress stages, and contract validation.
Candidate real-document goldens are never accepted automatically; the
whole-document scorer's hard bar is zero false ties.

The eight-document corpus currently publishes 2,188 totals: 2,034 confirmed,
two breaks, and 152 Not checked. Before statement-aware unresolved admission it
published 2,978 totals with 1,081 Not checked; the crossing walk, the subtotal
reduction, the statement-region merge, relative-offset parallel corroboration,
and verified-structure propagation have since moved recall from 1,887 to 2,034
confirmations without adding a break. Apple publishes 249 confirmations and
nothing Not checked; Quest two, Disney three, while retaining 141 and 694. The
two Amazon breaks remain. Every newly confirmed total was read against the
printed page; the sampled set — the two commercial-paper subtotals, the
fair-value hierarchy totals, the reclassification total, the Amazon
balance-sheet grand total, and the RoyCarver conversion total — is
arithmetically correct. The admission pass itself cannot reduce confirmed or
break outcomes. No structure block reaches the hypothesis cap. The version is
`reconcile-detector-9+financial-table-detector-5`. These are regression
observations rather than hand-approved correctness goldens: the corpus still has
no approved golden, and building one is the next phase.

The recall gain was measured with the null instrument (`sums`/crossing added
about 0.8 points to the one-seed five-document false-tie rate, 11.66% to
12.48%), which is why the zero-false-tie bar is now enforced by hand-approved
goldens rather than by the null rate alone.

## 8. Related documents

- [`sum-tree.md`](sum-tree.md) — the arithmetic layer: what is added, what is eligible, and what a tie is allowed to conclude. Governs its subject matter.
- [`corroboration.md`](corroboration.md) — nomination by agreement across parallel columns.
- [`../financial-table/README.md`](../financial-table/README.md) — statement
  block recognition upstream of arithmetic.
- [`../../architecture.md`](../../architecture.md) — scope tiers, the two-runtime line, and where Reconcile sits against the three cache-build engines.
- [`../../architecture.md`](../../architecture.md) §4 — why this engine runs on demand rather than in the cache build.
