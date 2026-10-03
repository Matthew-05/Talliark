import io
import unittest
from unittest.mock import patch

import pymupdf as fitz
from PIL import Image, ImageDraw
from schemas.models import OcrJob

from engines.ocr_engine import (
    apply_page_rotation_corrections,
    detect_direct_page_rotations,
    _dominant_image_clip,
    _geometry_page_from_hocr,
    _prepare_faint_ink_image,
    _remap_cropped_page_geometry,
    _remap_rotated_page_geometry,
    merge_geometry_pages,
    merge_missing_text_lines,
    needs_adaptive_retry,
    needs_garbled_text_retry,
    select_pages_requiring_ocr,
    should_merge_faint_ink_retry,
    should_select_rotated_retry,
    summarize_geometry_quality,
)


def _geometry(*lines: str) -> dict:
    characters = []
    for line_index, text in enumerate(lines):
        for char in text:
            characters.append({"char": char, "lineIndex": line_index})
    return {
        "version": 1,
        "coordinateSpace": "normalized",
        "pages": [{"pageIndex": 0, "characters": characters}],
    }


def _geometry_pages(*page_lines: tuple[str, ...]) -> dict:
    pages = []
    for page_index, lines in enumerate(page_lines):
        characters = []
        for line_index, text in enumerate(lines):
            for char in text:
                characters.append({"char": char, "lineIndex": line_index})
        pages.append({"pageIndex": page_index, "characters": characters})
    return {
        "version": 1,
        "coordinateSpace": "normalized",
        "pages": pages,
    }


def _pdf_with_image(
    image_size: tuple[int, int],
    display_rect: fitz.Rect,
) -> bytes:
    image = Image.new("RGB", image_size, "white")
    image_stream = io.BytesIO()
    image.save(image_stream, format="PNG")
    doc = fitz.open()
    try:
        page = doc.new_page(width=600, height=800)
        page.insert_image(display_rect, stream=image_stream.getvalue())
        return doc.tobytes()
    finally:
        doc.close()


class GeometryQualityTests(unittest.TestCase):
    def test_catastrophically_sparse_text_requests_retry(self) -> None:
        summary = summarize_geometry_quality(
            _geometry("Fac Sani", "DCS Facity Sevices", "Fac Sarvs")
        )

        self.assertEqual(summary["page_count"], 1)
        self.assertEqual(summary["word_count"], 7)
        self.assertTrue(needs_adaptive_retry(summary))

    def test_dense_multi_line_text_keeps_default_profile(self) -> None:
        summary = summarize_geometry_quality(
            _geometry(
                *[
                    "06/30/2016 DCS Facility Services Closure Permanent"
                    for _ in range(12)
                ]
            )
        )

        self.assertGreater(summary["word_count"], 50)
        self.assertFalse(needs_adaptive_retry(summary))

    def test_dense_punctuation_garbage_requests_forced_ocr(self) -> None:
        summary = summarize_geometry_quality(_geometry("* $ # ? H " * 30))

        self.assertEqual(summary["non_whitespace_characters"], 150)
        self.assertEqual(summary["alphanumeric_ratio"], 0.2)
        self.assertTrue(needs_garbled_text_retry(summary))

    def test_normal_transaction_text_is_not_garbled(self) -> None:
        summary = summarize_geometry_quality(
            _geometry(
                "Nov 03 DESKTOP REMOTE DEPOSIT REF 81531242 194,705.05 " * 5
            )
        )

        self.assertGreater(summary["alphanumeric_ratio"], 0.8)
        self.assertFalse(needs_garbled_text_retry(summary))

    def test_healthy_pages_cannot_hide_one_garbled_page(self) -> None:
        summary = summarize_geometry_quality(
            _geometry_pages(
                tuple(
                    "Nov 03 DESKTOP REMOTE DEPOSIT REF 81531242 194,705.05"
                    for _ in range(30)
                ),
                tuple("* $ # ? H " * 10 for _ in range(8)),
            )
        )

        self.assertGreater(summary["alphanumeric_ratio"], 0.70)
        self.assertEqual(summary["garbled_page_numbers"], [2])
        self.assertTrue(needs_garbled_text_retry(summary))

    def test_invalid_unicode_page_requests_forced_ocr(self) -> None:
        summary = summarize_geometry_quality(
            _geometry("Account activity " + "\ufffd\ue000" * 50)
        )

        self.assertEqual(summary["garbled_page_numbers"], [1])
        self.assertTrue(needs_garbled_text_retry(summary))

    def test_short_symbol_sample_does_not_force_ocr(self) -> None:
        summary = summarize_geometry_quality(_geometry("* $ # ?"))

        self.assertFalse(needs_garbled_text_retry(summary))


class DirectGeometryTests(unittest.TestCase):
    def test_hocr_character_boxes_preserve_text_spaces_and_coordinates(self) -> None:
        hocr = b"""<?xml version='1.0' encoding='UTF-8'?>
        <html xmlns='http://www.w3.org/1999/xhtml'><body>
          <span class='ocr_line' title='bbox 10 20 100 40'>
            <span class='ocrx_word' title='bbox 10 20 40 40; x_wconf 91'>
              <span class='ocrx_cinfo' title='x_bboxes 10 20 20 40'>H</span>
              <span class='ocrx_cinfo' title='x_bboxes 20 20 30 40'>i</span>
            </span>
            <span class='ocrx_word' title='bbox 50 20 80 40; x_wconf 95'>
              <span class='ocrx_cinfo' title='x_bboxes 50 20 60 40'>4</span>
              <span class='ocrx_cinfo' title='x_bboxes 60 20 70 40'>2</span>
            </span>
          </span>
          <span class='ocr_header' title='bbox 10 60 40 80'>
            <span class='ocrx_word' title='bbox 10 60 30 80; x_wconf 88'>
              <span class='ocrx_cinfo' title='x_bboxes 10 80 20 80'>O</span>
              <span class='ocrx_cinfo' title='x_bboxes 20 80 30 80'>K</span>
            </span>
          </span>
        </body></html>"""

        page, stats = _geometry_page_from_hocr(hocr, 2, (100, 200))

        self.assertEqual("".join(c["char"] for c in page["characters"]), "Hi 42OK")
        self.assertEqual(page["pageIndex"], 2)
        self.assertAlmostEqual(page["characters"][0]["x"], 0.1)
        self.assertAlmostEqual(page["characters"][0]["y"], 0.1)
        self.assertEqual(stats["word_count"], 3)
        self.assertEqual(stats["character_count"], 6)
        self.assertEqual(stats["mean_confidence"], 91.33)

    def test_merge_replaces_only_selected_pages(self) -> None:
        source = _geometry_pages(("native one",), ("native two",))
        replacement = _geometry("ocr two")["pages"][0]
        replacement["pageIndex"] = 1

        merged = merge_geometry_pages(source, {2: replacement})

        self.assertEqual(
            "".join(c["char"] for c in merged["pages"][0]["characters"]),
            "native one",
        )
        self.assertEqual(
            "".join(c["char"] for c in merged["pages"][1]["characters"]),
            "ocr two",
        )

    def test_faint_ink_retry_requires_material_coverage_without_confidence_collapse(
        self,
    ) -> None:
        primary = {
            "character_count": 500,
            "word_count": 100,
            "mean_confidence": 71.0,
        }

        self.assertTrue(
            should_merge_faint_ink_retry(
                primary,
                {
                    "character_count": 560,
                    "word_count": 112,
                    "mean_confidence": 62.0,
                },
            )
        )
        self.assertFalse(
            should_merge_faint_ink_retry(
                primary,
                {
                    "character_count": 560,
                    "word_count": 112,
                    "mean_confidence": 40.0,
                },
            )
        )

    def test_faint_ink_cleanup_strengthens_colored_strokes(self) -> None:
        image = Image.new("RGB", (300, 120), (238, 224, 195))
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 45, 220, 54), fill=(85, 105, 190))

        cleaned = _prepare_faint_ink_image(image)

        self.assertLess(cleaned.getpixel((150, 49)), cleaned.getpixel((150, 20)))

    def test_missing_line_merge_preserves_primary_and_adds_only_new_regions(
        self,
    ) -> None:
        def line(text: str, y: float, line_index: int) -> list[dict]:
            return [
                {
                    "char": character,
                    "x": 0.10 + index * 0.02,
                    "y": y,
                    "width": 0.018,
                    "height": 0.03,
                    "lineIndex": line_index,
                }
                for index, character in enumerate(text)
            ]

        primary = {
            "pageIndex": 0,
            "characters": line("Printed label", 0.10, 0)
            + line("Next section", 0.80, 1),
        }
        candidate = {
            "pageIndex": 0,
            "characters": line("Printed labe1", 0.10, 0)
            + line("faint handwritten response", 0.45, 1),
        }

        merged, added_lines, added_words, added_characters = (
            merge_missing_text_lines(primary, candidate)
        )
        merged_text = "".join(
            character["char"] for character in merged["characters"]
        )

        self.assertEqual(added_lines, 1)
        self.assertEqual(added_words, 3)
        self.assertEqual(added_characters, len("fainthandwrittenresponse"))
        self.assertEqual(
            merged_text,
            "Printed labelfaint handwritten responseNext section",
        )

    def test_missing_line_merge_replaces_short_weak_overlap_with_better_coverage(
        self,
    ) -> None:
        def line(text: str) -> list[dict]:
            return [
                {
                    "char": character,
                    "x": 0.10 + index * 0.02,
                    "y": 0.40,
                    "width": 0.018,
                    "height": 0.03,
                    "lineIndex": 0,
                }
                for index, character in enumerate(text)
            ]

        primary = {"pageIndex": 0, "characters": line("short noise")}
        candidate = {
            "pageIndex": 0,
            "characters": line("substantially longer response text"),
        }

        merged, changed_lines, word_delta, character_delta = (
            merge_missing_text_lines(
                primary,
                candidate,
                [{"line_index": 0, "mean_confidence": 50.0}],
                [{"line_index": 0, "mean_confidence": 38.0}],
            )
        )

        self.assertEqual(changed_lines, 1)
        self.assertGreater(word_delta, 0)
        self.assertGreater(character_delta, 0)
        self.assertEqual(
            "".join(character["char"] for character in merged["characters"]),
            "substantially longer response text",
        )

    def test_cropped_geometry_maps_back_to_original_page(self) -> None:
        page = {
            "pageIndex": 0,
            "characters": [
                {
                    "char": "A",
                    "x": 0.25,
                    "y": 0.50,
                    "width": 0.10,
                    "height": 0.20,
                    "lineIndex": 0,
                }
            ],
        }

        _remap_cropped_page_geometry(
            page,
            fitz.Rect(100, 0, 500, 800),
            fitz.Rect(0, 0, 600, 800),
        )

        character = page["characters"][0]
        self.assertAlmostEqual(character["x"], 1 / 3)
        self.assertAlmostEqual(character["y"], 0.50)
        self.assertAlmostEqual(character["width"], 1 / 15)
        self.assertAlmostEqual(character["height"], 0.20)

    def test_rotated_geometry_maps_back_to_original_page(self) -> None:
        expected_by_rotation = {
            90: (0.30, 0.70, 0.05, 0.10),
            180: (0.70, 0.65, 0.10, 0.05),
            270: (0.65, 0.20, 0.05, 0.10),
        }
        for rotation, expected in expected_by_rotation.items():
            with self.subTest(rotation=rotation):
                page = {
                    "pageIndex": 0,
                    "characters": [
                        {
                            "char": "A",
                            "x": 0.20,
                            "y": 0.30,
                            "width": 0.10,
                            "height": 0.05,
                            "lineIndex": 0,
                        }
                    ],
                }

                _remap_rotated_page_geometry(page, rotation)

                character = page["characters"][0]
                actual = (
                    character["x"],
                    character["y"],
                    character["width"],
                    character["height"],
                )
                for value, expected_value in zip(actual, expected):
                    self.assertAlmostEqual(value, expected_value)

    def test_accepted_rotation_is_persisted_and_geometry_moves_with_page(self) -> None:
        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.set_rotation(90)
        pdf_bytes = document.tobytes()
        document.close()
        geometry = {
            "version": 1,
            "coordinateSpace": "normalized",
            "pages": [
                {
                    "pageIndex": 0,
                    "characters": [
                        {
                            "char": "A",
                            "x": 0.20,
                            "y": 0.30,
                            "width": 0.10,
                            "height": 0.05,
                            "lineIndex": 0,
                        }
                    ],
                }
            ],
        }

        corrected = apply_page_rotation_corrections(
            pdf_bytes,
            geometry,
            {0: 270},
        )

        result = fitz.open(stream=corrected, filetype="pdf")
        try:
            self.assertEqual(result[0].rotation, 0)
        finally:
            result.close()
        character = geometry["pages"][0]["characters"][0]
        self.assertAlmostEqual(character["x"], 0.30)
        self.assertAlmostEqual(character["y"], 0.70)
        self.assertAlmostEqual(character["width"], 0.05)
        self.assertAlmostEqual(character["height"], 0.10)

    def test_rotated_retry_requires_material_recognition_improvement(self) -> None:
        primary = {
            "mean_confidence": 28.0,
            "word_count": 200,
            "character_count": 1000,
        }

        self.assertTrue(
            should_select_rotated_retry(
                primary,
                {
                    "mean_confidence": 82.0,
                    "word_count": 280,
                    "character_count": 1700,
                },
            )
        )
        self.assertFalse(
            should_select_rotated_retry(
                primary,
                {
                    "mean_confidence": 35.0,
                    "word_count": 300,
                    "character_count": 1800,
                },
            )
        )

    @patch("engines.ocr_engine._detect_direct_page_rotation")
    def test_rotation_detection_ignores_weak_and_upright_suggestions(
        self,
        mock_detect,
    ) -> None:
        detections = {
            1: (90, 14.8),
            2: (270, 3.0),
            3: (0, 16.4),
        }
        mock_detect.side_effect = (
            lambda _pdf, page_number, _dpi: detections[page_number]
        )

        rotations = detect_direct_page_rotations(b"pdf", [1, 2, 3])

        self.assertEqual(rotations, {1: 90})

    def test_dominant_inset_scan_is_selected_as_ocr_clip(self) -> None:
        pdf_bytes = _pdf_with_image((600, 1600), fitz.Rect(150, 0, 450, 800))
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            clip = _dominant_image_clip(doc[0])
        finally:
            doc.close()

        self.assertIsNotNone(clip)
        self.assertEqual(tuple(clip), (150.0, 0.0, 450.0, 800.0))

    def test_full_page_scan_does_not_create_redundant_clip(self) -> None:
        pdf_bytes = _pdf_with_image((1200, 1600), fitz.Rect(0, 0, 600, 800))
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            clip = _dominant_image_clip(doc[0])
        finally:
            doc.close()

        self.assertIsNone(clip)

    def test_dense_native_page_is_not_selected_for_ocr(self) -> None:
        doc = fitz.open()
        try:
            page = doc.new_page(width=600, height=800)
            page.insert_text(
                (40, 60),
                "Transaction description reference 123456 amount 1,234.56 " * 5,
            )
            pdf_bytes = doc.tobytes()
        finally:
            doc.close()
        summary = summarize_geometry_quality(
            _geometry("Transaction description reference 123456 amount 1,234.56 " * 5)
        )

        self.assertEqual(select_pages_requiring_ocr(pdf_bytes, summary), [])

    def test_substantial_image_without_text_is_selected_for_ocr(self) -> None:
        pdf_bytes = _pdf_with_image((800, 800), fitz.Rect(50, 100, 550, 600))
        summary = summarize_geometry_quality(
            {
                "version": 1,
                "coordinateSpace": "normalized",
                "pages": [{"pageIndex": 0, "characters": []}],
            }
        )

        self.assertEqual(select_pages_requiring_ocr(pdf_bytes, summary), [1])


class RetrySelectionTests(unittest.TestCase):
    def test_empty_document_does_not_retry(self) -> None:
        summary = summarize_geometry_quality({"pages": []})

        self.assertFalse(needs_adaptive_retry(summary))

class ProtocolModelTests(unittest.TestCase):
    def test_ocr_job_reads_geometry_first_full_mode(self) -> None:
        job = OcrJob.from_dict(
            {
                "job_id": "job-1",
                "command": "ocr",
                "pdf_base64": "JVBERg==",
                "mode": "full",
            }
        )

        self.assertEqual(job.mode, "full")
        self.assertFalse(job.detect_tables)

    def test_table_detection_is_explicit_and_reconcile_identity_has_a_version(self) -> None:
        job = OcrJob.from_dict({
            "job_id": "job-2", "command": "ocr", "pdf_base64": "JVBERg==",
            "detect_tables": True, "analysis": True,
            "document_id": "reconcile-document", "version_id": "reconcile-version",
        })
        self.assertTrue(job.detect_tables)
        self.assertTrue(job.analysis)
        self.assertEqual(job.document_id, "reconcile-document")
        self.assertEqual(job.version_id, "reconcile-version")

if __name__ == "__main__":
    unittest.main()
