"""Split, merge, re-measure and deduplicate fitted candidates.

Generation is deliberately generous, so this is where a proposal becomes a
table: a region whose schema changes half way down is two tables, a region
interrupted by a paragraph is two tables, two fragments of one statement are one
table, and two proposals describing the same region are one table.
"""
from __future__ import annotations

from statistics import median

from engines.table.candidates import (
    TableCandidate,
    containment,
    intersection_over_union,
    make_bounds,
)
from engines.table.grid import GridHypothesis, build_logical_rows, fit_grid
from engines.table.headers import _PERIOD, _is_value_cell, detect_header_cells
from engines.table.layout import LogicalRow, PageLayout, VisualLine


_MAX_DEPTH = 4


def _value_signature(row: LogicalRow) -> frozenset[int]:
    return frozenset(index for index in row.occupied if index != 0)


def _first_body_row(grid: GridHypothesis) -> int:
    """The first row that carries an amount rather than a label.

    Everything above it is header machinery — a stacked column title can change
    shape from band to band without meaning a new table starts, so no split is
    allowed to land there.
    """
    needed = min(2, max(1, grid.column_count - 1))
    for index, row in enumerate(grid.rows):
        values = sum(
            1
            for line in row.lines
            for token in line.tokens
            if token.kind in ("numeric", "percent")
        )
        if values >= needed:
            return index
    return 0


def _dated_band(row: LogicalRow) -> bool:
    """Every cell this band fills is a period or a date."""
    filled = [value for value in row.cells if value]
    return bool(filled) and all(_PERIOD.match(value) for value in filled)


def _group_segments(row: LogicalRow, grid: GridHypothesis) -> list[dict]:
    """Horizontally merged labels in one leading band.

    A group is recognized from the printed island crossing one or more fitted
    column boundaries. Its text bounds are retained separately from its column
    coverage: centred text is usually much narrower than the columns it names.
    Multiple islands permit two independent groups on the same tier.
    """
    groups: list[dict] = []
    for line in row.lines:
        for segment in line.segments:
            crossed = [
                index
                for index, boundary in enumerate(grid.boundaries)
                if segment.x0 < boundary < segment.x1
            ]
            if not crossed:
                continue
            start = crossed[0]
            end = crossed[-1] + 1
            groups.append(
                {
                    "text": segment.text.strip(),
                    "columnStart": start,
                    "columnEnd": end,
                    "bounds": {
                        "x": segment.x0,
                        "y": line.y0,
                        "width": segment.x1 - segment.x0,
                        "height": line.y1 - line.y0,
                    },
                    "coverageX0": grid.columns[start]["x0"],
                    "coverageX1": grid.columns[end]["x1"],
                    "source": (line.index, segment.x0, segment.x1),
                }
            )
    return groups


def _single_group(row: LogicalRow, grid: GridHypothesis, columns: tuple[int, ...]) -> dict:
    """One centred label whose children, rather than its ink, reveal its span."""
    tokens = [token for line in row.lines for token in line.tokens]
    start = min(columns)
    end = max(columns)
    return {
        "text": " ".join(value for value in row.cells if value).strip(),
        "columnStart": start,
        "columnEnd": end,
        "bounds": {
            "x": min(token.x0 for token in tokens),
            "y": row.y0,
            "width": max(token.x1 for token in tokens) - min(token.x0 for token in tokens),
            "height": row.y1 - row.y0,
        },
        "coverageX0": grid.columns[start]["x0"],
        "coverageX1": grid.columns[end]["x1"],
        "sourceLines": tuple(line.index for line in row.lines),
    }


def _remove_group_segments(
    grid: GridHypothesis, groups: list[dict], layout: PageLayout
) -> None:
    """Remove only group-label ink, preserving leaf labels on the same band."""
    whole_lines = {
        index for group in groups for index in group.get("sourceLines", ())
    }
    segments = {group["source"] for group in groups if "source" in group}
    remaining: list[VisualLine] = []
    for row in grid.rows:
        for line in row.lines:
            if line.index in whole_lines:
                continue
            kept = [
                segment
                for segment in line.segments
                if (line.index, segment.x0, segment.x1) not in segments
            ]
            if not kept:
                continue
            if len(kept) == len(line.segments):
                remaining.append(line)
                continue
            tokens = [token for segment in kept for token in segment.tokens]
            remaining.append(
                VisualLine(
                    index=line.index,
                    tokens=tokens,
                    segments=kept,
                    x0=min(segment.x0 for segment in kept),
                    y0=line.y0,
                    x1=max(segment.x1 for segment in kept),
                    y1=line.y1,
                    source_lines=line.source_lines,
                )
            )
    grid.rows = build_logical_rows(remaining, grid.boundaries, layout)


def strip_spanning_labels(grid: GridHypothesis, layout: PageLayout) -> None:
    """Drop leading bands that name a group of columns rather than fill one.

    "Years ended" above three dated columns, or "Incorporated by Reference" above
    Form/Exhibit/Date, labels the header rather than belonging to it: it carries
    no value and labels no row. Read as a row it produced an empty leading band
    and glued its words onto whichever column label sat beneath it.

    Two shapes give one away. It may lie across the boundary between the columns
    it spans. Or it may fill a single column above a band that dates every value
    column and labels no row — a column whose title is already a date does not
    take a second title, so a lone phrase above the set of them qualifies all of
    them.

    A wrapped column title ("Filing Date/" over "Period End") sits inside one
    column, never straddles, and the band below it labels the row column too, so
    it survives — and merges into its own band.
    """
    while len(grid.rows) > 2:
        row = grid.rows[0]
        below = grid.rows[1]
        if not row.occupied or 0 in row.occupied:
            break
        # A bare period filling one cell over a band that titles the columns is
        # the block's caption — "2025" above a segment or securities table — not
        # a row and not a column title. The split that separates one block from
        # the next has already read it by the time this runs.
        titles_below = len(below.occupied) >= 2 and 0 not in below.occupied
        block_caption = (
            len(row.occupied) == 1
            and titles_below
            and _dated_band(row)
        )
        segments = [segment for line in row.lines for segment in line.segments]
        qualifies_a_dated_band = (
            len(segments) == 1
            and row.occupied
            and 0 not in row.occupied
            and 0 not in below.occupied
            and len(below.occupied) >= 2
            and set(below.occupied) == set(range(1, len(grid.columns)))
            and _dated_band(below)
        )
        groups = _group_segments(row, grid)
        if qualifies_a_dated_band or block_caption:
            groups = [_single_group(row, grid, below.occupied)]
        if not groups:
            break
        # Why it was removed says what it was. A dated band over titled columns
        # is the block's period; a phrase over dated columns qualifies them;
        # anything else — "Incorporated by Reference" — is a group label that
        # carries no period at all.
        if block_caption:
            kind = "period"
        elif qualifies_a_dated_band:
            kind = "qualifier"
        else:
            kind = "label"
        grid.captions.append(
            {"kind": kind, "text": " ".join(group["text"] for group in groups).strip()}
        )
        grid.header_groups.extend(groups)
        _remove_group_segments(grid, groups, layout)


def _looks_like_header_band(row: LogicalRow) -> bool:
    """An unlabelled band of period or word cells: how a new table announces itself."""
    if not row.cells or row.cells[0]:
        return False
    filled = [value for value in row.cells if value]
    if len(filled) < 2:
        return False
    return all(
        _PERIOD.match(value) or any(character.isalpha() for character in value)
        for value in filled
    )


def _split_is_marked(grid: GridHypothesis, index: int, layout: PageLayout) -> bool:
    """Is there anything on the page that says a new table starts here?

    A table does not change its column schema in mid-flow. What does change is
    which optional columns a run of rows happens to fill — an aging report whose
    "Terms" cell is blank for five consecutive invoices looks exactly like a new
    schema, and splitting there tore a single 78-row report into fragments. So a
    schema change is only believed when the page also shows a break: a blank band
    taller than a line, or a fresh unlabelled header band.
    """
    if index <= 0 or index >= len(grid.rows):
        return False
    pitches = [
        grid.rows[position + 1].y0 - grid.rows[position].y0
        for position in range(len(grid.rows) - 1)
        if grid.rows[position + 1].y0 > grid.rows[position].y0
    ]
    pitch = median(pitches) if pitches else layout.line_height
    gap = grid.rows[index].y0 - grid.rows[index - 1].y1
    if gap > max(layout.line_height * 0.9, pitch * 0.5):
        return True
    return _looks_like_header_band(grid.rows[index])


def _value_columns(values: list[str]) -> frozenset[int]:
    """Columns containing measurements in one already-fitted row."""
    return frozenset(
        index for index, value in enumerate(values) if value and _is_value_cell(value)
    )


def _leading_preamble_split(
    grid: GridHypothesis, layout: PageLayout, *, ruled: bool
) -> int | None:
    """Trim document furniture above a complete internal table header.

    Invoice mastheads and continuation labels can sit close enough to a table to
    enter the same whitespace band.  A split is safe only when the later row is
    a nearly complete word header, a visible break precedes it, and at least two
    following rows repeat a multi-value body schema.  Header groups that span
    fitted columns are left for ``strip_spanning_labels`` so their period and
    coverage metadata are not lost.
    """
    if grid.column_count < 3 or len(grid.rows) < 4:
        return None
    matrix = grid.cell_matrix()
    for index in range(1, len(grid.rows) - 2):
        if not _split_is_marked(grid, index, layout):
            continue
        # Only a group directly attached to this header qualifies it. Earlier
        # document furniture can cross provisional boundaries simply because
        # those boundaries were inferred from the table below (for example the
        # large word "INVOICE" centred over two amount columns).
        if _group_segments(grid.rows[index - 1], grid):
            continue

        header = detect_header_cells(
            matrix[index:], grid.column_count, ruled=ruled
        )
        if header is None or int(header["rowCount"]) != 1:
            continue
        values = matrix[index]
        filled = [value for value in values if value]
        if (
            not values[0]
            or len(filled) < max(3, grid.column_count - 1)
            or not all(any(character.isalpha() for character in value) for value in filled)
            or any(_is_value_cell(value) for value in filled)
        ):
            continue

        # The rows above the header must not already establish a value table.
        if any(len(_value_columns(row)) >= 2 for row in matrix[:index]):
            continue
        signatures: dict[frozenset[int], int] = {}
        for row in matrix[index + 1 : index + 6]:
            signature = _value_columns(row)
            if len(signature) >= 2:
                signatures[signature] = signatures.get(signature, 0) + 1
        if signatures and max(signatures.values()) >= 2:
            return index
    return None


def _schema_split(grid: GridHypothesis, layout: PageLayout) -> int | None:
    """The row where one column schema gives way to another and stays changed.

    A statement that puts a three-period table directly above a two-period table
    shares a band with it, and fitting them together invents a column that half
    the rows never fill. A change is only believed when at least two consecutive
    rows held the old schema and at least two consecutive rows hold the new one,
    which is what keeps an alternating "percentage of net sales" row from looking
    like a new table every other line.
    """
    informative = [
        (index, _value_signature(row))
        for index, row in enumerate(grid.rows)
        if len(_value_signature(row)) >= 1
    ]
    if len(informative) < 4:
        return None
    runs: list[tuple[frozenset[int], list[int]]] = []
    for index, signature in informative:
        if runs and runs[-1][0] == signature:
            runs[-1][1].append(index)
        else:
            runs.append((signature, [index]))
    body_start = _first_body_row(grid)
    for position in range(1, len(runs)):
        before = runs[position - 1]
        after = runs[position]
        if (
            len(before[1]) >= 2
            and len(after[1]) >= 2
            and after[1][0] > body_start
            and before[1][-1] >= body_start
            and _split_is_marked(grid, after[1][0], layout)
        ):
            return after[1][0]
    return None


def _repeated_header_split(grid: GridHypothesis) -> int | None:
    """A header band that starts again part way down is a second table.

    A segment-reporting page stacks the same table once per year, each block
    introduced by a bare period band. The blocks share a column layout, so no
    schema change betrays them; the repeated header does.
    """
    header = detect_header_cells(grid.cell_matrix(), grid.column_count)
    header_rows = int(header["rowCount"]) if header else 0
    opening = tuple(value for value in grid.rows[0].cells if value) if grid.rows else ()
    body_start = _first_body_row(grid)
    for index in range(max(2, header_rows + 1, body_start + 1), len(grid.rows) - 2):
        cells = [value for value in grid.rows[index].cells if value]
        if len(cells) == 1 and _PERIOD.match(cells[0]):
            return index
        if len(cells) >= 2 and opening and tuple(cells) == opening:
            return index
    return None


def _prose_split(grid: GridHypothesis, layout: PageLayout) -> int | None:
    """A paragraph that survived band formation still ends the table."""
    for index in range(1, len(grid.rows) - 1):
        row = grid.rows[index]
        if len(row.occupied) != 1 or row.merged:
            continue
        line = row.anchor
        words = [token for token in line.tokens if token.kind in ("word", "ordinal")]
        crossed = sum(
            1
            for boundary in grid.boundaries
            if line.x0 < boundary < line.x1
        )
        if len(words) >= 10 and crossed >= 2:
            return index
    return None


def _scoped_rule_evidence(
    candidate: TableCandidate, bounds: dict, lines: list[VisualLine]
) -> tuple[str | None, list, list]:
    """Rule evidence that physically reaches one fitted or split region."""
    left = bounds["x"]
    right = left + bounds["width"]
    top = bounds["y"]
    bottom = top + bounds["height"]

    def band_positions(rules, low: float, high: float) -> set[float]:
        """Positions inside the ink plus the nearest border on either side."""
        positions = {rule.position for rule in rules if low <= rule.position <= high}
        before = [rule.position for rule in rules if rule.position < low]
        after = [rule.position for rule in rules if rule.position > high]
        if before:
            positions.add(max(before))
        if after:
            positions.add(min(after))
        return positions

    vertical_positions = band_positions(candidate.vertical, left, right)
    horizontal_positions = band_positions(candidate.horizontal, top, bottom)
    vertical = [
        rule
        for rule in candidate.vertical
        if rule.position in vertical_positions
        and rule.end >= top
        and rule.start <= bottom
    ]
    horizontal = [
        rule
        for rule in candidate.horizontal
        if rule.position in horizontal_positions
        and rule.end >= left
        and rule.start <= right
    ]
    evidence = candidate.evidence
    if evidence == "ruled":
        intersections = sum(
            1
            for v_rule in vertical
            for h_rule in horizontal
            if h_rule.spans(v_rule.position, slack=0.006)
            and v_rule.spans(h_rule.position, slack=0.006)
        )
        if len(vertical) < 2 or len(horizontal) < 2 or intersections < 4:
            # A fragment split from a larger ruled object does not inherit that
            # object's evidence. It may continue as a whitespace proposal when
            # its own text repeats a cell-like segmentation; otherwise there is
            # no local basis for a table candidate at all.
            if sum(line.segment_count >= 2 for line in lines) < 2:
                return None, [], []
            evidence = "whitespace"
            vertical = []
            horizontal = []
    elif evidence == "mixed" and len(vertical) < 2 and len(horizontal) < 2:
        evidence = "whitespace"
        vertical = []
        horizontal = []
    return evidence, vertical, horizontal


def _sub_candidate(candidate: TableCandidate, lines, layout: PageLayout) -> TableCandidate | None:
    lines = [line for line in lines if line.tokens]
    if len(lines) < 2:
        return None
    top = min(line.y0 for line in lines) - layout.line_height * 0.4
    bottom = max(line.y1 for line in lines) + layout.line_height * 0.4
    bounds = make_bounds(candidate.left, top, candidate.right, bottom)
    evidence, vertical, horizontal = _scoped_rule_evidence(candidate, bounds, lines)
    if evidence is None:
        return None
    return TableCandidate(
        lines=lines,
        bounds=bounds,
        evidence=evidence,
        origin=candidate.origin,
        vertical=vertical,
        horizontal=horizontal,
        graphics=candidate.graphics,
        sealed=True,
    )


def tighten(candidate: TableCandidate, grid: GridHypothesis, layout: PageLayout) -> None:
    """Re-measure the region from the content the grid actually assigned."""
    tokens = [token for row in grid.rows for line in row.lines for token in line.tokens]
    if not tokens:
        return
    left = min(token.x0 for token in tokens) - layout.character_width * 0.5
    right = max(token.x1 for token in tokens) + layout.character_width * 0.5
    top = min(row.y0 for row in grid.rows) - layout.line_height * 0.4
    bottom = max(row.y1 for row in grid.rows) + layout.line_height * 0.4
    if grid.boundaries:
        # A ruled column boundary can sit outside the ink it separates. The
        # published columns have to stay inside the published bounds, so the
        # bounds give way, never the columns.
        left = min(left, min(grid.boundaries))
        right = max(right, max(grid.boundaries))
    candidate.bounds = make_bounds(left, top, right, bottom)
    evidence, vertical, horizontal = _scoped_rule_evidence(
        candidate, candidate.bounds, [line for row in grid.rows for line in row.lines]
    )
    candidate.evidence = evidence or "whitespace"
    candidate.vertical = vertical
    candidate.horizontal = horizontal
    grid.columns[0]["x0"] = candidate.bounds["x"]
    grid.columns[-1]["x1"] = candidate.bounds["x"] + candidate.bounds["width"]


def refine(
    candidate: TableCandidate, layout: PageLayout, depth: int = 0
) -> list[tuple[TableCandidate, GridHypothesis]]:
    """Fit a grid, then split it until every part has one consistent schema."""
    grid = fit_grid(candidate, layout)
    if grid is None:
        candidate.rejection = "no-grid"
        return []
    if depth < _MAX_DEPTH:
        split = _leading_preamble_split(
            grid,
            layout,
            ruled=candidate.evidence in ("ruled", "mixed"),
        )
        if split is not None:
            lines = [line for row in grid.rows[split:] for line in row.lines]
            part = _sub_candidate(candidate, lines, layout)
            if part is not None:
                trimmed = refine(part, layout, depth + 1)
                if trimmed:
                    return trimmed
        split = _prose_split(grid, layout)
        if split is not None:
            above = [line for row in grid.rows[:split] for line in row.lines]
            below = [line for row in grid.rows[split + 1 :] for line in row.lines]
            parts = []
            for lines in (above, below):
                part = _sub_candidate(candidate, lines, layout)
                if part is not None:
                    parts.extend(refine(part, layout, depth + 1))
            if parts:
                return parts
        split = _repeated_header_split(grid)
        if split is None:
            split = _schema_split(grid, layout)
        if split is not None:
            above = [line for row in grid.rows[:split] for line in row.lines]
            below = [line for row in grid.rows[split:] for line in row.lines]
            parts = []
            for lines in (above, below):
                part = _sub_candidate(candidate, lines, layout)
                if part is not None:
                    parts.extend(refine(part, layout, depth + 1))
            if parts:
                return parts
    strip_spanning_labels(grid, layout)
    tighten(candidate, grid, layout)
    return [(candidate, grid)]


def _has_own_header(grid: GridHypothesis) -> bool:
    return detect_header_cells(grid.cell_matrix(), grid.column_count) is not None


def merge_adjacent(
    fitted: list[tuple[TableCandidate, GridHypothesis]], layout: PageLayout
) -> list[tuple[TableCandidate, GridHypothesis]]:
    """Rejoin fragments of one table that a blank band happened to separate.

    The test is narrow on purpose. Two statements stacked on one page share a
    column layout and sit a line apart, so matching columns alone is not enough:
    a fragment that carries its own header band is a table in its own right, and
    is never folded into the one above it.
    """
    ordered = sorted(fitted, key=lambda item: item[0].bounds["y"])
    merged: list[tuple[TableCandidate, GridHypothesis]] = []
    for candidate, grid in ordered:
        if merged:
            previous_candidate, previous_grid = merged[-1]
            same_columns = previous_grid.column_count == grid.column_count and all(
                abs(first - second) <= 0.015
                for first, second in zip(previous_grid.boundaries, grid.boundaries)
            )
            gap = candidate.bounds["y"] - (
                previous_candidate.bounds["y"] + previous_candidate.bounds["height"]
            )
            interrupted = any(
                layout.is_block_prose(line)
                and previous_candidate.bottom <= line.center <= candidate.top
                for line in layout.lines
            )
            if (
                same_columns
                and gap <= layout.line_height * 3
                and not interrupted
                and not candidate.sealed
                and not previous_candidate.sealed
                and not _has_own_header(grid)
            ):
                combined = _sub_candidate(
                    previous_candidate,
                    previous_candidate.lines + candidate.lines,
                    layout,
                )
                if combined is not None:
                    combined.bounds = make_bounds(
                        min(previous_candidate.left, candidate.left),
                        previous_candidate.top,
                        max(previous_candidate.right, candidate.right),
                        candidate.bottom,
                    )
                    refitted = fit_grid(combined, layout)
                    if refitted is not None:
                        # The combined candidate still carries the original
                        # source lines, including any group header stripped from
                        # the earlier fragment. Reclassify it before publishing
                        # the refitted grid so merging cannot turn it back into a
                        # per-column header row.
                        strip_spanning_labels(refitted, layout)
                        tighten(combined, refitted, layout)
                        merged[-1] = (combined, refitted)
                        continue
        merged.append((candidate, grid))
    return merged


def deduplicate(scored: list[dict]) -> list[dict]:
    """Resolve overlapping proposals deterministically.

    Ordering is by confidence, then by position, so the same page always produces
    the same answer regardless of the order the generators happened to run in.
    """
    ordered = sorted(
        scored,
        key=lambda item: (
            -item["confidence"],
            item["bounds"]["y"],
            item["bounds"]["x"],
        ),
    )
    kept: list[dict] = []
    for item in ordered:
        conflict = False
        for existing in kept:
            if (
                intersection_over_union(item["bounds"], existing["bounds"]) >= 0.30
                or containment(item["bounds"], existing["bounds"]) >= 0.70
            ):
                conflict = True
                break
        if not conflict:
            kept.append(item)
    return sorted(kept, key=lambda item: (item["bounds"]["y"], item["bounds"]["x"]))
