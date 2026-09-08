"""General grid x text geometry x values -> a financial cell layer.

The financial-table engine's first job is the one nothing else in the system
does: connect a
value to a cell. `document-values-v1` carries bounds and no membership -- no
table id, no row, no column, because the values engine's test for a figure
standing in a column is a geometric proxy that deliberately works whether or not
a table was detected around it. And `table-structure-v1` carries grid geometry
and header labels but no cell text. Neither can answer "what is in row 4 of
column 2", so this module builds the join.

The join is derived per scan, over the grid the scan itself re-detected, and is
never published back into `document-values-v1`. That is what keeps the
membership a description of the grid the analysis actually used, which the cache
build's grid may no longer be.

Two narrowings are decisions rather than omissions, and both serve the hard pass
bar of zero false ties:

* **Only a number becomes a cell value.** A percentage or a date is published
  with its text and no `normalizedValue`, so it can never enter a sum. A column
  of percentages therefore reads to the run search as a column with no values,
  which ends a candidate rather than contributing to it -- the correct outcome,
  and one reached without the module knowing what "per share" means.
* **Two spans in one cell publish none.** A cell reading "2025 - 2062" or
  "0.03% - 5.75%" is a range, not an addend. Its text is published so the row
  still reads, and its value is withheld.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from engines.table.handoff import (
    AnalysisLayout,
    AnalysisLine,
    JsonMapping,
    assign_line_tokens,
    table_geometry_digest,
)

from . import labels


# A dash alone in a detected table cell is an addend worth zero, uniformly and
# wherever in a run it appears. Every dash a filing sets: hyphen, figure dash,
# en dash, em dash, horizontal bar.
DASHES = "-‐‒–—―"

# The accounting convention is a single rule above a total and a double rule
# beneath a grand total, and the double rule is the one a reader looks for. Two
# rules are that mark when they are far enough apart to be two lines and close
# enough to be one gesture; a gap outside this range is two unrelated rules.
DOUBLE_RULE_MIN_GAP = 0.0004
DOUBLE_RULE_MAX_GAP = 0.006

# How far below a row's own glyphs its rule may be drawn, as a multiple of that
# row's text height. Bounded above by the next row's glyphs as well, so a tightly
# set statement cannot lend one row the rule belonging to the row beneath it.
RULE_REACH = 1.25

# A rule is sometimes drawn a hair above the glyph box it underlines.
RULE_OVERLAP = 0.0015

# Above what share of ruled rows a table stops being a statement drawing the
# accounting convention and starts being a ruled grid, where every row is
# bordered and a rule above a row says nothing about that row at all.
RULED_GRID_SHARE = 0.5


@dataclass
class TableCells:
    """One table's cell layer, in the two shapes its consumers need.

    `published` is the contract's `cells` array. `by_position` and `row_labels`
    are the working index nomination and the run search read: both walk a single
    column upward from a row, which a flat array in row-then-column order cannot
    answer without a scan per step.
    """

    table_id: str
    page_index: int
    column_count: int
    row_count: int
    published: list[dict] = field(default_factory=list)
    by_position: dict[tuple[int, int], dict] = field(default_factory=dict)
    by_id: dict[str, dict] = field(default_factory=dict)
    row_labels: list[str] = field(default_factory=list)
    header_labels: list[dict] = field(default_factory=list)
    bounds: dict = field(default_factory=dict)
    provenance: str = "lattice"
    # Exact lineage back to the accepted general grid, when one contributed.
    # The digest pins geometry as well as id so a fallback cannot silently drift
    # while still appearing to describe the same source table.
    source_general_table_id: str | None = None
    source_general_geometry_digest: str | None = None
    # Stable numeric right-edge alignments for lattice blocks. Grid fallbacks
    # leave this empty because their columns are the general table's bands.
    value_column_edges: tuple[float, ...] = ()
    # Body rows carrying the grand-total convention -- a double rule beneath
    # them. Empty when the block has no ruling evidence to read, which is a
    # different fact from a block whose rows carry no double rule.
    double_ruled: frozenset = frozenset()
    # Body rows with a rule drawn in the gap above them: the other half of the
    # same convention, a single rule above a total. Empty for a ruled grid,
    # where every row is bordered and the mark carries no information.
    ruled_above: frozenset = frozenset()

    def cell(self, row_index: int, column_index: int) -> dict | None:
        return self.by_position.get((row_index, column_index))

    def is_caption_row(self, row_index: int) -> bool:
        """A row that fills only the label column and no value column at all.

        "Deferred tax assets:" and "August 3, 2025 to August 30, 2025:" are the
        statement separating one block from the next, not a row of it. This is a
        different fact from a row that carries figures elsewhere and leaves this
        column blank, which really is the end of a block.
        """
        return not any(
            column for (row, column) in self.by_position if row == row_index and column
        )


def text_bands(built: "TableCells") -> dict[int, tuple[float, float]]:
    """The top and bottom of each row's own glyphs, which is what a rule hangs off.

    Not the grid's row band: a band runs from one row's boundary to the next and
    swallows the rules on both sides of it, so a rule read against a band cannot
    be told from the rule belonging to the row above.
    """
    bands: dict[int, tuple[float, float]] = {}
    for cell in built.published:
        bounds = cell.get("bounds")
        if not bounds:
            continue
        row = int(cell["rowIndex"])
        top = float(bounds["y"])
        bottom = top + float(bounds["height"])
        if row in bands:
            top = min(top, bands[row][0])
            bottom = max(bottom, bands[row][1])
        bands[row] = (top, bottom)
    return bands


def mark_rules_above(built: "TableCells", rule_positions) -> None:
    """Record which rows have a rule drawn in the gap above them.

    The other half of the accounting convention, and the half that reaches the
    rows no lexicon can: *Cash generated by operating activities*,
    *Increase/(Decrease) in cash*, and the second net line of Apple's commercial
    paper note, whose label opens with "Proceeds" and which nothing else in the
    system can propose.

    The window is the gap between the row above's glyphs and this row's own, so
    the same drawn line is the rule below one row and the rule above the next --
    which is what it is. A caption row consumes the rule above it exactly as a
    figure row does, so a rule separating a block from its heading is not lent
    to the first figure row of that block.

    A table where most rows are ruled is a ruled grid rather than a statement
    observing the convention, and the mark is dropped entirely for it: every row
    being bordered says nothing about any row.
    """
    positions = sorted({round(float(position), 6) for position in rule_positions or ()})
    if not positions:
        return
    bands = text_bands(built)
    ordered = sorted(bands)
    found: set[int] = set()
    for index, row in enumerate(ordered):
        top = bands[row][0]
        if index == 0:
            continue
        floor = bands[ordered[index - 1]][1]
        if any(floor <= position <= top + RULE_OVERLAP for position in positions):
            found.add(row)
    if len(found) > len(ordered) * RULED_GRID_SHARE:
        return
    built.ruled_above = frozenset(found)


def mark_double_rules(built: "TableCells", rule_positions) -> None:
    """Record which rows a double rule is drawn beneath.

    Reads `table-structure-v1`'s published horizontal rule positions, which the
    table module owns; what a rule *means* is decided here, per the division in
    `labels`. The positions carry no extent, so a double rule is read as a fact
    about the row rather than about one cell of it -- which is how a filing draws
    it, across every value column of the total.
    """
    positions = sorted({round(float(position), 6) for position in rule_positions or ()})
    if len(positions) < 2:
        return
    bands = text_bands(built)
    ordered = sorted(bands)
    found: set[int] = set()
    for index, row in enumerate(ordered):
        top, bottom = bands[row]
        reach = bottom + max(1e-4, (bottom - top) * RULE_REACH)
        if index + 1 < len(ordered):
            reach = min(reach, bands[ordered[index + 1]][0])
        window = [
            position
            for position in positions
            if bottom - RULE_OVERLAP <= position <= reach
        ]
        if any(
            DOUBLE_RULE_MIN_GAP <= second - first <= DOUBLE_RULE_MAX_GAP
            for first, second in zip(window, window[1:])
        ):
            found.add(row)
    built.double_ruled = frozenset(found)


def horizontal_rules(table: JsonMapping | None) -> list[float]:
    """The horizontal rule positions a detected table published, if any."""
    if not table:
        return []
    return list((table.get("rulings") or {}).get("horizontal") or [])


def boundaries_of(table: JsonMapping) -> list[float]:
    """The column boundaries the published extents were cut from.

    `table-structure-v1` publishes columns as contiguous extents, so every
    interior edge is one column's `x1` and the next column's `x0`. Recovering
    them is what lets this module place tokens through the stable table handoff
    -- the same placement, including its currency and percent re-attachment,
    that the grid was fitted with. Placing tokens a second way here would let
    the cell layer disagree with the table the scan is describing.
    """
    columns = table.get("columns", [])
    return [float(column["x1"]) for column in columns[:-1]]


def decimals_of(text: str) -> int:
    """Printed decimal places, read from the span's own text.

    Not from `normalizedValue`: that is canonical decimal and strips trailing
    zeros, so "1.50" arrives as "1.5" and a column of two-decimal amounts would
    disagree with itself. The printed form is what the eligibility rule is about.
    """
    digits = ""
    for character in reversed(text or ""):
        if character.isdigit():
            digits += character
            continue
        if character == "." and digits:
            return len(digits)
        if character in ",'  ":
            # A group separator inside the integer part; keep scanning left.
            digits = ""
            continue
        if digits:
            return 0
    return 0


def _is_dash(tokens: list) -> bool:
    """Is this cell a dash and nothing else?

    Markers are ignored, because an accounting layout floats the currency symbol
    beside the dash it qualifies and "$ --" is still a dash.
    """
    meaningful = [token for token in tokens if not token.is_marker]
    if len(meaningful) != 1:
        return False
    text = meaningful[0].text.strip()
    return bool(text) and all(character in DASHES for character in text)


def _bounds_of(tokens: list) -> dict:
    x0 = min(token.x0 for token in tokens)
    y0 = min(token.y0 for token in tokens)
    x1 = max(token.x1 for token in tokens)
    y1 = max(token.y1 for token in tokens)
    return {
        "x": max(0.0, min(1.0, round(x0, 6))),
        "y": max(0.0, min(1.0, round(y0, 6))),
        "width": max(1e-6, min(1.0, round(x1 - x0, 6))),
        "height": max(1e-6, min(1.0, round(y1 - y0, 6))),
    }


def _numbers_on(page_values: dict | None) -> list[tuple[float, float, dict]]:
    """Every number span on the page, with the point that places it in a cell.

    Percentages and dates are left out: they measure, but they do not add, and
    the first pass would rather publish a cell with no value than let one into a
    column of amounts.
    """
    if not page_values:
        return []
    placed = []
    for value in page_values.get("values", []):
        if value.get("kind") != "number":
            continue
        if not value.get("normalizedValue"):
            continue
        bounds = value["bounds"]
        placed.append(
            (
                bounds["x"] + bounds["width"] / 2,
                bounds["y"] + bounds["height"] / 2,
                value,
            )
        )
    return placed


def build_table_cells(
    table: JsonMapping,
    layout: AnalysisLayout,
    page_values: dict | None,
    *,
    page_index: int,
) -> TableCells:
    """The cell layer for one detected table."""
    columns = table.get("columns", [])
    rows = table.get("rows", [])
    body = [row for row in rows if row.get("kind") == "body"]
    column_count = len(columns)
    built = TableCells(
        table_id=table["id"],
        page_index=page_index,
        column_count=column_count,
        row_count=len(body),
        row_labels=[""] * len(body),
        bounds=dict(table.get("bounds", {})),
        provenance="table",
        source_general_table_id=str(table["id"]),
        source_general_geometry_digest=table_geometry_digest(table),
    )
    header = table.get("header") or None
    if header:
        built.header_labels = labels.header_semantics(list(header.get("labels", [])))
    if column_count < 2 or not body:
        return built

    boundaries = boundaries_of(table)
    left = float(columns[0]["x0"])
    right = float(columns[-1]["x1"])
    numbers = _numbers_on(page_values)

    # Every row's tokens are placed first, and the labels are decided afterwards,
    # because which column labels a cell is a fact about the whole table.
    rows_of_tokens: list[list[list]] = []
    for row in body:
        y0, y1 = float(row["y0"]), float(row["y1"])
        buckets: list[list] = [[] for _ in range(column_count)]
        for line in _lines_in(layout, y0, y1):
            # Tokens outside the table's own extent would otherwise fall into
            # the first or last column -- `column_index` has no notion of an
            # edge -- and a page number or a marginal note would arrive as a row
            # label.
            inside = [token for token in line.tokens if left <= token.center <= right]
            if not inside:
                continue
            for index, bucket in enumerate(
                assign_line_tokens(replace(line, tokens=inside), boundaries)
            ):
                if index < column_count:
                    buckets[index].extend(bucket)
        rows_of_tokens.append(buckets)

    label_columns = _label_columns(rows_of_tokens, columns, body, numbers)

    for row_index, buckets in enumerate(rows_of_tokens):
        y0, y1 = float(body[row_index]["y0"]), float(body[row_index]["y1"])
        texts = [" ".join(token.text for token in bucket).strip() for bucket in buckets]
        built.row_labels[row_index] = labels.row_label(texts[0])

        for column_index, tokens in enumerate(buckets):
            if not tokens:
                # An empty cell is not published. Its absence is the fact a run
                # search reads: a blank ends a candidate rather than
                # contributing a zero to it.
                continue
            cell = {
                "id": f"{table['id']}-r{row_index}-c{column_index}",
                "rowIndex": row_index,
                "columnIndex": column_index,
                "text": texts[column_index],
                "bounds": _bounds_of(tokens),
            }
            span = _value_in(numbers, columns[column_index], y0, y1)
            if span is not None:
                cell["spanId"] = span["id"]
                cell["normalizedValue"] = span["normalizedValue"]
                cell["decimals"] = decimals_of(span["text"])
            elif _is_dash(tokens):
                cell["dash"] = True
            if "spanId" in cell or "dash" in cell:
                label = labels.row_label(
                    _label_for(texts, column_index, label_columns)
                )
                if label:
                    cell["rowLabel"] = label
            built.published.append(cell)
            built.by_position[(row_index, column_index)] = cell
            built.by_id[cell["id"]] = cell

    mark_double_rules(built, horizontal_rules(table))
    mark_rules_above(built, horizontal_rules(table))
    return built


def _label_columns(
    rows_of_tokens: list[list[list]], columns: list[dict], body: list[dict], numbers
) -> list[int]:
    """Which columns carry row labels rather than figures.

    Column 0 always does. The reason to look for others is the two-panel balance
    sheet: assets down the left, liabilities and equity down the right, printed
    as one grid. Its fourth column's figures are labelled by its third, and
    reading them against the first produces nonsense with a straight face --
    "Total current assets" beside the balance of income taxes payable, which is a
    break reported in a statement that foots perfectly.

    A label column is one whose cells are mostly words and mostly not values. The
    alphabetic test is what keeps a floated currency column or a footnote column
    from being mistaken for one: those carry text and no letters, and treating
    one as a label column would quietly retitle every row to its right.
    """
    found = [0]
    for column_index in range(1, len(columns)):
        cells = [
            " ".join(token.text for token in row[column_index]).strip()
            for row in rows_of_tokens
            if row[column_index]
        ]
        valued = sum(
            1
            for row_index, row in enumerate(rows_of_tokens)
            if row[column_index]
            and _value_in(
                numbers,
                columns[column_index],
                float(body[row_index]["y0"]),
                float(body[row_index]["y1"]),
            )
            is not None
        )
        if is_label_column(cells, valued):
            found.append(column_index)
    return found


def is_label_column(cells: list[str], valued: int) -> bool:
    """Mostly words, mostly not values, and more than one of them."""
    if len(cells) < 2:
        return False
    wordy = sum(1 for text in cells if any(character.isalpha() for character in text))
    return wordy * 2 > len(cells) and valued * 2 <= len(cells)


def _label_for(texts: list[str], column_index: int, label_columns: list[int]) -> str:
    """The nearest label column to this cell's left, and what it says."""
    for candidate in reversed(label_columns):
        if candidate < column_index and texts[candidate]:
            return texts[candidate]
    return texts[0] if column_index else ""


def _lines_in(
    layout: AnalysisLayout, y0: float, y1: float
) -> list[AnalysisLine]:
    """The visual lines whose centre falls in this row band.

    By centre rather than by overlap, so a tall line straddling a band edge
    belongs to one row instead of both -- which is also how the grid built the
    row in the first place.
    """
    return [line for line in layout.lines if y0 <= line.center <= y1 and line.tokens]


def _value_in(
    numbers: list[tuple[float, float, dict]], column: dict, y0: float, y1: float
) -> dict | None:
    """The one number span standing in this cell, or nothing.

    Two spans in one cell publish none: a range is not an addend, and a cell
    that cannot be reduced to a single figure must not be able to enter a sum.
    """
    x0, x1 = float(column["x0"]), float(column["x1"])
    found = [
        value
        for center_x, center_y, value in numbers
        if x0 <= center_x <= x1 and y0 <= center_y <= y1
    ]
    return found[0] if len(found) == 1 else None
