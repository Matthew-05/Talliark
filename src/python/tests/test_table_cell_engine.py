import unittest
from unittest.mock import patch

import pymupdf as fitz
from PIL import Image, ImageDraw

from engines.table_cell_engine import (
    _batch_cell_result,
    _bounded_sparse_ocr_scale,
    _canonicalize_repeated_cells,
    _cell_candidates,
    _cleanup_text,
    _cleanup_sparse_word,
    _header_line_texts,
    _geometry_characters_for_text,
    _ink_box,
    _ink_line_boxes,
    _is_table_recovery_useful,
    _largest_table_placement,
    _select_general_text,
    _select_numeric,
    _select_city,
    _select_number,
    _select_status,
)


class TablePlacementTests(unittest.TestCase):
    def test_rejects_barcode_sized_page_placement(self) -> None:
        page = fitz.Rect(0, 0, 600, 800)
        barcode = fitz.Rect(30, 700, 75, 745)

        self.assertIsNone(_largest_table_placement(page, [barcode]))

    def test_chooses_largest_significant_placement(self) -> None:
        page = fitz.Rect(0, 0, 600, 800)
        small = fitz.Rect(10, 10, 60, 60)
        table = fitz.Rect(100, 150, 500, 650)

        self.assertEqual(_largest_table_placement(page, [small, table]), table)


class TextCleanupTests(unittest.TestCase):
    def test_normalizes_spacing_and_dash_artifacts(self) -> None:
        self.assertEqual(
            _cleanup_text("  Loan  Amount —  Total  "),
            "Loan Amount - Total",
        )

    def test_does_not_invent_document_specific_corrections(self) -> None:
        self.assertEqual(_cleanup_text("Pittsbura"), "Pittsbura")
        self.assertEqual(_cleanup_sparse_word("10�"), "10")
        self.assertEqual(_cleanup_sparse_word("—"), "-")
        self.assertIsNone(_cleanup_sparse_word("~~"))


class InkGeometryTests(unittest.TestCase):
    def test_recovered_cell_geometry_ends_with_a_structural_space(self) -> None:
        characters = _geometry_characters_for_text(
            "ALPHA",
            fitz.Rect(10, 20, 60, 30),
            fitz.Rect(0, 0, 100, 100),
            7,
        )

        self.assertEqual("".join(item["char"] for item in characters), "ALPHA ")
        self.assertTrue(all(item["lineIndex"] == 7 for item in characters))
        self.assertAlmostEqual(characters[0]["x"], 0.1)
        self.assertAlmostEqual(
            sum(item["width"] for item in characters),
            0.5,
        )

    def test_detects_tight_single_line_ink_box(self) -> None:
        image = Image.new("L", (30, 12), 255)
        draw = ImageDraw.Draw(image)
        draw.rectangle((4, 3, 20, 8), fill=0)

        self.assertEqual(_ink_line_boxes(image), [(4, 3, 21, 9)])
        self.assertEqual(_ink_box(image), (4, 3, 21, 9))

    def test_preserves_known_multiline_header_layout(self) -> None:
        image = Image.new("L", (40, 20), 255)
        draw = ImageDraw.Draw(image)
        draw.rectangle((3, 2, 30, 7), fill=0)
        draw.rectangle((10, 12, 25, 17), fill=0)

        self.assertEqual(
            _ink_line_boxes(image),
            [(3, 2, 31, 8), (10, 12, 26, 18)],
        )
        self.assertEqual(
            _header_line_texts("Effective Date", 2),
            ["Effective", "Date"],
        )

    @patch("pytesseract.image_to_data")
    def test_multiline_cell_uses_block_segmentation(self, mock_image_to_data) -> None:
        mock_image_to_data.return_value = {
            "text": ["PD", "LOAN", "1"],
            "conf": ["90", "90", "90"],
        }
        image = Image.new("L", (40, 24), 255)
        draw = ImageDraw.Draw(image)
        draw.rectangle((3, 3, 25, 8), fill=0)
        draw.rectangle((3, 14, 10, 19), fill=0)

        candidates = _cell_candidates(image, image, "eng", kind="status")

        self.assertEqual(candidates[0]["text"], "PD LOAN 1")
        self.assertIn("--psm 6", mock_image_to_data.call_args.kwargs["config"])


class ColumnSelectionTests(unittest.TestCase):
    def test_number_prefers_stronger_evidence_over_repeated_low_confidence_error(self) -> None:
        candidates = [
            {"source": "raw-threshold", "text": "643", "confidence": 67.0},
            {"source": "clean", "text": "543", "confidence": 65.0},
            {"source": "clean-small", "text": "543", "confidence": 62.0},
        ]

        self.assertEqual(_select_number(candidates), "643")

    def test_city_consensus_uses_raw_result_as_tie_break(self) -> None:
        candidates = [
            {"source": "raw", "text": "Antelope", "confidence": 61.0},
            {"source": "raw-small", "text": "Antelope", "confidence": 71.0},
            {"source": "clean", "text": "Antelooe", "confidence": 75.0},
            {"source": "clean-small", "text": "Antelooe", "confidence": 86.0},
        ]

        self.assertEqual(_select_city(candidates), "Antelope")

    def test_status_preserves_unseen_domain_values(self) -> None:
        candidates = [
            {"source": "raw", "text": "A-ACTIVE", "confidence": 90.0}
        ]

        self.assertEqual(_select_status(candidates), "A-ACTIVE")

    def test_general_text_uses_variant_consensus(self) -> None:
        candidates = [
            {"source": "raw", "text": "PD LOAN", "confidence": 62.0},
            {"source": "clean", "text": "PD LOAN", "confidence": 71.0},
            {"source": "clean-small", "text": "PO LOAN", "confidence": 89.0},
        ]

        self.assertEqual(_select_general_text(candidates), "PD LOAN")

    def test_currency_format_outweighs_invalid_high_confidence_punctuation(self) -> None:
        candidates = [
            {"source": "raw", "text": "$4,700.00", "confidence": 28.0},
            {"source": "threshold", "text": "$4.700.00", "confidence": 91.0},
            {"source": "clean", "text": "$4,700.00", "confidence": 20.0},
        ]

        self.assertEqual(_select_numeric(candidates, "currency"), "$4,700.00")

    def test_currency_marker_beats_spurious_leading_digit(self) -> None:
        candidates = [
            {"source": "raw", "text": "3471.82", "confidence": 63.0},
            {"source": "clean", "text": "$471.82", "confidence": 72.0},
        ]

        self.assertEqual(_select_numeric(candidates, "currency"), "$471.82")

    def test_batched_currency_repairs_unambiguous_thousands_separator(self) -> None:
        result = _batch_cell_result(
            {"text": "$4.700.00", "confidence": 90.0},
            {"text": "$4.700.00", "confidence": 88.0},
            "currency",
            True,
        )

        self.assertEqual(result["text"], "$4,700.00")
        self.assertTrue(result["reliable"])


class RecoveryAcceptanceTests(unittest.TestCase):
    def test_sparse_ocr_scale_stays_inside_dimension_and_pixel_budgets(self) -> None:
        scale = _bounded_sparse_ocr_scale(5090, 1061)

        self.assertLess(scale, 8)
        self.assertLessEqual(5090 * scale, 24_000)
        self.assertLessEqual(5090 * 1061 * scale * scale, 60_000_000)

    def test_ordinary_sparse_region_keeps_the_preferred_enlargement(self) -> None:
        self.assertEqual(_bounded_sparse_ocr_scale(600, 150), 8)

    def test_verified_blank_cells_do_not_veto_recovery(self) -> None:
        items = [
            {"has_ink": True, "text": "Header"},
            {"has_ink": True, "text": "Value 1"},
            {"has_ink": True, "text": "Value 2"},
        ] + [{"has_ink": False, "text": None} for _ in range(20)]

        self.assertTrue(_is_table_recovery_useful(items))

    def test_sparse_recognition_is_not_applied(self) -> None:
        items = [
            {"has_ink": True, "text": "Header"},
            {"has_ink": True, "text": None},
            {"has_ink": True, "text": None},
            {"has_ink": True, "text": None},
        ]

        self.assertFalse(_is_table_recovery_useful(items))


class RepeatedValueTests(unittest.TestCase):
    def test_gridless_consensus_repairs_repeated_company_name(self) -> None:
        cells = [
            {
                "text": "OCS Facility Services",
                "candidates": [
                    {"source": "raw", "text": "OCS Facility Services"},
                    {"source": "clean", "text": "DCS Facility Services"},
                ],
            }
            for _ in range(5)
        ]
        cells.append(
            {
                "text": "DCS Facility Services",
                "candidates": [
                    {"source": "raw", "text": "DCS Facility Services"},
                    {"source": "clean", "text": "DCS Facility Services"},
                ],
            }
        )

        _canonicalize_repeated_cells(cells)

        self.assertEqual(
            {cell["text"] for cell in cells},
            {"DCS Facility Services"},
        )


if __name__ == "__main__":
    unittest.main()
