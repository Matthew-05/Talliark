"""Detect a leading header band and merge its label text by column."""
from __future__ import annotations

import re


_NUMBER = re.compile(r"^[\s($+\-]*[\d,.%]+[)\s]*$")

# A dash is the accounting zero, often with its floated currency marker. It
# belongs to the numeric body when a period band is being tested against the row
# below it: the first row of a maturity schedule is mostly dashes, and refusing
# to count them made the year band look like a row over non-numeric text.
_DASH = re.compile(r"^[\s$€£¥—–-]+$")

# The tail of a date whose head was wrapped onto an earlier line: the
# "31, 2017" that finishes "Estimated Fair Value as of December". It shares a
# visual line with the period band -- the wrapped label is set to the right of
# the years -- so the band reads as years plus one stray fragment. The fragment
# is a continuation of the header label above, not a member of the period band.
_DATE_TAIL = re.compile(r"^\d{1,2},?\s+(?:19|20)\d{2}$")


# A period as it appears inside a longer label. `_PERIOD` matches a cell that is
# nothing but a period; this one finds the period within "Years ended September
# 28, 2024" or "As of 2025".
_PERIOD_IN_TEXT = re.compile(
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s*(?:19|20)\d{2}"
    r"|FY\s*\d{2,4}"
    r"|Q[1-4]\s*(?:19|20)?\d{2,4}"
    r"|(?:19|20)\d{2}",
    re.IGNORECASE,
)


def period_in(text: str) -> str:
    """The period a label carries, or an empty string when it carries none."""
    found = _PERIOD_IN_TEXT.search(text or "")
    return found.group(0).strip() if found else ""


def _is_number(value: str) -> bool:
    """Is this cell an amount?

    Spaces are removed first: a table sets its percentages as "10 %" and its
    amounts as "$ 34,550", and treating those as non-numeric hid the period
    header of every table that mixes amounts with change columns.
    """
    return bool(_NUMBER.match(value.replace(" ", "")))


def _is_value_cell(value: str) -> bool:
    """Does this cell hold a measurement rather than a label?

    Amounts are not always single numbers. A term-debt table states its maturities
    as "2025 – 2062" and its rates as "0.03% – 5.75%", and reading those as labels
    left the table looking as though its body never began — so its three-line
    header was never recognized. A cell with digits and no letters is a value.
    """
    if _is_number(value):
        return True
    return any(character.isdigit() for character in value) and not any(
        character.isalpha() for character in value
    )
# Period labels carry no alphabetic characters at all, so alpha-density scoring
# rejects them. They are still the strongest header evidence a financial
# statement offers.
_PERIOD = re.compile(
    r"^(?:(?:19|20)\d{2}|FY\s*\d{2,4}|Q[1-4](?:\s*(?:19|20)\d{2})?"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
    r"(?:\s+\d{1,2})?(?:,?\s*(?:19|20)\d{2})?)$",
    re.IGNORECASE,
)


def _is_caption_row(values: list[str], filled_columns: list[int]) -> bool:
    """A section label such as "Deferred tax assets:" carrying no values."""
    return bool(values) and bool(values[0]) and not any(
        values[index] for index in filled_columns if index < len(values)
    )


def _is_offset_period_band(values: list[str], below: list[str]) -> bool:
    """A period band that starts partway across the table, leaving cell 0 empty.

    `2025 2024 2023` over an unlabelled first column is the standard financial
    statement shape. It is only accepted as a header when the band below it is
    numeric in the same columns, so a value-only body row can never be promoted.
    """
    if not values or values[0]:
        return False
    filled = [(index, value) for index, value in enumerate(values) if value]
    if len(filled) < 2:
        return False
    # "2025 | Change | 2024 | Change | 2023" is one band: a comparison column
    # labelled in words sits between the periods. A date tail such as
    # "31, 2017" is the continuation of a wrapped label, not a member of the
    # band, and is ignored rather than read as an amount.
    if not all(
        _PERIOD.match(value)
        or _DATE_TAIL.match(value)
        or any(character.isalpha() for character in value)
        for _index, value in filled
    ):
        return False
    if not any(_PERIOD.match(value) for _index, value in filled):
        return False
    numeric_below = [
        index
        for index, _value in filled
        if index < len(below)
        and below[index]
        and (_is_number(below[index]) or _DASH.match(below[index]))
    ]
    return len(numeric_below) >= max(2, len(filled) - 1)


def _header_score(values: list[str]) -> float:
    nonempty = [value for value in values if value]
    if not nonempty:
        return 0.0
    alpha = sum(any(character.isalpha() for character in value) for value in nonempty)
    return alpha / len(nonempty)


def _numeric_score(values: list[str]) -> float:
    nonempty = [value for value in values if value]
    if not nonempty:
        return 0.0
    return sum(_is_value_cell(value) for value in nonempty) / len(nonempty)


def _is_period_only_band(values: list[str]) -> bool:
    """A band of nothing but periods above the labels they date.

    "2025" alone over a segment table, or "2025    2024" over two column groups.
    The first cell has to be empty: a maturities table whose first column really
    is a year ("2026   $12,393") is data, not a header.
    """
    filled = [value for value in values if value]
    if not filled or (values and values[0]):
        return False
    return all(_PERIOD.match(value) for value in filled)


def header_row_count(
    row_texts: list[list[str]],
    first_column_body,
    *,
    ruled: bool,
    column_count: int,
) -> int:
    """How many leading bands are header, judged from cell text alone.

    `first_column_body(index)` returns the first-column text of the few rows that
    follow band `index`; a band only becomes a header when real row labels start
    underneath it.
    """
    if len(row_texts) < 2 or column_count < 2:
        return 0

    def labels_a_real_column(index: int) -> bool:
        body = first_column_body(index)
        return bool(body) and sum(bool(value) for value in body) * 2 > len(body)

    # A bare period band over one or more label bands is the segment-reporting
    # shape: "2025" alone, then "Americas Europe ... Total", then the numbers.
    # It carries no alphabetic text at all, so the alpha-density rules below can
    # never see it, and without this branch the labels become body rows.
    if _is_period_only_band(row_texts[0]):
        for count in range(min(3, len(row_texts) - 1), 0, -1):
            if (
                all(
                    # A real title band labels several columns. A section caption
                    # ("Gross margin:") fills only the first cell and is a row of
                    # the table, not part of its header.
                    _header_score(row_texts[index]) >= 0.5
                    and sum(1 for value in row_texts[index] if value) >= 2
                    for index in range(1, count)
                )
                # A section caption often sits between the titles and the first
                # amounts, so the numeric body is looked for over the next few
                # bands rather than only the one directly below.
                and any(
                    _numeric_score(row_texts[index]) >= 0.25
                    for index in range(count, min(count + 3, len(row_texts)))
                )
                and labels_a_real_column(count)
            ):
                return count

    # The offset-period branches come first: a period band is itself numeric, so a
    # spanning caption above one would otherwise satisfy the generic alpha-then-
    # numeric rule and claim the whole header, leaving the periods as body data.
    if (
        len(row_texts) >= 3
        and _header_score(row_texts[0]) >= 0.5
        and _is_offset_period_band(row_texts[1], row_texts[2])
        and labels_a_real_column(2)
    ):
        # A spanning caption such as "Years ended December 31," above the periods.
        return 2
    if any(
        _is_offset_period_band(row_texts[0], row_texts[index])
        for index in range(1, min(3, len(row_texts)))
    ) and labels_a_real_column(1):
        # A statement often puts a section caption ("Deferred tax assets:") between
        # the period band and the first numbers. The periods are still the header.
        return 1
    if _header_score(row_texts[0]) >= 0.5 and _numeric_score(row_texts[1]) >= 0.25:
        return 1
    if (
        len(row_texts) >= 3
        and _header_score(row_texts[0]) >= 0.5
        and _header_score(row_texts[1]) >= 0.5
        and _numeric_score(row_texts[2]) >= 0.25
    ):
        return 2
    # A stacked header: several label-only bands above the first numeric row, as
    # a share-repurchase or segment table sets its column titles across four or
    # five lines. Each band alone looks like a body row of words; together they
    # are one header, and treating them as data buried the real rows.
    def _label_only(values: list[str]) -> bool:
        filled = [value for value in values if value]
        return len(filled) == 1 and bool(values and values[0])

    # A caption sitting above the titles ("(in thousands, and per-share amounts):")
    # belongs to the header block, but it fills only the first cell, which is also
    # what a section label looks like — so it is skipped here and counted back in.
    start = 0
    while start < len(row_texts) and _label_only(row_texts[start]):
        start += 1
    bands = 0
    for values in row_texts[start:]:
        if _label_only(values):
            break
        if not any(values) or _numeric_score(values) >= 0.25 or _header_score(values) < 0.6:
            break
        bands += 1
    if (
        bands >= 2
        and column_count >= 3
        and start + bands < len(row_texts)
        # A section caption often sits between the titles and the first amounts,
        # so the numeric body is looked for over the next few bands.
        and any(
            _numeric_score(row_texts[index]) >= 0.25
            for index in range(start + bands, min(start + bands + 3, len(row_texts)))
        )
        and labels_a_real_column(start + bands)
    ):
        return start + bands

    if (
        _header_score(row_texts[0]) >= 0.9
        and sum(bool(value) for value in row_texts[0]) >= max(2, column_count - 1)
        and (ruled or len(row_texts) >= 3)
    ):
        # Forms and reference tables (an exhibit index, a class-of-stock table) may
        # contain no numeric column at all, so the numeric-body test never fires.
        # A first band that labels essentially every column is still strong header
        # evidence; requiring a body of at least two rows keeps a two-line block
        # from declaring itself a header.
        return 1

    # A header that leaves the row-label column unnamed: "| Jurisdiction of
    # Incorporation" above a list of subsidiaries. It labels every value column,
    # the column it does not label carries the row labels, and the table may hold
    # no numbers at all — so none of the numeric tests above can see it.
    if (
        len(row_texts) >= 3
        and not row_texts[0][0]
        and all(row_texts[0][index] for index in range(1, column_count))
        and _header_score(row_texts[0]) >= 0.9
        and labels_a_real_column(1)
    ):
        return 1
    return 0


def detect_header_cells(
    matrix: list[list[str]], column_count: int, *, ruled: bool = False
) -> dict | None:
    """Header detection over an already-fitted grid's cell text.

    The grid is authoritative by this point, so the header rules read cells rather
    than rescanning page characters — which is both cheaper and consistent with
    the columns the rest of the table was built from.
    """
    if len(matrix) < 2 or column_count < 2:
        return None

    def first_column_body(index: int) -> list[str]:
        return [row[0] if row else "" for row in matrix[index : index + 5]]

    count = header_row_count(
        matrix[:12], first_column_body, ruled=ruled, column_count=column_count
    )
    if count == 0:
        return None
    labels = []
    for column in range(column_count):
        pieces = [
            matrix[index][column]
            for index in range(count)
            if column < len(matrix[index]) and matrix[index][column]
        ]
        labels.append(" ".join(pieces).strip())
    return {"rowCount": count, "labels": labels}
