# Reconcile engine

> **Partly written.** §4.1, §4.2, §5 and §6 carry the substrate decision, folded
> in from ADR-0001 when it landed. The remaining sections are still the fixed
> eight-section skeleton every engine doc in this folder follows.

| | |
| --- | --- |
| **Source** | `src/python/engines/reconcile/` |
| **Contract** | `contracts/reconcile-v1.json` |
| **Entry point** | `engines/reconcile/detector.py` |
| **Consumes** | `table-structure-v1`, `document-values-v1`, `text-geometry-v1` |
| **Tests** | `src/python/tests/test_reconcile.py`; fixtures in `src/python/tests/fixtures/reconcile/` |

**Modules**

`cells.py`, `detector.py`, `findings.py`, `labels.py`, `lattice.py`, `nominate.py`, `structures.py`, `sums.py`

The only one of the four engines that does not run in the cache build — it is on-demand.

## 1. Purpose

*What this engine is for, and the one job it owns that no sibling engine does.
Two or three sentences. If a reader only reads this section, they should know
when to reach for this engine and when not to.*

## 2. Inputs and outputs

*What comes in, what goes out, and which contract governs the boundary. Name the
contract fields that matter and say what the engine guarantees about them —
ordering, completeness, coordinate space, units. Anything a downstream engine is
allowed to rely on belongs here; anything it is not allowed to rely on belongs
here too, stated as such.*

## 3. Types

*The vocabulary. Every kind of thing the engine names — the categories, the
tags, the states — with the one-line definition that distinguishes each from its
neighbours. This is the section a reader comes back to; keep the definitions
sharp enough to settle an argument about which bucket a case falls in.*

## 4. Algorithm

*How it actually works, stage by stage, in the order the code runs. Say what each
stage decides and on what evidence. Where a stage rejects a candidate, say what
would have had to be true for it to pass. Prefer the shape of the reasoning over
a line-by-line narration of the source.*

### 4.1 Substrate: the alignment lattice

**The sum tree stands on an alignment lattice derived from value bounding boxes
and text geometry. `table-structure-v1` is a corroborating input, never a
dependency.**

The lattice is built per page, not per table:

- **Columns** are clusters of value **right edges**. A financial statement
  right-aligns its figures to the units place, which makes the right edge the
  strongest alignment fact on the page.
- **Rows** are clusters of vertical centres.
- **Blocks** end at a drawn horizontal rule (`rulings.py`), a prose line
  (`layout.py`), or a break in vertical pitch. Ordinary horizontal rules were
  measured and rejected as *hard* fences: they conventionally sit between the
  addends and the total, and would sever the relationship being tested.
- **Row labels** are the text on a band, left of the leftmost value column.
- **Dashes** are found as glyphs inside a (column × band) rectangle, because a
  dash is an addend worth zero and does not appear in `document-values-v1`.

Where a detected table covers the same region it is still read — for its header
row count and header labels, its confident boundaries on ruled tables, and its
id and bounds for a finding to anchor to and the window to draw. It is never
required.

**What shipped is the union.** The bake-off measured pure lattice at 151
confirmations against the prototype union's 159, so the cached grid remains as a
fallback substrate under the *identical* corroboration policy. What was removed
is the private unbudgeted re-detection the scan used to perform. A
substrate-disagreement counter in the scan diagnostics records where the lattice
and a confident grid disagree about a column boundary, because one of them is
then wrong and it is worth knowing which.

`reconcile-v1`'s `tables` property name survives for the existing storage and
viewer envelope, but its entries are value-alignment blocks with inferred bounds
and a provenance field, not detected tables.

### 4.2 Why the substrate is not the detected grid

Three facts, all measured, and they are the reason the dependency was severed:

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

Consequences that followed: the two-panel balance sheet, the caption row and the
merged row stopped being special cases, because none of them exists in a lattice;
cross-footing became a transpose over a substrate that needs no header rule to
target a total column; and every number measured on the grid — 159 confirmations,
95% detection — had to be re-measured. The goldens survived, because they are
keyed by page, column header, row label and printed value rather than by grid
indices.

## 5. Tuning and thresholds

*Every constant that could have been a different number, where it lives, and what
moves if it changes. For each, the measurement or the case that set it — a
threshold with no recorded reason is a threshold nobody can safely touch.*

**Right-edge clustering tolerance — 0.005.** The parameter has a plateau and it
fails in the safe direction. Sweeping it on the Apple 10-K:

| tolerance | 0.002 | 0.0035 | **0.005** | 0.007 | 0.010 | 0.015 |
|---|---|---|---|---|---|---|
| structures | 29 | 35 | **47** | 47 | 47 | 47 |
| false misses | 0 | 0 | **0** | 0 | 0 | 0 |

Three times the sensible tolerance changes nothing and accuses nobody. Below the
plateau the failure is under-clustering — lost recall, never a false finding.
The degradation below 0.005 has a known cause: `(1,234)` puts a closing
parenthesis past the last digit, so a negative sits about one character right of
the positives in its column. Clustering on the right edge of the last *digit*
removes it.

**`sums.SKIP_CAPTION_ROWS`.** Stepping over caption rows was measured and
rejected: it buys 8 confirmations at the cost of 6 false breaks.

## 6. Failure modes

*What this engine gets wrong, and how it fails when it does. Distinguish the
cases it is designed to decline (and how a caller can tell) from the cases where
it produces a confident wrong answer. Record real observed failures from the
corpus, with the document that produced them.*

**Right-edge clustering assumes right alignment.** It holds across the whole
corpus and should be expected to fail on a centred or decimal-tab layout. This is
the substrate's one structural assumption; nothing downstream re-checks it.

**Two structural models must stay coherent.** The lattice and the cached grid are
reconciled at scan time by code with no prior art in the repo. The
substrate-disagreement counter (§4.1) is the instrument for catching a drift
between them.

## 7. Tests and corpus

*What the tests cover, what the fixtures are, and the bar the engine is held to.
Note anything the corpus exercises that the unit tests do not.*

## 8. Related documents

- [`sum-tree.md`](sum-tree.md) — the arithmetic layer: what is added, what is eligible, and what a tie is allowed to conclude. Governs its subject matter.
- [`corroboration.md`](corroboration.md) — nomination by agreement across parallel columns.
- [`../../architecture.md`](../../architecture.md) — scope tiers, the two-runtime line, and where Reconcile sits against the three cache-build engines.
- [`../../architecture.md`](../../architecture.md) §4 — why this engine runs on demand rather than in the cache build.
