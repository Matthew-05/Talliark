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
        "ruling-above",
        enabled=False,
        sees="a horizontal rule crossing this cell's column in the gap above it",
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
        enabled=False,
        sees="the grand-total convention",
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
            signal for signal in (label_total(cell),) if signal is not None
        )
        if signals:
            found.append(Nomination(cell=cell, signals=signals))
    return found
