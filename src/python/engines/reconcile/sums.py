"""Run search, addition, and outcome.

Bounded by nomination, not by a time limit -- there is no runtime target for
this pass. For each nominated total the candidate runs are the contiguous
sequences ending at the row above it, bounded above by the previous nominated
total in the column, a blank, or the top of the table. Subsets are not searched
and non-contiguous combinations are not searched: both would restore precisely
the coincidence problem that making structure the only nominator exists to
remove.

Addition is decimal, always. `normalizedValue` is exact canonical decimal text
and a binary float would quietly destroy the specificity a near-miss diagnosis
reads.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from engines.financial_table import labels

from . import nominate


# The first pass confirms on exact ties only. The tolerance model
# `0.5 x 10^-decimals x addend count` is the intended shape, but it ships after
# the drift is measured on real statements rather than before: applying a
# tolerance nobody has measured is how a rounding allowance quietly blesses a
# real error.
TOLERANCE = Decimal(0)

# A delta that divides by 9 is the signature of two adjacent digits swapped.
TRANSPOSITION_DIVISOR = 9

# How far a run may miss and still be reported as a break rather than recorded
# as unresolved. A break accuses the document of an arithmetic error, so a run
# that misses by more than the total it was supposed to make is not a near miss
# -- it is evidence that the block above the cell was never that cell's addends,
# which is a limit of the scan and must not be worded as a fault in the page.
#
# This number is a hypothesis, not a law. It is exactly the kind of constant
# `scripts/score_reconcile.py` exists to settle, and it should move on a
# measurement rather than on a judgement.
PLAUSIBLE_DELTA_FRACTION = Decimal("0.5")

# Whether a caption row -- one filling the label column and no value column at
# all -- is stepped over rather than ending a run.
#
# Off in general, because it amends a decision in the plan of record: blank
# cells break the run, and a row with no value in this column is not a zero but
# the end of the candidate. Measured on the corpus, stepping over every caption
# row bought 8 confirmations for 6 wrong findings, and that is the trade the
# plan refuses.
#
# One caption is different, and `STEP_OVER_SECTION_CAPTIONS` below governs it.
SKIP_CAPTION_ROWS = False

# Whether a caption row sitting directly on top of a block this run has already
# consumed in full is stepped over.
#
# The reason a caption ends a run is that a run stopped at one holds a fragment
# of the addends rather than all of them. That reason does not apply to the
# caption immediately above a resolved subtotal's own block: the block below it
# is accounted for entire, in one addend, so the caption is that block's heading
# rather than a boundary the run failed to cross. Apple's marketable-securities
# note is the shape -- "Level 1:" over its two rows and their subtotal,
# "Level 2 (1):" over its eight and theirs, and a Total that is Cash plus both
# subtotals. Without this the walk stops at "Level 2 (1):" holding one addend,
# and the three columns where the Cash row carries a figure rather than a dash
# go unresolved while the four where it carries a dash confirm.
#
# The step is narrow twice over: only in the subtotal walk, and only for the row
# directly above the block just jumped. A run that took it is marked, and the
# marking carries §7.3's rule -- such a run may confirm and may never break.
STEP_OVER_SECTION_CAPTIONS = True

# Whether the walk crosses any vertical gap inside a block -- an absent cell, a
# caption, or a row that carries no value in this column -- rather than ending
# the candidate there.
#
# A blank or caption is a real boundary, and the plan's default is to stop: a
# run cut short by one holds a fragment of the addends, and a fragment that
# misses says something about the scan, not the page. But a total may genuinely
# sum across one. Apple's fair-value hierarchy sets "Assets valued at NAV as a
# practical expedient:" between a subtotal and the rows it sums; Disney's
# reclassification table sets a caption between each component subtotal; a
# comparative statement leaves a cell blank in one period column while the row
# carries a figure in another. Refusing to cross loses those totals entirely.
#
# So the walk crosses, and every run that crossed is marked: it may confirm on
# an exact tie and may never accuse the document (the `crossed` flag, §7.3).
# The gap is where the scan stopped guessing, not where the page changed
# subject, so the arithmetic alone decides whether the run ties. Runs that cross
# still stop at a nominated total, the top of the table, and the balance-sheet
# side boundary.
CROSS_GAPS = True

# A statement occasionally asserts a difference while printing every component
# as a positive figure: gross margin is net sales less cost of sales, for
# example. The run stays contiguous and no cell is omitted; what the search
# chooses is where the subtraction starts.
#
# **Only a suffix may be reversed, and the first member always keeps its printed
# sign.** A statement writes *A less B less C*: the figure being subtracted from
# comes first, and what is taken off it follows. Reversing the first member
# instead asserts *-A + B + C*, which is not a shape a statement lays out -- had
# the page meant that, it would have printed A negative or put it elsewhere.
#
# This is a constraint on the search space before it is a constraint on the
# answer, and that is the point. Reversing an arbitrary subset of n members is
# 2^n readings to choose from, and a search that large finds an exact tie in
# almost any column. Measured on the corpus with every value permuted at random
# inside its own column -- labels, rules, captions and grid left exactly as
# printed, so every reason to propose a total survives and every real
# relationship is destroyed -- the subset search confirmed 595 totals that were
# not there, against 52 for the unsigned search. Suffixes number n-1.
#
# Reading the real corpus back the same way: all 55 true signed confirmations
# reverse a contiguous suffix and keep the first member's printed sign -- gross
# margin, operating income, net income, net deferred tax assets, non-current
# borrowings, property and equipment net -- and every one of the five that
# reversed the first member was arithmetic nonsense that happened to tie.
#
# How many starting points may be tried at all. The longest real signed run in
# the corpus is seven members; past twenty even one reading per start is more
# readings than a run that long has earned.
MAX_SIGN_CANDIDATES = 20


@dataclass(frozen=True)
class Run:
    """A contiguous sequence of cells combined together, and what they came to."""

    basis: str
    cells: tuple[dict, ...]
    total: Decimal
    decimals: int
    negated_indices: tuple[int, ...] = ()

    @property
    def sum(self) -> Decimal:
        negated = set(self.negated_indices)
        return sum(
            (
                -(value_of(cell) or Decimal(0))
                if index in negated
                else (value_of(cell) or Decimal(0))
                for index, cell in enumerate(self.cells)
            ),
            Decimal(0),
        )

    @property
    def delta(self) -> Decimal:
        return self.total - self.sum

    def with_negated(self, indices: tuple[int, ...]) -> Run:
        return Run(self.basis, self.cells, self.total, self.decimals, indices)

    def as_dict(self) -> dict:
        published = {
            "basis": self.basis,
            "addendCellIds": [cell["id"] for cell in self.cells],
            "negatedAddendCellIds": [self.cells[index]["id"] for index in self.negated_indices],
            "sum": _quantize(self.sum, self.decimals),
            "delta": _quantize(self.delta, self.decimals),
        }
        diagnosis = diagnose(self)
        if diagnosis is not None:
            published["diagnosis"] = diagnosis
        return published


def tied_variants(run: Run) -> tuple[Run, ...]:
    """The least-complex exact sign interpretations of one contiguous run.

    Printed signs are authoritative when they already tie. Otherwise the
    equation `printed sum - 2 * negated values = total` identifies the required
    subset. Only the smallest number of reversals is returned. Parallel-column
    corroboration then requires the same row positions to be reversed in the
    independent columns, keeping a coincidental sign choice from becoming a
    structural signal.
    """
    if run.delta == TOLERANCE:
        return (run,)

    if len(run.cells) - 1 > MAX_SIGN_CANDIDATES:
        return ()

    target = (run.sum - run.total) / Decimal(2)
    found: list[Run] = []
    # Later starts first, so the fewest reversals -- the least complex reading
    # of the statement -- is the variant returned.
    for start in range(len(run.cells) - 1, 0, -1):
        indices = tuple(
            index
            for index in range(start, len(run.cells))
            if (value_of(run.cells[index]) or Decimal(0)) != 0
        )
        if not indices:
            # A suffix of dashes reverses nothing, so it is not a reading of its
            # own; the next start up is.
            continue
        if sum(
            (value_of(run.cells[index]) or Decimal(0) for index in indices),
            Decimal(0),
        ) != target:
            continue
        candidate = run.with_negated(indices)
        if not double_counts(candidate):
            found.append(candidate)
    return tuple(found)


def preferred_tied_variant(run: Run) -> Run | None:
    """One deterministic exact interpretation for label-nominated arithmetic."""
    variants = tied_variants(run)
    return variants[0] if variants else None


def figure_count(run: Run) -> int:
    """How many members carry a figure rather than a dash."""
    return sum(1 for cell in run.cells if (value_of(cell) or Decimal(0)) != 0)


def reduce_double_counted(run: Run) -> Run | None:
    """Collapse a run that holds both a subtotal and the block it sums.

    A statement's own identity printed inside a run is not a coincidence to be
    refused; it is a subtotal the walk did not know was one. Where a contiguous
    block of members sums exactly to the member immediately below it, that block
    *is* that member's addends, and counting both double-counts them. Replacing
    the block and its subtotal with the subtotal recovers the tree the page
    asserts -- the repair for the shape `double_counts` refuses.

    Only a printed-sign sum collapses, so no sign choice can manufacture one,
    and the subtotal must itself carry a figure. The reduction repeats, because
    collapsing an inner subtotal can expose the outer one. Returns `None` when
    the run is already free of the shape.
    """
    cells = list(run.cells)
    changed = False
    while True:
        collapsed = False
        for index in range(2, len(cells)):
            subtotal = value_of(cells[index])
            if subtotal is None or subtotal == 0:
                continue
            for start in range(index - 2, -1, -1):
                if sum(
                    (value_of(cell) or Decimal(0) for cell in cells[start:index]),
                    Decimal(0),
                ) == subtotal:
                    cells = cells[:start] + [cells[index]] + cells[index + 1 :]
                    changed = True
                    collapsed = True
                    break
            if collapsed:
                break
        if not collapsed:
            break
    return Run("subtotals", tuple(cells), run.total, run.decimals) if changed else None


def is_transposition(delta: Decimal) -> bool:
    """Whether a delta has the shape of a digit transposition."""
    if delta == 0:
        return False
    scaled = delta.copy_abs()
    if scaled != scaled.to_integral_value():
        return False
    return int(scaled) % TRANSPOSITION_DIVISOR == 0


def value_of(cell: dict | None) -> Decimal | None:
    """What this cell contributes to a sum, or nothing when it contributes.

    A dash is an addend worth zero, uniformly and wherever in a run it appears.
    A cell with text and no value contributes nothing and is not an addend --
    which the run search reads as the end of the candidate, never as a zero.
    """
    if cell is None:
        return None
    if "normalizedValue" in cell:
        return Decimal(cell["normalizedValue"])
    if cell.get("dash"):
        return Decimal(0)
    return None


def _quantize(value: Decimal, decimals: int) -> str:
    """A decimal rendered at the run's shared specificity, never as a float."""
    quantized = value.quantize(Decimal(1).scaleb(-decimals)) if decimals else value
    return format(quantized, "f")


def rounding_can_explain(run: Run) -> bool:
    """Whether independently rounded displayed figures can contain an exact tie.

    A value printed to d decimal places represents an interval half a displayed
    unit wide. For n addends and one total, the largest explainable displayed
    delta is therefore (n + 1) / 2 units. This classifies the limit; it never
    confirms the arithmetic because the unrounded source figures are unknown.

    The first measured use is decimal key metrics. Whole-number statement scales
    are not carried into the cell model yet, so treating every one-unit whole
    number miss as rounding would be broader than the available evidence.
    """
    if run.decimals <= 0 or run.delta == 0:
        return False
    unit = Decimal(1).scaleb(-run.decimals)
    allowance = unit * Decimal(len(run.cells) + 1) / Decimal(2)
    return run.delta.copy_abs() <= allowance


def proportional_values(left: list[Decimal], right: list[Decimal]) -> bool:
    """Whether two runs are the same figures scaled, and so not independent.

    Two columns that agree only because one is a fixed multiple of the other are
    one piece of evidence wearing two hats -- a percentage column beside the
    amounts it is a percentage of, most often. Corroboration counts independent
    columns, and this is the test for independence.
    """
    pairs = [(a, b) for a, b in zip(left, right) if a != 0 or b != 0]
    if not pairs:
        return True
    anchor_a, anchor_b = pairs[0]
    return all(a * anchor_b == b * anchor_a for a, b in pairs[1:])


def min_addends(signal_count: int) -> int:
    """How many addends a run needs before it may be published.

    A run of three or more may be confirmed on `label-total` alone. A run of two
    requires a second enabled signal -- and until one is enabled, two-addend
    runs are not published at all, because a pair that happens to sum is nearly
    evidence-free.
    """
    if signal_count >= 2 and nominate.two_addend_runs_publishable():
        return nominate.MIN_ADDENDS
    return nominate.MIN_ADDENDS_ON_ONE_SIGNAL


def _eligible(cell: dict, decimals: int) -> bool:
    """Light filtering, not semantics.

    Members of a run must agree in printed decimal count with each other and
    with the total. That is enough to keep a per-share figure out of a column of
    whole millions without knowing what "per share" means, and it is offered as
    a rule to be validated against the corpus rather than as a law. A dash has
    no printed decimals and agrees with anything.
    """
    if cell.get("dash") and "normalizedValue" not in cell:
        return True
    return int(cell.get("decimals", -1)) == decimals


def leaf_run(
    table_cells,
    column_index: int,
    total_row: int,
    decimals: int,
    nominated_rows,
    *,
    cross_gaps: bool = False,
):
    """The contiguous body cells above a total, taking no nominated total in.

    Bounded above by a blank, a cell that carries no value, a decimal
    disagreement, the previous nominated total in this column, or the top of the
    table. Returns the run and what stopped it, because the reason a run is too
    short is the difference between a scan limit and an accusation.

    With `cross_gaps`, an absent cell or caption is stepped over instead and the
    run is marked confirm-only. The caller asks for both readings: the bounded
    one is the page's own, and the crossing one is the fallback that proves an
    exact relationship the page did not draw a boundary around.
    """
    return _walk(
        table_cells,
        column_index,
        total_row,
        decimals,
        nominated_rows,
        jump=None,
        cross_gaps=cross_gaps,
    )


def subtotal_run(
    table_cells,
    column_index: int,
    total_row: int,
    decimals: int,
    nominated_rows,
    jump: dict,
    *,
    cross_gaps: bool = False,
):
    """The same walk, consuming nominated subtotals instead of stopping at them.

    Where a nominated total could be confirmed either by the subtotals between
    it and the previous total or by the leaf rows beneath them, the subtotals
    win: that is the tree the statement is asserting. `jump` maps a nominated
    total's row to the topmost row its own resolution consumed, which is what
    lets this walk step over a subtotal's addends instead of double-counting
    them.
    """
    return _walk(
        table_cells,
        column_index,
        total_row,
        decimals,
        nominated_rows,
        jump=jump,
        cross_gaps=cross_gaps,
    )


def cross_run(
    table_cells,
    row_index: int,
    total_column: int,
    decimals: int,
    total_columns,
    excluded_columns=frozenset(),
):
    """The contiguous cells to the left of a row's total, and what stopped them.

    The horizontal twin of `leaf_run`, and deliberately the same walk: a blank
    ends the candidate, a dash is an addend worth zero, and the printed decimal
    count has to agree. It stops at the label column without needing to know
    which column that is, because a label carries no value and a cell with no
    value is where a run ends.

    It also stops at the previous total-headed column, exactly as the vertical
    walk stops at the previous nominated total. A statement of equity prints
    "Total Disney Shareholders' Equity" and then "Total Equity", and the second
    is the first plus noncontrolling interests -- not the first plus every
    component the first already consumed.
    """
    cells: list[dict] = []
    stopped = "edge-of-table"
    for column in range(total_column - 1, 0, -1):
        if column in total_columns:
            stopped = "previous-total"
            break
        if column in excluded_columns:
            continue
        cell = table_cells.cell(row_index, column)
        if cell is None:
            stopped = "blank"
            break
        if value_of(cell) is None:
            stopped = "no-value"
            break
        if not _eligible(cell, decimals):
            stopped = "mixed-decimals"
            break
        cells.insert(0, cell)
    return cells, stopped


# Whether a cross-foot that misses may be published as a break.
#
# Off, and this is a decision about evidence rather than about the code. The
# vertical pass has two signals -- a label that announces a total and the same
# row structure footing independently in another column -- and only accuses when
# it has them. A row has one: the word in its own column header. Nothing
# corroborates it, and the corpus says what that costs. Disney's statement of
# equity sets a share count in the first column, so every row of it "misses" by
# the share count; its market-risk table cross-foots value-at-risk figures that
# do not add across by construction, there being a diversification benefit and
# no header word that says so. Thirty-four accusations on two documents, none of
# them real.
#
# So a cross-foot confirms and stays quiet otherwise, which is worth having on
# its own: an exact tie across six segment columns is a real corroboration of
# every figure in the row, and it is what ticks the row totals of a segment
# schedule. A miss becomes `no-plausible-run` and is counted, never spoken.
CROSS_FOOT_MAY_BREAK = False


def resolve_cross(table_cells, total_cell: dict, signal_count: int, total_columns) -> dict:
    """What the arithmetic makes of one row against a total-headed column.

    Narrow by design, per the plan: a cross-foot is attempted only against a
    column whose own header names a total, and the caller enforces the period
    guard that disables the whole table. What is left for here is the same
    addition and the same floor as the vertical pass, and -- until a second
    signal exists for a row -- two outcomes rather than three.
    """
    decimals = int(total_cell.get("decimals", 0))
    total = value_of(total_cell)
    if total is None:
        return {"outcome": "unresolved", "unresolvedReason": "no-candidate-run"}

    cells, stopped = cross_run(
        table_cells,
        int(total_cell["rowIndex"]),
        int(total_cell["columnIndex"]),
        decimals,
        total_columns,
        frozenset(
            int(header["columnIndex"])
            for header in table_cells.header_labels
            if labels.is_non_additive_column(header.get("text", ""))
        ),
    )
    # A result header applies deliberately to every row beneath it.  Two-addend
    # runs are resolved here so the caller can test whether the same equation
    # shape repeats in another row; an isolated pair is never published.
    if len(cells) < nominate.MIN_ADDENDS:
        return {
            "outcome": "unresolved",
            "unresolvedReason": (
                "mixed-decimals" if stopped == "mixed-decimals"
                else "no-candidate-run" if not cells
                else "run-too-short"
            ),
        }

    run = Run("row", tuple(cells), total, decimals)
    if double_counts(run):
        return {"outcome": "unresolved", "unresolvedReason": "no-plausible-run"}
    chosen = preferred_tied_variant(run) or run
    if chosen.delta == TOLERANCE:
        return {"outcome": "confirmed", "resolution": chosen.as_dict()}
    if not CROSS_FOOT_MAY_BREAK or not _plausible(chosen):
        return {"outcome": "unresolved", "unresolvedReason": "no-plausible-run"}
    return {"outcome": "break", "resolution": chosen.as_dict()}


def _walk(
    table_cells,
    column_index: int,
    total_row: int,
    decimals: int,
    nominated_rows,
    *,
    jump: dict | None,
    cross_gaps: bool = False,
):
    cells: list[dict] = []
    stopped = "top-of-table"
    consumed_a_subtotal = False
    highest_subtotal: int | None = None
    crossed_a_caption = False
    # The top row of the block the walk has just stepped over, which is the one
    # row whose caption is that block's own heading rather than a boundary.
    block_below = None
    row = total_row - 1
    total_cell = table_cells.cell(total_row, column_index)
    total_label = (total_cell or {}).get("rowLabel", "")
    while row >= 0:
        cell = table_cells.cell(row, column_index)
        if cell is None:
            if cross_gaps:
                # An absent cell in this column -- a caption, a blank, or a row
                # whose figure is printed in another period column -- is crossed,
                # and the run is marked confirm-only for having done so.
                crossed_a_caption = True
                row -= 1
                continue
            if table_cells.is_caption_row(row):
                if SKIP_CAPTION_ROWS:
                    crossed_a_caption = True
                    row -= 1
                    continue
                if STEP_OVER_SECTION_CAPTIONS and block_below == row + 1:
                    # The caption of a block already taken whole. Crossing it
                    # fragments nothing, and the crossing is recorded so the
                    # run may confirm without ever being able to accuse.
                    crossed_a_caption = True
                    block_below = None
                    row -= 1
                    continue
                stopped = "caption"
            else:
                stopped = "blank"
            break
        block_below = None
        if value_of(cell) is None:
            stopped = "no-value"
            break
        if not _eligible(cell, decimals):
            stopped = "mixed-decimals"
            break
        if (
            jump is not None
            and row in nominated_rows
            and labels.separates_balance_sheet_sides(
                cell.get("rowLabel", ""), total_label
            )
        ):
            stopped = "previous-total"
            break
        if jump is None and row in nominated_rows:
            stopped = "previous-total"
            break
        cells.insert(0, cell)
        above = jump.get(row) if jump is not None else None
        if jump is not None and row in nominated_rows:
            consumed_a_subtotal = True
            if above is None:
                # A subtotal nothing resolved cannot be stepped over without
                # risking its own addends being counted twice, so it is the last
                # thing this run takes.
                stopped = "unresolved-subtotal"
                break
            # Only a subtotal the walk actually stepped over marks a boundary.
            # The row that *stopped* the walk is the one whose status is in
            # question, so counting it here would make the shorter reading
            # identical to the full run and offer nothing.
            highest_subtotal = row
            block_below = above
            row = above - 1
            continue
        row -= 1
    return cells, stopped, consumed_a_subtotal, crossed_a_caption, highest_subtotal


def resolve(
    table_cells,
    total_cell: dict,
    signal_count: int,
    nominated_rows,
    jump: dict,
    *,
    floor: int | None = None,
) -> dict:
    """What the arithmetic makes of one nominated total.

    Three outcomes, and the distinction between the last two is the module's
    credibility. `unresolved` describes the scan's limitation and is never
    phrased as a failure of the document.

    `floor` overrides how many addends a run needs before it may be published.
    The caller passes it only to ask the second question -- what would this total
    resolve to if two addends were enough -- and may publish that answer only on
    evidence the floor exists to demand, which is parallel-column agreement.
    """
    decimals = int(total_cell.get("decimals", 0))
    column = total_cell["columnIndex"]
    row = total_cell["rowIndex"]
    total = value_of(total_cell)
    if total is None:
        return {"outcome": "unresolved", "unresolvedReason": "no-candidate-run"}

    floor = min_addends(signal_count) if floor is None else floor
    leaves, leaf_stop, _, leaf_crossed, _ = leaf_run(
        table_cells, column, row, decimals, nominated_rows
    )
    nested, nested_stop, consumed, nested_crossed, highest = subtotal_run(
        table_cells, column, row, decimals, nominated_rows, jump
    )

    # Each candidate carries its own stop reason and its own right to accuse,
    # because two candidates can share a basis: the walk that ends on a
    # nominated total whose block was never resolved yields two readings of the
    # same run, and they are not the same evidence.
    #
    # The bounded readings -- the page's own, where a blank or caption ends the
    # run -- are kept apart from the crossing fallback. A bounded miss reports
    # the bounded walk's stop reason, so a two-addend bounded run is still
    # `run-too-short` and the parallel-column upgrade can still find it; only a
    # crossing run that ties is allowed to change the answer.
    bounded: list[tuple[Run, str, bool]] = []
    # A run that crossed a caption may confirm and may never break, whether the
    # caption ended it (§7.3) or it stepped over the heading of a block it took
    # whole. Both hold a run whose extent was decided by something other than
    # the arithmetic, and a miss there is about the scan and not about the page.
    if consumed and len(nested) >= floor:
        bounded.append(
            (Run("subtotals", tuple(nested), total, decimals), nested_stop, nested_crossed)
        )
    if consumed and highest is not None:
        # A run that took subtotals in has a boundary the page drew: the topmost
        # subtotal it consumed. Above that lies whatever came before the block,
        # and whether those rows belong is exactly what is unknown -- they may be
        # the run's remaining addends, or they may be the statement's opening
        # figure, which is the addend of nothing.
        #
        # Apple shows both readings on facing pages. Its statement of
        # shareholders' equity opens with "Total shareholders' equity, beginning
        # balances" and closes with the ending balances its three sections make,
        # so the opening balance must be left out; its cash flow statement makes
        # *Increase/(Decrease) in cash* out of the three activity subtotals and
        # nothing else, while the row below it is that increase plus the opening
        # cash balance -- the same shape, cut in two different places.
        #
        # So the shorter reading is offered as a second candidate and never
        # preferred: the full run is evaluated first and this one can only win by
        # tying exactly. Being a boundary the arithmetic chose rather than one
        # the page marked, it may confirm and may never accuse.
        short = [cell for cell in nested if int(cell["rowIndex"]) >= highest]
        if len(short) < len(nested) and len(short) >= floor:
            bounded.append(
                (Run("subtotals", tuple(short), total, decimals), nested_stop, True)
            )
    if len(leaves) >= floor:
        bounded.append(
            (Run("leaves", tuple(leaves), total, decimals), leaf_stop, leaf_crossed)
        )

    # The bounded readings above are the page's own: a blank or caption ends the
    # run. They are offered first, so a bounded exact tie is always the tree
    # published. Only when none of them resolves does the walk ask the second
    # question -- what would this total be if the gaps were crossed? -- and that
    # reading may confirm and may never accuse, because its extent was decided
    # by the arithmetic rather than by a boundary the page drew.
    crossing: list[tuple[Run, str, bool]] = []
    if CROSS_GAPS:
        cross_leaves, cross_leaf_stop, _, _, _ = leaf_run(
            table_cells, column, row, decimals, nominated_rows, cross_gaps=True
        )
        (
            cross_nested,
            cross_nested_stop,
            cross_consumed,
            _,
            _,
        ) = subtotal_run(
            table_cells,
            column,
            row,
            decimals,
            nominated_rows,
            jump,
            cross_gaps=True,
        )
        cross_nested_run = Run("subtotals", tuple(cross_nested), total, decimals)
        if (
            cross_consumed
            and len(cross_nested) >= floor
            and tuple(cross_nested) != tuple(nested)
            and figure_count(cross_nested_run) >= 2
        ):
            crossing.append((cross_nested_run, cross_nested_stop, True))
        cross_leaf_run = Run("leaves", tuple(cross_leaves), total, decimals)
        if (
            len(cross_leaves) >= floor
            and tuple(cross_leaves) != tuple(leaves)
            and figure_count(cross_leaf_run) >= 2
        ):
            crossing.append((cross_leaf_run, cross_leaf_stop, True))

    if not bounded and not crossing:
        if len(leaves) >= nominate.MIN_ADDENDS:
            displayed = Run("leaves", tuple(leaves), total, decimals)
            if not double_counts(displayed) and rounding_can_explain(displayed):
                return {
                    "outcome": "unresolved",
                    "unresolvedReason": "rounding-indeterminate",
                }
        return {"outcome": "unresolved", "unresolvedReason": _why(leaves, nested, leaf_stop)}

    # A run that still holds a subtotal together with that subtotal's own
    # addends is not a candidate; it is a tree with one level left uncollapsed.
    # Offer the collapsed reading first -- it is the relationship the page
    # asserts -- and keep the original behind it. The collapsed reading may
    # confirm and may never accuse: it was reconstructed by arithmetic rather
    # than drawn by the page, and only an exact tie earns it a place.
    def _expand(entries):
        expanded: list[tuple[Run, str, bool]] = []
        for run, stop, quiet in entries:
            reduced = reduce_double_counted(run)
            if reduced is not None and figure_count(reduced) >= 2:
                expanded.append((reduced, stop, True))
            expanded.append((run, stop, quiet))
        return [entry for entry in expanded if not double_counts(entry[0])]

    # Whether the bounded walk produced a candidate at all, before the
    # double-count filter. A bounded run too short to offer reports the walk's
    # limit (`run-too-short`); one that was offered and refused because it
    # contains its own subtotal is a different fact -- `no-plausible-run`.
    bounded_formed = bool(bounded)
    bounded = _expand(bounded)
    crossing = _expand(crossing)
    candidates = bounded + crossing
    if not candidates:
        # A run that contains its own subtotal is not a candidate at all, and
        # this is the one place arithmetic is allowed near nomination: it
        # refutes a run it may not propose.
        return {"outcome": "unresolved", "unresolvedReason": "no-plausible-run"}

    evaluated = [
        (preferred_tied_variant(run) or run, stop, quiet)
        for run, stop, quiet in candidates
    ]
    tied = [entry for entry in evaluated if entry[0].delta == TOLERANCE]
    if tied:
        # The subtotals win where both resolve: that is the tree the statement
        # is asserting. The leaf resolution is recorded too, since it is the
        # same arithmetic and costs nothing, and never becomes the tree.
        chosen = tied[0][0]
        published = {"outcome": "confirmed", "resolution": chosen.as_dict()}
        others = [run for run, _, _ in evaluated if run.basis != chosen.basis]
        if others and others[0].cells != chosen.cells:
            published["leafResolution"] = others[0].as_dict()
        return published

    if not bounded:
        # Only the crossing fallback was available and none of it tied. A
        # bounded run that was offered and refused is `no-plausible-run`; a
        # bounded walk too short to offer reports its own stop reason --
        # `run-too-short` or `no-candidate-run` -- which is also what lets the
        # caller's parallel-column upgrade still reconsider the two-addend case.
        if bounded_formed:
            return {"outcome": "unresolved", "unresolvedReason": "no-plausible-run"}
        return {"outcome": "unresolved", "unresolvedReason": _why(leaves, nested, leaf_stop)}

    chosen, chosen_stop, chosen_quiet = evaluated[0]
    if rounding_can_explain(chosen):
        return {
            "outcome": "unresolved",
            "unresolvedReason": "rounding-indeterminate",
        }
    if len(chosen.cells) < nominate.MIN_ADDENDS_ON_ONE_SIGNAL:
        # A pair that happens to sum is nearly evidence-free, which is why the
        # floor exists; a pair that happens not to sum is exactly as thin, and
        # the same reasoning has to run both ways. A short run reaching here has
        # already been let past the floor by corroboration or a second signal --
        # enough to confirm on, never enough to accuse on.
        return {"outcome": "unresolved", "unresolvedReason": "no-plausible-run"}
    if chosen_quiet:
        return {"outcome": "unresolved", "unresolvedReason": "no-plausible-run"}
    if chosen_stop not in {"top-of-table", "previous-total", "edge-of-table"}:
        # A run cut short by a caption, blank, non-value, decimal change, or an
        # unresolved subtotal is only a fragment.  An exact tie remains useful,
        # but a fragment that misses says something about the scan's reach, not
        # about the page.  Only the top of the block or an established subtotal
        # boundary closes a run strongly enough to support an accusation.
        return {"outcome": "unresolved", "unresolvedReason": "no-plausible-run"}
    if not _plausible(chosen):
        return {"outcome": "unresolved", "unresolvedReason": "no-plausible-run"}
    published = {"outcome": "break", "resolution": chosen.as_dict()}
    others = [run for run, _, _ in evaluated if run.basis != chosen.basis]
    if others and others[0].cells != chosen.cells:
        published["leafResolution"] = others[0].as_dict()
    return published


def double_counts(run: Run) -> bool:
    """Does this run contain a subtotal of its own earlier members?

    Not every subtotal announces itself. Apple's commercial-paper table sets
    proceeds, repayments and the net of the two under one caption, and the net
    row's label opens with "Proceeds", so `label-total` cannot see it -- the run
    search then adds the two components and their own net, and reports a break in
    a table that foots perfectly.

    Refuting a run is not nominating one. Structure remains the only thing that
    may propose a total; arithmetic may still say that a proposed run cannot be
    the addends of anything, and a run that counts part of itself twice cannot.
    The test is narrow on purpose, and only the last member is examined. A
    subtotal sits at the end of the block it sums, directly above the total that
    consumes it, which is exactly where Apple's net row sits. Looking anywhere
    else in the run buys nothing and costs real findings: three rows reading
    100, 200 and 300 are three ordinary figures, and a rule that refuses them
    because the first two happen to make the third refuses a great deal of a
    filing. Two preceding members are required for the same reason -- one member
    equal to the one after it is simply two equal figures, which a statement
    prints constantly.
    """
    values = [value_of(cell) or Decimal(0) for cell in run.cells]
    # A signed search must not manufacture a tie by subtracting a subtotal from
    # the very members that make it. That is algebraic cancellation, not the
    # relationship the statement asserts (A + B - (A + B) + C = C).
    negated = set(run.negated_indices)
    for index in run.negated_indices:
        if index >= 2 and sum(values[:index], Decimal(0)) == values[index]:
            return True
    # The plainest cancellation of all: a member subtracted and the same figure
    # added somewhere else in the run. The pair contributes nothing, so whatever
    # ties is really the shorter run underneath -- which the search can find on
    # its own, without inventing two signs to get there. Disney's borrowings
    # table pairs 10,558 against 10,558 exactly this way.
    for index in run.negated_indices:
        if values[index] == 0:
            continue
        if any(
            other not in negated and values[other] == values[index]
            for other in range(len(values))
        ):
            return True
    # And anywhere in the run, not only at its end. A statement's own identities
    # make a long leaf run a rich source of these: Disney's income statement
    # prints services revenues, products revenues and then total revenues, and a
    # fifteen-row span that takes all three ties to the small figure at the
    # bottom for reasons that have nothing to do with the page. The test stays
    # what it was -- a member equal to everything before it -- and only stops
    # looking exclusively at the last row.
    for index in range(2, len(values)):
        if values[index] != 0 and sum(values[:index], Decimal(0)) == values[index]:
            return True
    return False


def _why(leaves: list[dict], nested: list[dict], leaf_stop: str) -> str:
    """Why nothing was resolved, in the vocabulary the contract closes over.

    `no-candidate-run` is the common one and is a statement about what was above
    the cell, not an accusation about the document.
    """
    if not leaves and not nested:
        return "no-candidate-run"
    if leaf_stop == "mixed-decimals":
        return "mixed-decimals"
    # A run cut short by a caption row is short for a structural reason, and
    # `run-too-short` is what it is. `no-plausible-run` is reserved for a run
    # that was long enough to publish and was refused anyway, which is the list
    # worth reading.
    return "run-too-short"


def _plausible(run: Run) -> bool:
    """Whether a miss is near enough to report as a break."""
    delta = run.delta.copy_abs()
    if delta == 0:
        return True
    reference = max(run.total.copy_abs(), run.sum.copy_abs())
    if reference == 0:
        return False
    return delta <= reference * PLAUSIBLE_DELTA_FRACTION


def block_top(cells: list[dict], jump: dict, nominated_rows) -> int | None:
    """The topmost row a run consumed, which is where the run above resumes.

    A run's own addends are not always the top of it: where an addend is itself
    a nominated subtotal, the block reaches up through the rows that subtotal
    consumed. Totals are resolved from the top of a column downwards precisely
    so this is already known by the time it is asked for.

    `None` means the top is not known, and it is returned rather than guessed
    whenever an addend is a nominated total whose own block was never resolved.
    Guessing costs more than it looks: resuming one row above such an addend
    lands inside the block it owns, and the run above then adds one of that
    subtotal's own components a second time. A run built that way ties or misses
    for reasons that have nothing to do with the page.
    """
    if not cells:
        return None
    top = None
    for cell in cells:
        row = cell["rowIndex"]
        reach = jump.get(row)
        if reach is None:
            if row in nominated_rows:
                return None
            reach = row
        top = reach if top is None else min(top, reach)
    return top


# --- diagnoses -------------------------------------------------------------
# A named shape of a miss, offered so a reviewer can check one thing rather than
# re-add the column. Ordered most specific first: one that points at a cell is
# worth more than one that only describes the delta.


def diagnose(run: Run) -> dict | None:
    delta = run.delta
    if delta == 0:
        return None
    for candidate in (_sign, _transposition, _single_glyph):
        found = candidate(run, delta)
        if found is not None:
            return found
    return None


def _sign(run: Run, delta: Decimal) -> dict | None:
    """The delta is exactly twice an addend: that addend carries the wrong sign."""
    for cell in run.cells:
        value = value_of(cell) or Decimal(0)
        # Reversing the printed sign changes the sum by -2 * value.  `delta` is
        # total minus the current sum, so it has that direction, not +2 * value.
        if value != 0 and value * -2 == delta:
            return {
                "kind": "sign",
                "detail": f"the difference is exactly twice {cell['text'].strip()}",
                "cellId": cell["id"],
            }
    return None


def _transposition(run: Run, delta: Decimal) -> dict | None:
    if not is_transposition(delta):
        return None
    return {
        "kind": "transposition",
        "detail": "the difference divides by 9, the signature of two adjacent digits swapped",
    }


def _single_glyph(run: Run, delta: Decimal) -> dict | None:
    """The delta is one digit in one place: 400, 7, 0.02."""
    scaled = delta.copy_abs().normalize()
    digits = format(scaled, "f").replace(".", "").lstrip("0").rstrip("0")
    if len(digits) != 1:
        return None
    return {
        "kind": "single-glyph",
        "detail": "the difference is a single digit in one place, as one misread glyph would be",
    }
