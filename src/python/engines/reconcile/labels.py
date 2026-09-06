"""What a label means.

Reconcile owns interpretation; table detection keeps owning which bands are
header and what they say as printed. `engines.table.headers.header_row_count`
stays exactly where it is -- the grid cannot build its rows without it, and
table detection has to remain highly accurate for linking, which is what it is
used for almost all of the time. The other half of that module, reading what a
label *means*, belongs here.

R-1 fills this in: row-label extraction and normalization, and the header-label
semantics moved out of `engines/table/headers.py`. What already exists is the
total lexicon, because `nominate.label_total` is the one enabled signal and
reads it.
"""
from __future__ import annotations

import re


# The words a row label uses to say it is a total. Compound forms matter as much
# as the bare ones -- "Total net sales", "Balance at December 31" -- so this is
# matched as a leading phrase rather than as an equality test.
TOTAL_LEXICON: tuple[str, ...] = (
    "total",
    "net",
    "subtotal",
    "sub-total",
    "gross",
    "balance at",
    "balance as of",
    "balances at",
    "balance",
)

_COLLAPSE = re.compile(r"\s+")
# Leading list and footnote apparatus, which sits between the margin and the
# word the label actually starts with.
_LEADING_APPARATUS = re.compile(r"^[\s\-–—•\(\)\[\]0-9.]+")


def normalize(text: str) -> str:
    """A label reduced to what it says: collapsed whitespace, lower case."""
    return _COLLAPSE.sub(" ", (text or "").strip()).lower()


def is_total_label(text: str) -> bool:
    """Whether a row label announces itself as a total.

    Leading apparatus is stripped first so "(1) Total revenue" reads the same as
    "Total revenue". The test is on the opening phrase: a label that merely
    contains "total" somewhere in a sentence is not announcing one.
    """
    label = _LEADING_APPARATUS.sub("", normalize(text))
    return any(
        label == word or label.startswith(word + " ") for word in TOTAL_LEXICON
    )


# --- R-1 -------------------------------------------------------------------
# row_label(cells)          the leading cell of a row, normalized
# header_semantics(labels)  is_total_column / is_period_column / period,
#                           moved out of engines/table/headers.py
