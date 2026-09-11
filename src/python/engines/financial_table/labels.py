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
    "gross profit",
    "gross margin",
    "gross income",
    "gross earnings",
    "balance at",
    "balance as of",
    "balances at",
    "balance",
)

# The words a *column* header uses to say the column is a result of the columns
# beside it. A Net column is included because the cross-foot search can preserve
# printed signs or reverse a contiguous suffix: gross less accumulated
# amortization is a financial-statement cross-foot just as surely as fees plus
# awards equals Total. The comparative-period guard remains separate.
TOTAL_COLUMN_LEXICON: tuple[str, ...] = (
    "total",
    "consolidated",
    "combined",
    "net",
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
    "useful life",
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

# Typographic apostrophes and quotes, folded to their ASCII form. A statement
# sets "stockholders' equity" with a curly apostrophe and a lexicon that
# compares against the straight one silently fails to recognise the row --
# which is exactly how the balance-sheet side boundary is read.
_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u02bc": "'", "\u0060": "'"})

_NONCONTROLLING_ALLOCATION = re.compile(
    r"^net (?:income|loss|earnings) attributable to "
    r"(?:redeemable )?noncontrolling\b"
)

_CASH_FLOW_ACTIVITY = re.compile(
    r"^(?:net )?cash\b.*\b(operating|investing|financing) activities$"
)

_EQUITY_HEADER_TERMS: tuple[str, ...] = (
    "common stock",
    "additional paid-in capital",
    "additional paid in capital",
    "retained earnings",
    "accumulated deficit",
    "accumulated other comprehensive",
    "treasury stock",
    "total stockholders' equity",
    "total shareholders' equity",
    "total equity",
)

_EQUITY_MOVEMENT_TERMS: tuple[str, ...] = (
    "common stock",
    "dividend",
    "share settlement",
    "equity award",
    "stock-based compensation",
    "stock option",
    "restricted stock",
    "treasury stock",
)


def normalize(text: str) -> str:
    """A label reduced to what it says: collapsed whitespace, lower case."""
    return _COLLAPSE.sub(" ", (text or "").strip().translate(_QUOTES)).lower()


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


def is_balance_state_label(text: str) -> bool:
    """Whether a row states a balance that may seed a rollforward.

    A balance can be an ending total when rows precede it, but the same printed
    phrase at the top of a block is its opening state and has no addends above.
    The caller combines this semantic fact with the arithmetic outcome rather
    than globally removing balance rows from nomination.
    """
    label = normalize(text)
    if is_explicit_opening_state_label(label):
        return True
    return _opens_with(
        label,
        (
            "balance at",
            "balances at",
            "balance as of",
            "balances as of",
            "balance at the beginning",
            "beginning balance",
            "opening balance",
        ),
    )


def is_explicit_opening_state_label(text: str) -> bool:
    """Whether the words themselves identify the start of a rollforward."""
    label = normalize(text)
    return any(
        phrase in label
        for phrase in (
            "beginning balance",
            "beginning balances",
            "at beginning of",
            "at the beginning of",
            "opening balance",
            "balance at january 1",
            "balances at january 1",
        )
    )


def is_noncontrolling_allocation_label(text: str) -> bool:
    """Whether a Net-labelled row is an allocation component, not a total."""
    return bool(_NONCONTROLLING_ALLOCATION.match(normalize(text)))


def is_non_total_net_measure(text: str) -> bool:
    """Whether Net modifies the name of a component rather than a result row."""
    label = normalize(text)
    return _opens_with(
        label,
        (
            "net operating loss",
            "net operating losses",
            "net actuarial",
            "net product sales",
            "net service sales",
            "net of tax",
        ),
    )


def is_net_income_or_loss_label(text: str) -> bool:
    """Whether a row is the Net income/loss result another statement may reuse."""
    label = normalize(text)
    return bool(
        re.match(
            r"^net (?:income|loss|earnings)(?: \((?:income|loss)\))?"
            r"(?: attributable to .+)?$",
            label,
        )
    )


def cash_flow_activity(text: str) -> str | None:
    """The activity named by a cash-flow result row, when it names one."""
    match = _CASH_FLOW_ACTIVITY.match(normalize(text))
    return match.group(1) if match else None


def is_equity_period_movement(
    text: str,
    header_labels: list[dict],
    row_labels: list[str] | None = None,
) -> bool:
    """Whether Net income/loss is a movement inside an equity rollforward.

    The same words are a genuine total on an income statement. They are a
    period movement when the surrounding columns are equity accounts, so the
    context—not an issuer or form name—decides the meaning.
    """
    label = normalize(text)
    if not _opens_with(label, ("net income", "net loss", "net earnings")):
        return False
    context = " ".join(
        [normalize(item.get("text", "")) for item in header_labels]
        + [normalize(item) for item in (row_labels or [])]
    )
    if any(
        cash_flow_activity(item) is not None
        or "cash generated by operating activities" in item
        or "cash provided by operations" in item
        for item in (normalize(row) for row in (row_labels or []))
    ):
        return False
    equity_terms = sum(term in context for term in _EQUITY_HEADER_TERMS)
    movement_terms = sum(term in context for term in _EQUITY_MOVEMENT_TERMS)
    rollforward_markers = (
        any(marker in context for marker in ("beginning balance", "beginning balances"))
        and any(marker in context for marker in ("ending balance", "ending balances"))
    )
    return (
        equity_terms >= 2
        or (equity_terms >= 1 and rollforward_markers)
        or (movement_terms >= 2 and rollforward_markers)
    )


def is_clearly_non_additive_column(text: str) -> bool:
    """Whether a header unambiguously names a rate, average, or state measure.

    General header bands can occasionally concatenate an Amount column with a
    neighbouring Interest Rate column. `amount` therefore vetoes word-only
    rate/average evidence, while a percent sign and per-share/per-unit wording
    remain unambiguous. Maximum shares remaining under a repurchase program is
    a state at each date, not a column whose Total row should foot vertically.
    """
    header = normalize(text)
    if not header:
        return False
    if "maximum number of shares" in header or "may yet be purchased" in header:
        return True
    if "amount" in header and not any(
        marker in header for marker in ("%", "per share", "per unit")
    ):
        return False
    return is_non_additive_column(header)


def separates_balance_sheet_sides(previous: str, current: str) -> bool:
    """Whether the earlier total closes assets before liabilities and equity.

    A balance sheet presents two equal sides in one vertical statement. Total
    assets is proof of the first side and a boundary for the second; it is never
    an addend of Total liabilities and equity even though both totals often
    share one detected table and the same numeric columns.
    """
    before = normalize(previous)
    after = normalize(current).replace("&", "and")
    return _opens_with(before, ("total assets",)) and _opens_with(
        after,
        ("total liabilities and equity", "total liabilities and stockholders' equity"),
    )


def is_total_column(text: str) -> bool:
    """Whether a column header names an additive or subtractive row result."""
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
