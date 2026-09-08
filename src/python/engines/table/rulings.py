"""Raster and vector ruling-line detection.

Two views of the same evidence are published. `detect_page_rulings` returns bare
normalized axis positions, which is all a single page-global grid ever needed.
`detect_page_ruling_segments` keeps each rule's extent, strength and source, so
the redesigned detector can group rules into connected components and treat two
unrelated ruled objects on one page as two proposals instead of one.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from PIL import Image, ImageOps
import math
from statistics import median


@dataclass
class RulingSegment:
    """One drawn rule, with the extent it actually covers."""

    axis: str  # "vertical" | "horizontal"
    position: float  # normalized cross-axis coordinate
    start: float  # normalized along-axis start
    end: float  # normalized along-axis end
    source: str  # "vector" | "raster"
    strength: float = 1.0
    thickness: float = 0.0

    @property
    def length(self) -> float:
        return max(0.0, self.end - self.start)

    def spans(self, value: float, *, slack: float = 0.0) -> bool:
        return self.start - slack <= value <= self.end + slack


@dataclass
class PageRulings:
    """Every rule on a page, plus the graphics that are emphatically not rules."""

    vertical: list[RulingSegment] = field(default_factory=list)
    horizontal: list[RulingSegment] = field(default_factory=list)
    # Bounding boxes of curves, diagonals, thick fills and embedded images. A
    # chart is made almost entirely of these; a table contains none of them.
    graphics: list[tuple[float, float, float, float]] = field(default_factory=list)


_GRID_DARKNESS_LEVELS = (110, 140, 170, 200, 220)
# A rule is a hairline. Filled rectangles thicker than this are row shading,
# highlight blocks or chart fills, whose edges are not table structure.
_MAX_RULE_THICKNESS_PT = 2.5
# A drawn shape is only treated as figure content when it covers area in both
# directions; anything thinner is furniture (shading, an underline, a rule).
_MIN_GRAPHIC_EXTENT = 0.04
_MIN_IMAGE_WIDTH = 400
_MIN_IMAGE_HEIGHT = 200
_MIN_RASTER_RULE_CONTRAST = 18.0
_MAX_RASTER_RULE_LUMA = 225.0
_MIN_RASTER_VERTICAL_SPAN = 0.16
_MIN_RASTER_HORIZONTAL_SPAN = 0.30


def _contains_diagonal_series(image: Image.Image) -> bool:
    """Does a raster image contain a long plotted line rather than cell ink?

    Embedded-image coverage is neutral because scans and spreadsheet screenshots
    are legitimate table sources. A long diagonal stroke is different evidence:
    it is characteristic of a plotted series and cannot be a horizontal or
    vertical cell boundary. The small Hough-style vote tolerates dashed lines
    without requiring OpenCV in the shipped worker.
    """
    sample = image.convert("L")
    sample.thumbnail((400, 400))
    width, height = sample.size
    if width < 80 or height < 60:
        return False
    pixels = sample.load()
    dark = [
        (x, y)
        for y in range(height)
        for x in range(width)
        if pixels[x, y] < 100
    ]
    if not dark:
        return False
    # These slopes cover shallow financial plots and steeper general charts.
    # Horizontal and vertical strokes are deliberately absent: they are also the
    # defining furniture of tables.
    for slope in (-1.0, -0.67, -0.5, -0.33, -0.2, 0.2, 0.33, 0.5, 0.67, 1.0):
        votes: dict[int, list[int]] = defaultdict(list)
        for x, y in dark:
            votes[round((y - slope * x) / 3.0)].append(x)
        for xs in votes.values():
            span = max(xs) - min(xs)
            if (
                span >= width * 0.60
                and len(xs) >= width * 0.30
                and len(xs) / max(1, span) >= 0.30
            ):
                return True
    return False


def _raster_chart_boxes(page, image: Image.Image) -> list[tuple[float, float, float, float]]:
    """Bounds of embedded images that carry chart-specific raster evidence."""
    width = float(page.rect.width) or 1.0
    height = float(page.rect.height) or 1.0
    boxes: list[tuple[float, float, float, float]] = []
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        return boxes
    for info in infos:
        rect = info.get("bbox")
        if rect is None:
            continue
        x0, y0, x1, y1 = (float(value) for value in rect)
        box = (
            max(0.0, (x0 - page.rect.x0) / width),
            max(0.0, (y0 - page.rect.y0) / height),
            min(1.0, (x1 - page.rect.x0) / width),
            min(1.0, (y1 - page.rect.y0) / height),
        )
        box_width = box[2] - box[0]
        box_height = box[3] - box[1]
        if box_width < _MIN_GRAPHIC_EXTENT or box_height < _MIN_GRAPHIC_EXTENT:
            continue
        if box_width >= 0.90 and box_height >= 0.90:
            continue
        crop = image.crop(
            (
                round(box[0] * image.width),
                round(box[1] * image.height),
                round(box[2] * image.width),
                round(box[3] * image.height),
            )
        )
        try:
            if _contains_diagonal_series(crop):
                boxes.append(box)
        finally:
            crop.close()
    return boxes


def _pixel_values(image: Image.Image) -> list[int]:
    flattened = getattr(image, "get_flattened_data", None)
    return list(flattened() if flattened is not None else image.getdata())


def _line_centers(
    counts: list[int],
    minimum: int,
    *,
    merge_distance: int = 1,
) -> list[int]:
    runs: list[list[int]] = []
    for index, count in enumerate(counts):
        if count < minimum:
            continue
        if not runs or index > runs[-1][-1] + merge_distance:
            runs.append([index])
        else:
            runs[-1].append(index)
    centers: list[int] = []
    for run in runs:
        weights = [counts[index] for index in run]
        total_weight = sum(weights)
        centers.append(round(sum(index * weight for index, weight in zip(run, weights)) / max(1, total_weight)))
    return centers


def detect_ruled_grid(image: Image.Image, *, dpi: int = 150) -> tuple[list[int], list[int]]:
    """Return validated raster-rule centers in pixels."""
    width, height = image.size
    segments = detect_ruled_grid_segments(image, dpi=dpi)
    return (
        [round(rule.position * width) for rule in segments if rule.axis == "vertical"],
        [round(rule.position * height) for rule in segments if rule.axis == "horizontal"],
    )


def _strip_projected_lines(dark: Image.Image, *, vertical: bool) -> list[int]:
    width, height = dark.size
    segment_count = 16
    axis_size = width if vertical else height
    cross_size = height if vertical else width
    tolerance = max(2, round(axis_size * 0.018))
    clusters: list[dict] = []
    for segment in range(segment_count):
        start = round(cross_size * segment / segment_count)
        end = round(cross_size * (segment + 1) / segment_count)
        crop = dark.crop((0, start, width, end)) if vertical else dark.crop((start, 0, end, height))
        projected = crop.resize((axis_size, 1) if vertical else (1, axis_size), Image.Resampling.BOX)
        centers = _line_centers(
            _pixel_values(projected),
            round(255 * 0.58),
            merge_distance=max(1, round(axis_size * 0.003)),
        )
        for center in centers:
            if center < axis_size * 0.02 or center > axis_size * 0.98:
                continue
            nearest = min(
                clusters,
                key=lambda cluster: abs(center - sum(cluster["values"]) / len(cluster["values"])),
                default=None,
            )
            if nearest is None or abs(center - sum(nearest["values"]) / len(nearest["values"])) > tolerance:
                clusters.append({"values": [center], "segments": {segment}})
            else:
                nearest["values"].append(center)
                nearest["segments"].add(segment)
    # A line-item grid near the bottom of an invoice may occupy only a quarter
    # of the page height. Horizontal rules still need broad page-width support,
    # while vertical rules may legitimately appear in fewer cross-axis strips.
    support_ratio = 0.18 if vertical else 0.35
    minimum_support = max(3, math.ceil(segment_count * support_ratio))
    centers = [
        round(sum(cluster["values"]) / len(cluster["values"]))
        for cluster in clusters
        if len(cluster["segments"]) >= minimum_support
    ]
    return _line_centers(
        [255 if index in centers else 0 for index in range(axis_size)],
        1,
        merge_distance=max(1, round(axis_size * 0.012)),
    )


def _dedupe(values: list[float], tolerance: float = 0.004) -> list[float]:
    result: list[float] = []
    for value in sorted(max(0.0, min(1.0, item)) for item in values):
        if not result or value - result[-1] > tolerance:
            result.append(value)
        else:
            result[-1] = (result[-1] + value) / 2
    return result


def _to_displayed(matrix: tuple[float, ...], x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def _vector_ruling_segments(page) -> PageRulings:
    """Rules and graphics from the page's vector content, with real extents."""
    width = float(page.rect.width)
    height = float(page.rect.height)
    result = PageRulings()
    if width <= 0 or height <= 0:
        return result
    # `get_drawings` reports the unrotated page, while `page.rect` and the raster
    # pass below both describe the displayed one. On a rotated page that put every
    # rule on the wrong axis at the wrong offset — a rule displaying vertically at
    # x=0.83 was emitted as horizontal at y=0.25 — and mixed two coordinate spaces
    # into one list. The rotation matrix is the identity for upright pages.
    try:
        matrix = tuple(float(value) for value in page.rotation_matrix)
        if len(matrix) != 6:
            raise ValueError
    except Exception:
        matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    def _normalize(x: float, y: float) -> tuple[float, float]:
        display_x, display_y = _to_displayed(matrix, x, y)
        return (display_x - page.rect.x0) / width, (display_y - page.rect.y0) / height

    def _record_graphic(rect, *, fill=None) -> None:
        """Remember a shape only if it covers area in both directions.

        Row shading and highlight bars are filled rectangles too, and they are a
        table's furniture rather than a chart's. A band one line high says
        nothing about whether the region is plotted; a block that is broad and
        tall in equal measure is the plot area of a figure.
        """
        try:
            x0, y0 = _normalize(float(rect.x0), float(rect.y0))
            x1, y1 = _normalize(float(rect.x1), float(rect.y1))
        except Exception:
            return
        box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        box_width = box[2] - box[0]
        box_height = box[3] - box[1]
        if box_width < _MIN_GRAPHIC_EXTENT or box_height < _MIN_GRAPHIC_EXTENT:
            return
        # A PDF commonly paints the paper itself as one page-sized rectangle.
        # That is canvas, not content. Counting it as a figure gives every table
        # on the page 100% graphics coverage. The same is true of a white panel:
        # it is visually empty regardless of how large its drawing command is.
        near_page_backdrop = box_width >= 0.90 and box_height >= 0.90
        components = tuple(float(component) for component in (fill or ()))
        visually_white = bool(components) and min(components) >= 0.95
        if near_page_backdrop or visually_white:
            return
        result.graphics.append(box)

    for drawing in page.get_drawings():
        stroked = drawing.get("type") in ("s", "fs") or drawing.get("color") is not None
        line_width = float(drawing.get("width") or 0.0)
        for item in drawing.get("items", []):
            kind = item[0]
            segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
            if kind == "l" and len(item) >= 3:
                segments.append((item[1], item[2]))
            elif kind in ("c", "qu") and len(item) >= 2:
                # A curve or quad is never a rule. It is, however, exactly what a
                # plotted series is made of, so it is worth remembering.
                _record_graphic(drawing.get("rect"))
                continue
            elif kind == "re" and len(item) >= 2:
                rect = item[1]
                thickness = min(abs(rect.x1 - rect.x0), abs(rect.y1 - rect.y0))
                if stroked:
                    # An outlined box: all four edges are drawn, so all four are rules.
                    segments.extend(
                        [
                            ((rect.x0, rect.y0), (rect.x1, rect.y0)),
                            ((rect.x1, rect.y0), (rect.x1, rect.y1)),
                            ((rect.x1, rect.y1), (rect.x0, rect.y1)),
                            ((rect.x0, rect.y1), (rect.x0, rect.y0)),
                        ]
                    )
                elif thickness <= _MAX_RULE_THICKNESS_PT:
                    # A hairline drawn as a filled rectangle — the usual way an
                    # accounting underline is emitted. Collapse it to its centreline.
                    if abs(rect.y1 - rect.y0) <= abs(rect.x1 - rect.x0):
                        middle = (rect.y0 + rect.y1) / 2
                        segments.append(((rect.x0, middle), (rect.x1, middle)))
                    else:
                        middle = (rect.x0 + rect.x1) / 2
                        segments.append(((middle, rect.y0), (middle, rect.y1)))
                else:
                    # Row shading or a filled plot area. Taking its edges as rules
                    # made every stripe look like a cell border, which drove row
                    # detection down the ruled path and discarded the header.
                    _record_graphic(rect, fill=drawing.get("fill"))
                    continue
            for first, second in segments:
                x0, y0 = _to_displayed(matrix, float(first[0]), float(first[1]))
                x1, y1 = _to_displayed(matrix, float(second[0]), float(second[1]))
                horizontal_run = abs(x1 - x0)
                vertical_run = abs(y1 - y0)
                if vertical_run > 1.5 and horizontal_run > 1.5:
                    # A diagonal: a leader line, a chart series or a strike-through.
                    box = (
                        (min(x0, x1) - page.rect.x0) / width,
                        (min(y0, y1) - page.rect.y0) / height,
                        (max(x0, x1) - page.rect.x0) / width,
                        (max(y0, y1) - page.rect.y0) / height,
                    )
                    if (
                        box[2] - box[0] >= _MIN_GRAPHIC_EXTENT
                        and box[3] - box[1] >= _MIN_GRAPHIC_EXTENT
                    ):
                        result.graphics.append(box)
                    continue
                thickness = max(line_width, 0.4) / max(width, height)
                if horizontal_run <= 1.5 and vertical_run >= height * 0.04:
                    result.vertical.append(
                        RulingSegment(
                            axis="vertical",
                            position=((x0 + x1) / 2 - page.rect.x0) / width,
                            start=(min(y0, y1) - page.rect.y0) / height,
                            end=(max(y0, y1) - page.rect.y0) / height,
                            source="vector",
                            strength=1.0,
                            thickness=thickness,
                        )
                    )
                if vertical_run <= 1.5 and horizontal_run >= width * 0.04:
                    result.horizontal.append(
                        RulingSegment(
                            axis="horizontal",
                            position=((y0 + y1) / 2 - page.rect.y0) / height,
                            start=(min(x0, x1) - page.rect.x0) / width,
                            end=(max(x0, x1) - page.rect.x0) / width,
                            source="vector",
                            strength=1.0,
                            thickness=thickness,
                        )
                    )
    return result


def _vector_rulings(page) -> tuple[list[float], list[float]]:
    rulings = _vector_ruling_segments(page)
    return (
        _dedupe([rule.position for rule in rulings.vertical]),
        _dedupe([rule.position for rule in rulings.horizontal]),
    )


def _run_extent(values: list[int], center: int, minimum: int, tolerance: int) -> tuple[int, int]:
    """Walk out from a detected line centre while the ink continues.

    A rule may be interrupted where it passes behind a cell value or where the
    rasterizer dropped a pixel, so short breaks are stepped over; a long break is
    the end of the rule.
    """
    size = len(values)
    if not size:
        return 0, 0
    center = max(0, min(size - 1, center))
    left = center
    gap = 0
    for index in range(center, -1, -1):
        if values[index] >= minimum:
            left = index
            gap = 0
        else:
            gap += 1
            if gap > tolerance:
                break
    right = center
    gap = 0
    for index in range(center, size):
        if values[index] >= minimum:
            right = index
            gap = 0
        else:
            gap += 1
            if gap > tolerance:
                break
    return left, right


def _measure_raster_extents(
    dark: Image.Image, centers: list[int], *, vertical: bool
) -> list[RulingSegment]:
    width, height = dark.size
    axis_size = height if vertical else width
    cross_size = width if vertical else height
    tolerance = max(2, round(axis_size * 0.02))
    search_radius = max(2, round(cross_size * 0.018))
    segments: list[RulingSegment] = []
    for center in centers:
        values = [
            255
            if _nearest_dark_run(
                dark,
                along,
                center,
                search_radius,
                vertical=vertical,
            )
            is not None
            else 0
            for along in range(axis_size)
        ]
        start, end = _run_extent(values, _densest(values), 128, tolerance)
        coverage = sum(1 for value in values[start : end + 1] if value >= 128)
        span = max(1, end - start + 1)
        segments.append(
            RulingSegment(
                axis="vertical" if vertical else "horizontal",
                position=center / (width if vertical else height),
                start=start / axis_size,
                end=(end + 1) / axis_size,
                source="raster",
                strength=coverage / span,
                thickness=1.0 / (width if vertical else height),
            )
        )
    return segments


def _nearest_dark_run(
    dark: Image.Image,
    along: int,
    center: int,
    radius: int,
    *,
    vertical: bool,
) -> tuple[int, int] | None:
    """Nearest ink run on one scanline inside a drifting-rule corridor."""
    width, height = dark.size
    cross_size = width if vertical else height
    axis_size = height if vertical else width
    if not (0 <= along < axis_size):
        return None
    low = max(0, center - radius)
    high = min(cross_size, center + radius + 1)
    pixels = dark.load()
    runs: list[tuple[int, int]] = []
    start = None
    for cross in range(low, high):
        value = pixels[cross, along] if vertical else pixels[along, cross]
        if value >= 128:
            if start is None:
                start = cross
        elif start is not None:
            runs.append((start, cross))
            start = None
    if start is not None:
        runs.append((start, high))
    return min(
        runs,
        key=lambda run: abs((run[0] + run[1]) / 2 - center),
        default=None,
    )


def _validate_raster_rule(
    gray: Image.Image,
    dark: Image.Image,
    rule: RulingSegment,
    *,
    dpi: int,
) -> RulingSegment | None:
    """Reject a projected photo band unless it is a thin contrasted rule."""
    vertical = rule.axis == "vertical"
    cross_size = gray.width if vertical else gray.height
    along_size = gray.height if vertical else gray.width
    along_start = max(0, min(along_size - 1, round(rule.start * along_size)))
    along_end = max(along_start + 1, min(along_size, round(rule.end * along_size)))
    minimum_span = (
        _MIN_RASTER_VERTICAL_SPAN if vertical else _MIN_RASTER_HORIZONTAL_SPAN
    )
    if (along_end - along_start) / along_size < minimum_span:
        return None

    maximum_thickness = max(2, math.ceil(_MAX_RULE_THICKNESS_PT * dpi / 72.0))
    center = max(0, min(cross_size - 1, round(rule.position * cross_size)))
    search_radius = max(2, round(cross_size * 0.018))
    sample_step = max(1, (along_end - along_start) // 128)
    gray_pixels = gray.load()
    thicknesses: list[int] = []
    core_lumas: list[float] = []
    contrasts: list[float] = []
    sample_count = 0
    for along in range(along_start, along_end, sample_step):
        sample_count += 1
        run = _nearest_dark_run(
            dark, along, center, search_radius, vertical=vertical
        )
        if run is None:
            continue
        core_start, core_end = run
        thickness = core_end - core_start
        thicknesses.append(thickness)
        core_values = [
            gray_pixels[cross, along] if vertical else gray_pixels[along, cross]
            for cross in range(core_start, core_end)
        ]
        core_luma = sum(core_values) / len(core_values)
        core_lumas.append(core_luma)
        sample_width = max(2, thickness)
        gap = max(2, math.ceil(thickness / 2))
        neighbours: list[float] = []
        before_end = core_start - gap
        before_start = max(0, before_end - sample_width)
        if before_end > before_start:
            values = [
                gray_pixels[cross, along] if vertical else gray_pixels[along, cross]
                for cross in range(before_start, before_end)
            ]
            neighbours.append(sum(values) / len(values))
        after_start = core_end + gap
        after_end = min(cross_size, after_start + sample_width)
        if after_end > after_start:
            values = [
                gray_pixels[cross, along] if vertical else gray_pixels[along, cross]
                for cross in range(after_start, after_end)
            ]
            neighbours.append(sum(values) / len(values))
        if neighbours:
            # A nearby annotation or doubled scan stroke can darken one side of
            # an otherwise valid rule. The line still has a paper-side contrast;
            # a low-contrast photographic band has neither.
            contrasts.append(max(neighbours) - core_luma)

    if not thicknesses or len(thicknesses) / max(1, sample_count) < 0.55:
        return None
    upper_quartile_thickness = sorted(thicknesses)[int((len(thicknesses) - 1) * 0.75)]
    if upper_quartile_thickness > maximum_thickness:
        return None
    core_luma = median(core_lumas)
    local_contrast = median(contrasts) if contrasts else 0.0
    if core_luma > _MAX_RASTER_RULE_LUMA or local_contrast < _MIN_RASTER_RULE_CONTRAST:
        return None

    rule.strength = len(thicknesses) / max(1, sample_count)
    rule.thickness = median(thicknesses) / cross_size
    return rule


def _coherent_raster_grid(
    vertical: list[RulingSegment], horizontal: list[RulingSegment]
) -> tuple[list[RulingSegment], list[RulingSegment]]:
    """Keep only rules participating in a connected cell-like lattice."""
    previous = None
    while previous != (len(vertical), len(horizontal)):
        previous = (len(vertical), len(horizontal))
        vertical = [
            rule
            for rule in vertical
            if sum(
                1
                for cross in horizontal
                if rule.spans(cross.position, slack=0.004)
                and cross.spans(rule.position, slack=0.004)
            )
            >= 2
        ]
        horizontal = [
            rule
            for rule in horizontal
            if sum(
                1
                for cross in vertical
                if rule.spans(cross.position, slack=0.004)
                and cross.spans(rule.position, slack=0.004)
            )
            >= 2
        ]
    return vertical, horizontal


def _densest(values: list[int]) -> int:
    """The index at the centre of the longest inked run, used as a seed."""
    best_start = best_length = 0
    start = None
    for index, value in enumerate(values):
        if value >= 128:
            if start is None:
                start = index
        elif start is not None:
            if index - start > best_length:
                best_start, best_length = start, index - start
            start = None
    if start is not None and len(values) - start > best_length:
        best_start, best_length = start, len(values) - start
    return best_start + best_length // 2


def detect_ruled_grid_segments(image: Image.Image, *, dpi: int = 150) -> list[RulingSegment]:
    """Validated raster rules with their individually measured extents."""
    gray = ImageOps.grayscale(image)
    width, height = gray.size
    if width < _MIN_IMAGE_WIDTH or height < _MIN_IMAGE_HEIGHT:
        return []
    for darkness in _GRID_DARKNESS_LEVELS:
        dark = gray.point(lambda value, limit=darkness: 255 if value < limit else 0)
        vertical_density = _pixel_values(dark.resize((width, 1), Image.Resampling.BOX))
        horizontal_density = _pixel_values(dark.resize((1, height), Image.Resampling.BOX))
        x_lines = _line_centers(
            vertical_density, round(255 * 0.70), merge_distance=max(1, round(width * 0.008))
        )
        y_lines = _line_centers(
            horizontal_density, round(255 * 0.50), merge_distance=max(1, round(height * 0.008))
        )
        if len(x_lines) < 3 or len(y_lines) < 3:
            x_lines = _strip_projected_lines(dark, vertical=True)
            y_lines = _strip_projected_lines(dark, vertical=False)
        if len(x_lines) >= 3 and len(y_lines) >= 3:
            vertical = [
                validated
                for rule in _measure_raster_extents(dark, x_lines, vertical=True)
                if (validated := _validate_raster_rule(gray, dark, rule, dpi=dpi))
                is not None
            ]
            horizontal = [
                validated
                for rule in _measure_raster_extents(dark, y_lines, vertical=False)
                if (validated := _validate_raster_rule(gray, dark, rule, dpi=dpi))
                is not None
            ]
            vertical, horizontal = _coherent_raster_grid(vertical, horizontal)
            if len(vertical) >= 3 and len(horizontal) >= 3:
                return vertical + horizontal
    return []


def detect_page_ruling_segments(page, *, dpi: int = 150) -> PageRulings:
    """Vector and raster rules for one page, keeping extents and page graphics."""
    rulings = _vector_ruling_segments(page)
    # An embedded image is a transport, not a semantic role: a chart, a scanned
    # invoice and an Excel screenshot can all cover the same rectangle. Raster
    # content therefore remains neutral here. Its table evidence is measured by
    # the alignment and ruling passes; chart-specific vector curves and diagonals
    # are still recorded above.
    try:
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        rulings.graphics.extend(_raster_chart_boxes(page, image))
        for segment in detect_ruled_grid_segments(image, dpi=dpi):
            if segment.axis == "vertical":
                rulings.vertical.append(segment)
            else:
                rulings.horizontal.append(segment)
    except Exception:  # Raster evidence is opportunistic; vector evidence remains useful.
        pass
    return rulings


def merge_parallel(segments: list[RulingSegment], tolerance: float = 0.004) -> list[RulingSegment]:
    """Collapse rules that describe the same line, unioning their extents.

    A rule found by both the vector and the raster pass, or a dashed rule found in
    pieces, must count once — otherwise a single underline looks like a grid.
    """
    ordered = sorted(segments, key=lambda item: (item.position, item.start))
    merged: list[RulingSegment] = []
    for segment in ordered:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and segment.position - previous.position <= tolerance
            # Two rules at the same offset that never overlap are two rules — the
            # left and right halves of a two-up layout, for instance.
            and segment.start <= previous.end + tolerance * 4
        ):
            previous.position = (previous.position + segment.position) / 2
            previous.start = min(previous.start, segment.start)
            previous.end = max(previous.end, segment.end)
            previous.strength = max(previous.strength, segment.strength)
            if previous.source != segment.source:
                previous.source = "mixed"
            continue
        merged.append(
            RulingSegment(
                segment.axis,
                segment.position,
                segment.start,
                segment.end,
                segment.source,
                segment.strength,
                segment.thickness,
            )
        )
    return merged


def ruling_components(
    rulings: PageRulings, *, slack: float = 0.006
) -> list[tuple[list[RulingSegment], list[RulingSegment]]]:
    """Group rules into independent drawn objects.

    Two rules belong to the same object when they cross, or nearly cross. The old
    page-global min/max over every ruling treated a chart, a signature box and a
    real grid as one enormous table; a connected component is the smallest honest
    unit of "one ruled thing".
    """
    vertical = merge_parallel([rule for rule in rulings.vertical])
    horizontal = merge_parallel([rule for rule in rulings.horizontal])
    nodes = vertical + horizontal
    parent = list(range(len(nodes)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> None:
        first, second = find(first), find(second)
        if first != second:
            parent[second] = first

    for v_index, v_rule in enumerate(vertical):
        for h_offset, h_rule in enumerate(horizontal):
            h_index = len(vertical) + h_offset
            if h_rule.spans(v_rule.position, slack=slack) and v_rule.spans(
                h_rule.position, slack=slack
            ):
                union(v_index, h_index)

    groups: dict[int, tuple[list[RulingSegment], list[RulingSegment]]] = {}
    for index, node in enumerate(nodes):
        root = find(index)
        bucket = groups.setdefault(root, ([], []))
        bucket[0 if node.axis == "vertical" else 1].append(node)
    return [
        (sorted(v, key=lambda item: item.position), sorted(h, key=lambda item: item.position))
        for v, h in groups.values()
    ]


def detect_page_rulings(page, *, dpi: int = 150) -> tuple[list[float], list[float]]:
    """Return normalized displayed-page rulings from vector paths and raster projection."""
    vertical, horizontal = _vector_rulings(page)
    try:
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        x_lines, y_lines = detect_ruled_grid(image, dpi=dpi)
        vertical.extend(value / pixmap.width for value in x_lines)
        horizontal.extend(value / pixmap.height for value in y_lines)
    except Exception:  # Raster evidence is opportunistic; vector evidence remains useful.
        pass
    return _dedupe(vertical), _dedupe(horizontal)
