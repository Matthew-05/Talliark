"""Unit tests for the redesigned detection stages.

Each test pins one decision the pipeline makes, using synthetic geometry rather
than a PDF, so a regression names the stage that broke.
"""
from __future__ import annotations

import unittest

from engines.table.candidates import TableCandidate, generate, whitespace_candidates
from engines.table.grid import (
    GridHypothesis,
    build_logical_rows,
    cells_for,
    coalesce_empty_body_columns,
    fit_grid,
    infer_boundaries,
    occupied_columns,
)
from engines.table.headers import detect_header_cells, period_in
from engines.table.redesign import _period, _published_header
from engines.table.layout import build_page_layout, classify
from engines.table.refine import _sub_candidate, refine
from engines.table.rulings import PageRulings, RulingSegment, merge_parallel, ruling_components
from engines.table.scoring import evaluate


CHARACTER_WIDTH = 0.008


def text(value: str, x: float, y: float, line: int) -> list[dict]:
    """Lay a string out one character at a time, spaces included."""
    return [
        {
            "char": character,
            "x": x + index * CHARACTER_WIDTH,
            "y": y,
            "width": CHARACTER_WIDTH,
            "height": 0.012,
            "lineIndex": line,
        }
        for index, character in enumerate(value)
    ]


def page(rows: list[tuple[float, list[tuple[float, str]]]]) -> dict:
    characters: list[dict] = []
    for line, (y, cells) in enumerate(rows):
        for x, value in cells:
            characters.extend(text(value, x, y, line))
    return {"pageIndex": 0, "characters": characters}


def statement_page() -> dict:
    return page(
        [
            (0.10, [(0.60, "2025"), (0.80, "2024")]),
            (0.14, [(0.08, "Products"), (0.60, "112,887"), (0.80, "109,633")]),
            (0.18, [(0.08, "Services"), (0.60, "82,314"), (0.80, "71,050")]),
            (0.22, [(0.08, "Total"), (0.60, "195,201"), (0.80, "180,683")]),
        ]
    )


class TokenTests(unittest.TestCase):
    def test_classifies_the_shapes_that_change_decisions(self) -> None:
        self.assertEqual(classify("2025"), "period")
        self.assertEqual(classify("13,016"), "numeric")
        self.assertEqual(classify("(1,804)"), "numeric")
        self.assertEqual(classify("36.8%"), "percent")
        self.assertEqual(classify("$"), "currency")
        self.assertEqual(classify("•"), "bullet")
        self.assertEqual(classify("(a)"), "ordinal")
        self.assertEqual(classify("9/17/15"), "date")
        self.assertEqual(classify("Products"), "word")

    def test_a_footnote_mark_rides_along_with_its_number(self) -> None:
        # "10.1*" has no alphabetic character; without this it fell through to a
        # symbol and a whole exhibit index looked like a column of list markers.
        self.assertEqual(classify("10.1*"), "numeric")

    def test_a_currency_symbol_attached_to_an_amount_is_a_value(self) -> None:
        self.assertEqual(classify("$10,663.00"), "numeric")
        self.assertEqual(classify("($10,663.00)"), "numeric")
        self.assertEqual(classify("£-42"), "numeric")

    def test_words_are_split_on_the_space_between_them(self) -> None:
        # A proportional font sets the next glyph flush against the space it drew,
        # so the geometric gap between two words is frequently zero.
        layout = build_page_layout(page([(0.10, [(0.08, "Total net sales")])]))
        self.assertEqual(
            [token.text for token in layout.lines[0].tokens], ["Total", "net", "sales"]
        )
        self.assertEqual(layout.lines[0].segment_count, 1)


class ProseTests(unittest.TestCase):
    def test_a_full_width_sentence_is_prose(self) -> None:
        layout = build_page_layout(
            page(
                [
                    (0.10, [(0.03, "Products gross margin increased during 2025 compared to 2024")]),
                    (0.14, [(0.03, "Services gross margin increased during 2025 as well as this")]),
                ]
            )
        )
        self.assertTrue(all(layout.is_block_prose(line) for line in layout.lines))

    def test_a_wide_row_with_a_far_right_value_is_not_prose(self) -> None:
        # An index of financial statements fills the page width and puts its page
        # number against the right margin. Reading that as prose lost the table.
        layout = build_page_layout(
            page(
                [
                    (0.10, [(0.03, "Consolidated Statements of Operations for the years"), (0.93, "29")]),
                    (0.14, [(0.03, "Consolidated Balance Sheets as of September 27, 2025"), (0.93, "31")]),
                ]
            )
        )
        self.assertFalse(any(layout.is_block_prose(line) for line in layout.lines))

    def test_two_substantial_columns_are_both_running_prose(self) -> None:
        layout = build_page_layout(
            page(
                [
                    (0.10, [(0.03, "Sustain pedal and program changes remain available"),
                            (0.54, "Composition should never require complex technology")]),
                    (0.14, [(0.03, "Editing a note takes only one simple gesture"),
                            (0.54, "Musicians can keep their attention on the performance")]),
                ]
            )
        )

        self.assertTrue(all(layout.is_block_prose(line) for line in layout.lines))

    def test_prose_ends_a_candidate(self) -> None:
        rows = [
            (0.10, [(0.60, "2025"), (0.80, "2024")]),
            (0.14, [(0.08, "Products"), (0.60, "112,887"), (0.80, "109,633")]),
            (0.18, [(0.08, "Services"), (0.60, "82,314"), (0.80, "71,050")]),
            (0.24, [(0.03, "Products gross margin increased during 2025 compared to 2024")]),
            (0.30, [(0.60, "2025"), (0.80, "2024")]),
            (0.34, [(0.08, "Research"), (0.60, "34,550"), (0.80, "31,370")]),
            (0.38, [(0.08, "Selling"), (0.60, "27,601"), (0.80, "26,097")]),
        ]
        candidates = whitespace_candidates(build_page_layout(page(rows)))
        self.assertEqual(len(candidates), 2)
        self.assertLess(candidates[0].bottom, candidates[1].top)


class ParagraphTailTests(unittest.TestCase):
    """The last line of an introductory sentence is not the table's caption."""

    def _page(self, tail_gap: float, tail: str = "thousands, and per-share amounts):"):
        rows = [
            (0.100, [(0.03, "Share repurchase activity during the three months ended September 27")]),
            (0.100 + tail_gap, [(0.03, tail)]),
            (0.150, [(0.30, "Total Number"), (0.55, "Average Price"), (0.80, "Purchased")]),
            (0.166, [(0.05, "Open market"), (0.30, "33,265"), (0.55, "210.43"), (0.80, "33,265")]),
            (0.182, [(0.05, "Open market"), (0.30, "28,986"), (0.55, "224.25"), (0.80, "28,986")]),
            (0.198, [(0.05, "Total"), (0.30, "89,498"), (0.55, "238.56"), (0.80, "89,498")]),
        ]
        return build_page_layout(page(rows))

    def test_the_tail_is_left_out_of_the_table(self) -> None:
        layout = self._page(tail_gap=0.014)
        candidate = whitespace_candidates(layout)[0]
        self.assertNotIn("thousands", candidate.lines[0].text)
        self.assertIn("Total Number", candidate.lines[0].text)

    def test_a_caption_a_line_further_down_is_kept(self) -> None:
        # A section label sits below the paragraph on its own leading, not tight
        # against it, and it does not continue the sentence above.
        layout = self._page(tail_gap=0.040, tail="Gross margin:")
        candidate = whitespace_candidates(layout)[0]
        self.assertIn("Gross margin:", candidate.lines[0].text)

    def test_a_period_band_over_the_values_is_kept(self) -> None:
        # Wide, but it starts inside the table rather than at the body margin.
        rows = [
            (0.100, [(0.40, "September 2020 September 2021 September 2022")]),
            (0.116, [(0.05, "Apple"), (0.42, "100"), (0.62, "132"), (0.82, "136")]),
            (0.132, [(0.05, "S&P"), (0.42, "100"), (0.62, "137"), (0.82, "115")]),
            (0.148, [(0.05, "Dow"), (0.42, "100"), (0.62, "147"), (0.82, "107")]),
        ]
        layout = build_page_layout(page(rows))
        candidate = whitespace_candidates(layout)[0]
        self.assertIn("September", candidate.lines[0].text)


class WrappedLabelTests(unittest.TestCase):
    def test_a_label_that_wraps_above_its_amounts_stays_one_row(self) -> None:
        # A balance sheet writes the label across a full line and puts the amounts
        # on the line that finishes it. The first line reads exactly like prose.
        rows = [
            (0.100, [(0.05, "Accounts payable and accrued liabilities"), (0.70, "69,860"), (0.86, "68,960")]),
            (0.116, [(0.05, "Common stock and additional paid-in capital, 14,773,260")]),
            (0.128, [(0.06, "and 15,116,786 shares issued"), (0.70, "93,568"), (0.86, "83,276")]),
            (0.144, [(0.05, "Accumulated other comprehensive loss"), (0.70, "14,264"), (0.86, "19,154")]),
        ]
        layout = build_page_layout(page(rows))
        candidate = whitespace_candidates(layout)[0]
        grid = fit_grid(candidate, layout)
        # Four source lines, three logical rows: the label and its amounts are one.
        self.assertEqual(len(grid.rows), 3)
        merged = grid.rows[1]
        self.assertTrue(merged.merged)
        self.assertIn("Common stock", merged.cells[0])
        self.assertIn("15,116,786", merged.cells[0])
        self.assertEqual(merged.cells[1], "93,568")


class MarkerColumnTests(unittest.TestCase):
    def test_a_trailing_currency_symbol_moves_to_the_amount_it_marks(self) -> None:
        # The marker for the next column is set tight against the previous
        # amount, so it is swept into that cell. Currency precedes its amount,
        # so a symbol at the end of a filled cell is never that cell's own.
        rows = [
            (0.100, [(0.05, "Instrument"), (0.35, "Cost"), (0.60, "Gains"), (0.85, "Value")]),
            (0.116, [(0.05, "Cash"), (0.30, "$"), (0.35, "28,267"), (0.56, "$"), (0.60, "—"),
                     (0.81, "$"), (0.85, "28,267")]),
            (0.132, [(0.05, "Money market"), (0.35, "5,272"), (0.60, "—"), (0.85, "5,272")]),
            (0.148, [(0.05, "Total"), (0.35, "33,539"), (0.60, "—"), (0.85, "33,539")]),
        ]
        layout = build_page_layout(page(rows))
        _candidate, grid = refine(whitespace_candidates(layout)[0], layout)[0]
        self.assertEqual(grid.rows[1].cells[1], "$ 28,267")
        self.assertEqual(grid.rows[1].cells[2], "$ —")
        self.assertEqual(grid.rows[1].cells[3], "$ 28,267")

    def test_the_boundary_falls_on_the_far_side_of_a_floated_marker(self) -> None:
        """The published columns must agree with the cells they describe.

        A consumer that only has the contract re-derives cells from the column
        geometry. If the boundary sits between "$" and its amount, that consumer
        reads the symbol into the row label however tidy the cell text looks.
        """
        rows = [
            (0.100, [(0.05, "Expense"), (0.45, "2025"), (0.75, "2024")]),
            (0.116, [(0.05, "Research"), (0.38, "$"), (0.45, "34,550"), (0.68, "$"), (0.75, "31,370")]),
            (0.132, [(0.05, "Selling"), (0.45, "27,601"), (0.75, "26,097")]),
            (0.148, [(0.05, "Total"), (0.38, "$"), (0.45, "62,151"), (0.68, "$"), (0.75, "57,467")]),
        ]
        layout = build_page_layout(page(rows))
        _candidate, grid = refine(whitespace_candidates(layout)[0], layout)[0]
        marker = [
            token
            for token in grid.rows[1].anchor.tokens
            if token.kind == "currency"
        ]
        self.assertEqual(len(marker), 2)
        for token, boundary in zip(marker, grid.boundaries):
            self.assertLess(boundary, token.x0, "the boundary must sit left of the marker")
        self.assertEqual(grid.rows[1].cells[1], "$ 34,550")

    def test_a_floated_currency_column_is_folded_into_its_amount(self) -> None:
        # "$   2,416" with a wide gap looks like two columns, one holding only a
        # currency symbol. It is one column, and the symbol belongs to the amount.
        rows = [
            (0.100, [(0.05, "Instrument"), (0.40, "Impact"), (0.70, "2025"), (0.88, "2024")]),
            (0.116, [(0.05, "Investment portfolio"), (0.40, "Decline in fair value"),
                     (0.62, "$"), (0.70, "2,416"), (0.82, "$"), (0.88, "2,755")]),
            (0.132, [(0.05, "Term debt"), (0.40, "Increase in expense"),
                     (0.62, "$"), (0.70, "129"), (0.82, "$"), (0.88, "139")]),
        ]
        layout = build_page_layout(page(rows))
        candidate = whitespace_candidates(layout)[0]
        grid = fit_grid(candidate, layout)
        self.assertEqual(grid.column_count, 4)
        self.assertEqual(grid.rows[1].cells[2], "$ 2,416")
        self.assertEqual(grid.rows[1].cells[3], "$ 2,755")


class HeaderBandTests(unittest.TestCase):
    """A statement's column titles, as a filing actually sets them."""

    def _statement(self, with_span: bool = True):
        rows = []
        if with_span:
            # A date-bearing group label floats across the date columns. The
            # day number is still label text, not evidence that this is a row.
            rows.append((0.100, [(0.59, "Years Ended December 31,")]))
        rows += [
            (0.116, [(0.50, "September 27,"), (0.68, "September 28,"), (0.86, "September 30,")]),
            (0.126, [(0.55, "2025"), (0.73, "2024"), (0.91, "2023")]),
            (0.146, [(0.05, "Net sales:")]),
            (0.162, [(0.06, "Products"), (0.52, "307,003"), (0.70, "294,866"), (0.88, "298,085")]),
            (0.178, [(0.06, "Services"), (0.52, "109,158"), (0.70, "96,169"), (0.88, "85,200")]),
            (0.194, [(0.07, "Total net sales"), (0.52, "416,161"), (0.70, "391,035"), (0.88, "383,285")]),
        ]
        layout = build_page_layout(page(rows))
        candidate, grid = refine(whitespace_candidates(layout)[0], layout)[0]
        return candidate, grid

    def test_a_wrapped_date_header_is_one_row(self) -> None:
        # "September 27," and "2025" fill the same columns on wrapped leading:
        # one header band, not a header and a stray first body row.
        _candidate, grid = self._statement(with_span=False)
        self.assertTrue(grid.rows[0].merged)
        self.assertEqual(grid.rows[0].cells[1], "September 27, 2025")
        self.assertEqual(grid.rows[0].cells[3], "September 30, 2023")
        header = detect_header_cells(grid.cell_matrix(), grid.column_count)
        self.assertEqual(header["rowCount"], 1)
        self.assertEqual(header["labels"][2], "September 28, 2024")

    def test_a_band_naming_a_group_of_columns_is_not_a_row(self) -> None:
        # "Years ended" carries no value, labels no row, and lies across the
        # boundaries between the columns it spans.
        candidate, grid = self._statement(with_span=True)
        self.assertNotIn("Years", " ".join(grid.rows[0].cells))
        self.assertEqual(grid.rows[0].cells[1], "September 27, 2025")
        header = _published_header(
            detect_header_cells(grid.cell_matrix(), grid.column_count), grid
        )
        self.assertEqual(header["rowCount"], 1)
        self.assertEqual(
            header["labels"],
            ["", "September 27, 2025", "September 28, 2024", "September 30, 2023"],
        )
        self.assertEqual(len(header["groups"]), 1)
        group = header["groups"][0]
        self.assertEqual(group["text"], "Years Ended December 31,")
        self.assertEqual((group["columnStart"], group["columnEnd"]), (1, 3))
        self.assertLess(group["bounds"]["y"], candidate.bounds["y"])

    def test_a_group_is_peeled_from_a_logical_row_shared_with_leaf_headers(self) -> None:
        # Tight leading causes the group and years to enter grid fitting as one
        # logical row on Quest page 29. Removing that whole logical row would
        # also remove the real leaf headers.
        rows = [
            (0.100, [(0.60, "Years Ended December 31,")]),
            (0.112, [(0.60, "2025"), (0.80, "2024")]),
            (0.140, [(0.08, "Revenue"), (0.60, "250,217"), (0.80, "288,532")]),
            (0.160, [(0.08, "Cost of revenue"), (0.60, "207,673"), (0.80, "238,537")]),
            (0.180, [(0.08, "Gross profit"), (0.60, "42,544"), (0.80, "49,995")]),
        ]
        layout = build_page_layout(page(rows))
        candidate, grid = refine(whitespace_candidates(layout)[0], layout)[0]
        header = _published_header(
            detect_header_cells(grid.cell_matrix(), grid.column_count), grid
        )

        self.assertEqual(header["rowCount"], 1)
        self.assertEqual(header["labels"], ["", "2025", "2024"])
        self.assertEqual(header["groups"][0]["text"], "Years Ended December 31,")
        self.assertEqual(
            (header["groups"][0]["columnStart"], header["groups"][0]["columnEnd"]),
            (1, 2),
        )
        self.assertLess(header["groups"][0]["bounds"]["y"], candidate.bounds["y"])

    def test_a_caption_over_dated_columns_is_dropped_even_inside_one_column(self) -> None:
        # "Years ended" centred over the middle date column straddles nothing, so
        # position alone cannot betray it. What does is the band below: every
        # value column is already titled with a date, and a dated column does not
        # take a second title.
        rows = [
            (0.100, [(0.68, "Years ended")]),
            (0.116, [(0.50, "September 27,"), (0.68, "September 28,"), (0.86, "September 30,")]),
            (0.126, [(0.55, "2025"), (0.73, "2024"), (0.91, "2023")]),
            (0.146, [(0.05, "Net income"), (0.52, "112,010"), (0.70, "93,736"), (0.88, "96,995")]),
            (0.162, [(0.05, "Other income"), (0.52, "1,010"), (0.70, "736"), (0.88, "995")]),
            (0.178, [(0.05, "Total"), (0.52, "113,020"), (0.70, "94,472"), (0.88, "97,990")]),
        ]
        layout = build_page_layout(page(rows))
        _candidate, grid = refine(whitespace_candidates(layout)[0], layout)[0]
        self.assertNotIn("Years", " ".join(grid.rows[0].cells))
        self.assertEqual(grid.rows[0].cells[2], "September 28, 2024")
        header = detect_header_cells(grid.cell_matrix(), grid.column_count)
        self.assertEqual(header["rowCount"], 1)

    def test_a_title_over_undated_columns_is_kept(self) -> None:
        # The same shape, but the columns below carry words rather than dates:
        # "Weighted-Average" is the first line of that column's own title.
        rows = [
            (0.100, [(0.70, "Weighted-Average")]),
            (0.116, [(0.45, "Number of"), (0.70, "Grant-Date")]),
            (0.132, [(0.05, "Balance 2024"), (0.47, "163,326"), (0.72, "158.32")]),
            (0.148, [(0.05, "Granted"), (0.47, "44,624"), (0.72, "228.83")]),
            (0.164, [(0.05, "Balance 2025"), (0.47, "151,574"), (0.72, "189.75")]),
        ]
        layout = build_page_layout(page(rows))
        _candidate, grid = refine(whitespace_candidates(layout)[0], layout)[0]
        self.assertIn("Weighted-Average", " ".join(grid.rows[0].cells))

    def test_a_title_stack_that_widens_is_one_band(self) -> None:
        # Only the long titles wrap, so the upper lines cover fewer columns than
        # the lower ones. It is still one header band.
        rows = [
            (0.100, [(0.62, "Cash and"), (0.78, "Current"), (0.92, "Non-Current")]),
            (0.112, [(0.30, "Adjusted"), (0.46, "Unrealized"), (0.62, "Cash"),
                     (0.78, "Marketable"), (0.92, "Marketable")]),
            (0.124, [(0.30, "Cost"), (0.46, "Gains"), (0.62, "Equivalents"),
                     (0.78, "Securities"), (0.92, "Securities")]),
            (0.144, [(0.05, "Cash"), (0.31, "28,267"), (0.47, "—"), (0.63, "28,267"),
                     (0.79, "—"), (0.93, "—")]),
            (0.160, [(0.05, "Money market"), (0.31, "5,272"), (0.47, "—"), (0.63, "5,272"),
                     (0.79, "—"), (0.93, "—")]),
            (0.176, [(0.05, "Total"), (0.31, "33,539"), (0.47, "—"), (0.63, "33,539"),
                     (0.79, "—"), (0.93, "—")]),
        ]
        layout = build_page_layout(page(rows))
        _candidate, grid = refine(whitespace_candidates(layout)[0], layout)[0]
        header = detect_header_cells(grid.cell_matrix(), grid.column_count)
        self.assertEqual(header["rowCount"], 1)
        self.assertEqual(grid.rows[0].cells[3], "Cash and Cash Equivalents")
        self.assertEqual(grid.rows[0].cells[1], "Adjusted Cost")

    def test_a_bare_period_above_a_title_band_is_the_blocks_caption(self) -> None:
        # "2025" alone over a securities table names the block. The split that
        # separates one year from the next has already read it by this point.
        rows = [
            (0.100, [(0.55, "2025")]),
            (0.116, [(0.30, "Adjusted"), (0.55, "Unrealized"), (0.80, "Fair")]),
            (0.128, [(0.30, "Cost"), (0.55, "Gains"), (0.80, "Value")]),
            (0.148, [(0.05, "Cash"), (0.31, "28,267"), (0.56, "—"), (0.81, "28,267")]),
            (0.164, [(0.05, "Money market"), (0.31, "5,272"), (0.56, "—"), (0.81, "5,272")]),
            (0.180, [(0.05, "Total"), (0.31, "33,539"), (0.56, "—"), (0.81, "33,539")]),
        ]
        layout = build_page_layout(page(rows))
        _candidate, grid = refine(whitespace_candidates(layout)[0], layout)[0]
        self.assertNotIn("2025", " ".join(grid.rows[0].cells))
        self.assertEqual(grid.rows[0].cells[1], "Adjusted Cost")

    def test_a_wrapped_column_title_inside_one_column_survives(self) -> None:
        # "Filing Date/" over "Period End" never straddles a boundary, so it is
        # the column's own title and stays.
        rows = [
            (0.100, [(0.80, "Filing Date/")]),
            (0.110, [(0.80, "Period End")]),
            (0.126, [(0.05, "Exhibit"), (0.40, "Description"), (0.80, "Date")]),
            (0.142, [(0.05, "4.9"), (0.40, "Officer certificate"), (0.80, "9/17/15")]),
            (0.158, [(0.05, "4.10"), (0.40, "Officer certificate"), (0.80, "2/23/16")]),
        ]
        layout = build_page_layout(page(rows))
        _candidate, grid = refine(whitespace_candidates(layout)[0], layout)[0]
        self.assertIn("Filing Date/ Period End", grid.rows[0].cells[-1])


class PeriodTests(unittest.TestCase):
    """What the data is dated to, kept even when the label is not a row."""

    def _fit(self, rows):
        layout = build_page_layout(page(rows))
        _candidate, grid = refine(whitespace_candidates(layout)[0], layout)[0]
        header = detect_header_cells(grid.cell_matrix(), grid.column_count)
        return _period(grid, header), grid

    def test_a_period_is_read_out_of_the_column_labels(self) -> None:
        period, _grid = self._fit(
            [
                (0.100, [(0.50, "September 27,"), (0.68, "September 28,"), (0.86, "September 30,")]),
                (0.110, [(0.55, "2025"), (0.73, "2024"), (0.91, "2023")]),
                (0.130, [(0.05, "Net sales"), (0.52, "416,161"), (0.70, "391,035"), (0.88, "383,285")]),
                (0.146, [(0.05, "Cost of sales"), (0.52, "220,960"), (0.70, "210,352"), (0.88, "214,137")]),
                (0.162, [(0.05, "Gross margin"), (0.52, "195,201"), (0.70, "180,683"), (0.88, "169,148")]),
            ]
        )
        self.assertEqual(
            period["columns"],
            ["", "September 27, 2025", "September 28, 2024", "September 30, 2023"],
        )
        self.assertEqual(period["table"], "")
        self.assertEqual(period["axis"], "columns")
        self.assertEqual(period["rows"], [""] * len(period["rows"]))

    def test_an_excluded_block_caption_is_still_reported(self) -> None:
        # "2025" is dropped from the grid because it names the block rather than
        # filling a row. What it said has to survive that.
        period, grid = self._fit(
            [
                (0.100, [(0.55, "2025")]),
                (0.116, [(0.30, "Adjusted"), (0.55, "Unrealized"), (0.80, "Fair")]),
                (0.128, [(0.30, "Cost"), (0.55, "Gains"), (0.80, "Value")]),
                (0.148, [(0.05, "Cash"), (0.31, "28,267"), (0.56, "—"), (0.81, "28,267")]),
                (0.164, [(0.05, "Money market"), (0.31, "5,272"), (0.56, "—"), (0.81, "5,272")]),
                (0.180, [(0.05, "Total"), (0.31, "33,539"), (0.56, "—"), (0.81, "33,539")]),
            ]
        )
        self.assertNotIn("2025", " ".join(grid.rows[0].cells))
        self.assertEqual(period["table"], "2025")

    def test_an_excluded_qualifier_is_still_reported(self) -> None:
        period, grid = self._fit(
            [
                (0.100, [(0.68, "Years ended")]),
                (0.116, [(0.50, "September 27,"), (0.68, "September 28,"), (0.86, "September 30,")]),
                (0.126, [(0.55, "2025"), (0.73, "2024"), (0.91, "2023")]),
                (0.146, [(0.05, "Net income"), (0.52, "112,010"), (0.70, "93,736"), (0.88, "96,995")]),
                (0.162, [(0.05, "Other income"), (0.52, "1,010"), (0.70, "736"), (0.88, "995")]),
                (0.178, [(0.05, "Total"), (0.52, "113,020"), (0.70, "94,472"), (0.88, "97,990")]),
            ]
        )
        self.assertNotIn("Years", " ".join(grid.rows[0].cells))
        self.assertEqual(period["qualifier"], "Years ended")
        self.assertEqual(period["columns"][1], "September 27, 2025")

    def test_a_maturity_schedule_dates_its_rows(self) -> None:
        # The periods run down the first column and the columns name the data.
        period, _grid = self._fit(
            [
                (0.100, [(0.05, "2026"), (0.80, "12,393")]),
                (0.116, [(0.05, "2027"), (0.80, "10,078")]),
                (0.132, [(0.05, "2028"), (0.80, "9,300")]),
                (0.148, [(0.05, "2029"), (0.80, "5,235")]),
                (0.164, [(0.05, "Thereafter"), (0.80, "49,303")]),
                (0.180, [(0.05, "Total"), (0.80, "91,281")]),
            ]
        )
        self.assertEqual(period["axis"], "rows")
        self.assertEqual(period["rows"][:4], ["2026", "2027", "2028", "2029"])
        self.assertEqual(period["rows"][4:], ["", ""])
        self.assertEqual(period["columns"], ["", ""])

    def test_an_activity_table_dates_only_its_balances(self) -> None:
        # Rows are movements between two dated balances, so only the opening and
        # closing rows carry a period.
        period, _grid = self._fit(
            [
                (0.100, [(0.45, "Number of"), (0.75, "Weighted-Average")]),
                (0.112, [(0.45, "RSUs"), (0.75, "Fair Value")]),
                (0.132, [(0.05, "Balance as of September 28, 2024"), (0.47, "163,326"), (0.77, "158.73")]),
                (0.148, [(0.05, "RSUs granted"), (0.47, "73,466"), (0.77, "226.68")]),
                (0.164, [(0.05, "RSUs vested"), (0.47, "(76,845)"), (0.77, "159.85")]),
                (0.180, [(0.05, "Balance as of September 27, 2025"), (0.47, "151,574"), (0.77, "189.75")]),
            ]
        )
        self.assertEqual(period["axis"], "rows")
        self.assertEqual(
            period["rows"],
            ["", "September 28, 2024", "", "", "September 27, 2025"],
        )

    def test_a_table_with_no_dates_reports_none(self) -> None:
        period, _grid = self._fit(
            [
                (0.100, [(0.05, "Apple Asia Limited"), (0.80, "Hong Kong")]),
                (0.116, [(0.05, "Apple Canada Inc."), (0.80, "Canada")]),
                (0.132, [(0.05, "Apple India Private"), (0.80, "India")]),
            ]
        )
        self.assertIsNone(period)

    def test_period_extraction_reads_the_shapes_a_filing_uses(self) -> None:
        self.assertEqual(period_in("September 27, 2025"), "September 27, 2025")
        self.assertEqual(period_in("Years ended September 28, 2024"), "September 28, 2024")
        self.assertEqual(period_in("2025"), "2025")
        self.assertEqual(period_in("FY 2024"), "FY 2024")
        self.assertEqual(period_in("Q1 2025"), "Q1 2025")
        self.assertEqual(period_in("Adjusted Cost"), "")
        self.assertEqual(period_in("Change"), "")


class RulingComponentTests(unittest.TestCase):
    def test_two_ruled_objects_are_two_proposals(self) -> None:
        def grid(offset: float) -> PageRulings:
            return PageRulings(
                vertical=[
                    RulingSegment("vertical", x, offset, offset + 0.2, "vector")
                    for x in (0.1, 0.3, 0.5)
                ],
                horizontal=[
                    RulingSegment("horizontal", offset + y, 0.1, 0.5, "vector")
                    for y in (0.0, 0.1, 0.2)
                ],
            )

        first, second = grid(0.05), grid(0.60)
        combined = PageRulings(
            vertical=first.vertical + second.vertical,
            horizontal=first.horizontal + second.horizontal,
        )
        components = ruling_components(combined)
        self.assertEqual(len(components), 2)
        for vertical, horizontal in components:
            self.assertEqual(len(vertical), 3)
            self.assertEqual(len(horizontal), 3)

    def test_the_same_rule_found_twice_counts_once(self) -> None:
        merged = merge_parallel(
            [
                RulingSegment("horizontal", 0.25, 0.1, 0.6, "vector"),
                RulingSegment("horizontal", 0.2505, 0.55, 0.9, "raster"),
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertAlmostEqual(merged[0].start, 0.1)
        self.assertAlmostEqual(merged[0].end, 0.9)
        self.assertEqual(merged[0].source, "mixed")

    def test_rules_that_never_cross_stay_apart(self) -> None:
        merged = merge_parallel(
            [
                RulingSegment("horizontal", 0.25, 0.05, 0.30, "vector"),
                RulingSegment("horizontal", 0.25, 0.60, 0.95, "vector"),
            ]
        )
        self.assertEqual(len(merged), 2)


class ColumnFittingTests(unittest.TestCase):
    def test_columns_follow_the_values_not_a_spanning_header(self) -> None:
        # The header labels are wider than the amounts they title, so they lie
        # across the value columns. They must not be allowed to veto the columns
        # they label, and they must not be allowed to place them either.
        rows = [
            (0.10, [(0.14, "Cumulative total return"), (0.72, "Note")]),
            (0.14, [(0.05, "Apple"), (0.30, "100"), (0.55, "132"), (0.78, "136")]),
            (0.18, [(0.05, "S&P"), (0.30, "100"), (0.55, "137"), (0.78, "115")]),
            (0.22, [(0.05, "Dow"), (0.30, "100"), (0.55, "147"), (0.78, "107")]),
        ]
        layout = build_page_layout(page(rows))
        boundaries = infer_boundaries(layout.lines, 0.03, 0.95, layout)
        self.assertEqual(len(boundaries), 3)
        # Each boundary sits in the corridor between two columns of amounts.
        for boundary, (low, high) in zip(boundaries, ((0.09, 0.30), (0.32, 0.55), (0.58, 0.78))):
            self.assertTrue(low < boundary < high, boundaries)

    def test_a_currency_marker_joins_the_amount_it_marks(self) -> None:
        layout = build_page_layout(
            page([(0.10, [(0.08, "Total"), (0.50, "$"), (0.60, "195,201")])])
        )
        self.assertEqual(cells_for(layout.lines[0], [0.30, 0.55]), ["Total", "", "$ 195,201"])

    def test_a_currency_marker_joins_an_accounting_dash(self) -> None:
        layout = build_page_layout(
            page([(0.10, [(0.08, "Total"), (0.50, "$"), (0.60, "-")])])
        )
        self.assertEqual(cells_for(layout.lines[0], [0.30, 0.55]), ["Total", "", "$ -"])

    def test_a_percent_sign_stays_with_its_number(self) -> None:
        layout = build_page_layout(
            page([(0.10, [(0.08, "Products"), (0.40, "10"), (0.47, "%"), (0.60, "31,370")])])
        )
        self.assertEqual(
            cells_for(layout.lines[0], [0.30, 0.55]), ["Products", "10 %", "31,370"]
        )

    def test_a_band_no_row_fills_is_collapsed(self) -> None:
        layout = build_page_layout(statement_page())
        candidate = whitespace_candidates(layout)[0]
        grid = fit_grid(candidate, layout)
        self.assertEqual(grid.column_count, 3)

    def test_final_alignment_does_not_leave_header_only_columns(self) -> None:
        rows = [
            (0.100, [(0.42, "Stock"), (0.76, "Option")]),
            (0.116, [(0.05, "Name"), (0.39, "Awards (1)"), (0.73, "Awards")]),
            (0.140, [(0.05, "Glenn Culpepper"), (0.52, "41,629"), (0.86, "25,000")]),
            (0.156, [(0.05, "Audrey Dunning"), (0.52, "21,893"), (0.86, "—")]),
            (0.172, [(0.05, "Daniel Friedberg"), (0.52, "75,644"), (0.86, "513,819")]),
        ]
        layout = build_page_layout(page(rows))
        candidate = whitespace_candidates(layout)[0]
        grid = fit_grid(candidate, layout)
        header = detect_header_cells(grid.cell_matrix(), grid.column_count)
        coalesce_empty_body_columns(
            grid, layout, header_rows=header["rowCount"]
        )

        self.assertEqual(grid.column_count, 3)

    def test_dash_does_not_keep_a_floated_currency_column_alive(self) -> None:
        rows = [
            (0.100, [(0.05, "Plan Category"), (0.49, "Number"), (0.59, "Weighted average"), (0.84, "Remaining")]),
            (0.140, [(0.05, "2012 Plan"), (0.49, "2,216,555"), (0.61, "$"), (0.70, "2.84"), (0.86, "-")]),
            (0.160, [(0.05, "2024 Plan"), (0.49, "927,670"), (0.61, "$"), (0.70, "-"), (0.84, "672,200")]),
            (0.180, [(0.05, "Total"), (0.49, "3,144,225"), (0.61, "$"), (0.70, "3.18"), (0.84, "672,200")]),
        ]
        layout = build_page_layout(page(rows))
        boundaries = [0.40, 0.58, 0.66, 0.80]
        grid = GridHypothesis(
            columns=[
                {"x0": left, "x1": right}
                for left, right in zip([0.03] + boundaries, boundaries + [0.95])
            ],
            rows=build_logical_rows(layout.lines, boundaries, layout),
            boundaries=boundaries,
        )

        self.assertTrue(coalesce_empty_body_columns(grid, layout, header_rows=1))
        self.assertEqual(grid.column_count, 4)
        self.assertEqual(grid.rows[2].cells[2], "$ -")


class LogicalRowTests(unittest.TestCase):
    def test_a_wrapped_cell_stays_one_row(self) -> None:
        rows = [
            (0.100, [(0.05, "4.9"), (0.15, "Officer's Certificate of the Registrant"), (0.85, "8-K")]),
            (0.113, [(0.16, "including the form of global notes")]),
            (0.140, [(0.05, "4.10"), (0.15, "Officer's Certificate of the Registrant"), (0.85, "8-K")]),
        ]
        layout = build_page_layout(page(rows))
        boundaries = [0.12, 0.80]
        logical = build_logical_rows(layout.lines, boundaries, layout)
        self.assertEqual(len(logical), 2)
        self.assertTrue(logical[0].merged)
        self.assertEqual(len(logical[0].lines), 2)

    def test_a_section_label_is_its_own_row(self) -> None:
        rows = [
            (0.100, [(0.05, "Current"), (0.60, "11,487"), (0.80, "5,571")]),
            (0.114, [(0.03, "Federal:")]),
            (0.128, [(0.05, "Deferred"), (0.60, "1,804"), (0.80, "3,080")]),
        ]
        layout = build_page_layout(page(rows))
        logical = build_logical_rows(layout.lines, [0.40, 0.70], layout)
        self.assertEqual(len(logical), 3)
        self.assertEqual(logical[1].kind, "section")

    def test_an_unpunctuated_section_label_is_not_wrapped_into_its_neighbours(self) -> None:
        rows = [
            (0.100, [(0.05, "Net loss"), (0.60, "(15,382)"), (0.80, "(15,063)")]),
            (0.114, [(0.05, "Net loss per share applicable to common shareholders")]),
            (0.128, [(0.07, "Basic"), (0.60, "(0.73)"), (0.80, "(0.73)")]),
            (0.142, [(0.07, "Diluted"), (0.60, "(0.73)"), (0.80, "(0.73)")]),
        ]
        layout = build_page_layout(page(rows))
        logical = build_logical_rows(layout.lines, [0.50, 0.72], layout)

        self.assertEqual(len(logical), 4)
        self.assertEqual(logical[1].kind, "section")
        self.assertEqual(logical[2].cells[0], "Basic")

    def test_occupancy_ignores_a_lone_currency_marker(self) -> None:
        layout = build_page_layout(
            page([(0.10, [(0.08, "Total"), (0.50, "$"), (0.60, "195,201")])])
        )
        self.assertEqual(occupied_columns(layout.lines[0], [0.30, 0.55]), (0, 2))


class RefinementTests(unittest.TestCase):
    def test_invoice_furniture_above_an_internal_header_is_trimmed(self) -> None:
        rows = [
            (0.100, [(0.05, "TALLIARK LABS"), (0.70, "INVOICE")]),
            (0.130, [(0.05, "Synthetic document services"), (0.70, "TL-2026-0041")]),
            (0.170, [(0.05, "Invoice TL-2026-0041 continued")]),
            (0.210, [(0.05, "DESCRIPTION"), (0.55, "QTY"), (0.70, "RATE"), (0.84, "AMOUNT")]),
            (0.240, [(0.05, "Invoice layout evaluation"), (0.55, "1"), (0.70, "$85.00"), (0.84, "$85.00")]),
            (0.270, [(0.05, "Table boundary analysis"), (0.55, "2"), (0.70, "$60.00"), (0.84, "$120.00")]),
            (0.300, [(0.05, "Geometry validation"), (0.55, "1"), (0.70, "$45.00"), (0.84, "$45.00")]),
        ]
        layout = build_page_layout(page(rows))
        proposals = whitespace_candidates(layout)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(len(proposals[0].lines), len(rows))
        parts = refine(proposals[0], layout)

        self.assertEqual(len(parts), 1)
        _candidate, grid = parts[0]
        matrix = grid.cell_matrix()
        self.assertEqual(matrix[0], ["DESCRIPTION", "QTY", "RATE", "AMOUNT"])
        self.assertFalse(any("TALLIARK" in value for row in matrix for value in row))
        header = detect_header_cells(matrix, grid.column_count)
        self.assertEqual(header["rowCount"], 1)

    def test_a_split_fragment_does_not_inherit_rules_that_do_not_reach_it(self) -> None:
        layout = build_page_layout(
            page(
                [
                    (0.10, [(0.05, "Alpha description"), (0.60, "Current value")]),
                    (0.14, [(0.05, "Beta description"), (0.60, "Prior value")]),
                ]
            )
        )
        candidate = TableCandidate(
            lines=layout.lines,
            bounds={"x": 0.04, "y": 0.08, "width": 0.90, "height": 0.70},
            evidence="ruled",
            vertical=[
                RulingSegment("vertical", x, 0.08, 0.78, "raster")
                for x in (0.04, 0.50, 0.94)
            ],
            horizontal=[
                RulingSegment("horizontal", y, 0.04, 0.94, "raster")
                for y in (0.30, 0.50, 0.78)
            ],
        )

        fragment = _sub_candidate(candidate, layout.lines, layout)

        self.assertIsNotNone(fragment)
        self.assertEqual(fragment.evidence, "whitespace")
        self.assertEqual(fragment.vertical, [])
        self.assertEqual(fragment.horizontal, [])

    def test_tightening_removes_rules_outside_the_fitted_rows(self) -> None:
        layout = build_page_layout(
            page(
                [
                    (0.10, [(0.05, "Alpha description"), (0.60, "Current value")]),
                    (0.14, [(0.05, "Beta description"), (0.60, "Prior value")]),
                    (0.18, [(0.05, "Gamma description"), (0.60, "Ending value")]),
                ]
            )
        )
        candidate = TableCandidate(
            lines=layout.lines,
            bounds={"x": 0.04, "y": 0.08, "width": 0.90, "height": 0.70},
            evidence="ruled",
            vertical=[
                RulingSegment("vertical", x, 0.08, 0.78, "raster")
                for x in (0.04, 0.50, 0.94)
            ],
            horizontal=[
                RulingSegment("horizontal", y, 0.04, 0.94, "raster")
                for y in (0.30, 0.50, 0.78)
            ],
        )

        fitted = refine(candidate, layout)

        self.assertEqual(len(fitted), 1)
        refined, _grid = fitted[0]
        self.assertEqual(refined.evidence, "whitespace")
        self.assertEqual(refined.vertical, [])
        self.assertEqual(refined.horizontal, [])

    def test_tightening_keeps_rules_that_bracket_a_small_table(self) -> None:
        layout = build_page_layout(
            page(
                [
                    (0.10, [(0.08, "Account"), (0.65, "Amount")]),
                    (0.16, [(0.08, "Cash"), (0.65, "100")]),
                ]
            )
        )
        candidate = TableCandidate(
            lines=layout.lines,
            bounds={"x": 0.05, "y": 0.08, "width": 0.85, "height": 0.10},
            evidence="ruled",
            vertical=[
                RulingSegment("vertical", x, 0.08, 0.18, "raster")
                for x in (0.05, 0.55, 0.90)
            ],
            horizontal=[
                RulingSegment("horizontal", y, 0.05, 0.90, "raster")
                for y in (0.08, 0.13, 0.18)
            ],
        )

        fitted = refine(candidate, layout)

        self.assertEqual(len(fitted), 1)
        refined, grid = fitted[0]
        self.assertEqual(refined.evidence, "ruled")
        features = evaluate(refined, grid, layout, header_rows=1)
        self.assertTrue(features.accepted())

    def test_a_new_period_band_after_a_gap_starts_a_new_table(self) -> None:
        rows = [
            (0.100, [(0.55, "2025"), (0.70, "2024"), (0.85, "2023")]),
            (0.116, [(0.05, "U.S."), (0.55, "151,790"), (0.70, "142,196"), (0.85, "138,573")]),
            (0.132, [(0.05, "China"), (0.55, "64,377"), (0.70, "66,952"), (0.85, "72,559")]),
            (0.148, [(0.05, "Other"), (0.55, "199,994"), (0.70, "181,887"), (0.85, "172,153")]),
            (0.180, [(0.70, "2025"), (0.85, "2024")]),
            (0.196, [(0.05, "U.S."), (0.70, "40,274"), (0.85, "35,664")]),
            (0.212, [(0.05, "China"), (0.70, "3,617"), (0.85, "4,797")]),
            (0.228, [(0.05, "Other"), (0.70, "5,943"), (0.85, "5,219")]),
        ]
        layout = build_page_layout(page(rows))
        parts = refine(whitespace_candidates(layout)[0], layout)
        self.assertEqual(len(parts), 2)
        self.assertLess(parts[0][0].bottom, parts[1][0].top)

    def test_sparse_rows_alone_do_not_split_a_table(self) -> None:
        # An aging report leaves its "Terms" cell blank for a run of invoices.
        # That is a gap in the data, not a new schema, and splitting there tore a
        # single long report into fragments.
        rows = []
        for index in range(12):
            cells = [(0.05, "Invoice"), (0.30, "31,4%02d" % index), (0.85, "5,266.12")]
            if index < 4 or index > 7:
                cells.insert(2, (0.55, "Net 30"))
            rows.append((0.100 + index * 0.016, cells))
        layout = build_page_layout(page(rows))
        parts = refine(whitespace_candidates(layout)[0], layout)
        self.assertEqual(len(parts), 1)

    def test_a_repeated_period_band_splits_a_stacked_report(self) -> None:
        rows = []
        y = 0.10
        for year in ("2025", "2024"):
            rows.append((y, [(0.55, year)]))
            y += 0.016
            rows.append((y, [(0.30, "Americas"), (0.55, "Europe"), (0.80, "Total")]))
            y += 0.016
            for label, a, b, c in (("Net sales", "178,353", "111,032", "416,161"),
                                   ("Cost of sales", "95,699", "58,617", "220,960")):
                rows.append((y, [(0.05, label), (0.30, a), (0.55, b), (0.80, c)]))
                y += 0.016
        layout = build_page_layout(page(rows))
        parts = refine(whitespace_candidates(layout)[0], layout)
        self.assertEqual(len(parts), 2)


class ScoringTests(unittest.TestCase):
    def _score(self, geometry: dict, *, ignore_prose: bool = False):
        layout = build_page_layout(geometry)
        # Scoring is the safety net for narrative that upstream segmentation did
        # not recognize (for example after noisy OCR invents another boundary).
        if ignore_prose:
            layout.prose_lines.clear()
        candidates = generate(layout, PageRulings())
        self.assertTrue(candidates)
        candidate, grid = refine(candidates[0], layout)[0]
        header = detect_header_cells(grid.cell_matrix(), grid.column_count)
        rows = int(header["rowCount"]) if header else 0
        return evaluate(candidate, grid, layout, header_rows=rows), grid

    def test_a_statement_scores_high_and_is_accepted(self) -> None:
        features, _grid = self._score(statement_page())
        self.assertTrue(features.accepted())
        self.assertGreater(features.confidence(), 0.8)

    def test_a_bullet_list_is_rejected(self) -> None:
        rows = [
            (0.10 + index * 0.02, [(0.08, "•"), (0.12, item)])
            for index, item in enumerate(
                ["MacBook Pro", "Mac mini", "iMac", "iPad mini", "Apple Watch"]
            )
        ]
        features, _grid = self._score(page(rows))
        self.assertFalse(features.accepted())
        self.assertEqual(features.weakest(), "marker-first-column")

    def test_numbered_footnotes_with_measurements_are_rejected(self) -> None:
        # Measurements embedded in prose do not form value cells. This is the
        # Quest shape: aligned ordinals plus sentences containing share counts.
        rows = [
            (0.10, [(0.05, "(6)"), (0.12, "Includes 25,000 shares and 21,629 DSUs.")]),
            (0.13, [(0.05, "(7)"), (0.12, "Includes 1,893 DSUs.")]),
        ]
        features, _grid = self._score(page(rows), ignore_prose=True)

        self.assertEqual(features.header, 1.0)
        self.assertEqual(features.marker_first_column, 1.0)
        self.assertFalse(features.accepted())
        self.assertEqual(features.weakest(), "marker-first-column")

    def test_parenthesized_amounts_against_numeric_cells_are_not_list_markers(self) -> None:
        # Parenthesized numbers are also accounting values, so the marker guard
        # requires prose to their right before treating them as ordinals.
        rows = [
            (0.10, [(0.05, "(6)"), (0.75, "10")]),
            (0.13, [(0.05, "(7)"), (0.75, "12")]),
            (0.16, [(0.05, "(8)"), (0.75, "14")]),
        ]
        features, _grid = self._score(page(rows))

        self.assertEqual(features.marker_first_column, 0.0)

    def test_a_list_of_names_against_short_values_is_not_prose(self) -> None:
        # Two columns and no header, but the right column is one word: an exhibit
        # of subsidiaries, not a page laid out in columns.
        rows = [
            (0.10 + index * 0.02, [(0.05, name), (0.80, place)])
            for index, (name, place) in enumerate(
                [
                    ("Apple Asia Limited", "Hong Kong"),
                    ("Apple Canada Inc.", "Canada"),
                    ("Apple India Private Limited", "India"),
                    ("Apple Japan, Inc.", "Japan"),
                    ("Apple Operations Limited", "Ireland"),
                ]
            )
        ]
        features, _grid = self._score(page(rows))
        self.assertEqual(features.prose_pair, 0.0)
        self.assertTrue(features.accepted())

    def test_a_heading_does_not_turn_parallel_prose_into_a_table(self) -> None:
        rows = [
            (0.100, [(0.05, "Operational Excellence"), (0.55, "Looking Ahead")]),
            (0.124, [(0.05, "Our teams improved service quality today"),
                     (0.55, "We enter the year with stronger clients")]),
            (0.148, [(0.05, "Those changes increased consistency across operations"),
                     (0.55, "The platform now supports continued profitable growth")]),
            (0.172, [(0.05, "Managers gained better visibility into requirements"),
                     (0.55, "New programs expand the services now available")]),
            (0.196, [(0.05, "This discipline strengthened execution during the year"),
                     (0.55, "Our partners remain central to future work")]),
        ]
        features, _grid = self._score(page(rows), ignore_prose=True)

        self.assertEqual(features.header, 0.0)
        self.assertGreater(features.prose_pair, 0.5)
        self.assertFalse(features.accepted())

    def test_parallel_prose_is_checked_across_every_adjacent_column_pair(self) -> None:
        rows = [
            (0.100, [(0.03, "Customer Service Priorities"),
                     (0.37, "Operational Excellence Goals"),
                     (0.70, "Looking Ahead This Year")]),
            (0.124, [(0.03, "Teams resolved requests promptly"),
                     (0.37, "Service quality improved again today"),
                     (0.70, "Clients strengthened our future work")]),
            (0.148, [(0.03, "Specialists gave customers clear answers"),
                     (0.37, "Managers saw requirements more clearly"),
                     (0.70, "New programs expanded available services")]),
        ]
        features, grid = self._score(page(rows), ignore_prose=True)

        self.assertGreaterEqual(grid.column_count, 3)
        self.assertEqual(features.prose_pair, 1.0)
        self.assertFalse(features.accepted())

    def test_an_uncorroborated_word_header_earns_no_header_credit(self) -> None:
        rows = [
            (0.100, [(0.05, "Editing"), (0.55, "Composition Without Compromise")]),
            (0.130, [(0.05, "Erase a wrong note with one simple gesture"),
                     (0.55, "Technology should never interrupt a musical idea")]),
            (0.160, [(0.05, "Repeat the final bars for a gradual fadeout"),
                     (0.55, "Every control remains close to the performer")]),
        ]
        features, _grid = self._score(page(rows), ignore_prose=True)

        self.assertEqual(features.header, 0.0)

    def test_two_rows_without_a_header_are_not_a_table(self) -> None:
        # A signature block: two aligned lines is not a repeated structure.
        rows = [
            (0.100, [(0.05, "Date: October 31, 2025"), (0.55, "Apple Inc.")]),
            (0.130, [(0.05, "By:"), (0.55, "/s/ Kevan Parekh")]),
        ]
        features, _grid = self._score(page(rows))
        self.assertFalse(features.accepted())
        self.assertEqual(features.weakest(), "unrepeated")

    def test_confidence_is_not_a_row_count(self) -> None:
        short, _grid = self._score(statement_page())
        rows = [(0.10, [(0.60, "2025"), (0.80, "2024")])]
        for index in range(12):
            rows.append((0.14 + index * 0.02, [(0.08, "Item"), (0.60, "1,000"), (0.80, "900")]))
        long_table, _grid = self._score(page(rows))
        # Both are clean statements; length alone must not separate them much.
        self.assertLess(abs(short.confidence() - long_table.confidence()), 0.2)


if __name__ == "__main__":
    unittest.main()
