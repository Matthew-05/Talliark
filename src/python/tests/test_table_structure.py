"""Component tests for the pieces of table detection that stand alone.

Ruling extraction reads pages and images directly; header analysis reads an
already-fitted grid's cell text. The grid fitting between them is exercised
end to end in test_table_pipeline.py.
"""
import unittest

from PIL import Image, ImageDraw

from engines.table.headers import detect_header_cells
from engines.table.rulings import (
    _contains_diagonal_series,
    _vector_ruling_segments,
    _vector_rulings,
    detect_ruled_grid,
)


class RulingTests(unittest.TestCase):
    def test_extracts_synthetic_ruled_grid(self) -> None:
        image = Image.new("L", (500, 300), 255)
        draw = ImageDraw.Draw(image)
        for x in (30, 220, 470):
            draw.line((x, 0, x, 299), fill=0, width=2)
        for y in (20, 100, 180, 280):
            draw.line((0, y, 499, y), fill=0, width=2)
        self.assertEqual(detect_ruled_grid(image), ([30, 220, 470], [20, 100, 180, 280]))

    def test_rotated_page_rulings_are_reported_in_displayed_space(self) -> None:
        class FakeRect:
            x0 = y0 = 0.0
            x1 = width = 600.0
            y1 = height = 400.0

        class FakePage:
            rect = FakeRect()
            # 90-degree rotation: unrotated (x, y) displays at (600 - y, x).
            rotation_matrix = (0.0, 1.0, -1.0, 0.0, 600.0, 0.0)

            def get_drawings(self):
                # Spans the unrotated page horizontally; displays as a vertical rule.
                return [{"type": "s", "color": (0, 0, 0),
                         "items": [("l", (0.0, 100.0), (400.0, 100.0))]}]

        vertical, horizontal = _vector_rulings(FakePage())

        self.assertEqual(horizontal, [])
        self.assertEqual(len(vertical), 1)
        self.assertAlmostEqual(vertical[0], 500.0 / 600.0, places=3)

    def test_a_page_background_is_not_graphic_content(self) -> None:
        class FakeRect:
            def __init__(self, x0, y0, x1, y1):
                self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1

            @property
            def width(self):
                return self.x1 - self.x0

            @property
            def height(self):
                return self.y1 - self.y0

        class FakePage:
            rect = FakeRect(0.0, 0.0, 600.0, 800.0)
            rotation_matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

            def get_drawings(self):
                return [{
                    "type": "f",
                    "fill": (1.0, 1.0, 1.0),
                    "color": None,
                    "items": [("re", self.rect)],
                    "rect": self.rect,
                }]

        rulings = _vector_ruling_segments(FakePage())

        self.assertEqual(rulings.graphics, [])

    def test_a_raster_plot_is_graphic_but_a_grid_is_not(self) -> None:
        plot = Image.new("L", (400, 240), 255)
        plot_draw = ImageDraw.Draw(plot)
        plot_draw.line((20, 210, 380, 30), fill=0, width=3)

        grid = Image.new("L", (400, 240), 255)
        grid_draw = ImageDraw.Draw(grid)
        for x in (20, 140, 260, 380):
            grid_draw.line((x, 20, x, 220), fill=0, width=2)
        for y in (20, 70, 120, 170, 220):
            grid_draw.line((20, y, 380, y), fill=0, width=2)

        self.assertTrue(_contains_diagonal_series(plot))
        self.assertFalse(_contains_diagonal_series(grid))


class HeaderTests(unittest.TestCase):
    """Header analysis over fitted cell text. Band selection on the page itself
    lives in test_table_pipeline.HeaderBandTests."""

    def test_collapses_multiline_header_labels(self) -> None:
        matrix = [
            ["Account", "Current"],
            ["name", "year"],
            ["Cash", "100"],
        ]

        header = detect_header_cells(matrix, 2)

        self.assertEqual(header, {"rowCount": 2, "labels": ["Account name", "Current year"]})

    def test_ruled_text_table_uses_complete_first_row_as_header(self) -> None:
        matrix = [
            ["Item", "Description"],
            ["Cash", "Operating account"],
        ]

        header = detect_header_cells(matrix, 2, ruled=True)

        self.assertEqual(header, {"rowCount": 1, "labels": ["Item", "Description"]})

    def test_year_header_with_empty_first_cell_stays_one_row(self) -> None:
        matrix = [
            ["", "2025", "2024"],
            ["Income taxes payable", "$ 13,016", "$ 26,601"],
        ]

        header = detect_header_cells(matrix, 3)

        self.assertEqual(header, {"rowCount": 1, "labels": ["", "2025", "2024"]})

    def test_value_only_row_below_a_header_is_never_promoted(self) -> None:
        matrix = [
            ["Account", "2025"],
            ["Subtotal", "13,016"],
            ["", "2,044"],
        ]

        header = detect_header_cells(matrix, 2)

        self.assertEqual(header["rowCount"], 1)


if __name__ == "__main__":
    unittest.main()
