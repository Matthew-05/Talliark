"""Column-aware OCR geometry recovery for low-resolution ruled tables."""
from __future__ import annotations

import copy
import io
import math
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from typing import Callable

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from engines.ocr_engine import configure_tesseract
from schemas.models import Stage
from engines.table_date_engine import (
    _map_image_rect,
    _prepare_cell,
    _recognize_date,
    normalize_date,
)
from engines.table.rulings import detect_ruled_grid


_MIN_TABLE_IMAGE_PAGE_COVERAGE = 0.05
_MIN_TABLE_IMAGE_VISIBLE_FRACTION = 0.80
_DOCUMENT_IMAGE_SAMPLE_SIZE = 256
_MIN_DOCUMENT_LIGHT_PIXEL_RATIO = 0.35
_MAX_CELL_OCR_WORKERS = 8

# Sparse page text is enlarged for Tesseract, but its raster has a hard practical
# ceiling. A fixed 8x enlargement turned Boeing's 5,090 x 1,061 title region into
# a 40,720-pixel-wide image and aborted the whole optional recovery pass. Keep the
# preferred enlargement on ordinary regions and reduce it deterministically when
# either dimension or total pixel work would become pathological.
_PREFERRED_SPARSE_OCR_SCALE = 8
_MAX_SPARSE_OCR_DIMENSION = 24_000
_MAX_SPARSE_OCR_PIXELS = 60_000_000


def _cell_ocr_worker_count(task_count: int) -> int:
    return min(
        max(1, task_count),
        max(1, min(_MAX_CELL_OCR_WORKERS, os.cpu_count() or 1)),
    )


def _bounded_sparse_ocr_scale(width: int, height: int) -> int:
    """Largest integer enlargement inside the sparse-region OCR budget."""
    if width <= 0 or height <= 0:
        return 1
    by_dimension = _MAX_SPARSE_OCR_DIMENSION // max(width, height)
    by_pixels = int(math.sqrt(_MAX_SPARSE_OCR_PIXELS / (width * height)))
    return max(1, min(_PREFERRED_SPARSE_OCR_SCALE, by_dimension, by_pixels))


def _largest_table_placement(
    page_rect: fitz.Rect,
    placements: list[fitz.Rect],
) -> fitz.Rect | None:
    """Return the largest placement only when it could plausibly hold a table."""
    page_area = page_rect.get_area()
    if page_area <= 0 or not placements:
        return None
    candidates: list[tuple[float, fitz.Rect]] = []
    for placement in placements:
        placement_area = placement.get_area()
        if placement_area <= 0:
            continue
        visible_area = (placement & page_rect).get_area()
        if (
            visible_area / page_area < _MIN_TABLE_IMAGE_PAGE_COVERAGE
            or visible_area / placement_area < _MIN_TABLE_IMAGE_VISIBLE_FRACTION
        ):
            continue
        candidates.append((visible_area, placement))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _looks_like_document_scan(image: Image.Image) -> bool:
    """Reject photographs and design art before expensive cell OCR.

    Cell recovery assumes dark type and rules on a light document surface. Long
    edges in photographs can satisfy the ruling detector, but their continuous
    mid-tone backgrounds make them poor OCR inputs and can create enormous false
    grids. Autocontrast accommodates gray or tinted paper before the inexpensive
    thumbnail histogram is measured.
    """
    sample = ImageOps.grayscale(image)
    sample.thumbnail(
        (_DOCUMENT_IMAGE_SAMPLE_SIZE, _DOCUMENT_IMAGE_SAMPLE_SIZE),
        Image.Resampling.BILINEAR,
    )
    if sample.width <= 0 or sample.height <= 0:
        return False
    histogram = ImageOps.autocontrast(sample).histogram()
    pixel_count = sum(histogram)
    if pixel_count <= 0:
        return False
    light_pixel_ratio = sum(histogram[220:]) / pixel_count
    return light_pixel_ratio >= _MIN_DOCUMENT_LIGHT_PIXEL_RATIO


def _clean_grid(image: Image.Image, x_lines: list[int], y_lines: list[int]) -> Image.Image:
    clean = ImageOps.grayscale(image).copy()
    draw = ImageDraw.Draw(clean)
    for x in x_lines:
        draw.rectangle(
            (x - 2, y_lines[0] - 1, x + 2, y_lines[-1] + 1),
            fill=255,
        )
    for y in y_lines:
        draw.rectangle(
            (x_lines[0] - 1, y - 2, x_lines[-1] + 1, y + 2),
            fill=255,
        )
    return clean


def _ink_line_boxes(
    image: Image.Image,
    *,
    threshold: int = 200,
) -> list[tuple[int, int, int, int]]:
    """Return tight foreground boxes for each printed text line in a cell."""
    gray = ImageOps.grayscale(image)
    pixels = gray.load()
    active_rows = [
        y
        for y in range(gray.height)
        if any(pixels[x, y] < threshold for x in range(gray.width))
    ]
    if not active_rows:
        return []

    row_groups: list[list[int]] = [[active_rows[0]]]
    for y in active_rows[1:]:
        # Join tiny vertical gaps from dotted glyphs and scan dropout while
        # retaining the larger whitespace between genuinely wrapped lines.
        if y <= row_groups[-1][-1] + 3:
            row_groups[-1].append(y)
        else:
            row_groups.append([y])

    boxes: list[tuple[int, int, int, int]] = []
    for rows in row_groups:
        y0 = rows[0]
        y1 = rows[-1] + 1
        columns = [
            x
            for x in range(gray.width)
            if any(pixels[x, y] < threshold for y in rows)
        ]
        dark_pixels = sum(
            pixels[x, y] < threshold
            for x in columns
            for y in rows
        )
        if not columns or dark_pixels < 2:
            continue
        boxes.append((columns[0], y0, columns[-1] + 1, y1))
    return boxes


def _ink_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    boxes = _ink_line_boxes(image)
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _header_line_texts(text: str, line_count: int) -> list[str]:
    if line_count <= 1:
        return [text]
    words = text.split()
    if len(words) < line_count:
        return [text]
    return [
        " ".join(words[start:end])
        for start, end in (
            (
                round(index * len(words) / line_count),
                round((index + 1) * len(words) / line_count),
            )
            for index in range(line_count)
        )
    ]


def _map_cell_segments(
    placement: fitz.Rect,
    image_size: tuple[int, int],
    cell_origin: tuple[int, int],
    cell_image: Image.Image,
    texts: list[str],
) -> list[dict]:
    boxes = _ink_line_boxes(cell_image)
    if len(boxes) != len(texts):
        union = _ink_box(cell_image)
        boxes = [union] if union else []
        texts = [" ".join(texts)]
    origin_x, origin_y = cell_origin
    return [
        {
            "text": text,
            "rect": _map_image_rect(
                placement,
                image_size,
                (
                    origin_x + box[0],
                    origin_y + box[1],
                    origin_x + box[2],
                    origin_y + box[3],
                ),
            ),
        }
        for text, box in zip(texts, boxes)
        if text
    ]


def _cleanup_text(text: str) -> str:
    cleaned = text.replace("�", " ").replace("—", "-").replace("–", "-")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" |_-")
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    return cleaned.strip()


def _ocr_candidate(
    cell: Image.Image,
    language: str,
    *,
    scale: int = 10,
    threshold: int | None = None,
    sharpen: bool = False,
    digits_only: bool = False,
    numeric_only: bool = False,
    psm: int = 7,
) -> tuple[str, float]:
    import pytesseract

    prepared = ImageOps.autocontrast(ImageOps.grayscale(cell))
    if sharpen:
        prepared = prepared.filter(ImageFilter.SHARPEN)
    if threshold is not None:
        prepared = prepared.point(lambda value: 0 if value < threshold else 255)
    prepared = prepared.resize(
        (prepared.width * scale, prepared.height * scale),
        Image.Resampling.LANCZOS,
    )
    padded = Image.new("L", (prepared.width + 40, prepared.height + 40), 255)
    padded.paste(prepared, (20, 20))
    config = f"--psm {psm} --dpi 600 -c preserve_interword_spaces=1"
    if digits_only:
        config += " -c tessedit_char_whitelist=0123456789"
    elif numeric_only:
        config += " -c tessedit_char_whitelist=0123456789.,$%()-/"
    data = pytesseract.image_to_data(
        padded,
        lang=language,
        config=config,
        output_type=pytesseract.Output.DICT,
    )
    words: list[str] = []
    confidences: list[float] = []
    for raw_text, raw_confidence in zip(data["text"], data["conf"]):
        text = str(raw_text).strip()
        if not text:
            continue
        words.append(text)
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            continue
        if confidence >= 0:
            confidences.append(confidence)
    text = _cleanup_text(" ".join(words))
    mean_confidence = sum(confidences) / len(confidences) if confidences else -1.0
    return text, mean_confidence


def _cell_candidates(
    raw_cell: Image.Image,
    clean_cell: Image.Image,
    language: str,
    *,
    kind: str,
    digits_only: bool = False,
    numeric_only: bool = False,
) -> list[dict]:
    if kind == "status":
        variants = [("raw", raw_cell, 10, None, False)]
    elif kind in {"number", "numeric", "currency", "percentage"}:
        variants = [
            ("raw", raw_cell, 10, None, False),
            ("raw-threshold", raw_cell, 10, 160, False),
            ("clean", clean_cell, 10, None, False),
        ]
    else:
        variants = [
            ("raw", raw_cell, 10, None, False),
            ("clean", clean_cell, 10, None, False),
        ]

    candidates: list[dict] = []
    psm = 6 if len(_ink_line_boxes(clean_cell)) > 1 else 7

    def append_variant(variant: tuple[str, Image.Image, int, int | None, bool]) -> None:
        source, image, scale, threshold, sharpen = variant
        text, confidence = _ocr_candidate(
            image,
            language,
            scale=scale,
            threshold=threshold,
            sharpen=sharpen,
            digits_only=digits_only,
            numeric_only=numeric_only,
            psm=psm,
        )
        if text:
            candidates.append(
                {"source": source, "text": text, "confidence": confidence}
            )

    for variant in variants:
        append_variant(variant)

    best_confidence = max(
        (candidate["confidence"] for candidate in candidates),
        default=-1,
    )
    if kind in {"number", "numeric", "currency", "percentage"} and (
        best_confidence < 80 or len({c["text"] for c in candidates}) > 1
    ):
        retries = (
            ("raw-small", raw_cell, 8, None, False),
            ("clean-small", clean_cell, 8, None, False),
            ("clean-sharp", clean_cell, 10, None, True),
        )
    elif kind == "city" and (
        best_confidence < 85 or len({c["text"] for c in candidates}) > 1
    ):
        retries = (
            ("raw-small", raw_cell, 8, None, False),
            ("clean-small", clean_cell, 8, None, False),
        )
    elif kind == "text" and best_confidence < 50:
        retries = (
            ("raw-small", raw_cell, 8, None, False),
            ("raw-threshold", raw_cell, 10, 160, False),
            ("clean-small", clean_cell, 8, None, False),
            ("clean-sharp", clean_cell, 10, None, True),
        )
    elif kind == "status" and not candidates:
        retries = (("clean", clean_cell, 10, None, False),)
    else:
        retries = ()
    for variant in retries:
        append_variant(variant)
    return candidates


def _select_number(candidates: list[dict]) -> str | None:
    numeric = [candidate for candidate in candidates if candidate["text"].isdigit()]
    if not numeric:
        return None
    scores: dict[str, float] = {}
    for text in {candidate["text"] for candidate in numeric}:
        matching = [candidate for candidate in numeric if candidate["text"] == text]
        scores[text] = max(candidate["confidence"] for candidate in matching) + len(matching) * 0.5
    return max(scores, key=lambda text: (scores[text], len(text)))


def _select_numeric(candidates: list[dict], kind: str) -> str | None:
    """Choose numeric OCR using generic column format, confidence, and consensus."""
    numeric = [candidate for candidate in candidates if any(c.isdigit() for c in candidate["text"])]
    if not numeric:
        return None
    counts = Counter(candidate["text"] for candidate in numeric)

    def format_score(text: str) -> float:
        if kind == "currency":
            if re.fullmatch(r"\$\d{1,3}(?:,\d{3})*\.\d{2}", text):
                return 35.0
            if re.fullmatch(r"\$\d+\.\d{2}", text):
                return 28.0
            if text.startswith("$"):
                return 8.0
            return 0.0
        if kind == "percentage":
            if re.fullmatch(r"\d+\.\d+%", text):
                return 30.0
            if re.fullmatch(r"\d+%", text):
                return 8.0
            return 0.0
        return 8.0 if re.fullmatch(r"[\d,.$%()/\-]+", text) else 0.0

    return max(
        numeric,
        key=lambda candidate: (
            format_score(candidate["text"])
            + min(15.0, counts[candidate["text"]] * 3.0)
            + float(candidate["confidence"]) * 0.30,
            float(candidate["confidence"]),
        ),
    )["text"]


def _normalize_currency_separators(text: str | None) -> str | None:
    """Repair a US-dollar thousands separator only when the pattern is unambiguous."""
    if not text or not re.fullmatch(r"\$\d{1,3}(?:\.\d{3})+\.\d{2}", text):
        return text
    integer, decimal = text.rsplit(".", 1)
    return integer.replace(".", ",") + "." + decimal


def _select_status(candidates: list[dict]) -> str | None:
    return _select_general_text(candidates)


def _looks_noisy(text: str) -> bool:
    return not text or bool(re.search(r"[_|�]", text))


def _select_city(candidates: list[dict]) -> str | None:
    if not candidates:
        return None
    counts = Counter(candidate["text"] for candidate in candidates)

    def score(text: str) -> tuple[int, int, float]:
        matching = [candidate for candidate in candidates if candidate["text"] == text]
        has_raw = any(candidate["source"].startswith("raw") for candidate in matching)
        return (
            counts[text],
            1 if has_raw else 0,
            max(candidate["confidence"] for candidate in matching),
        )

    return max(counts, key=score)


def _select_general_text(candidates: list[dict]) -> str | None:
    if not candidates:
        return None
    usable = [candidate for candidate in candidates if not _looks_noisy(candidate["text"])]
    pool = usable or candidates
    counts = Counter(_normalized_key(candidate["text"]) for candidate in pool)

    def score(candidate: dict) -> tuple[int, float, int, int]:
        text = candidate["text"]
        return (
            counts[_normalized_key(text)],
            float(candidate["confidence"]),
            1 if candidate["source"].startswith("raw") else 0,
            len(text),
        )

    return max(pool, key=score)["text"]


def _normalized_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _canonicalize_repeated_cells(cells: list[dict]) -> None:
    """Use repeated values in a text column to repair faint or confused glyphs."""
    groups: list[list[dict]] = []
    for cell in cells:
        text = cell.get("text") or ""
        if not text:
            continue
        key = _normalized_key(text)
        for group in groups:
            representative = _normalized_key(group[0]["text"])
            if SequenceMatcher(None, key, representative).ratio() >= 0.82:
                group.append(cell)
                break
        else:
            groups.append([cell])

    for group in groups:
        if len(group) < 3:
            continue
        pool = [
            candidate["text"]
            for cell in group
            for candidate in cell.get("candidates", [])
            if candidate["source"].startswith("clean") and candidate["text"]
        ]
        if not pool:
            continue
        normalized_counts = Counter(_normalized_key(text) for text in pool)
        winning_key = max(normalized_counts, key=normalized_counts.get)
        spellings = [text for text in pool if _normalized_key(text) == winning_key]
        canonical = Counter(spellings).most_common(1)[0][0]
        for cell in group:
            if SequenceMatcher(
                None,
                _normalized_key(cell["text"]),
                winning_key,
            ).ratio() >= 0.80:
                cell["text"] = canonical


def _column_kind(header: str) -> str:
    normalized = _normalized_key(header)
    if "date" in normalized:
        return "date"
    if any(
        hint in normalized
        for hint in ("count", "quantity", "number", "employees", "noof")
    ):
        return "number"
    if "rate" in normalized or "percent" in normalized:
        return "percentage"
    if any(
        hint in normalized
        for hint in ("amount", "balance", "price", "cost", "total")
    ):
        return "currency"
    if "status" in normalized:
        return "status"
    if "city" in normalized:
        return "city"
    return "text"


def _recognize_cell_images(
    raw_cell: Image.Image,
    clean_cell: Image.Image,
    kind: str,
    language: str,
) -> dict:
    """Recognize one already-cropped cell; safe to run in the cell thread pool."""
    has_ink = _ink_box(clean_cell) is not None
    candidates: list[dict] = []
    if not has_ink:
        text = None
    elif kind == "date":
        text = _recognize_date(raw_cell, language)
    else:
        candidates = _cell_candidates(
            raw_cell,
            clean_cell,
            language,
            kind=kind,
            digits_only=kind == "number",
            numeric_only=kind in {"numeric", "currency", "percentage"},
        )
        if kind == "number":
            text = _select_number(candidates)
        elif kind in {"numeric", "currency", "percentage"}:
            text = _select_numeric(candidates, kind)
            if kind == "currency":
                text = _normalize_currency_separators(text)
        elif kind == "status":
            text = _select_status(candidates)
        elif kind == "city":
            text = _select_city(candidates)
        else:
            text = _select_general_text(candidates)
    return {
        "has_ink": has_ink,
        "text": text,
        "candidates": candidates,
    }


def _batch_ocr_cell_images(
    cells: list[Image.Image],
    language: str,
    kind: str,
    *,
    threshold: int | None = None,
) -> list[dict]:
    """OCR one homogeneous table column in a single Tesseract process."""
    import pytesseract

    scale = 8
    gap = 32
    prepared_cells: list[Image.Image] = []
    for cell in cells:
        prepared = ImageOps.autocontrast(ImageOps.grayscale(cell))
        if threshold is not None:
            prepared = prepared.point(
                lambda value, limit=threshold: 0 if value < limit else 255
            )
        prepared_cells.append(
            prepared.resize(
                (prepared.width * scale, prepared.height * scale),
                Image.Resampling.LANCZOS,
            )
        )

    strip_width = max(cell.width for cell in prepared_cells) + gap * 2
    strip_height = sum(cell.height + gap * 2 for cell in prepared_cells)
    strip = Image.new("L", (strip_width, strip_height), 255)
    ranges: list[tuple[int, int]] = []
    y = 0
    for cell in prepared_cells:
        top = y
        strip.paste(cell, (gap, y + gap))
        y += cell.height + gap * 2
        ranges.append((top, y))

    config = "--psm 6 --dpi 600 -c preserve_interword_spaces=1"
    if kind == "number":
        config += " -c tessedit_char_whitelist=0123456789"
    elif kind in {"numeric", "currency", "percentage"}:
        config += " -c tessedit_char_whitelist=0123456789.,$%()-/"
    elif kind == "date":
        config += " -c tessedit_char_whitelist=0123456789/"
    data = pytesseract.image_to_data(
        strip,
        lang=language,
        config=config,
        output_type=pytesseract.Output.DICT,
    )

    texts: list[list[str]] = [[] for _ in cells]
    confidences: list[list[float]] = [[] for _ in cells]
    range_index = 0
    for index, raw_text in enumerate(data.get("text", [])):
        text = str(raw_text).strip()
        if not text:
            continue
        center_y = int(data["top"][index]) + int(data["height"][index]) / 2
        while range_index + 1 < len(ranges) and center_y >= ranges[range_index][1]:
            range_index += 1
        if not ranges[range_index][0] <= center_y < ranges[range_index][1]:
            continue
        texts[range_index].append(text)
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            continue
        if confidence >= 0:
            confidences[range_index].append(confidence)

    return [
        {
            "text": _cleanup_text(" ".join(parts)),
            "confidence": sum(scores) / len(scores) if scores else -1.0,
        }
        for parts, scores in zip(texts, confidences)
    ]


def _batch_cell_result(
    normal: dict,
    thresholded: dict,
    kind: str,
    has_ink: bool,
) -> dict:
    """Select a batched reading and flag only ambiguous cells for retry."""
    if not has_ink:
        return {"has_ink": False, "text": None, "candidates": [], "reliable": True}
    candidates = [
        {"source": source, "text": result["text"], "confidence": result["confidence"]}
        for source, result in (("batch", normal), ("batch-threshold", thresholded))
        if result["text"]
    ]
    if kind == "date":
        dates = [normalize_date(candidate["text"]) for candidate in candidates]
        dates = [date for date in dates if date]
        text = dates[0] if dates else None
        reliable = bool(text) and (
            len(set(dates)) == 1
            or max((candidate["confidence"] for candidate in candidates), default=-1) >= 80
        )
    elif kind == "number":
        text = _select_number(candidates)
        reliable = bool(text) and text.isdigit()
    elif kind in {"numeric", "currency", "percentage"}:
        selected_text = _select_numeric(candidates, kind)
        text = (
            _normalize_currency_separators(selected_text)
            if kind == "currency"
            else selected_text
        )
        if kind == "currency":
            valid_format = bool(
                text and re.fullmatch(r"\$\d{1,3}(?:,\d{3})*\.\d{2}", text)
            )
        elif kind == "percentage":
            valid_format = bool(text and re.fullmatch(r"\d+\.\d+%", text))
        else:
            valid_format = bool(text)
        selected_confidence = max(
            (
                candidate["confidence"]
                for candidate in candidates
                if candidate["text"] == selected_text
            ),
            default=-1,
        )
        reliable = valid_format and (
            selected_text == text
            or sum(candidate["text"] == selected_text for candidate in candidates) >= 2
            or selected_confidence >= 70
        ) and (
            len({_normalized_key(candidate["text"]) for candidate in candidates}) == 1
            or selected_confidence >= 0
        )
    elif kind == "status":
        text = _select_status(candidates)
        reliable = bool(text)
    elif kind == "city":
        text = _select_city(candidates)
        reliable = bool(text)
    else:
        text = _select_general_text(candidates)
        reliable = bool(text) and (
            len({_normalized_key(candidate["text"]) for candidate in candidates}) == 1
            or max((candidate["confidence"] for candidate in candidates), default=-1) >= 75
        )
    return {
        "has_ink": True,
        "text": text,
        "candidates": candidates,
        "reliable": reliable,
    }


def _recognize_cell_column(specs: list[dict], language: str) -> list[dict]:
    """Batch a column twice, preserving the individual-cell retry contract."""
    cells = [spec["clean_cell"] for spec in specs]
    kind = specs[0]["kind"]
    normal = _batch_ocr_cell_images(cells, language, kind)
    thresholded = _batch_ocr_cell_images(cells, language, kind, threshold=160)
    return [
        _batch_cell_result(
            normal_result,
            threshold_result,
            kind,
            _ink_box(spec["clean_cell"]) is not None,
        )
        for spec, normal_result, threshold_result in zip(specs, normal, thresholded)
    ]


def _cleanup_sparse_word(text: str) -> str | None:
    raw = text.strip()
    if raw in {"-", "—", "–"}:
        return "-"
    if not any(character.isalnum() for character in raw):
        return None
    cleaned = raw.replace("�", "")
    return cleaned or None


def _recognize_header_rows(
    gray: Image.Image,
    x_lines: list[int],
    y_lines: list[int],
    language: str,
) -> tuple[int, list[str]] | None:
    """Infer a header row from grid structure instead of document vocabulary."""
    import pytesseract

    column_count = len(x_lines) - 1
    if column_count < 2:
        return None
    for row in range(min(3, len(y_lines) - 1)):
        def recognize_header(column: int) -> str:
            cell = gray.crop(
                (
                    x_lines[column] + 1,
                    y_lines[row] + 1,
                    x_lines[column + 1],
                    y_lines[row + 1],
                )
            )
            raw = pytesseract.image_to_string(
                _prepare_cell(cell, scale=8),
                lang=language,
                config="--psm 6 --dpi 600",
            )
            return _cleanup_text(raw)

        with ThreadPoolExecutor(
            max_workers=_cell_ocr_worker_count(column_count)
        ) as executor:
            texts = list(executor.map(recognize_header, range(column_count)))

        populated = [text for text in texts if any(char.isalnum() for char in text)]
        alphabetic = [text for text in populated if any(char.isalpha() for char in text)]
        if (
            len(populated) < max(2, (column_count + 2) // 3)
            or len(alphabetic) < max(1, (column_count + 3) // 4)
        ):
            continue
        # Header rows are overwhelmingly the first sufficiently textual grid row.
        # Returning immediately avoids OCRing two data rows on ordinary tables.
        return row, texts
    return None


def _recognize_sparse_region(
    gray: Image.Image,
    placement: fitz.Rect,
    image_size: tuple[int, int],
    pixel_rect: tuple[int, int, int, int],
    language: str,
) -> list[dict]:
    import pytesseract

    region = ImageOps.autocontrast(gray.crop(pixel_rect))
    if region.width <= 0 or region.height <= 0:
        return []
    scale = _bounded_sparse_ocr_scale(region.width, region.height)
    prepared = region.resize(
        (region.width * scale, region.height * scale),
        Image.Resampling.LANCZOS,
    )
    data = pytesseract.image_to_data(
        prepared,
        lang=language,
        config="--psm 11 --dpi 600",
        output_type=pytesseract.Output.DICT,
    )
    region_x, region_y = pixel_rect[0], pixel_rect[1]
    items: list[dict] = []
    for index, raw_text in enumerate(data["text"]):
        text = _cleanup_sparse_word(str(raw_text))
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            continue
        if confidence < 0:
            continue
        left = region_x + int(data["left"][index]) / scale
        top = region_y + int(data["top"][index]) / scale
        right = left + int(data["width"][index]) / scale
        bottom = top + int(data["height"][index]) / scale
        items.append(
            {
                "kind": "page-text",
                "text": text,
                "rect": _map_image_rect(
                    placement,
                    image_size,
                    (left, top, right, bottom),
                ),
            }
        )
    return items


def _geometry_characters_for_text(
    text: str,
    rect: fitz.Rect,
    page_rect: fitz.Rect,
    line_index: int,
) -> list[dict]:
    """Lay recognized text into its ink box without writing it into the PDF.

    The old recovery path inserted horizontally-scaled Helvetica and then asked
    PyMuPDF to extract the resulting glyph boxes. Producing those boxes directly
    keeps geometry authoritative and removes the possibility of a second PDF
    text layer while retaining the same proportional character placement.
    """
    if not text or rect.width <= 0 or rect.height <= 0:
        return []
    page_width = page_rect.width
    page_height = page_rect.height
    if page_width <= 0 or page_height <= 0:
        return []

    # A trailing blank is structural: when independently recovered cells share
    # a baseline, the layout builder merges their source lines. The blank keeps
    # the last glyph of one cell and the first glyph of the next from becoming
    # one cross-boundary token even when their estimated boxes touch.
    layout_text = text if text[-1].isspace() else text + " "
    weights = [
        max(0.1, fitz.get_text_length(char, fontname="helv", fontsize=1))
        for char in layout_text
    ]
    total = sum(weights)
    if total <= 0:
        return []

    characters: list[dict] = []
    cursor = rect.x0
    for char, weight in zip(layout_text, weights):
        width = rect.width * weight / total
        characters.append(
            {
                "char": char,
                "x": (cursor - page_rect.x0) / page_width,
                "y": (rect.y0 - page_rect.y0) / page_height,
                "width": width / page_width,
                "height": rect.height / page_height,
                "lineIndex": line_index,
            }
        )
        cursor += width
    return characters


def _character_center_in_rect(character: dict, rect: fitz.Rect, page_rect: fitz.Rect) -> bool:
    x = page_rect.x0 + (
        float(character.get("x", 0)) + float(character.get("width", 0)) / 2
    ) * page_rect.width
    y = page_rect.y0 + (
        float(character.get("y", 0)) + float(character.get("height", 0)) / 2
    ) * page_rect.height
    return rect.x0 <= x <= rect.x1 and rect.y0 <= y <= rect.y1


def _reindex_geometry_lines(characters: list[dict]) -> None:
    """Keep recovered and retained lines in stable displayed-page reading order."""
    groups: dict[int, list[dict]] = {}
    for character in characters:
        groups.setdefault(int(character.get("lineIndex", 0)), []).append(character)
    ordered = sorted(
        groups.values(),
        key=lambda group: (
            min(float(char.get("y", 0)) for char in group),
            min(float(char.get("x", 0)) for char in group),
        ),
    )
    for line_index, group in enumerate(ordered):
        for character in group:
            character["lineIndex"] = line_index


def _is_table_recovery_useful(items: list[dict]) -> bool:
    """Require broad cell success while excluding verified blanks from failures."""
    ink_items = [item for item in items if item["has_ink"]]
    recognized_ink = [item for item in ink_items if item["text"]]
    return (
        len(recognized_ink) >= 3
        and len(recognized_ink) / max(1, len(ink_items)) >= 0.50
    )


def has_recoverable_ruled_table(
    pdf_bytes: bytes,
    language: str = "eng",
) -> tuple[bool, dict]:
    """Cheaply detect a ruled grid that benefits from cell-aware recovery."""
    stats = {
        "table_images_examined": 0,
        "table_images_skipped_small": 0,
        "table_images_skipped_non_document": 0,
        "table_grid_candidates": 0,
    }
    del language  # retained for API compatibility; detection is image-only
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page in doc:
            seen_xrefs: set[int] = set()
            for image_info in page.get_images(full=True):
                xref = int(image_info[0])
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                stats["table_images_examined"] += 1
                placement = _largest_table_placement(
                    page.rect,
                    page.get_image_rects(xref),
                )
                if placement is None:
                    stats["table_images_skipped_small"] += 1
                    continue
                try:
                    image = Image.open(
                        io.BytesIO(doc.extract_image(xref)["image"])
                    ).convert("RGB")
                except (KeyError, OSError):
                    continue
                if not _looks_like_document_scan(image):
                    stats["table_images_skipped_non_document"] += 1
                    continue
                x_lines, y_lines = detect_ruled_grid(image)
                if not x_lines or not y_lines:
                    continue
                stats["table_grid_candidates"] += 1
                return True, stats
        return False, stats
    finally:
        doc.close()


def recover_table_geometry(
    source_pdf_bytes: bytes,
    geometry: dict,
    language: str = "eng",
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[dict, dict]:
    """Recognize ruled-table cells and replace only their geometry regions."""
    stats = {
        "date_tables_detected": 0,
        "date_cells_detected": 0,
        "date_cells_resolved": 0,
        "date_cells_unresolved": 0,
        "table_cells_detected": 0,
        "table_cells_resolved": 0,
        "table_cells_unresolved": 0,
        "table_images_examined": 0,
        "table_images_skipped_small": 0,
        "table_images_skipped_non_document": 0,
        "table_grid_candidates": 0,
        "page_text_regions_detected": 0,
        "page_text_regions_failed": 0,
        "page_text_words_resolved": 0,
        "changed": False,
    }
    configure_tesseract()
    previous_thread_limit = os.environ.get("OMP_THREAD_LIMIT")
    os.environ["OMP_THREAD_LIMIT"] = "1"
    source_doc = fitz.open(stream=source_pdf_bytes, filetype="pdf")
    recovered_geometry = copy.deepcopy(geometry)
    geometry_pages = {
        int(page.get("pageIndex", -1)): page
        for page in recovered_geometry.get("pages", [])
    }
    page_tables: dict[int, list[dict]] = {}
    try:
        for page_index in range(source_doc.page_count):
            if progress_callback:
                progress_callback(
                    f"Scanning tables page {page_index + 1} "
                    f"of {source_doc.page_count}…",
                    Stage.TABLE_RECOVERY,
                    current=page_index + 1,
                    total=source_doc.page_count,
                    unit="page",
                )
            source_page = source_doc.load_page(page_index)
            seen_xrefs: set[int] = set()
            for image_info in source_page.get_images(full=True):
                xref = int(image_info[0])
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                stats["table_images_examined"] += 1
                placements = source_page.get_image_rects(xref)
                placement = _largest_table_placement(source_page.rect, placements)
                if placement is None:
                    stats["table_images_skipped_small"] += 1
                    continue
                try:
                    image = Image.open(
                        io.BytesIO(source_doc.extract_image(xref)["image"])
                    ).convert("RGB")
                except (KeyError, OSError):
                    continue
                if not _looks_like_document_scan(image):
                    stats["table_images_skipped_non_document"] += 1
                    continue
                x_lines, y_lines = detect_ruled_grid(image)
                if not x_lines or not y_lines:
                    continue
                stats["table_grid_candidates"] += 1
                gray = ImageOps.grayscale(image)
                header_detection = _recognize_header_rows(
                    gray,
                    x_lines,
                    y_lines,
                    language,
                )
                if not header_detection:
                    continue
                header_row, header_texts = header_detection
                clean = _clean_grid(gray, x_lines, y_lines)
                first_data_row = header_row + 1
                column_count = len(x_lines) - 1
                if first_data_row >= len(y_lines) - 1:
                    continue

                if progress_callback:
                    progress_callback(
                        f"Recovering {column_count}-column ruled table "
                        f"on page {page_index + 1}…",
                        Stage.TABLE_RECOVERY,
                    )

                headers: list[dict] = []
                for column in range(column_count):
                    header_pixel_rect = (
                        x_lines[column],
                        y_lines[header_row],
                        x_lines[column + 1],
                        y_lines[header_row + 1],
                    )
                    raw_header = gray.crop(
                        (
                            x_lines[column] + 1,
                            y_lines[header_row] + 1,
                            x_lines[column + 1],
                            y_lines[header_row + 1],
                        )
                    )
                    text = header_texts[column]
                    clean_header = clean.crop(header_pixel_rect)
                    header_boxes = _ink_line_boxes(clean_header)
                    line_texts = _header_line_texts(text, len(header_boxes))
                    rect = _map_image_rect(
                        placement,
                        image.size,
                        (
                            x_lines[column] + 1,
                            y_lines[header_row] + 1,
                            x_lines[column + 1],
                            y_lines[header_row + 1],
                        ),
                    )
                    headers.append(
                        {
                            "column": column,
                            "text": text or None,
                            "kind": _column_kind(text),
                            "has_ink": _ink_box(clean_header) is not None,
                            "rect": rect,
                            "segments": _map_cell_segments(
                                placement,
                                image.size,
                                (x_lines[column], y_lines[header_row]),
                                clean_header,
                                line_texts,
                            ),
                        }
                    )

                cells: list[dict] = []
                specs: list[dict] = []
                column_specs: dict[int, list[dict]] = {
                    header["column"]: [] for header in headers
                }
                for row in range(first_data_row, len(y_lines) - 1):
                    for header in headers:
                        column = header["column"]
                        pixel_rect = (
                            x_lines[column] + 1,
                            y_lines[row] + 1,
                            x_lines[column + 1],
                            y_lines[row + 1],
                        )
                        spec = {
                            "row": row,
                            "column": column,
                            "kind": header["kind"],
                            "pixel_rect": pixel_rect,
                            "raw_cell": gray.crop(pixel_rect),
                            "clean_cell": clean.crop(
                                (
                                    x_lines[column],
                                    y_lines[row],
                                    x_lines[column + 1],
                                    y_lines[row + 1],
                                )
                            ),
                        }
                        specs.append(spec)
                        column_specs[column].append(spec)

                with ThreadPoolExecutor(
                    max_workers=_cell_ocr_worker_count(column_count)
                ) as executor:
                    column_futures = {
                        column: executor.submit(
                            _recognize_cell_column,
                            items,
                            language,
                        )
                        for column, items in column_specs.items()
                    }
                    for column, future in column_futures.items():
                        for spec, recognized in zip(column_specs[column], future.result()):
                            spec["recognized"] = recognized

                    retry_futures = {
                        id(spec): executor.submit(
                            _recognize_cell_images,
                            spec["raw_cell"],
                            spec["clean_cell"],
                            spec["kind"],
                            language,
                        )
                        for spec in specs
                        if not spec["recognized"]["reliable"]
                    }
                    for spec in specs:
                        retry = retry_futures.get(id(spec))
                        if retry is not None:
                            spec["recognized"] = retry.result()

                for spec in specs:
                    recognized = spec["recognized"]
                    text = recognized["text"]
                    kind = spec["kind"]
                    has_ink = recognized["has_ink"]
                    if kind == "date" and has_ink:
                        stats["date_cells_detected"] += 1
                        if text:
                            stats["date_cells_resolved"] += 1
                        else:
                            stats["date_cells_unresolved"] += 1
                    clean_cell = spec["clean_cell"]
                    column = spec["column"]
                    cells.append(
                        {
                            "row": spec["row"],
                            "column": column,
                            "kind": kind,
                            "has_ink": has_ink,
                            "text": text,
                            "candidates": recognized["candidates"],
                            "rect": _map_image_rect(
                                placement,
                                image.size,
                                spec["pixel_rect"],
                            ),
                            "segments": _map_cell_segments(
                                placement,
                                image.size,
                                (x_lines[column], y_lines[spec["row"]]),
                                clean_cell,
                                _header_line_texts(
                                    text,
                                    len(_ink_line_boxes(clean_cell)),
                                )
                                if text
                                else [],
                            ),
                        }
                    )

                for header in headers:
                    if header["kind"] == "text":
                        _canonicalize_repeated_cells(
                            [cell for cell in cells if cell["column"] == header["column"]]
                        )
                for cell in cells:
                    if cell["segments"] and cell["text"]:
                        line_texts = _header_line_texts(
                            cell["text"],
                            len(cell["segments"]),
                        )
                        for segment, line_text in zip(cell["segments"], line_texts):
                            segment["text"] = line_text

                table_items = headers + cells
                region_items: list[dict] = []
                region_rect: fitz.Rect | None = None
                table_top = y_lines[header_row]
                if 20 <= table_top <= image.height * 0.35:
                    region_pixel_rect = (0, 0, image.width, table_top)
                    try:
                        region_items = _recognize_sparse_region(
                            gray,
                            placement,
                            image.size,
                            region_pixel_rect,
                            language,
                        )
                    except Exception:
                        # Page-text recovery is optional context around a table.
                        # A failure here must not discard the table cells already
                        # recognized, much less abort every later page.
                        region_items = []
                        stats["page_text_regions_failed"] += 1
                    if region_items:
                        region_rect = _map_image_rect(
                            placement,
                            image.size,
                            region_pixel_rect,
                        )
                        stats["page_text_regions_detected"] += 1
                        stats["page_text_words_resolved"] += len(region_items)
                resolved = sum(
                    not item["has_ink"] or bool(item["text"])
                    for item in table_items
                )
                stats["date_tables_detected"] += 1
                stats["table_cells_detected"] += len(table_items)
                stats["table_cells_resolved"] += resolved
                stats["table_cells_unresolved"] += len(table_items) - resolved
                page_tables.setdefault(page_index, []).append(
                    {
                        "placement": placement,
                        "image_size": image.size,
                        "grid_rect": _map_image_rect(
                            placement,
                            image.size,
                            (
                                x_lines[0],
                                y_lines[header_row],
                                x_lines[-1],
                                y_lines[-1],
                            ),
                        ),
                        "x_lines": x_lines,
                        "y_lines": y_lines,
                        "header_row": header_row,
                        "items": table_items,
                        "region_items": region_items,
                        "region_rect": region_rect,
                    }
                )

        if not page_tables:
            return recovered_geometry, stats

        changed_pages: set[int] = set()
        next_line_index = -1
        for page_index, tables in page_tables.items():
            geometry_page = geometry_pages.get(page_index)
            if geometry_page is None:
                continue
            page_rect = source_doc.load_page(page_index).rect
            characters = list(geometry_page.get("characters", []))
            for table in tables:
                items = table["items"]
                if not _is_table_recovery_useful(items):
                    continue

                # The page-level OCR pass can produce words whose boxes span
                # several cells. Removing per-cell rectangles by character
                # centre cannot reliably erase such a word, so replace the
                # complete recognized grid as one atomic geometry region.
                replacement_rects: list[fitz.Rect] = [table["grid_rect"]]
                recovered_segments: list[dict] = list(table["region_items"])
                if table["region_rect"] is not None:
                    replacement_rects.append(table["region_rect"])
                for item in items:
                    # Once the cell-aware pass is trustworthy, it owns the
                    # complete grid. Keeping full-page OCR in unresolved cells
                    # leaves fragments that cross cell boundaries and can also
                    # duplicate a recovered neighbour. An unresolved cell is an
                    # honest blank in sidecar geometry; it must not resurrect
                    # the less reliable page-level pass.
                    replacement_rects.append(item["rect"])
                    if item["text"]:
                        recovered_segments.extend(
                            item["segments"]
                            or [{"rect": item["rect"], "text": item["text"]}]
                        )

                characters = [
                    character
                    for character in characters
                    if not any(
                        _character_center_in_rect(character, rect, page_rect)
                        for rect in replacement_rects
                    )
                ]
                for segment in recovered_segments:
                    characters.extend(
                        _geometry_characters_for_text(
                            segment["text"],
                            segment["rect"],
                            page_rect,
                            next_line_index,
                        )
                    )
                    next_line_index -= 1
                changed_pages.add(page_index)
            geometry_page["characters"] = characters

        for page_index in changed_pages:
            _reindex_geometry_lines(geometry_pages[page_index]["characters"])
        if not changed_pages:
            return recovered_geometry, stats
        stats["changed"] = True
        return recovered_geometry, stats
    finally:
        source_doc.close()
        if previous_thread_limit is None:
            os.environ.pop("OMP_THREAD_LIMIT", None)
        else:
            os.environ["OMP_THREAD_LIMIT"] = previous_thread_limit
