"""Value geometry -> the footing lattice.

The sum tree needs alignment, not proof that a table exists.  This module
clusters recognized number spans by baseline and by the right edge of their
last digit, then cuts the page into vertical blocks.  Table structure is read
only afterwards, to lend headers, bounds and provenance where it agrees.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from statistics import median

from engines.table.layout import PageLayout

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


def _has_fence(layout: PageLayout, y0: float, y1: float) -> bool:
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


def _row_groups(values: list[_Value], layout: PageLayout) -> list[list[_Value]]:
    tolerance = max(0.003, layout.line_height * 0.65)
    return _cluster(values, lambda value: value.center_y, tolerance)


def _blocks(rows: list[list[_Value]], layout: PageLayout) -> list[list[list[_Value]]]:
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
    return [block for block in result if len(block) >= MIN_BLOCK_ROWS]


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


def _table_for(bounds: dict, tables: list[dict]) -> dict | None:
    matches = [(table, _overlap(bounds, table["bounds"])) for table in tables]
    match, area = max(matches, key=lambda pair: pair[1], default=(None, 0.0))
    return match if area >= bounds["width"] * bounds["height"] * 0.25 else None


def _label_for(layout: PageLayout, value: _Value, row_values: list[_Value]) -> str:
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


def _header_semantics(table: dict | None, column_edges: list[float]) -> list[dict]:
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


def _add_dashes(built: TableCells, layout: PageLayout, rows, column_edges: list[float]) -> None:
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


def build_page_lattice(
    layout: PageLayout,
    page_values: dict | None,
    *,
    page_index: int,
    detected_tables: list[dict] | None = None,
) -> list[TableCells]:
    """Build every footing block on a page from value alignment alone."""
    values = _values(page_values)
    blocks = _blocks(_row_groups(values, layout), layout)
    result: list[TableCells] = []
    for block_number, rows in enumerate(blocks):
        flat = [value for row in rows for value in row]
        column_groups = _cluster(flat, lambda value: value.digit_right, RIGHT_EDGE_TOLERANCE)
        column_groups = [group for group in column_groups if len(group) >= 2]
        if not column_groups:
            continue
        column_edges = [sum(value.digit_right for value in group) / len(group) for group in column_groups]
        bounds = _bounds(flat)
        table = _table_for(bounds, detected_tables or [])
        digest = hashlib.sha1(f"{page_index}:{bounds['y']:.5f}:{bounds['height']:.5f}".encode()).hexdigest()[:10]
        built = TableCells(
            table_id=f"lattice-p{page_index}-{digest}",
            page_index=page_index,
            column_count=len(column_edges) + 1,
            row_count=len(rows),
            row_labels=[""] * len(rows),
        )
        built.bounds = bounds
        built.provenance = "lattice+table" if table else "lattice"
        built.header_labels = _header_semantics(table, column_edges)
        for row_index, row in enumerate(rows):
            for value in row:
                nearest = min(range(len(column_edges)), key=lambda index: abs(column_edges[index] - value.digit_right))
                if abs(column_edges[nearest] - value.digit_right) > RIGHT_EDGE_TOLERANCE:
                    continue
                column_index = nearest + 1
                label = _label_for(layout, value, row)
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
        # Rules are the detected table's fact to publish and Reconcile's to read.
        # A lattice block with no table under it has no ruling evidence at all,
        # which is why the signal is absent there rather than false.
        mark_double_rules(built, horizontal_rules(table))
        mark_rules_above(built, horizontal_rules(table))
        result.append(built)
    return result
