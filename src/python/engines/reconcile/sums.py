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

R-2 fills this in. The eligibility and tolerance decisions are recorded here
now, because they are the decisions rather than the mechanics.
"""
from __future__ import annotations

from decimal import Decimal


# The first pass confirms on exact ties only. The tolerance model
# `0.5 x 10^-decimals x addend count` is the intended shape, but it ships after
# the drift is measured on real statements rather than before: applying a
# tolerance nobody has measured is how a rounding allowance quietly blesses a
# real error.
TOLERANCE = Decimal(0)

# A delta that divides by 9 is the signature of two adjacent digits swapped.
TRANSPOSITION_DIVISOR = 9


def is_transposition(delta: Decimal) -> bool:
    """Whether a delta has the shape of a digit transposition."""
    if delta == 0:
        return False
    scaled = delta.copy_abs()
    if scaled != scaled.to_integral_value():
        return False
    return int(scaled) % TRANSPOSITION_DIVISOR == 0


# --- R-2 -------------------------------------------------------------------
# candidate_runs(cells, total)  contiguous runs only, bounded as described above
# add(run)                      Decimal, at the run's shared decimal specificity
# resolve(total, runs)          confirmed | break | unresolved
#
# Eligibility, deliberately primitive -- offered as a rule to be validated
# against the corpus rather than as a law, because heavier eligibility logic is
# overfitting before there is anything to fit to:
#   * a dash alone is an addend worth zero, wherever in a run it appears;
#   * a blank cell BREAKS the run -- it is not a zero, it is the end of the
#     candidate;
#   * members must agree in printed decimal count with each other and with the
#     total, which keeps a per-share figure out of a column of whole millions
#     without knowing what "per share" means.
#
# Nesting: where a total could be confirmed either by the subtotals between it
# and the previous total or by the leaf rows beneath them, the SUBTOTALS win --
# that is the tree the statement is asserting. The leaf resolution is recorded
# too, since it is the same arithmetic and costs nothing.
#
# Cross-footing (R-5) is attempted only against a column whose own header names
# a total, and is disabled entirely for a table with two or more period columns:
# that is a comparative statement, and adding 2025 to 2024 is nonsense no
# tolerance model would catch.
