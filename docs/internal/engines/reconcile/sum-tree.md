# Reconcile: the sum tree

What is added, what is eligible to be added, how a run is found, and what the
arithmetic is allowed to conclude. This is the record for that layer. What the
engine is and where it sits is [`README.md`](README.md); how a total is
nominated in the first place is [`corroboration.md`](corroboration.md), whose
parallel-column amendment is adopted here.

Sections 1 to 6 state the layer as built. Sections 7 to 9 are the rules the
corpus forced, each written after a false finding on a real statement rather
than in advance. The
signed-run and sibling-subtotal refinements were measured afterwards against
the Apple, Carver and sample-statement corpus.

## 1. What is added

The raw presented value, per R3's scope note — `normalizedValue` as the values
engine published it, exactly, as decimal arithmetic and never as a binary float.
Decimal specificity is preserved through the addition, because it is what a
near-miss diagnosis reads. A run may reverse the printed signs of a
contiguous suffix of its members when that produces an exact relationship, never
its first member (§3); `negatedAddendCellIds` records those members explicitly. This is how a statement that prints positive
sales and cost figures can assert *gross margin = sales − cost* without changing
the values model's fact about what the page printed.

The printed form is not consulted. `(1,234)` and `-1,234` are the same addend.

**Only a number is an addend.** A percentage or a date is published in the cell
layer with its text and no `normalizedValue`, so it can never enter a sum. A
column of percentages therefore reads to the run search as a column with no
values, which ends a candidate rather than contributing to it — the correct
outcome, reached without the module knowing what "per share" means. A cell
holding two spans (`2025 – 2062`, `0.03% – 5.75%`) is a range, not an addend:
its text is published and its value withheld.

## 2. What is eligible

A leaf run is a contiguous sequence of body rows in one column. A subtotal run
may combine two already-corroborated sibling subtotal rows while stepping over
the leaf blocks each subtotal owns.

- **Em dashes are zero.** A dash alone in a detected table cell is an addend
  worth zero. Simple and uniform; no rule about where in a run it may appear.
- **Blank cells break the run.** A row with no value in this column is not a
  zero, it is the end of the candidate.
- **Light filtering, not semantics.** Members of a run must agree in printed
  decimal count with each other and with the total. That is enough to keep a
  per-share figure out of a column of whole millions without knowing what "per
  share" means, and it is offered as a rule to be validated against the corpus
  rather than as a law. Deliberately primitive: heavier eligibility logic is
  overfitting before there is anything to fit to.

- **A two-addend run needs the parallel columns.** A run of three or more may be
  confirmed on `label-total` alone; a pair that happens to sum is nearly
  evidence-free. The second signal the floor waits for is the one the plan
  already names — the same rows footing independently in another value column —
  and since the walk runs per column anyway, the answers are compared before any
  of them is published (`detector._corroborate_short_runs`). Two independent
  columns tying on the same row positions, and the same reversed positions, is
  the bar. Apple's *Total comprehensive income* is net income plus total other
  comprehensive income and nothing else, with a section caption between them: the
  structure search cannot reach it, because a contiguous span containing that
  caption has a hole in it, and the label walk reaches it in all three period
  columns and was refused in each for being two addends long.

  A pair that happens *not* to sum is exactly as thin, so the rule runs both
  ways: a run shorter than three addends may confirm and may never break.

- **A column with fewer than two figures may ride, never lead.** *778 + dash =
  778* proves nothing on its own, so such a column may neither establish a row
  structure nor corroborate one. But once two independent columns have
  established it, the column is entitled to confirm on it: the evidence is the
  structure the others proved, not this column's own arithmetic. Withholding it
  leaves a row half ticked, and a reviewer cannot tell a scan limit from a real
  gap. It confirms only — a degenerate column that misses says nothing.

Printed decimal count is read from the span's own text, never from
`normalizedValue`: that is canonical decimal and strips trailing zeros, so
"1.50" arrives as "1.5" and a column of two-decimal amounts would disagree with
itself.

## 3. The search

Bounded by nomination, not by a time limit — there is no hard runtime target for
this pass. For each nominated total, the candidate runs are the contiguous
sequences ending at the row above it, bounded above by the previous nominated
total in the column, a blank, or the top of the table. Subsets are not searched;
non-contiguous combinations are not searched. Both would restore precisely the
coincidence problem R1 exists to remove.

Sign reversal never omits a row. The run stays contiguous; what the search
chooses is where the subtraction starts. **Only a suffix may be reversed, and the
first member always keeps its printed sign** — a statement writes *A less B less
C*, with the figure being subtracted from first and what comes off it after.
Reversing the first member instead asserts *−A + B + C*, a shape no statement
lays out.

This is a constraint on the search space before it is a constraint on the answer,
and that is the point. An arbitrary subset of n members is 2^n readings, and a
search that large finds an exact tie in almost any column; suffixes number n−1.
§11 measures what the difference is worth. All 55 real signed confirmations on
the corpus reverse a contiguous suffix — gross margin, operating income, net
income, net deferred tax assets, non-current borrowings, property and equipment
net — and every one of the five that reversed a first member was arithmetic
nonsense that happened to tie. `MAX_SIGN_CANDIDATES` caps how many starting
points may be tried at all; the longest real signed run is seven members.

Parallel columns corroborate only when they reverse the same row positions. A
candidate that ties by subtracting a subtotal from the members that make that
subtotal is refused as algebraic cancellation rather than accepted as a tree, and
so is one that subtracts a figure it also adds — the pair cancels, and whatever
ties is really the shorter run underneath.

**Nesting.** Where a nominated total could be confirmed either by the subtotals
between it and the previous total or by the leaf rows beneath them, the
**subtotals win** — that is the tree the statement is asserting. The leaf
resolution is recorded too, since it is the same arithmetic and costs nothing,
but the tree is built on subtotals.

**Corroborated subtotals are subtotals.** A row the structure search proved by
parallel-column agreement is a subtotal exactly as surely as a label-nominated
one, and it already knows the top of the block it consumed. Both facts are handed
to the run search, or a total standing over corroborated subtotals has no tree to
foot on: the walk would either take a subtotal's own addends a second time or run
straight through the subtotal as if it were an ordinary row. A corroborated
*break* contributes the row and no top, which is §7.2 unchanged.

**Where a run stops is sometimes two questions.** A run that consumed subtotals
has a boundary the page drew — the topmost subtotal it took — and above that lies
whatever preceded the block. Those rows may be the run's remaining addends or
they may be the statement's opening figure, which is the addend of nothing, and
Apple prints both readings on facing pages: its statement of shareholders' equity
opens with *Total shareholders' equity, beginning balances* and closes with the
ending balances its three sections make, so the opening balance must be left out;
its cash flow statement makes *Increase/(Decrease) in cash* out of the three
activity subtotals alone, while the row below is that increase **plus** the
opening cash balance. Same shape, cut in two different places.

So both readings are offered as candidates and neither is preferred. The full run
is evaluated first and the shorter one can only win by tying exactly; being a
boundary the arithmetic chose rather than one the page marked, it may confirm and
may never accuse.

**Resolution repeats while it is still learning.** A total that foots on a
subtotal cannot be settled before that subtotal is, and corroboration can only
confirm a short run after every column has been walked — by which time the totals
above it have already been resolved without it. Apple's commercial paper note is
two deep exactly this way: its second net line is confirmed by the parallel
columns, and its Total rests on that line. The pass repeats (at most
`MAX_RESOLUTION_PASSES`) and stops as soon as a pass learns nothing.

**Sibling subtotals.** Section captions may split the value lattice even though
the detected grid still covers the whole statement. Two independently
corroborated subtotal rows may therefore nominate the immediately following row
as their result; newly confirmed rows can participate in the next iteration.
This is what builds *Gross margin* from net sales and cost of sales, then
*Operating income* from gross margin and operating expenses. The sparse route
confirms exact ties only. It never publishes a break: a non-tying sibling column
may legitimately contain components outside that pair, as Apple's marketable-
securities table demonstrates.

## 4. Outcomes

Three, and the distinction between the last two is the module's credibility.

| Outcome | Meaning |
|---|---|
| **confirmed** | a candidate run sums exactly to the nominated total |
| **break** | no run sums to it, but a plausible run misses by a small delta — reported with the delta, and with the transposition (divisible by 9) and single-glyph diagnoses where they apply |
| **unresolved** | nothing plausible was found. Recorded, never phrased as a failure of the document |

A nominated total with no candidate run is *unresolved, no candidate run*. It is
not an accusation, and the window must not word it as one.

**Tolerance.** The first pass confirms on exact ties only.
`0.5 × 10^−decimals × addend count` is the intended shape, but it ships after
the drift is measured on real statements rather than before. Applying a
tolerance nobody has measured is how a rounding allowance quietly blesses a real
error.

**Which unresolved totals are spoken.** `no-candidate-run` and `run-too-short`
describe what the scan could reach, not what the document did, and the Apple
10-K yields 127 of them; surfacing each as a finding would bury the exceptions
under the scan's own limits. Only `no-plausible-run` is published as a
`footing-unresolved` finding. Every unresolved total is still counted in
`summary` and carries its own outcome, so nothing is hidden by staying quiet.

## 5. Cross-footing

In scope, secondary, and narrow: a cross-foot is attempted **only against a
column whose own header names a total** — Total, Consolidated, Combined. No
total-headed column, no cross-foot. `total-column` is that nomination and is
declared in the registry beside the others.

One guard: if two or more of a table's column headers parse as periods or years,
cross-footing is disabled for that table entirely. It is a comparative
statement, not a segment schedule, and adding 2025 to 2024 is nonsense that no
tolerance model would catch.

**The run** is the horizontal twin of the leaf run and deliberately the same
walk — a blank ends it, a dash is an addend worth zero, printed decimals must
agree — with one addition: it stops at the previous total-headed column, exactly
as the vertical walk stops at the previous nominated total. A statement of equity
prints *Total Disney Shareholders' Equity* and then *Total Equity*, and the
second is the first plus noncontrolling interests, not the first plus every
component the first already consumed.

**A cross-foot confirms and never breaks** (`sums.CROSS_FOOT_MAY_BREAK`, off).
This is a decision about evidence, not about the code. The vertical pass has two
signals — a label announcing a total, and the same row structure footing
independently in another column — and only accuses when it has them. A row has
one: the word in its own column header. Nothing corroborates it, and the corpus
says exactly what that costs. Enabling breaks produced 34 accusations across
Disney and Amazon and not one was real: Disney's statement of equity sets a share
count in its first column, so every row "misses" by the share count, and its
market-risk table cross-foots value-at-risk figures that do not add across by
construction — there is a diversification benefit and no header word that says
so.

Confirmation is worth having on its own. An exact tie across six segment columns
corroborates every figure in the row, and it is what ticks the row totals of a
segment schedule that the vertical pass can only ever tick down. A miss becomes
`no-plausible-run`: counted, never spoken.

**A cross-foot is a second assertion about the same figure, not a duplicate of
the first.** A segment schedule's grand total is both the total of its column and
the total of its row, and deduplication keys on the axis as well as the span so
that whichever arrives first does not delete the other.

## 6. Corroboration by rulings

Treated as core, not decoration. The accounting convention is a single rule
above a total and a double rule beneath a grand total, and `rulings.py` already
finds horizontal segments with their extents.

**`double-rule-below` is enabled.** It is the second nomination route beside
`label-total`, and it exists because the convention is drawn whether or not the
row says "Total". *Provision for income taxes*, *Cash at end of period*,
*Outstanding at end of year*, *Unvested at end of year* and a great many
rollforward and segment lines announce themselves this way and no other, and the
total lexicon can never be extended far enough to cover them without swallowing
the ordinary rows in between. On the corpus it confirms 13 totals on its own and
supplies a second signal to 29 more.

**`ruling-above` is enabled too**, and it is the half that reaches what no
lexicon can: *Cash generated by operating activities*,
*Increase/(Decrease) in cash, cash equivalents and restricted cash*, and the
second net line of Apple's commercial paper note, whose label opens with
"Proceeds" — the row §7.1 was written about. Extending the total lexicon to cover
them would swallow the ordinary rows in between.

It is weaker than a double rule in a specific way: **the rule under a subtotal is
also the rule over the row that follows it**, and the two readings are
geometrically identical. That is fine for proposing, because the arithmetic
decides — and ruinous for blocking. A nominated total is a barrier to the walk
above it (§7.2), and a speculative nomination that resolves to nothing would stop
every real total above it: Disney's segment expense note draws a rule under each
of its three segment subtotals, so the first row of each following segment is
nominated too, and treating those as barriers hid *Total costs and expenses*
entirely. So only `label-total` and `double-rule-below` make a row a barrier
(`detector.BOUNDARY_SIGNALS`). A row proposed by a rule above blocks nothing until
it resolves, at which point the next pass admits it.

`mark_rules_above` also withholds the mark from a table where more than half the
rows are ruled: that is a ruled grid, which draws the line for every row and
means nothing by it.

**How a double rule is read.** `table-structure-v1` publishes each table's
horizontal rule positions; table detection owns finding them and Reconcile owns
what they mean, per `labels`. A rule belongs to the row whose glyphs it hangs
under — measured against the row's own text box, never against the grid's row
band, because a band runs from one boundary to the next and swallows the rules on
both sides of it. The window below a row reaches 1.25 text-heights down and stops
at the next row's glyphs, so a tightly set statement cannot lend one row the rule
belonging to the row beneath it. Two rules in that window, between 0.0004 and
0.006 of the page apart, are the mark.

**It may confirm and may never accuse.** The published positions carry no extent,
so the signal says a double rule is drawn beneath the *row* and not beneath this
*column*. A total holding it and no label therefore confirms and stays quiet
otherwise, and an unresolved total is spoken only where the document itself
called the row a total — a row proposed by a drawn rule and not resolved is the
scan reaching for something and missing, which is not worth a sentence. Publishing
rule extents would lift that restriction and is the obvious next measurement.

Rulings and arithmetic remain independent, so a disagreement between them is
itself a finding — a total drawn like a total that nothing sums to, or a confirmed
tie with no rule above it. That finding kind, and the `ruling-above` signal, are
still unbuilt; `docs/internal/engines/reconcile/corroboration.md` §6 measures `ruling-above` as no
longer a first-pass dependency, parallel-column agreement having supplied the
second signal it was being held open for.

## 7. Rules the corpus forced

Each of these was written after a false finding on a real statement. None is a
refinement; each removes a way the module accuses a document that foots
perfectly.

### 7.1 A run may not contain a subtotal of its own earlier members

Not every subtotal announces itself. Apple's commercial-paper table sets
proceeds, repayments and the net of the two under one caption, and the net row's
label opens with "Proceeds", so `label-total` cannot see it — the run search then
adds the two components together with their own net and reports a break in a
table that foots perfectly.

Refuting a run is not nominating one. Structure remains the only thing that may
propose a total; arithmetic may still say that a proposed run cannot be the
addends of anything, and a run that counts part of itself twice cannot.

The test is a member equal to everything before it, and two preceding members are
required: one member equal to the one after it is simply two equal figures.

**It now looks at every position, not only the last.** The original rule examined
only the final member, on the reasoning that a subtotal sits at the end of the
block it sums and that looking elsewhere would buy nothing. A long leaf run makes
that false, because a statement's own identities are scattered through it: Disney's
income statement prints services revenues, products revenues and then total
revenues, and a fifteen-row span that takes all three ties to the small figure at
the bottom — *Net income attributable to noncontrolling interests* — for reasons
that have nothing to do with the page. It confirmed in two places until the test
was widened, and widening it cost no real finding on the corpus.

### 7.2 A block whose own top is unknown is not stepped over

Where a total foots on its subtotals (§3), the run resumes above the block each
subtotal consumed. When a subtotal was itself unresolved that block has no known
top, and resuming one row above the subtotal lands *inside* it: Apple's term-debt
table then added the 2025 issuance a second time and reported a 4,500 break. The
walk stops instead of guessing, which costs a confirmation and buys the
correctness back.

### 7.3 A run cut short by a caption row may confirm but may never break

A caption — *Changes in assets and liabilities:*, *Cash Flows from Investing
Activities:* — is the statement separating one block from the next, so a run that
stopped at one holds a fragment of the addends rather than all of them. A
fragment that misses says something about the scan, not about the page; a
fragment that ties exactly is still a tie, because coincidental exact ties are
what the whole design exists to make unreachable. Without this the Carver Trust's
cash-flow statement reported a 3.4 M break against four of the eight rows that
make its total.

A run that a caption cut short is short for a structural reason, and
`run-too-short` is what it is; `no-plausible-run` stays reserved for a run that
was long enough to publish and was refused anyway, which is the list worth
reading.

### 7.4 Stepping over caption rows is measured, and off — except one caption

Skipping a row that fills only the label column would reach a large class of real
totals — Apple's issuer-purchases table sets three monthly captions between the
three rows that make its Total. It was implemented, measured and left **off**
(`sums.SKIP_CAPTION_ROWS`):

| | Blank always breaks (shipped) | Caption rows stepped over |
|---|---|---|
| Confirmed | 53 | 61 |
| Breaks | **0** | **6** |

All six were read by hand and none is real. Eight confirmations for six wrong
findings is the trade the plan refuses.

**One caption is not a boundary at all** (`sums.STEP_OVER_SECTION_CAPTIONS`,
on). The reason a caption ends a run is §7.3's: a run stopped at one holds a
fragment of the addends rather than all of them. That reason does not apply to
the caption sitting directly on top of a block the walk has already consumed
whole through a resolved subtotal — the block below it is accounted for entire,
in one addend, so the caption is that block's heading and not a boundary the run
failed to cross. The step is narrow twice over: only in the subtotal walk, and
only for the row directly above the block just jumped.

Apple's marketable-securities note is the shape it was written for. *Level 1:*
stands over its two rows and their Subtotal, *Level 2 (1):* over its eight and
theirs, and the Total is Cash plus both subtotals. Without the step the walk
stops at *Level 2 (1):* holding one addend, and the four columns whose Cash row
prints a dash confirm from the sibling-subtotal route while the three that carry
a figure there — Adjusted Cost, Fair Value, Cash and Cash Equivalents — go
unresolved. A row two-thirds ticked is worse than an untouched one, because a
reviewer cannot tell the gap from a finding.

A run that took the step is marked, and the marking carries §7.3's rule
unchanged: **it may confirm and may never break.**

## 8. Plausibility

`PLAUSIBLE_DELTA_FRACTION = 0.5`. A run that misses by more than half of the
larger of the total and the sum is recorded as unresolved rather than said out
loud: at that distance the block above the cell was never that cell's addends,
which is a limit of the scan and must not be worded as a fault in the page.

This constant is a hypothesis, not a law. It is exactly the kind of number
`scripts/score_reconcile.py` exists to settle, and its `nearFalseBreaks` watch
list — 11 totals on the Apple 10-K — is what should move it. One of those is
worth naming: page 48's RSU rollforward, whose second column is *Weighted-Average
Grant-Date Fair Value Per RSU*. Its four figures share two decimals with the
total, so §2's decimal rule cannot see that the column is not additive, and only
the magnitude guard keeps it quiet.

## 9. Diagnoses

A named shape of a miss, offered so a reviewer can check one thing rather than
re-add the column. Ordered most specific first, because one that points at a cell
is worth more than one that only describes the delta.

| Kind | What it means |
|---|---|
| `sign` | the delta is exactly twice an addend: that addend carries the wrong sign |
| `transposition` | the delta divides by 9, the signature of two adjacent digits swapped |
| `single-glyph` | the delta is one digit in one place, as one misread glyph would be |
| `omitted-addend` | the delta equals a nearby value the run did not include — defined in the contract, not yet produced |

## 10. The null test

`scripts/null_test_reconcile.py`. Recall is easy to measure and easy to argue
about; the false-tie rate had no instrument at all, and "zero false ties" was
being asserted from hand-reading the confirmations the engine happened to
produce. This measures it directly.

**The method.** Keep every structural signal exactly as the page prints it — the
labels, the rules, the captions, the grid, which cells hold figures and which
hold dashes — and permute the *values* within each column. Every real arithmetic
relationship is destroyed; every reason the engine had to propose a total
survives. Anything confirmed on that page is a coincidence, and the count over
the corpus is the false-tie rate of the whole nomination-and-search machine.

Permuting inside a column rather than across the table is deliberate: a
coincidence has to beat figures of the same magnitude, which is the hard case.

**Read it as an upper bound, not as an error rate.** A column that contains a
total and its parts always admits *total − part = other part* somewhere, so the
permutation relocates arithmetic that genuinely exists rather than inventing it,
and a two-member signed run is easier to fake here than on a real page — where
the correct run is usually found and ties first. What the number is good for is
comparison: run it before and after a change and see which way it moves.

**What it found.** The rate was 23.7% before this work and nothing had noticed,
because the mechanism responsible almost never fires on a real page: unrestricted
sign reversal. It accounted for 595 of 631 false ties. Constraining reversals to
a suffix (§3) took the rate to 13.2% and cost two real confirmations across seven
filings. The rules-based signals of §6 raised nominations by a quarter and left
the rate where it was; widening §7.1 took it to 11.4%.

| | nominations | false ties | rate |
|---|---|---|---|
| Before | 2,664 | 631 | 23.7% |
| Suffix reversals only | 2,648 | 350 | 13.2% |
| Plus `ruling-above`, caption spans, two run readings | 3,295 | 413 | 12.5% |
| Plus §7.1 widened | 3,294 | 375 | **11.4%** |

Over the same span, real confirmations went from 1,003 to 1,314 and breaks from
35 to 5.

**What it does not measure.** Nomination recall — a total nobody proposes never
appears in either column of the table. And it says nothing about whether a
confirmed run is the tree the statement *means*, only that it ties; Disney's
segment expense note tied to the right figure through the wrong rows for a while,
and only reading it caught that.

## 11. Open questions

- **Tolerance**, per §4: measured drift on real statements, then a model, in that
  order.
- **§8's fraction**, which no measurement has yet moved.
- **A non-additive-column rule read from the header.** The RSU case in §8 is the
  most likely source of the first false break, and the header already says what
  the column is. Header semantics is Reconcile's to own under R9. The rule that
  exists — rate, average, percent, per share, per unit, margin — now matches on a
  word boundary rather than as a bare substring, because *"rate"* inside
  *Corporate* silenced the Corporate column of every segment schedule a filing
  prints, and left one column of an otherwise fully ticked row unfooted.
  *Separate* and *Incorporated* failed the same way.
- **A second signal for a row**, which is what would let a cross-foot accuse
  rather than only confirm (§5). Row-to-row corroboration is the obvious
  candidate and has not been designed.
- **The `A - B + C` shape.** Suffix-only reversal refuses it, and the corpus has
  no example of it; a rollforward that prints additions after subtractions would
  be one. Relaxing the rule to "the first member keeps its sign" would admit it
  and restore most of the search space §3 removed, so it should move on a real
  statement that needs it, measured against §11.
- **Rule extents**, per §6. `rulings.py` keeps each rule's extent and
  `table-structure-v1` publishes only its position, so `double-rule-below` knows
  a double rule is under the row and not which columns it crosses. Publishing the
  extent is what would let a rule-nominated total accuse.
- **The non-additive column rule is still only the structure search's.** Applying
  it to every nomination route was implemented and measured and left **off**: a
  detected header label is the text of one column's header band, and on two real
  tables it had swallowed the neighbouring column's — Apple's term-debt note
  reads `2024 Amount Effective (in millions) Interest Rate` for its Amount
  column, and Disney's Hulu note reads `September 28, %` — so the guard silenced
  three columns of ordinary amounts and bought nothing, `MAX_SIGN_CANDIDATES`
  having already refused the RSU tie it was written for. The fix belongs in the
  header labels, not in a longer lexicon.
- **`omitted-addend`**, the one diagnosis the contract defines and the engine does
  not produce.
