"""What a financial-table label means.

The financial-table engine owns interpretation; general table detection keeps
owning which bands are
header and what they say as printed. `engines.table.headers.header_row_count`
stays exactly where it is -- the grid cannot build its rows without it, and
table detection has to remain highly accurate for linking, which is what it is
used for almost all of the time. The other half of that module, reading what a
label *means*, belongs here.

One thing did not move, deliberately. `engines.table.headers.period_in` reads
the printed period out of a label, and `table-structure-v1` still publishes that
text in its own `period` field -- so moving the function here would have made
table detection depend on the analysis tier to fill a field it owns. The
judgement built on top of it lives here: whether a column *is* a period column,
whether it names a total, and what a row label announces. Reconcile consumes
this interpretation and never reads `table-structure-v1.period` as an asserted
financial fact.
"""
from __future__ import annotations

import re

from engines.table.headers import period_in


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

# The words a *column* header uses to say the column is a total of the columns
# beside it. Narrower than the row lexicon on purpose: a cross-foot is attempted
# only against a column whose own header names a total, and "Net" heading a
# column means net of something rather than the sum of its neighbours.
TOTAL_COLUMN_LEXICON: tuple[str, ...] = (
    "total",
    "consolidated",
    "combined",
)

# What a *column* header says to disqualify the column from addition. Matched at
# a word boundary and not as a bare substring: "rate" inside "Corporate" is the
# most expensive false match there is, because Corporate is a real segment column
# in every segment schedule a filing prints, and silencing it leaves one column
# of an otherwise fully ticked row unfooted. "Separate" and "Incorporated" fail
# the same way. A trailing boundary is deliberately not required, so "rates",
# "averaged" and "percentage" still match.
NON_ADDITIVE_COLUMN_TERMS: tuple[str, ...] = (
    "percent", "%", "rate", "average", "per share", "per unit", "margin",
)

# "%" carries no word boundary of its own and is tested as a plain substring.
_NON_ADDITIVE_SYMBOLS = tuple(
    term for term in NON_ADDITIVE_COLUMN_TERMS if not term[0].isalpha()
)
_NON_ADDITIVE_WORDS = re.compile(
    r"\b(?:%s)"
    % "|".join(
        re.escape(term)
        for term in NON_ADDITIVE_COLUMN_TERMS
        if term[0].isalpha()
    )
)

_COLLAPSE = re.compile(r"\s+")
# Leading list and footnote apparatus, which sits between the margin and the
# word the label actually starts with.
_LEADING_APPARATUS = re.compile(r"^[\s\-–—•\(\)\[\]0-9.]+")


def normalize(text: str) -> str:
    """A label reduced to what it says: collapsed whitespace, lower case."""
    return _COLLAPSE.sub(" ", (text or "").strip()).lower()


def row_label(text: str) -> str:
    """A row's label as a finding will quote it back.

    Whitespace collapsed and case kept, because the sentence a finding prints
    names the row: *the five rows above "Total net sales" sum to…*. Matching is
    done through `normalize`, which lower-cases; what is published is what the
    page says.
    """
    return _COLLAPSE.sub(" ", (text or "").strip())


def _opens_with(label: str, lexicon: tuple[str, ...]) -> bool:
    """Does the label's opening phrase come from this lexicon?

    Leading apparatus is stripped first so "(1) Total revenue" reads the same as
    "Total revenue". The test is on the opening phrase: a label that merely
    contains "total" somewhere in a sentence is not announcing one.
    """
    stripped = _LEADING_APPARATUS.sub("", label)
    return any(
        stripped == word or stripped.startswith(word + " ") for word in lexicon
    )


def is_total_label(text: str) -> bool:
    """Whether a row label announces itself as a total."""
    return _opens_with(normalize(text), TOTAL_LEXICON)


def is_total_column(text: str) -> bool:
    """Whether a column header names a total of the columns beside it."""
    return _opens_with(normalize(text), TOTAL_COLUMN_LEXICON)


def is_period_column(text: str) -> bool:
    """Whether a column header parses as a period or a year.

    Two or more of these in one table disables cross-footing for the table
    entirely: it is a comparative statement, and adding 2025 to 2024 is nonsense
    no tolerance model would catch.
    """
    return bool(period_in(text))


def is_non_additive_column(text: str) -> bool:
    """Whether the header names a rate, average, or per-unit measure.

    On a word boundary, so a segment schedule's Corporate column is a column of
    amounts like any other rather than a rate.
    """
    normalized = normalize(text)
    if any(symbol in normalized for symbol in _NON_ADDITIVE_SYMBOLS):
        return True
    return bool(_NON_ADDITIVE_WORDS.search(normalized))


def header_semantics(labels: list[str]) -> list[dict]:
    """Per column, what Reconcile makes of that column's header label.

    `labels` is `table-structure-v1`'s `header.labels`, which is the printed
    text joined across the header bands -- table detection's fact. Everything
    returned here is Reconcile's reading of it.
    """
    published: list[dict] = []
    for index, text in enumerate(labels):
        entry = {
            "columnIndex": index,
            "text": text,
            "isTotalColumn": is_total_column(text),
            "isPeriodColumn": is_period_column(text),
        }
        period = period_in(text)
        if period:
            entry["period"] = period
        published.append(entry)
    return published
