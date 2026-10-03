from __future__ import annotations

import unittest

import pymupdf as fitz

from engines.geometry_engine import extract_text_geometry
from engines.ocr_engine import apply_page_rotation_corrections
from engines.pdf_export_engine import add_searchable_text_layer


def _geometry(text: str) -> dict:
    characters = []
    x = 0.12
    for character in text:
        width = 0.012 if character != " " else 0.008
        characters.append(
            {
                "char": character,
                "x": x,
                "y": 0.18,
                "width": width,
                "height": 0.03,
                "lineIndex": 0,
            }
        )
        x += width
    return {
        "version": 1,
        "coordinateSpace": "normalized",
        "pages": [{"pageIndex": 0, "characters": characters}],
    }


def _blank_pdf(rotation: int = 0) -> bytes:
    document = fitz.open()
    page = document.new_page(width=300, height=500)
    page.draw_rect(fitz.Rect(20, 20, 280, 480), color=(0, 0, 0))
    page.set_rotation(rotation)
    data = document.tobytes()
    document.close()
    return data


def _bounds(characters: list[dict]) -> tuple[float, float, float, float]:
    return (
        min(character["x"] for character in characters),
        min(character["y"] for character in characters),
        max(character["x"] + character["width"] for character in characters),
        max(character["y"] + character["height"] for character in characters),
    )


class PdfExportTests(unittest.TestCase):
    def test_adds_extractable_invisible_text_to_image_only_page(self) -> None:
        source = _blank_pdf()
        exported = add_searchable_text_layer(source, _geometry("Invoice 1042"))

        document = fitz.open(stream=exported, filetype="pdf")
        source_document = fitz.open(stream=source, filetype="pdf")
        try:
            self.assertIn("Invoice 1042", document[0].get_text())
            self.assertEqual(
                source_document[0].get_pixmap().samples,
                document[0].get_pixmap().samples,
            )
            spans = [
                span
                for block in document[0].get_text("rawdict").get("blocks", [])
                if block.get("type") == 0
                for line in block.get("lines", [])
                for span in line.get("spans", [])
            ]
            self.assertTrue(spans)
            self.assertTrue(
                all(
                    int(span.get("alpha", 255)) == 0
                    or not (int(span.get("char_flags", 0)) & ((1 << 3) | (1 << 4)))
                    for span in spans
                )
            )
        finally:
            source_document.close()
            document.close()

    def test_does_not_duplicate_existing_visible_text(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=500)
        page.insert_text((36, 90), "Native text", fontsize=12)
        source = document.tobytes()
        document.close()

        exported = add_searchable_text_layer(source, extract_text_geometry(source))
        result = fitz.open(stream=exported, filetype="pdf")
        try:
            self.assertEqual(result[0].get_text().count("Native text"), 1)
        finally:
            result.close()

    def test_adds_missing_sidecar_line_to_page_with_native_text(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=500)
        page.insert_text((36, 90), "Native heading", fontsize=12)
        source = document.tobytes()
        document.close()

        exported = add_searchable_text_layer(source, _geometry("Scanned body"))
        result = fitz.open(stream=exported, filetype="pdf")
        try:
            text = result[0].get_text()
            self.assertIn("Native heading", text)
            self.assertIn("Scanned body", text)
        finally:
            result.close()

    def test_replaces_hidden_source_text_with_sidecar_text(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=500)
        page.insert_text((36, 90), "Stale hidden text", fontsize=12, render_mode=3)
        source = document.tobytes()
        document.close()

        exported = add_searchable_text_layer(source, _geometry("Authoritative text"))
        result = fitz.open(stream=exported, filetype="pdf")
        try:
            text = result[0].get_text()
            self.assertNotIn("Stale hidden text", text)
            self.assertIn("Authoritative text", text)
        finally:
            result.close()

    def test_preserves_unicode_ocr_text(self) -> None:
        text = "€ – café 漢字"
        exported = add_searchable_text_layer(_blank_pdf(), _geometry(text))
        result = fitz.open(stream=exported, filetype="pdf")
        try:
            self.assertIn(text, result[0].get_text())
        finally:
            result.close()

    def test_intrinsic_page_rotation_keeps_text_extractable(self) -> None:
        for rotation in (90, 180, 270):
            with self.subTest(rotation=rotation):
                exported = add_searchable_text_layer(
                    _blank_pdf(rotation), _geometry("Rotated page")
                )
                document = fitz.open(stream=exported, filetype="pdf")
                try:
                    self.assertIn("Rotated page", document[0].get_text())
                finally:
                    document.close()

                actual = extract_text_geometry(exported)["pages"][0]["characters"]
                expected = _geometry("Rotated page")["pages"][0]["characters"]
                for actual_edge, expected_edge in zip(
                    _bounds(actual), _bounds(expected)
                ):
                    self.assertAlmostEqual(actual_edge, expected_edge, delta=0.002)
                visible = [character for character in actual if character["char"].strip()]
                self.assertLess(visible[0]["x"], visible[-1]["x"])

    def test_preserves_ocr_corrected_intrinsic_rotation(self) -> None:
        geometry = _geometry("Auto-oriented page")
        corrected_source = apply_page_rotation_corrections(
            _blank_pdf(),
            geometry,
            {0: 90},
        )

        exported = add_searchable_text_layer(corrected_source, geometry)
        document = fitz.open(stream=exported, filetype="pdf")
        try:
            self.assertEqual(document[0].rotation, 90)
            self.assertIn("Auto-oriented page", document[0].get_text())
        finally:
            document.close()

if __name__ == "__main__":
    unittest.main()
