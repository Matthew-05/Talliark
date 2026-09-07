"""Fit a column/row grid to a proposed region and assemble logical rows.

Fitting is iterative because the two halves depend on each other: columns are
inferred from the lines that look like rows, and which lines are rows depends on
where the columns are. Three passes are enough in practice — the second pass
exists mainly so that a wrapped description line stops voting on column
positions once it has been recognized as a continuation.
"""
from __future__ import annotations

import math
import re

from engines.table.layout import PageLayout, LogicalRow, TextToken, VisualLine
from engines.table.rulings import RulingSegment


# A boundary must be free of text on this share of the rows that could contradict
# it. One row may legitimately overflow its cell; systematic crossing means the
# boundary is imaginary.
_MAX_CROSSING = 0.15
# How much crossing the first, exploratory vote tolerates while it locates the
# corridors that later passes judge.
_SPANNING_CROSSING = 0.40
_MIN_SUPPORT = 0.35
_ITERATIONS = 3

# Accounting layouts float a currency or sign marker in its own whitespace island,
# well clear of the amount it belongs to. It is a marker, never a column.
_MARKER_ONLY = re.compile(r"^[$€£¥%()\[\]*†‡]+$")


class GridHypothesis:
    """Columns, logical rows and the cell text they imply."""

    def __init__(
        self,
        columns: list[dict],
        rows: list[LogicalRow],
        boundaries: list[float],
        *,
        ruled_columns: bool = False,
        ruled_rows: bool = False,
    ) -> None:
        self.columns = columns
        self.rows = rows
        self.boundaries = boundaries
        self.ruled_columns = ruled_columns
        self.ruled_rows = ruled_rows
        # Bands removed from the top of the grid because they name the columns
        # rather than fill them, as {"kind", "text"}. Kept so the period they
        # carry survives their removal.
        self.captions: list[dict] = []

    @property
    def column_count(self) -> int:
        return len(self.columns)

    def cell_matrix(self) -> list[list[str]]:
        return [row.cells for row in self.rows]


def column_index(boundaries: list[float], value: float) -> int:
    index = 0
    for boundary in boundaries:
        if value < boundary:
            return index
        index += 1
    return index


def assign_tokens(line: VisualLine, boundaries: list[float]) -> list[list[TextToken]]:
    """Place every word of a line into a column, by the word's centre."""
    buckets: list[list[TextToken]] = [[] for _ in range(len(boundaries) + 1)]
    for token in line.tokens:
        buckets[column_index(boundaries, token.center)].append(token)
    # An accounting layout floats the currency symbol in its own whitespace island
    # to the left of the amount it belongs to, which lands it at the tail of the
    # previous column. A percent sign is the mirror image: it trails its number
    # closely enough to be swept into the next column. Both are re-attached to the
    # value they qualify, so no cell reads "% $ 201,183".
    for index in range(len(buckets) - 1):
        while (
            buckets[index]
            and buckets[index][-1].kind == "currency"
            and buckets[index + 1]
            and (
                # The classic floated marker: "$" alone, then its amount.
                buckets[index + 1][0].is_value
                # Or a marker swept into the tail of the cell before it, because
                # the amount it belongs to sits tight against that cell. Currency
                # precedes its amount, so a symbol at the end of a cell that
                # already holds something is never that cell's own. Pushing it
                # right cascades: "$ 28,267 $" | "— $" | "—" becomes
                # "$ 28,267" | "$ —" | "$ —".
                or len(buckets[index]) > 1
            )
        ):
            buckets[index + 1].insert(0, buckets[index].pop())
        while (
            buckets[index + 1]
            and buckets[index + 1][0].text in ("%", ")")
            and buckets[index]
            and buckets[index][-1].is_value
        ):
            buckets[index].append(buckets[index + 1].pop(0))
    return buckets


def cells_for(line: VisualLine, boundaries: list[float]) -> list[str]:
    return [
        " ".join(token.text for token in bucket).strip()
        for bucket in assign_tokens(line, boundaries)
    ]


def occupied_columns(line: VisualLine, boundaries: list[float]) -> tuple[int, ...]:
    """Which columns hold real content — markers alone do not count as content."""
    buckets = assign_tokens(line, boundaries)
    return tuple(
        index
        for index, bucket in enumerate(buckets)
        if any(not token.is_marker for token in bucket)
    )


def _coverage(lines: list[VisualLine], left: float, right: float, step: float):
    count = max(1, int(math.ceil((right - left) / step)))
    profiles = []
    for line in lines:
        covered = bytearray(count)
        for token in line.tokens:
            start = max(0, int((token.x0 - left) / step))
            end = min(count - 1, int((token.x1 - left) / step))
            for index in range(start, end + 1):
                covered[index] = 1
        interior_start = min(token.x0 for token in line.tokens)
        interior_end = max(token.x1 for token in line.tokens)
        profiles.append((covered, interior_start, interior_end))
    return count, profiles


def _vote(
    anchors: list[VisualLine], left: float, right: float, layout: PageLayout, crossing: float
) -> list[float]:
    step = max(0.0015, (right - left) / 400)
    if len(anchors) < 2 or right <= left:
        return []
    count, profiles = _coverage(anchors, left, right, step)
    # A row of amounts is stronger evidence about where the columns are than a
    # line of column titles: titles are centred, wrapped and often span two
    # columns, while values sit squarely in theirs. Weighting the vote is what
    # lets a four-line stacked header sit above the body without dictating it.
    weights = [
        2.0 if any(token.kind in ("numeric", "percent") for token in line.tokens) else 1.0
        for line in anchors
    ]
    total_weight = sum(weights)
    required = max(2.0, total_weight * _MIN_SUPPORT)
    allowed = total_weight * crossing

    support = [0.0] * count
    against = [0.0] * count
    for weight, (covered, interior_start, interior_end) in zip(weights, profiles):
        for index in range(count):
            position = left + (index + 0.5) * step
            if covered[index]:
                against[index] += weight
            elif interior_start < position < interior_end:
                support[index] += weight

    runs: list[tuple[int, int]] = []
    start = None
    for index in range(count):
        good = support[index] >= required and against[index] <= allowed
        if good and start is None:
            start = index
        elif not good and start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, count - 1))

    boundaries: list[float] = []
    for run_start, run_end in runs:
        width = (run_end - run_start + 1) * step
        if width < layout.min_gutter * 0.5:
            continue
        # Weight the centre by how many rows support each sample, so a corridor
        # that is wide on one row and narrow on twenty settles on the twenty.
        weights = [support[index] for index in range(run_start, run_end + 1)]
        total = sum(weights) or 1
        centre = sum(
            (left + (index + 0.5) * step) * weight
            for index, weight in zip(range(run_start, run_end + 1), weights)
        ) / total
        boundaries.append(centre)
    return boundaries


def _spanning(line: VisualLine, boundaries: list[float]) -> bool:
    """Does this line lie across the columns rather than inside them?

    A header band or a spanning caption covers several columns with one word and
    fills fewer islands than there are columns. An ordinary row that happens to
    overflow one cell fails the second test, so it keeps its vote — and its veto.
    """
    crossed = sum(
        1
        for boundary in boundaries
        if any(token.x0 < boundary < token.x1 for token in line.tokens)
    )
    return crossed >= 2 and line.segment_count <= len(boundaries)


def infer_boundaries(
    lines: list[VisualLine], left: float, right: float, layout: PageLayout
) -> list[float]:
    """Column boundaries from whitespace corridors shared by many rows.

    A position supports a boundary when a row has text on both sides of it and no
    text on it. Positions beyond a short row's last word are neither support nor
    contradiction: a row that simply stops early says nothing about where the
    columns are.

    The vote is taken twice. A header band or a spanning caption sits across
    several columns at once, so on the first pass it looks like evidence against
    every boundary it covers; the second pass removes those lines from the vote
    entirely, which lets an ordinary row veto a boundary that cuts through its
    text while a header cannot veto the columns it spans.
    """
    anchors = [line for line in lines if line.segment_count >= 2 and line.tokens]
    if len(anchors) < 2 or right <= left:
        return []
    # The first pass is deliberately tolerant of text crossing a boundary: its job
    # is only to locate the corridors, and a two-line stacked header would
    # otherwise veto the very columns it labels before it can be recognized.
    proposed = _vote(anchors, left, right, layout, _SPANNING_CROSSING)
    if not proposed:
        return []
    ordinary = [line for line in anchors if not _spanning(line, proposed)]
    if len(ordinary) >= 2 and len(ordinary) < len(anchors):
        settled = _vote(ordinary, left, right, layout, _MAX_CROSSING)
        if settled:
            return settled
    # No spanning line to remove, or removing them left nothing: fall back to the
    # strict vote over every anchor, and to the tolerant proposal only if that
    # finds no columns at all.
    return _vote(anchors, left, right, layout, _MAX_CROSSING) or proposed


def align_boundaries_to_cells(
    boundaries: list[float], lines: list[VisualLine], layout: PageLayout
) -> list[float]:
    """Move each boundary so the geometry agrees with the cell assignment.

    A floated currency symbol is assigned to the amount it marks, but the
    boundary between the two columns was voted on by whitespace alone and lands
    between the symbol and its amount. The cell text then reads "$ 34,550" while
    the published columns say the "$" belongs to the row label — and every
    consumer that re-derives cells from the geometry, the viewer's extractor
    included, puts it in the wrong column.

    Each boundary is nudged past the markers that cross it, but never so far that
    it swallows a word that genuinely belongs on the other side.
    """
    if not boundaries:
        return boundaries
    aligned = list(boundaries)
    margin = layout.character_width * 0.3
    # Only rows carrying amounts get to veto a move. A column title sits between
    # the marker and the boundary — "2025" above the amounts it dates — and it
    # belongs on the same side as the amounts, so letting it object would freeze
    # the boundary in the one place that splits a cell.
    body = {
        line.index
        for line in lines
        if any(token.kind in ("numeric", "percent") for token in line.tokens)
    } or {line.index for line in lines}
    for index in range(len(aligned)):
        pushed_right: list[TextToken] = []
        pushed_left: list[TextToken] = []
        kept_left: list[TextToken] = []
        kept_right: list[TextToken] = []
        for line in lines:
            if not line.tokens:
                continue
            buckets = assign_tokens(line, aligned)
            vetoes = line.index in body
            for token in buckets[index + 1]:
                if token.center < aligned[index]:
                    pushed_right.append(token)
                elif vetoes:
                    kept_right.append(token)
            for token in buckets[index]:
                if token.center > aligned[index]:
                    pushed_left.append(token)
                elif vetoes:
                    kept_left.append(token)
        low = aligned[index - 1] if index else -1.0
        high = aligned[index + 1] if index + 1 < len(aligned) else 2.0
        # Only words that sit wholly on one side may veto the move. A centred
        # header label already lies across the boundary, and a band that is
        # broken wherever the line is drawn cannot argue about where to draw it.
        if pushed_right:
            edge = min(token.x0 for token in pushed_right)
            floor = max(
                (token.x1 for token in kept_left if token.x1 <= aligned[index]), default=low
            )
            # Centre the boundary in the corridor rather than hugging the marker:
            # the same cells either way, but a small difference in one document's
            # metrics cannot then push a glyph across it.
            candidate = (floor + edge) / 2 if low < floor < edge else edge - margin
            if low < candidate < high and candidate > floor:
                aligned[index] = candidate
        elif pushed_left:
            edge = max(token.x1 for token in pushed_left)
            ceiling = min(
                (token.x0 for token in kept_right if token.x0 >= aligned[index]), default=high
            )
            candidate = (edge + ceiling) / 2 if edge < ceiling < high else edge + margin
            if low < candidate < high and candidate < ceiling:
                aligned[index] = candidate
    return aligned


def _coalesce(
    boundaries: list[float], lines: list[VisualLine], left: float, right: float
) -> list[float]:
    """Remove boundaries that create columns holding no data of their own.

    Every removal drops a boundary, never a cell, so each word stays inside some
    column. Over-segmentation here is what produced empty spreadsheet columns and
    split a currency symbol away from the amount it belongs to.

    Three shapes are collapsed: a band no row fills; a band only the column titles
    fill, which is what a wide corridor's centre landing inside a header label
    produces; and a band holding nothing but a floated currency or sign marker,
    which belongs to the amount beside it.
    """
    anchors = [line for line in lines if line.segment_count >= 2 and line.tokens]
    if not anchors:
        return boundaries
    while boundaries:
        matrix = [cells_for(line, boundaries) for line in anchors]
        # A row that carries an amount is a body row; a row of words alone is a
        # column title. A column only titles fill carries no data.
        body_rows = [
            index
            for index, line in enumerate(anchors)
            if any(token.kind in ("numeric", "percent") for token in line.tokens)
        ]
        removed = False
        for index in range(len(boundaries) + 1):
            column = [row[index] for row in matrix if index < len(row)]
            filled = [row for row, value in enumerate(column) if value]
            values = [column[row] for row in filled]
            dead = not filled or (
                bool(body_rows) and not any(row in body_rows for row in filled)
            )
            marker_only = (
                bool(values)
                and index < len(boundaries)
                and all(_MARKER_ONLY.match(value) for value in values)
            )
            if not dead and not marker_only:
                continue
            if marker_only and not dead:
                # Fold the marker into the column on its right, where its amount is.
                boundaries = boundaries[:index] + boundaries[index + 1 :]
            elif index == 0:
                boundaries = boundaries[1:]
            elif index == len(boundaries):
                boundaries = boundaries[:-1]
            else:
                boundaries = boundaries[: index - 1] + boundaries[index:]
            removed = True
            break
        if not removed:
            break
    return boundaries


def coalesce_empty_body_columns(
    grid: GridHypothesis,
    layout: PageLayout,
    *,
    header_rows: int,
) -> bool:
    """Remove columns that only a centred header title occupies.

    This pass deliberately runs after header detection. Before then a footnote
    marker in ``Awards (1)`` looks numeric and makes the title line eligible as
    body evidence. Once the header boundary is known, a column that is empty in
    every body row carries no data and can be folded into its neighbour.
    """
    if header_rows <= 0 or not grid.boundaries:
        return False
    # Refinement may already have stripped a spanning caption. Rebuild only from
    # the lines that survived into this grid, never from the wider candidate.
    lines = [line for row in grid.rows for line in row.lines]
    boundaries = list(grid.boundaries)
    changed = False
    while boundaries:
        rows = build_logical_rows(lines, boundaries, layout)
        body = rows[header_rows:]
        if not body:
            break
        column_count = len(boundaries) + 1
        empty = next(
            (
                index
                for index in range(column_count)
                if not any(index < len(row.cells) and row.cells[index] for row in body)
            ),
            None,
        )
        if empty is None:
            break
        # A header-only band normally contains a centred title for the data
        # column on its right. At the outer right edge there is no right-hand
        # neighbour, so it folds left instead.
        boundary = empty if empty < len(boundaries) else empty - 1
        boundaries = boundaries[:boundary] + boundaries[boundary + 1 :]
        changed = True
    if not changed:
        return False
    grid.boundaries = sorted(boundaries)
    grid.rows = build_logical_rows(lines, grid.boundaries, layout)
    edges = [grid.columns[0]["x0"]] + grid.boundaries + [grid.columns[-1]["x1"]]
    grid.columns = [
        {"x0": edges[index], "x1": edges[index + 1]}
        for index in range(len(edges) - 1)
    ]
    return True


def ruled_boundaries(
    vertical: list[RulingSegment], bounds: dict
) -> list[float]:
    """Interior vertical rules that run down most of the region."""
    top = bounds["y"]
    bottom = top + bounds["height"]
    height = max(1e-6, bottom - top)
    inside = [
        rule
        for rule in vertical
        if bounds["x"] - 0.004 <= rule.position <= bounds["x"] + bounds["width"] + 0.004
        and (min(rule.end, bottom) - max(rule.start, top)) / height >= 0.55
    ]
    positions = sorted(rule.position for rule in inside)
    if len(positions) < 3:
        return []
    return positions[1:-1]


def _is_continuation(
    line: VisualLine,
    previous: LogicalRow,
    boundaries: list[float],
    layout: PageLayout,
) -> bool:
    """Is this line the tail of the row above rather than a row of its own?

    A wrapped cell holds content in exactly one column, that column already holds
    content in the row above, the line is indented no further left than the cell
    it continues, and it follows immediately. A section label such as
    "Deferred tax assets:" fails the last two tests, which is what keeps it a row.
    """
    # A line the layout marked as prose only reached this band because it is the
    # first line of a wrapped row label, whose amounts are on the line below. It
    # opens a row; it never closes the one above.
    if layout.is_prose(line):
        return False
    occupied = occupied_columns(line, boundaries)
    if len(occupied) != 1 or not previous.occupied:
        return False
    column = occupied[0]
    if column not in previous.occupied or len(previous.occupied) < 2:
        return False
    if line.y0 - previous.y1 > layout.line_height * 0.9:
        return False
    text = line.text.strip()
    if text.endswith(":"):
        return False
    meaningful = [token for token in line.tokens if not token.is_marker]
    if (
        meaningful
        and all(token.is_value for token in meaningful)
        and column < len(previous.cells)
        and previous.cells[column]
    ):
        # A cell wraps its words, not its amounts. A period band ("2024") landing
        # directly under a filled value column is the start of the next table, not
        # the second line of the number above it.
        return False
    previous_start = min(
        (token.x0 for token in previous.anchor.tokens if column_index(boundaries, token.center) == column),
        default=line.x0,
    )
    if column == 0:
        words = [token.text for token in meaningful if token.kind in ("word", "ordinal")]
        first_alpha = next((character for character in text if character.isalpha()), "")
        if (
            len(words) >= 3
            and first_alpha.isupper()
            and line.x0 <= previous_start + layout.character_width
        ):
            # A flush-left title between numeric rows ("Net loss per share ...")
            # is a section row, not wrapped text belonging to the amount above.
            # Real continuations normally indent or begin mid-sentence.
            return False
    return line.x0 >= previous_start - layout.character_width


def _continues_header_band(
    previous: LogicalRow,
    line: VisualLine,
    boundaries: list[float],
    layout: PageLayout,
) -> bool:
    """Is this the second line of a column title that wraps?

    A statement dates its columns "September 27," on one line and "2025" on the
    next. Both lines fill the same value columns, neither labels a row, and they
    sit on wrapped leading — so they are one header band. Read as two rows, the
    years became the first body row and the header lost half its text.

    A title stack is also allowed to widen as it descends: "Cash and / Current /
    Non-Current" over the last three columns, then a line naming all seven. The
    lines above fill a subset of the columns the line below fills, which is what
    a title wrapped over several lines looks like when only some titles are long
    enough to wrap.

    Only applies above the body: once a row has content in the first column, the
    table has started and two tightly spaced value lines are two rows.
    """
    if not previous.occupied or 0 in previous.occupied:
        return False
    occupied = occupied_columns(line, boundaries)
    if not occupied or 0 in occupied:
        return False
    if set(previous.occupied) != set(occupied):
        # A widening title stack, not a caption: a band that titles several
        # columns at once is part of the header, while a phrase filling a single
        # cell above them names the group and must stay its own row long enough
        # to be recognized as one. A phrase lying across a boundary is naming the
        # columns either side of it, so it is never a title stack.
        if len(previous.occupied) < 2 or not set(previous.occupied) < set(occupied):
            return False
        if any(
            token.x0 < boundary < token.x1
            for other in previous.lines
            for token in other.tokens
            for boundary in boundaries
        ):
            return False
    return line.y0 - previous.y1 <= layout.line_height * 0.6


def _absorbs_wrapped_label(
    previous: LogicalRow,
    line: VisualLine,
    boundaries: list[float],
    layout: PageLayout,
) -> bool:
    """Does this row finish a label that began on the line above?

    The mirror of `_is_continuation`: there the wrapped text follows its values,
    here it precedes them. A label long enough to fill the body width and holding
    nothing else is not a row of its own — the amounts belong to it. A section
    caption ("Deferred tax assets:") is short and punctuated, so it stays a row.
    """
    # An unfinished row: text in the first column and nothing anywhere else,
    # because its amounts are on the line below — which does reach the last
    # column. A row that already carries values of its own is complete.
    if len(previous.lines) > 1 or previous.occupied != (0,):
        return False
    occupied = occupied_columns(line, boundaries)
    if len(boundaries) not in occupied:
        return False
    cells = cells_for(line, boundaries)
    first_cell = cells[0].strip() if cells else ""
    first_words = [piece for piece in first_cell.split() if any(c.isalpha() for c in piece)]
    first_alpha = next((character for character in first_cell if character.isalpha()), "")
    if first_cell and len(first_words) <= 3 and first_alpha.isupper():
        # A short label plus amounts is already a complete row. Folding it into
        # the section title above turned "Weighted average ..." and "Basic" into
        # one record. A genuine wrapped continuation is sentence-like (typically
        # lower-case) and remains eligible below.
        return False
    label = previous.anchor
    if label.text.strip().endswith(":") or label.width < layout.body_width * 0.45:
        return False
    return line.y0 - previous.y1 <= layout.line_height * 0.9


def build_logical_rows(
    lines: list[VisualLine], boundaries: list[float], layout: PageLayout
) -> list[LogicalRow]:
    def refresh(row: LogicalRow) -> None:
        row.cells = _row_cells(row, boundaries)
        row.occupied = tuple(
            index for index, value in enumerate(row.cells) if value and not _marker_only(value)
        )

    rows: list[LogicalRow] = []
    body_started = False
    for line in lines:
        if not line.tokens:
            continue
        previous = rows[-1] if rows else None
        if previous is not None and (
            _is_continuation(line, previous, boundaries, layout)
            or _absorbs_wrapped_label(previous, line, boundaries, layout)
            or (
                not body_started
                and _continues_header_band(previous, line, boundaries, layout)
            )
        ):
            previous.lines.append(line)
            refresh(previous)
            continue
        row = LogicalRow(lines=[line])
        refresh(row)
        rows.append(row)
        if 0 in row.occupied:
            body_started = True
    for row in rows:
        if row.occupied == (0,):
            row.kind = "section"
    return rows


def _marker_only(value: str) -> bool:
    return all(character in "$€£¥%()[]*†‡§# " for character in value)


def _row_cells(row: LogicalRow, boundaries: list[float]) -> list[str]:
    cells = [""] * (len(boundaries) + 1)
    for line in row.lines:
        for index, bucket in enumerate(assign_tokens(line, boundaries)):
            text = " ".join(token.text for token in bucket).strip()
            if not text:
                continue
            cells[index] = f"{cells[index]} {text}".strip() if cells[index] else text
    return cells


def _row_bands(rows: list[LogicalRow], top: float, bottom: float) -> list[tuple[float, float]]:
    centers = [(row.y0 + row.y1) / 2 for row in rows]
    edges = [top]
    for index in range(len(centers) - 1):
        edges.append((centers[index] + centers[index + 1]) / 2)
    edges.append(bottom)
    return [(edges[index], edges[index + 1]) for index in range(len(rows))]


def ruled_row_edges(
    horizontal: list[RulingSegment], bounds: dict, *, minimum_span: float = 0.6
) -> list[float]:
    """Horizontal rules that band the whole region, not just underline a subtotal."""
    left = bounds["x"]
    width = max(1e-6, bounds["width"])
    top = bounds["y"]
    bottom = top + bounds["height"]
    spanning = [
        rule.position
        for rule in horizontal
        if top - 0.004 <= rule.position <= bottom + 0.004
        and (min(rule.end, left + width) - max(rule.start, left)) / width >= minimum_span
    ]
    return sorted(spanning)


def fit_grid(candidate, layout: PageLayout) -> GridHypothesis | None:
    """Iterate columns and logical rows until both stop changing."""
    lines = [line for line in candidate.lines if line.tokens]
    if len(lines) < 2:
        return None
    left, right = candidate.left, candidate.right
    ruled = ruled_boundaries(candidate.vertical, candidate.bounds)
    boundaries: list[float] = []
    rows: list[LogicalRow] = []
    voting = lines
    for _ in range(_ITERATIONS):
        if ruled:
            new_boundaries = list(ruled)
        else:
            new_boundaries = infer_boundaries(voting, left, right, layout)
            new_boundaries = _coalesce(new_boundaries, voting, left, right)
        if not new_boundaries:
            return None
        rows = build_logical_rows(lines, new_boundaries, layout)
        settled = len(new_boundaries) == len(boundaries) and all(
            abs(first - second) < 1e-4 for first, second in zip(new_boundaries, boundaries)
        )
        boundaries = new_boundaries
        # Continuations are wrapped text, not evidence about column positions, so
        # the next vote is taken without them.
        voting = [row.anchor for row in rows if row.anchor.segment_count >= 2]
        if settled or ruled:
            break
    if not boundaries or len(rows) < 2:
        return None
    if not ruled:
        # Drawn rules are the truth about where a ruled table's columns are; a
        # whitespace vote is only an estimate, and the cells it produced are the
        # better evidence of where the boundary belongs.
        aligned = align_boundaries_to_cells(boundaries, lines, layout)
        # Alignment can move a header token out of a narrow band and leave that
        # column empty in every body row. Coalesce once more over the final
        # geometry so centred titles cannot create phantom spreadsheet columns.
        aligned = _coalesce(aligned, lines, left, right)
        if aligned != boundaries:
            boundaries = aligned
            rows = build_logical_rows(lines, boundaries, layout)

    edges = [left] + sorted(boundaries) + [right]
    columns = [{"x0": edges[index], "x1": edges[index + 1]} for index in range(len(edges) - 1)]
    return GridHypothesis(
        columns=columns,
        rows=rows,
        boundaries=sorted(boundaries),
        ruled_columns=bool(ruled),
        ruled_rows=False,
    )
