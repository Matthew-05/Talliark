"""Materialize Talliark sidecar geometry as an invisible PDF text layer."""
from __future__ import annotations

from collections import defaultdict

import pymupdf as fitz


_PAINTED_CHARACTER_FLAGS = (1 << 3) | (1 << 4)
_LATIN_FONT_NAME = "TalliarkOcrLatin"
_LATIN_FONT = fitz.Font("helv")
_FALLBACK_FONT_NAME = "TalliarkOcrUnicode"
_FALLBACK_FONT: fitz.Font | None = None


def _visible_line_signatures(page: fitz.Page) -> list[tuple[str, fitz.Rect]]:
    """Return normalized text and displayed bounds for painted source lines."""
    raw = page.get_text("rawdict", sort=False)
    signatures: list[tuple[str, fitz.Rect]] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            visible_characters: list[dict] = []
            for span in line.get("spans", []):
                flags = int(span.get("char_flags", _PAINTED_CHARACTER_FLAGS))
                if int(span.get("alpha", 255)) == 0 or not (
                    flags & _PAINTED_CHARACTER_FLAGS
                ):
                    continue
                visible_characters.extend(span.get("chars", []))
            text = "".join(
                str(character.get("c", "")) for character in visible_characters
            )
            normalized = " ".join(text.casefold().split())
            boxes = [
                fitz.Rect(character["bbox"])
                for character in visible_characters
                if character.get("bbox") and str(character.get("c", "")).strip()
            ]
            if not normalized or not boxes:
                continue
            bounds = boxes[0]
            for box in boxes[1:]:
                bounds |= box
            if page.rotation:
                bounds = bounds * page.rotation_matrix
            signatures.append((normalized, bounds))
    return signatures


def _rectangles_coincide(first: fitz.Rect, second: fitz.Rect) -> bool:
    intersection = first & second
    if intersection.is_empty:
        return False
    minimum_area = min(first.get_area(), second.get_area())
    return minimum_area > 0 and intersection.get_area() / minimum_area >= 0.65


def _already_has_line(
    text: str,
    bounds: fitz.Rect,
    source_lines: list[tuple[str, fitz.Rect]],
) -> bool:
    normalized = " ".join(text.casefold().split())
    return any(
        normalized == source_text and _rectangles_coincide(bounds, source_bounds)
        for source_text, source_bounds in source_lines
    )


def _lines(characters: list[dict]) -> list[list[dict]]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for character in characters:
        try:
            grouped[int(character["lineIndex"])].append(character)
        except (KeyError, TypeError, ValueError):
            continue
    return [grouped[index] for index in sorted(grouped)]


def _line_text_and_rect(
    characters: list[dict], page_rect: fitz.Rect
) -> tuple[str, fitz.Rect] | None:
    first = 0
    last = len(characters)
    while first < last and not str(characters[first].get("char", "")).strip():
        first += 1
    while last > first and not str(characters[last - 1].get("char", "")).strip():
        last -= 1
    characters = characters[first:last]
    if not characters:
        return None

    text = "".join(str(character.get("char", "")) for character in characters)
    text = text.replace("\r", " ").replace("\n", " ")
    if not text.strip():
        return None

    try:
        x0 = min(float(character["x"]) for character in characters)
        y0 = min(float(character["y"]) for character in characters)
        x1 = max(
            float(character["x"]) + float(character["width"])
            for character in characters
        )
        y1 = max(
            float(character["y"]) + float(character["height"])
            for character in characters
        )
    except (KeyError, TypeError, ValueError):
        return None

    x0 = min(max(x0, 0.0), 1.0)
    y0 = min(max(y0, 0.0), 1.0)
    x1 = min(max(x1, 0.0), 1.0)
    y1 = min(max(y1, 0.0), 1.0)
    if x1 <= x0 or y1 <= y0:
        return None

    return text, fitz.Rect(
        page_rect.x0 + x0 * page_rect.width,
        page_rect.y0 + y0 * page_rect.height,
        page_rect.x0 + x1 * page_rect.width,
        page_rect.y0 + y1 * page_rect.height,
    )


def _insert_line(page: fitz.Page, text: str, displayed_rect: fitz.Rect) -> bool:
    """Insert one invisible line fitted to its displayed OCR bounds."""
    global _FALLBACK_FONT
    if all(
        character.isspace() or _LATIN_FONT.has_glyph(ord(character))
        for character in text
    ):
        font = _LATIN_FONT
        font_name = _LATIN_FONT_NAME
    else:
        if _FALLBACK_FONT is None:
            _FALLBACK_FONT = fitz.Font("china-s")
        font = _FALLBACK_FONT
        font_name = _FALLBACK_FONT_NAME

    # A custom Type0 resource preserves Unicode code points. Referring to the
    # Base-14 name directly would encode through a one-byte code page and turn
    # common OCR characters such as €, en dashes and accented letters into
    # question marks even though the underlying font has those glyphs.
    page.insert_font(fontname=font_name, fontbuffer=font.buffer, set_simple=False)
    font_height = max(font.ascender - font.descender, 0.01)
    fontsize = displayed_rect.height / font_height
    if fontsize <= 0.1:
        return False

    rotation = int(page.rotation) % 360
    # Page insertion coordinates are unrotated, while sidecar geometry is in the
    # displayed page. Start every line at its displayed left edge, then inverse-
    # map the baseline. Using the intrinsic rotation for the inserted text makes
    # its displayed advance left-to-right; the prior counter-rotation reversed
    # character order on quarter-turned pages.
    baseline = fitz.Point(
        displayed_rect.x0,
        displayed_rect.y0 + font.ascender * fontsize,
    )
    if rotation:
        baseline = baseline * page.derotation_matrix

    natural_width = max(font.text_length(text, fontsize=fontsize), 0.01)
    horizontal_scale = displayed_rect.width / natural_width
    scale = (
        fitz.Matrix(1, horizontal_scale)
        if rotation in (90, 270)
        else fitz.Matrix(horizontal_scale, 1)
    )
    page.insert_text(
        baseline,
        text,
        fontname=font_name,
        fontsize=fontsize,
        render_mode=3,
        rotate=rotation,
        morph=(baseline, scale),
        overlay=True,
    )
    return True


def add_searchable_text_layer(pdf_bytes: bytes, geometry: dict) -> bytes:
    """Return PDF bytes with sidecar lines absent from visible source text.

    Geometry uses displayed-page coordinates. PyMuPDF insertion uses unrotated
    page coordinates, so intrinsically rotated pages are inverse-mapped and the
    inserted line receives the counter-rotation needed to remain horizontal in
    the displayed page.
    """
    if not pdf_bytes:
        raise ValueError("PDF is empty.")
    if (
        geometry.get("version") != 1
        or geometry.get("coordinateSpace") != "normalized"
    ):
        raise ValueError("Unsupported text geometry.")

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if document.needs_pass:
            raise ValueError("Password-protected PDFs cannot be exported.")

        # The OCR pipeline treats hidden source text as untrustworthy. Remove it
        # from this output copy before adding the authoritative sidecar layer.
        document.scrub(
            attached_files=False,
            clean_pages=True,
            embedded_files=False,
            hidden_text=True,
            javascript=False,
            metadata=False,
            redactions=False,
            remove_links=False,
            reset_fields=False,
            reset_responses=False,
            thumbnails=False,
            xml_metadata=False,
        )

        pages = {
            int(page.get("pageIndex")): page
            for page in geometry.get("pages", [])
            if isinstance(page, dict) and isinstance(page.get("pageIndex"), int)
        }
        for page_index in range(document.page_count):
            geometry_page = pages.get(page_index)
            if geometry_page is None:
                continue
            page = document.load_page(page_index)
            displayed_rect = page.rect
            source_lines = _visible_line_signatures(page)
            for characters in _lines(geometry_page.get("characters", [])):
                prepared = _line_text_and_rect(characters, displayed_rect)
                if prepared is None:
                    continue
                if _already_has_line(prepared[0], prepared[1], source_lines):
                    continue
                _insert_line(page, prepared[0], prepared[1])

        return document.tobytes(
            garbage=4,
            clean=True,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
            use_objstms=1,
        )
    finally:
        document.close()
