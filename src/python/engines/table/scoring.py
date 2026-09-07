"""Measure a fitted candidate and turn the measurements into one confidence.

Confidence is computed after the grid exists, from what the grid actually
contains. That is the whole point of the redesign: the number the UI shows means
"how table-like is this region", not "how many rows did we happen to find".
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from statistics import mean, pstdev

from engines.table.layout import PageLayout, LogicalRow
from engines.table.grid import GridHypothesis, column_index


# A candidate scoring below this is not published. Chosen so that a bullet list,
# a chart and a two-column prose layout all fall short while a three-row
# whitespace table with a header clears it.
ACCEPT_THRESHOLD = 0.50

# What a list marker looks like when it stands alone in the first column. A
# parenthesized number is also how an accounting layout writes a negative amount,
# so the shape alone proves nothing — the penalty additionally requires the rest
# of the row to hold no values at all.
_LIST_MARKER = re.compile(r"^(?:[•·▪◦‣∙●○■□\-–—*]|\(?[0-9]{1,3}[.)]|\(?[a-zA-Z][.)])$")

_POSITIVE_WEIGHTS = {
    "alignment": 0.20,
    "multi_column": 0.15,
    "type_stability": 0.10,
    "spacing": 0.08,
    "header": 0.12,
    "ruling": 0.08,
    "numeric": 0.10,
    "agreement": 0.05,
    "size": 0.12,
}

_PENALTY_WEIGHTS = {
    "marker_first_column": 0.55,
    "prose_pair": 0.45,
    "narrow_marker": 0.30,
    "graphics": 0.50,
    "emptiness": 0.25,
    "density_irregularity": 0.20,
    "schema_instability": 0.30,
    "unrepeated": 0.35,
}


@dataclass
class CandidateFeatures:
    """Everything the acceptance decision is allowed to look at."""

    row_count: int = 0
    column_count: int = 0
    alignment: float = 0.0
    multi_column: float = 0.0
    type_stability: float = 0.0
    spacing: float = 0.0
    header: float = 0.0
    ruling: float = 0.0
    numeric: float = 0.0
    agreement: float = 0.0
    size: float = 0.0
    marker_first_column: float = 0.0
    prose_pair: float = 0.0
    narrow_marker: float = 0.0
    graphics: float = 0.0
    emptiness: float = 0.0
    density_irregularity: float = 0.0
    schema_instability: float = 0.0
    unrepeated: float = 0.0

    def raw(self) -> float:
        positive = sum(weight * getattr(self, name) for name, weight in _POSITIVE_WEIGHTS.items())
        penalty = sum(weight * getattr(self, name) for name, weight in _PENALTY_WEIGHTS.items())
        return positive - penalty

    def confidence(self) -> float:
        # A logistic keeps the published number inside 0-1 and spreads the
        # interesting range — raw scores between 0.2 and 0.7 — across most of it.
        return round(1.0 / (1.0 + math.exp(-(self.raw() - 0.42) * 7.0)), 4)

    def accepted(self) -> bool:
        return self.confidence() >= ACCEPT_THRESHOLD

    def as_dict(self) -> dict:
        return {
            name: round(float(getattr(self, name)), 4)
            for name in list(_POSITIVE_WEIGHTS) + list(_PENALTY_WEIGHTS)
        }

    def weakest(self) -> str:
        """The penalty that did the most damage, for the rejection reason."""
        ranked = sorted(
            _PENALTY_WEIGHTS.items(),
            key=lambda item: item[1] * getattr(self, item[0]),
            reverse=True,
        )
        name, weight = ranked[0]
        if weight * getattr(self, name) < 0.05:
            return "insufficient-evidence"
        return name.replace("_", "-")


def _value_signature(row: LogicalRow) -> frozenset[int]:
    return frozenset(index for index in row.occupied if index != 0)


def _graphics_coverage(bounds: dict, graphics: list[tuple[float, float, float, float]]) -> float:
    """Approximate the share of the region covered by curves, fills and images."""
    if not graphics:
        return 0.0
    left, top = bounds["x"], bounds["y"]
    width, height = bounds["width"], bounds["height"]
    if width <= 0 or height <= 0:
        return 0.0
    # A page can carry a hundred shapes; only the ones that reach this region can
    # possibly cover it, and filtering first keeps the sampling grid cheap.
    graphics = [
        box
        for box in graphics
        if box[0] <= left + width and box[2] >= left and box[1] <= top + height and box[3] >= top
    ]
    if not graphics:
        return 0.0
    steps = 24
    hits = 0
    for row in range(steps):
        y = top + (row + 0.5) * height / steps
        for column in range(steps):
            x = left + (column + 0.5) * width / steps
            for gx0, gy0, gx1, gy1 in graphics:
                if gx0 <= x <= gx1 and gy0 <= y <= gy1:
                    hits += 1
                    break
    return hits / (steps * steps)


def evaluate(
    candidate,
    grid: GridHypothesis,
    layout: PageLayout,
    *,
    header_rows: int = 0,
) -> CandidateFeatures:
    features = CandidateFeatures()
    rows = grid.rows
    body = [row for row in rows[header_rows:] if row.kind != "section"]
    features.row_count = len(rows)
    features.column_count = grid.column_count
    if not rows or grid.column_count < 2:
        return features

    boundaries = grid.boundaries
    straddling = 0
    for row in rows:
        if any(
            token.x0 < boundary < token.x1
            for line in row.lines
            for token in line.tokens
            for boundary in boundaries
        ):
            straddling += 1
    features.alignment = 1.0 - straddling / len(rows)

    features.multi_column = sum(1 for row in rows if len(row.occupied) >= 2) / len(rows)
    features.size = min(1.0, max(0.0, (len(rows) - 2) / 4.0))

    # Column typing: how consistently each value column holds one kind of thing.
    stabilities: list[float] = []
    numeric_share: list[float] = []
    for index in range(1, grid.column_count):
        kinds: list[str] = []
        for row in body:
            for line in row.lines:
                for token in line.tokens:
                    if column_index(boundaries, token.center) == index and not token.is_marker:
                        kinds.append("value" if token.is_value else "word")
        if not kinds:
            continue
        stabilities.append(max(kinds.count("value"), kinds.count("word")) / len(kinds))
        numeric_share.append(kinds.count("value") / len(kinds))
    features.type_stability = mean(stabilities) if stabilities else 0.0
    features.numeric = mean(numeric_share) if numeric_share else 0.0

    pitches = [
        rows[index + 1].y0 - rows[index].y0
        for index in range(len(rows) - 1)
        if rows[index + 1].y0 > rows[index].y0
    ]
    if len(pitches) >= 2 and mean(pitches) > 0:
        variation = pstdev(pitches) / mean(pitches)
        features.spacing = max(0.0, 1.0 - variation)
        features.density_irregularity = max(0.0, min(1.0, (variation - 0.45) / 0.55))
    elif pitches:
        features.spacing = 0.8

    features.header = 1.0 if header_rows else 0.0
    # Two rows and no header band is not a repeated structure: it is a pair of
    # aligned lines, which is what a signature block or an address is. A ruled
    # grid says otherwise, because someone drew the cells.
    if len(rows) <= 2 and not header_rows and candidate.evidence != "ruled":
        features.unrepeated = 1.0

    intersections = sum(
        1
        for v_rule in candidate.vertical
        for h_rule in candidate.horizontal
        if h_rule.spans(v_rule.position, slack=0.006) and v_rule.spans(h_rule.position, slack=0.006)
    )
    features.ruling = min(1.0, intersections / 6.0)

    # Agreement: rules that land where the whitespace vote already put a boundary,
    # or that band rows the row assembly already found.
    agreements = 0
    for rule in candidate.vertical:
        if any(abs(rule.position - boundary) <= 0.01 for boundary in boundaries):
            agreements += 1
    row_edges = [row.y1 for row in rows]
    for rule in candidate.horizontal:
        if any(abs(rule.position - edge) <= 0.012 for edge in row_edges):
            agreements += 1
    features.agreement = min(1.0, agreements / max(3.0, len(rows) * 0.5))

    # Penalties.
    first_column_markers = 0
    counted = 0
    for row in rows:
        cell = row.cells[0] if row.cells else ""
        if not cell:
            continue
        counted += 1
        kinds = {
            token.kind
            for line in row.lines
            for token in line.tokens
            if column_index(boundaries, token.center) == 0
        }
        carries_values = any(
            token.is_value and not token.is_marker
            for line in row.lines
            for token in line.tokens
            if column_index(boundaries, token.center) > 0
        )
        trailing_words = sum(
            1
            for line in row.lines
            for token in line.tokens
            if column_index(boundaries, token.center) > 0
            and token.kind in ("word", "ordinal")
        )
        # A numbered footnote often embeds measurements in its sentence. Those
        # numbers are not independent value cells and must not turn the ordinal
        # gutter into a data column. Conversely, ``(6) | 10`` remains ambiguous
        # accounting data and is not penalized on shape alone.
        marker_leads_prose = trailing_words >= 2
        if (kinds and kinds <= {"bullet", "ordinal", "marker"}) or (
            _LIST_MARKER.match(cell.strip()) and (not carries_values or marker_leads_prose)
        ):
            first_column_markers += 1
    features.marker_first_column = first_column_markers / counted if counted else 0.0

    if grid.column_count == 2 and features.ruling < 0.5:
        # Two columns of running text are a page layout, not a table. An inferred
        # header cannot waive this test: the first synchronized prose line is
        # itself liable to be misread as an all-word header. A coherent ruled
        # grid can waive it because the cells then exist independently of text
        # alignment. Both sides have to be prose for the penalty to apply:
        # a list of subsidiaries against their jurisdictions has a long left cell
        # and a one-word right cell, and reading that as prose lost a real table.
        wordy = 0
        for row in body:
            per_column = [0, 0]
            values = 0
            for line in row.lines:
                for token in line.tokens:
                    index = column_index(boundaries, token.center)
                    if token.kind in ("word", "ordinal") and index < 2:
                        per_column[index] += 1
                    elif token.is_value and not token.is_marker:
                        values += 1
            # Years and small counts occur naturally inside prose. Their mere
            # presence does not turn two editorial columns into table records.
            if values <= 2 and min(per_column) >= 4:
                wordy += 1
        ratio = wordy / len(body) if body else 0.0
        # Once most rows are parallel prose, treat the layout as the categorical
        # negative it is. A proportional score left long pages just above the
        # acceptance threshold when one short paragraph tail diluted the ratio.
        features.prose_pair = 1.0 if ratio >= 0.60 else ratio

    if candidate.bounds["width"] < 0.30 and features.marker_first_column > 0.5:
        features.narrow_marker = 1.0

    features.graphics = _graphics_coverage(candidate.bounds, candidate.graphics)

    filled = sum(1 for row in rows for cell in row.cells if cell)
    total = len(rows) * grid.column_count
    emptiness = 1.0 - filled / total if total else 1.0
    features.emptiness = max(0.0, min(1.0, (emptiness - 0.35) / 0.65))

    # Instability is measured over the body only. A stacked header changes shape
    # from band to band by design, and counting that as an unstable schema
    # penalized exactly the tables that label their columns most carefully.
    informative = [
        _value_signature(row)
        for row in rows[header_rows:]
        if row.kind != "section" and len(_value_signature(row)) >= 1
    ]
    if len(informative) >= 3:
        dominant = max(set(informative), key=informative.count)
        features.schema_instability = 1.0 - informative.count(dominant) / len(informative)

    return features
