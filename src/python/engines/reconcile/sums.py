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
# Off, because it amends a decision in the plan of record: blank cells break the
# run, and a row with no value in this column is not a zero but the end of the
# candidate. The corpus argues for the amendment. Apple's issuer-purchases table
# sets three monthly captions between the three rows that foot to its Total, and
# the share-repurchase, marketable-securities and deferred-tax tables are all
# shaped the same way, so the rule as written cannot reach a large class of real
# totals. The distinction the amendment turns on is structural rather than
# arithmetic: a row carrying figures in other columns and a blank in this one
# genuinely ends a block, while a row carrying no figures anywhere was never a
# row of the block to begin with. `scripts/score_reconcile.py` measures both
# settings; flipping this is a decision about the plan, not about the code.
SKIP_CAPTION_ROWS = False


@dataclass(frozen=True)
class Run:
    """A contiguous sequence of cells added together, and what they came to."""

    basis: str
    cells: tuple[dict, ...]
    total: Decimal
    decimals: int

    @property
    def sum(self) -> Decimal:
        return sum((value_of(cell) or Decimal(0) for cell in self.cells), Decimal(0))

    @property
    def delta(self) -> Decimal:
        return self.total - self.sum

    def as_dict(self) -> dict:
        published = {
            "basis": self.basis,
            "addendCellIds": [cell["id"] for cell in self.cells],
            "sum": _quantize(self.sum, self.decimals),
            "delta": _quantize(self.delta, self.decimals),
        }
        diagnosis = diagnose(self)
        if diagnosis is not None:
            published["diagnosis"] = diagnosis
        return published


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


def leaf_run(table_cells, column_index: int, total_row: int, decimals: int, nominated_rows):
    """The contiguous body cells above a total, taking no nominated total in.

    Bounded above by a blank, a cell that carries no value, a decimal
    disagreement, the previous nominated total in this column, or the top of the
    table. Returns the run and what stopped it, because the reason a run is too
    short is the difference between a scan limit and an accusation.
    """
    return _walk(table_cells, column_index, total_row, decimals, nominated_rows, jump=None)


def subtotal_run(
    table_cells, column_index: int, total_row: int, decimals: int, nominated_rows, jump: dict
):
    """The same walk, consuming nominated subtotals instead of stopping at them.

    Where a nominated total could be confirmed either by the subtotals between
    it and the previous total or by the leaf rows beneath them, the subtotals
    win: that is the tree the statement is asserting. `jump` maps a nominated
    total's row to the topmost row its own resolution consumed, which is what
    lets this walk step over a subtotal's addends instead of double-counting
    them.
    """
    return _walk(table_cells, column_index, total_row, decimals, nominated_rows, jump=jump)


def _walk(
    table_cells,
    column_index: int,
    total_row: int,
    decimals: int,
    nominated_rows,
    *,
    jump: dict | None,
):
    cells: list[dict] = []
    stopped = "top-of-table"
    consumed_a_subtotal = False
    row = total_row - 1
    while row >= 0:
        cell = table_cells.cell(row, column_index)
        if cell is None:
            if table_cells.is_caption_row(row):
                if SKIP_CAPTION_ROWS:
                    row -= 1
                    continue
                stopped = "caption"
            else:
                stopped = "blank"
            break
        if value_of(cell) is None:
            stopped = "no-value"
            break
        if not _eligible(cell, decimals):
            stopped = "mixed-decimals"
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
            row = above - 1
            continue
        row -= 1
    return cells, stopped, consumed_a_subtotal


def resolve(
    table_cells, total_cell: dict, signal_count: int, nominated_rows, jump: dict
) -> dict:
    """What the arithmetic makes of one nominated total.

    Three outcomes, and the distinction between the last two is the module's
    credibility. `unresolved` describes the scan's limitation and is never
    phrased as a failure of the document.
    """
    decimals = int(total_cell.get("decimals", 0))
    column = total_cell["columnIndex"]
    row = total_cell["rowIndex"]
    total = value_of(total_cell)
    if total is None:
        return {"outcome": "unresolved", "unresolvedReason": "no-candidate-run"}

    floor = min_addends(signal_count)
    leaves, leaf_stop, _ = leaf_run(table_cells, column, row, decimals, nominated_rows)
    nested, nested_stop, consumed = subtotal_run(
        table_cells, column, row, decimals, nominated_rows, jump
    )

    runs: list[Run] = []
    stops: dict[str, str] = {}
    if consumed and len(nested) >= floor:
        runs.append(Run("subtotals", tuple(nested), total, decimals))
        stops["subtotals"] = nested_stop
    if len(leaves) >= floor:
        runs.append(Run("leaves", tuple(leaves), total, decimals))
        stops["leaves"] = leaf_stop

    if not runs:
        return {"outcome": "unresolved", "unresolvedReason": _why(leaves, nested, leaf_stop)}

    runs = [run for run in runs if not double_counts(run)]
    if not runs:
        # A run that contains its own subtotal is not a candidate at all, and
        # this is the one place arithmetic is allowed near nomination: it
        # refutes a run it may not propose.
        return {"outcome": "unresolved", "unresolvedReason": "no-plausible-run"}

    tied = [run for run in runs if run.delta == TOLERANCE]
    if tied:
        # The subtotals win where both resolve: that is the tree the statement
        # is asserting. The leaf resolution is recorded too, since it is the
        # same arithmetic and costs nothing, and never becomes the tree.
        chosen = tied[0]
        published = {"outcome": "confirmed", "resolution": chosen.as_dict()}
        others = [run for run in runs if run is not chosen]
        if others and others[0].cells != chosen.cells:
            published["leafResolution"] = others[0].as_dict()
        return published

    chosen = runs[0]
    if stops.get(chosen.basis) == "caption":
        # The run was cut short by a caption row -- "Changes in assets and
        # liabilities:", "Cash Flows from Investing Activities:" -- which is the
        # statement separating blocks, not the top of this one. What was
        # collected is a fragment of the addends, and a fragment that misses is
        # arithmetic about the scan rather than about the page. An exact tie is
        # still a tie, because a fragment ties by coincidence about as often as
        # anything else does, which is to say almost never; a miss says nothing.
        return {"outcome": "unresolved", "unresolvedReason": "no-plausible-run"}
    if not _plausible(chosen):
        return {"outcome": "unresolved", "unresolvedReason": "no-plausible-run"}
    published = {"outcome": "break", "resolution": chosen.as_dict()}
    others = [run for run in runs if run is not chosen]
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
    if len(values) < 3 or values[-1] == 0:
        return False
    return sum(values[:-1], Decimal(0)) == values[-1]


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
        if value != 0 and value * 2 == delta:
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
