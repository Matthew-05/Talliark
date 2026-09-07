"""Candidate totals, and the registry of signals that may propose one.

Structure nominates; arithmetic confirms. Nomination is a registry of
independent signals -- each a detector returning a named result with the
evidence behind it -- and a policy layer deciding what is sufficient. The
registry exists so that the answer to a new table convention is a new entry with
its own goldens rather than a rewrite of the nominator, and financial statements
have many valid ways to present a total.

Note what is not available as a signal: `text-geometry-v1` carries no font or
weight, so **bold is not a signal that exists**. Rulings and glyph height are the
only typographic evidence there is.

The registry and the policy constants come first in this file because they are
the module's decisions; the nominator underneath them is only their mechanics.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import labels


@dataclass(frozen=True)
class SignalDefinition:
    """One way a cell can announce itself as a total, independent of its value."""

    name: str
    enabled: bool
    sees: str


# Declared in the order they were designed, not in any order of precedence. Only
# `label-total` is enabled in the first pass; the rest are defined, tested
# against goldens, and switched on as measurement says they earn it -- which is
# why R-3, the scorer, gates every phase after it.
SIGNALS: tuple[SignalDefinition, ...] = (
    SignalDefinition(
        "label-total",
        enabled=True,
        sees="the row label matches the total lexicon: Total, Net, Subtotal, "
        "Gross, Balance at / as of, and the compound forms",
    ),
    SignalDefinition(
        "column-corroboration",
        enabled=True,
        sees="the same row structure foots independently in another value column",
    ),
    SignalDefinition(
        "total-column",
        enabled=True,
        sees="this cell's own column header names a total -- Total, Consolidated, "
        "Combined -- which is the only thing that may propose a cross-foot",
    ),
    SignalDefinition(
        "ruling-above",
        enabled=True,
        sees="a rule is drawn in the gap above this cell's row -- the other half "
        "of the convention a double rule completes",
    ),
    SignalDefinition(
        "outdent",
        enabled=False,
        sees="the label's x0 sits left of the rows it would consume",
    ),
    SignalDefinition(
        "block-terminal",
        enabled=False,
        sees="the last body row before a rule, a blank band, or the end of the table",
    ),
    SignalDefinition(
        "double-rule-below",
        enabled=True,
        sees="two rules are drawn in the gap beneath this cell's row -- the "
        "grand-total convention, and the mark a reader looks for",
    ),
)

ENABLED_SIGNALS: tuple[str, ...] = tuple(s.name for s in SIGNALS if s.enabled)


# A run of three or more addends may be confirmed on `label-total` alone.
MIN_ADDENDS_ON_ONE_SIGNAL = 3

# A run of two addends requires a second enabled signal -- and until a second
# signal is enabled, two-addend runs are not published at all. Two-number
# coincidences are the most common accidental tie, and a pair that happens to sum
# is nearly evidence-free.
MIN_ADDENDS = 2


def two_addend_runs_publishable() -> bool:
    """Whether a two-addend run can be published yet.

    Parallel-column corroboration is the second signal. The structure search
    applies it per candidate, so a label-only nomination still carries one
    signal and still needs three addends.
    """
    return len(ENABLED_SIGNALS) >= 2


@dataclass(frozen=True)
class Nomination:
    """A cell proposed as a total, and the evidence that proposed it."""

    cell: dict
    signals: tuple[dict, ...]

    @property
    def row_index(self) -> int:
        return int(self.cell["rowIndex"])

    @property
    def column_index(self) -> int:
        return int(self.cell["columnIndex"])


def label_total(cell: dict) -> dict | None:
    """The one signal enabled in the first pass.

    The row label announces the total, and the evidence is the label itself --
    a phrase a reviewer can check against the page without re-running anything.
    """
    label = cell.get("rowLabel", "")
    if not labels.is_total_label(label):
        return None
    return {"name": "label-total", "evidence": f'row label reads "{label}"'}


def total_column(header: dict | None) -> dict | None:
    """The signal behind a cross-foot, and the only one there is.

    A cross-foot is attempted only against a column whose own header names a
    total. No total-headed column, no cross-foot: the alternative is adding
    across a comparative statement, and 2025 plus 2024 is nonsense no tolerance
    model would catch.
    """
    if header is None or not header.get("isTotalColumn"):
        return None
    text = " ".join((header.get("text") or "").split())
    return {"name": "total-column", "evidence": f'the column header reads "{text}"'}


def cross_foot_columns(header_labels: list[dict]) -> list[int]:
    """Which columns a row may be cross-footed against, if any.

    One guard, and it disables the table rather than a column: two or more
    headers parsing as periods or years make this a comparative statement, where
    a "Total" column totals something other than the columns beside it.
    """
    if sum(1 for header in header_labels if header.get("isPeriodColumn")) >= 2:
        return []
    return [
        int(header["columnIndex"])
        for header in header_labels
        if header.get("isTotalColumn") and int(header["columnIndex"]) > 1
    ]


def nominate_cross(table_cells) -> list[Nomination]:
    """Every cell structure proposes as the total of the row it stands in.

    The same discipline as `nominate`: structure proposes, arithmetic confirms,
    and a cell whose value was withheld has nothing for a run to foot to.
    """
    columns = cross_foot_columns(table_cells.header_labels)
    if not columns:
        return []
    by_column = {
        int(header["columnIndex"]): header for header in table_cells.header_labels
    }
    found: list[Nomination] = []
    for cell in table_cells.published:
        column_index = int(cell["columnIndex"])
        if column_index not in columns or "normalizedValue" not in cell:
            continue
        signal = total_column(by_column.get(column_index))
        if signal is not None:
            found.append(Nomination(cell=cell, signals=(signal,)))
    return found


def double_rule_below(cell: dict, table_cells) -> dict | None:
    """The grand-total convention, read off the page rather than off the label.

    A filing draws a single rule above a total and a double rule beneath a grand
    total, and it draws that mark whether or not the row says "Total" -- which is
    the point. *Operating income/(loss)*, *Net periodic benefit cost*, *Cash at
    end of period* and a great many segment lines announce themselves this way
    and no other, and the total lexicon can never be extended far enough to cover
    them without swallowing the ordinary rows in between.

    It is weaker evidence than a label, in one specific way: the published rule
    positions carry no extent, so this says a double rule is drawn beneath the
    row and not that it is drawn beneath this column. That is why a total holding
    this signal and no label may confirm and may never accuse.
    """
    if int(cell["rowIndex"]) not in getattr(table_cells, "double_ruled", frozenset()):
        return None
    return {
        "name": "double-rule-below",
        "evidence": "a double rule is drawn beneath this row",
    }


def ruling_above(cell: dict, table_cells) -> dict | None:
    """A single rule above a total, which is the other half of the convention.

    It reaches the rows no lexicon can and no label announces: *Cash generated
    by operating activities*, *Increase/(Decrease) in cash, cash equivalents and
    restricted cash*, and the second net line of Apple's commercial paper note,
    whose label opens with "Proceeds". Extending the total lexicon to cover them
    would swallow the ordinary rows in between; the page has already marked them.

    Weaker than a double rule, because a rule above a row is also just the rule
    below the row before it, and the published positions carry no extent. So a
    total holding this and nothing else may confirm and may never accuse -- and
    `mark_rules_above` withholds the mark entirely from a table where most rows
    are ruled, since a ruled grid draws the line for every row and means nothing
    by it.
    """
    if int(cell["rowIndex"]) not in getattr(table_cells, "ruled_above", frozenset()):
        return None
    return {"name": "ruling-above", "evidence": "a rule is drawn above this row"}


def nominate(table_cells) -> list[Nomination]:
    """Every cell in this table that structure proposes as a total.

    No signal may be inferred from the arithmetic. A cell that ties with the run
    above it is not thereby a total, and `sums` never calls back into here.

    Only a cell carrying a single recognized number can be nominated: a total is
    a figure, and a cell whose value was withheld -- a range, a percentage, a
    blank -- has nothing for a run to foot to.
    """
    found: list[Nomination] = []
    for cell in table_cells.published:
        if "normalizedValue" not in cell:
            continue
        if cell["columnIndex"] == 0:
            # The leading column carries the labels. A figure standing in it is
            # a row label that happens to be a number, not a column's total.
            continue
        signals = tuple(
            signal
            for signal in (
                label_total(cell),
                double_rule_below(cell, table_cells),
                ruling_above(cell, table_cells),
            )
            if signal is not None
        )
        if signals:
            found.append(Nomination(cell=cell, signals=signals))
    return found
