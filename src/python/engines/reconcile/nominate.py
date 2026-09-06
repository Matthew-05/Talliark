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

R-2 fills this in. The registry and the policy constants are here now because
they are the module's decisions, not its mechanics.
"""
from __future__ import annotations

from dataclasses import dataclass


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

    False for the whole of the first pass, and it becomes true by enabling
    `ruling-above` or `outdent` rather than by changing anything here. That is
    why one of those two is effectively a first-pass dependency and not a later
    refinement.
    """
    return len(ENABLED_SIGNALS) >= 2


# --- R-2 -------------------------------------------------------------------
# nominate(table_cells, rulings) -> list[NominatedTotal]
#
# No signal may be inferred from the arithmetic. A cell that ties with the run
# above it is not thereby a total, and sums.py never calls back into here.
