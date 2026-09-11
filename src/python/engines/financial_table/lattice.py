"""Value geometry -> a financial-statement footing lattice.

The sum tree needs alignment, not proof that a table exists.  This module
clusters recognized number spans by baseline and by the right edge of their
last digit, then cuts the page into vertical blocks.  Table structure is read
only afterwards, to lend headers, bounds and provenance where it agrees.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from statistics import median

from engines.table.handoff import AnalysisLayout, JsonMapping, table_geometry_digest

from . import labels
from .cells import (
    DASHES,
    TableCells,
    decimals_of,
    horizontal_rules,
    mark_double_rules,
    mark_rules_above,
)


RIGHT_EDGE_TOLERANCE = 0.005
MIN_BLOCK_ROWS = 3


@dataclass(frozen=True)
class _Value:
    span: dict
    x0: float
    x1: float
    digit_right: float
    center_y: float


def _cluster(items, key, tolerance: float) -> list[list]:
    groups: list[list] = []
    for item in sorted(items, key=key):
        value = key(item)
        if not groups or abs(value - sum(key(member) for member in groups[-1]) / len(groups[-1])) > tolerance:
            groups.append([item])
        else:
            groups[-1].append(item)
    return groups


def _digit_right(span: dict) -> float:
    """Approximate the last digit edge, excluding a closing accounting mark."""
    bounds = span["bounds"]
    right = float(bounds["x"]) + float(bounds["width"])
    text = (span.get("text") or "").rstrip()
    suffix = len(text) - max((index + 1 for index, char in enumerate(text) if char.isdigit()), default=len(text))
    if suffix:
        visible = max(1, sum(1 for char in text if not char.isspace()))
        right -= float(bounds["width"]) * min(suffix, 2) / visible
    return right


def _values(page_values: dict | None) -> list[_Value]:
    result: list[_Value] = []
    for span in (page_values or {}).get("values", []):
        if span.get("kind") != "number" or not span.get("normalizedValue"):
            continue
        bounds = span["bounds"]
        result.append(_Value(
            span=span,
            x0=float(bounds["x"]),
            x1=float(bounds["x"]) + float(bounds["width"]),
            digit_right=_digit_right(span),
            center_y=float(bounds["y"]) + float(bounds["height"]) / 2,
        ))
    return result


def _has_fence(layout: AnalysisLayout, y0: float, y1: float) -> bool:
    # A rule immediately above a total is evidence for that total, not the end
    # of the structure it sums. Pitch and textual captions decide the block;
    # rules are retained by the caller for corroboration rather than allowed to
    # sever the very addend/total relationship they conventionally mark.
    for line in layout.lines:
        if not y0 < line.center < y1:
            continue
        if layout.is_prose(line):
            return True
        meaningful = [token for token in line.tokens if not token.is_marker]
        if len(meaningful) >= 2 and all(token.kind in ("word", "ordinal") for token in meaningful):
            return True
    return False


def _row_groups(values: list[_Value], layout: AnalysisLayout) -> list[list[_Value]]:
    tolerance = max(0.003, layout.line_height * 0.65)
    return _cluster(values, lambda value: value.center_y, tolerance)


def _blocks(
    rows: list[list[_Value]],
    layout: AnalysisLayout,
    *,
    minimum_rows: int = MIN_BLOCK_ROWS,
) -> list[list[list[_Value]]]:
    if not rows:
        return []
    pitch = median(
        [min(v.center_y for v in rows[index]) - min(v.center_y for v in rows[index - 1])
         for index in range(1, len(rows))]
    ) if len(rows) > 1 else layout.line_height
    maximum_gap = max(layout.row_gap, pitch * 2.35)
    result: list[list[list[_Value]]] = [[rows[0]]]
    for row in rows[1:]:
        previous_y = sum(v.center_y for v in result[-1][-1]) / len(result[-1][-1])
        current_y = sum(v.center_y for v in row) / len(row)
        if current_y - previous_y > maximum_gap or _has_fence(layout, previous_y, current_y):
            result.append([row])
        else:
            result[-1].append(row)
    return [block for block in result if len(block) >= minimum_rows]


def _bounds(values: list[_Value]) -> dict:
    x0 = min(value.x0 for value in values)
    x1 = max(value.x1 for value in values)
    y0 = min(float(value.span["bounds"]["y"]) for value in values)
    y1 = max(float(value.span["bounds"]["y"]) + float(value.span["bounds"]["height"]) for value in values)
    return {"x": round(x0, 6), "y": round(y0, 6), "width": round(x1 - x0, 6), "height": round(y1 - y0, 6)}


def _overlap(first: dict, second: dict) -> float:
    x = max(0.0, min(first["x"] + first["width"], second["x"] + second["width"]) - max(first["x"], second["x"]))
    y = max(0.0, min(first["y"] + first["height"], second["y"] + second["height"]) - max(first["y"], second["y"]))
    return x * y


def _table_for(bounds: dict, tables: tuple[JsonMapping, ...]) -> JsonMapping | None:
    matches = [(table, _overlap(bounds, table["bounds"])) for table in tables]
    match, area = max(matches, key=lambda pair: pair[1], default=(None, 0.0))
    return match if area >= bounds["width"] * bounds["height"] * 0.25 else None


def _nearby_table_for(
    bounds: dict,
    tables: tuple[JsonMapping, ...],
    layout: AnalysisLayout,
) -> JsonMapping | None:
    """A table containing, or immediately adjacent to, one value fragment.

    General table fitting can classify the first data row as part of the header
    when its label wraps. The values remain aligned a single line above the
    published body bounds. Composition may recover that row, but the ordinary
    lattice keeps the stricter overlap rule so no independent block silently
    borrows table evidence.
    """
    direct = _table_for(bounds, tables)
    if direct is not None:
        return direct
    slack = max(layout.row_gap, layout.line_height * 2)
    expanded = []
    for table in tables:
        table_bounds = dict(table["bounds"])
        y = max(0.0, float(table_bounds["y"]) - slack)
        bottom = min(
            1.0,
            float(table_bounds["y"]) + float(table_bounds["height"]) + slack,
        )
        expanded.append(
            (
                table,
                {
                    **table_bounds,
                    "y": y,
                    "height": bottom - y,
                },
            )
        )
    matches = [(table, _overlap(bounds, expanded_bounds)) for table, expanded_bounds in expanded]
    match, area = max(matches, key=lambda pair: pair[1], default=(None, 0.0))
    return match if area >= bounds["width"] * bounds["height"] * 0.25 else None


def _label_for(layout: AnalysisLayout, value: _Value, row_values: list[_Value]) -> str:
    candidates: list[tuple[float, str]] = []
    tolerance = max(0.004, layout.line_height * 0.7)
    for line in layout.lines:
        if abs(line.center - value.center_y) > tolerance:
            continue
        for segment in line.segments:
            if segment.x1 >= value.x0 or not any(character.isalpha() for character in segment.text):
                continue
            candidates.append((segment.x1, segment.text))
    if not candidates:
        return ""
    return labels.row_label(max(candidates, key=lambda candidate: candidate[0])[1])


def _header_semantics(
    table: JsonMapping | None, column_edges: list[float]
) -> list[dict]:
    if not table or not table.get("header"):
        return []
    original = labels.header_semantics(list(table["header"].get("labels", [])))
    table_columns = table.get("columns", [])
    mapped = []
    for lattice_index, edge in enumerate(column_edges, start=1):
        for entry, column in zip(original, table_columns):
            if float(column["x0"]) <= edge <= float(column["x1"]):
                mapped.append({**entry, "columnIndex": lattice_index})
                break
    return mapped


def _add_dashes(
    built: TableCells, layout: AnalysisLayout, rows, column_edges: list[float]
) -> None:
    """Recover zero addends that the values artifact intentionally omits."""
    row_centres = [sum(value.center_y for value in row) / len(row) for row in rows]
    tolerance = max(0.004, layout.line_height * 0.7)
    for line in layout.lines:
        for token in line.tokens:
            text = token.text.strip()
            if not text or not all(character in DASHES for character in text):
                continue
            row_index = min(range(len(row_centres)), key=lambda index: abs(row_centres[index] - line.center))
            if abs(row_centres[row_index] - line.center) > tolerance:
                continue
            column_index = min(range(len(column_edges)), key=lambda index: abs(column_edges[index] - token.x1)) + 1
            if abs(column_edges[column_index - 1] - token.x1) > RIGHT_EDGE_TOLERANCE * 2:
                continue
            if built.cell(row_index, column_index) is not None:
                continue
            label = _label_for(layout, _Value({}, token.x0, token.x1, token.x1, line.center), rows[row_index])
            cell = {
                "id": f"{built.table_id}-r{row_index}-c{column_index}",
                "rowIndex": row_index,
                "columnIndex": column_index,
                "text": text,
                "bounds": {"x": token.x0, "y": token.y0, "width": token.x1 - token.x0, "height": token.y1 - token.y0},
                "dash": True,
            }
            if label:
                cell["rowLabel"] = label
            built.published.append(cell)
            built.by_position[(row_index, column_index)] = cell
            built.by_id[cell["id"]] = cell


def _build_lattice(
    rows: list[list[_Value]],
    layout: AnalysisLayout,
    *,
    page_index: int,
    detected_tables: tuple[JsonMapping, ...],
    table: JsonMapping | None = None,
    resolve_table: bool = True,
    id_prefix: str = "lattice",
) -> TableCells | None:
    flat = [value for row in rows for value in row]
    column_groups = _cluster(flat, lambda value: value.digit_right, RIGHT_EDGE_TOLERANCE)
    column_groups = [group for group in column_groups if len(group) >= 2]
    if not column_groups:
        return None
    column_edges = [
        sum(value.digit_right for value in group) / len(group)
        for group in column_groups
    ]
    bounds = _bounds(flat)
    if resolve_table:
        table = table or _table_for(bounds, detected_tables)
    digest = hashlib.sha1(
        f"{page_index}:{bounds['y']:.5f}:{bounds['height']:.5f}".encode()
    ).hexdigest()[:10]
    built = TableCells(
        table_id=f"{id_prefix}-p{page_index}-{digest}",
        page_index=page_index,
        column_count=len(column_edges) + 1,
        row_count=len(rows),
        row_labels=[""] * len(rows),
    )
    built.bounds = bounds
    built.provenance = "lattice+table" if table else "lattice"
    built.value_column_edges = tuple(round(edge, 6) for edge in column_edges)
    if table is not None:
        built.source_general_table_id = str(table["id"])
        built.source_general_geometry_digest = table_geometry_digest(table)
    built.header_labels = _header_semantics(table, column_edges)
    for row_index, row in enumerate(rows):
        for value in row:
            nearest = min(
                range(len(column_edges)),
                key=lambda index: abs(column_edges[index] - value.digit_right),
            )
            if abs(column_edges[nearest] - value.digit_right) > RIGHT_EDGE_TOLERANCE:
                continue
            column_index = nearest + 1
            label = _label_for(layout, value, row)
            if label and not built.row_labels[row_index]:
                built.row_labels[row_index] = labels.row_label(label)
            cell = {
                "id": f"{built.table_id}-r{row_index}-c{column_index}",
                "rowIndex": row_index,
                "columnIndex": column_index,
                "text": value.span.get("text", ""),
                "bounds": dict(value.span["bounds"]),
                "spanId": value.span["id"],
                "normalizedValue": value.span["normalizedValue"],
                "decimals": decimals_of(value.span.get("text", "")),
            }
            if label:
                cell["rowLabel"] = label
            built.published.append(cell)
            built.by_position[(row_index, column_index)] = cell
            built.by_id[cell["id"]] = cell
    _add_dashes(built, layout, rows, column_edges)
    built.published.sort(key=lambda cell: (cell["rowIndex"], cell["columnIndex"]))
    mark_double_rules(built, horizontal_rules(table))
    mark_rules_above(built, horizontal_rules(table))
    return built


def build_page_lattice(
    layout: AnalysisLayout,
    page_values: dict | None,
    *,
    page_index: int,
    detected_tables: tuple[JsonMapping, ...] | None = None,
) -> list[TableCells]:
    """Build every footing block on a page from value alignment alone."""
    values = _values(page_values)
    blocks = _blocks(_row_groups(values, layout), layout)
    result: list[TableCells] = []
    for rows in blocks:
        built = _build_lattice(
            rows,
            layout,
            page_index=page_index,
            detected_tables=detected_tables or (),
        )
        if built is not None:
            result.append(built)
    return result


# Two detected tables are one statement when they share the page's horizontal
# extent and are separated by no more than a caption band. The general table
# detector is tuned for linking and legitimately closes a table at a caption
# such as "Commitments and contingencies" or "Stockholders' equity:", which for
# arithmetic is the middle of a balance sheet: `Total liabilities` and
# `Total stockholders' equity` are the addends of the grand total beneath them.
# The gap is measured in row-pitch units, because a statement's caption band is
# a few rows tall while two unrelated schedules are set much further apart.
STATEMENT_MERGE_X_OVERLAP = 0.5
STATEMENT_MERGE_GAP_ROWS = 3.0
STATEMENT_MERGE_GAP_LINES = 8.0


def _mergeable(first: JsonMapping, second: JsonMapping, gap_limit: float) -> bool:
    a, b = first["bounds"], second["bounds"]
    overlap = max(
        0.0,
        min(float(a["x"]) + float(a["width"]), float(b["x"]) + float(b["width"]))
        - max(float(a["x"]), float(b["x"])),
    )
    if overlap < STATEMENT_MERGE_X_OVERLAP * min(float(a["width"]), float(b["width"])):
        return False
    gap = max(
        float(b["y"]) - (float(a["y"]) + float(a["height"])),
        float(a["y"]) - (float(b["y"]) + float(b["height"])),
        0.0,
    )
    return gap <= gap_limit


def _statement_regions(
    tables: tuple[JsonMapping, ...], layout: AnalysisLayout
) -> list[list[JsonMapping]]:
    """Group adjacent detected tables that a statement split in two.

    Clustering is transitive along the page's vertical order, so a statement
    the detector cut into three tables is rejoined whole. Only regions of more
    than one table are returned; a single table is already the ordinary view's
    business. The merged region carries no general-table lineage -- it is a
    private arithmetic view -- so it publishes as `lattice` provenance and is
    admitted under the same exact-only composed rule as every other fallback.
    """
    gap_limit = max(
        layout.row_gap * STATEMENT_MERGE_GAP_ROWS,
        layout.line_height * STATEMENT_MERGE_GAP_LINES,
    )
    ordered = sorted(tables, key=lambda table: float(table["bounds"]["y"]))
    regions: list[list[JsonMapping]] = []
    for table in ordered:
        for region in regions:
            if _mergeable(region[-1], table, gap_limit):
                region.append(table)
                break
        else:
            regions.append([table])
    return [region for region in regions if len(region) > 1]


def build_page_composed_lattice(
    layout: AnalysisLayout,
    page_values: dict | None,
    *,
    page_index: int,
    detected_tables: tuple[JsonMapping, ...] | None = None,
) -> list[TableCells]:
    """Reassemble value fragments belonging to one detected statement table.

    Captions, wrapped labels, and extra vertical space deliberately split the
    ordinary lattice: those cuts keep a partial run from accusing a document.
    They should not prevent an exact relationship from being proved, however.
    This fallback composes fragments only when the general table independently
    places them in the same visible table, and Reconcile admits only outcomes
    stronger than the ordinary blocks already published.

    One-row fragments are eligible only when they carry at least two figures.
    That recovers a statement's opening balance or first wrapped row without
    turning isolated page furniture into a one-column footing hypothesis.
    """
    tables = detected_tables or ()
    if not tables:
        return []
    raw_blocks = _blocks(
        _row_groups(_values(page_values), layout),
        layout,
        minimum_rows=1,
    )
    grouped: dict[str, tuple[JsonMapping, list[list[_Value]], int]] = {}
    for rows in raw_blocks:
        if len(rows) == 1 and len(rows[0]) < 2:
            continue
        flat = [value for row in rows for value in row]
        table = _nearby_table_for(_bounds(flat), tables, layout)
        if table is None:
            continue
        key = str(table["id"])
        if key not in grouped:
            grouped[key] = (table, [], 0)
        grouped[key][1].extend(rows)
        grouped[key] = (table, grouped[key][1], grouped[key][2] + 1)

    result: list[TableCells] = []
    for table, rows, fragment_count in grouped.values():
        if fragment_count < 2 or len(rows) < MIN_BLOCK_ROWS:
            continue
        built = _build_lattice(
            rows,
            layout,
            page_index=page_index,
            detected_tables=tables,
            table=table,
            id_prefix="lattice-composed",
        )
        if built is not None:
            result.append(built)

    # A statement the general detector closed at a caption band is still one
    # statement to arithmetic. Rebuild each merged region from the fragments of
    # its member tables, so a grand total whose addends straddle the split can
    # foot. The per-table composed block above is retained: it is the safer
    # view, and span identity lets whichever proves the stronger result win.
    for region in _statement_regions(tables, layout):
        rows: list[list[_Value]] = []
        for table in region:
            rows.extend(grouped.get(str(table["id"]), (None, [], 0))[1])
        if len(rows) < MIN_BLOCK_ROWS:
            continue
        built = _build_lattice(
            rows,
            layout,
            page_index=page_index,
            detected_tables=tables,
            table=None,
            resolve_table=False,
            id_prefix="lattice-merged",
        )
        if built is not None:
            result.append(built)
    return result
