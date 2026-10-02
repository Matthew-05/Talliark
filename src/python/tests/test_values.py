"""The value tier: recognition, categories and the document-values-v1 envelope."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from engines.values import categories
from engines.values.detector import detect_values
from engines.values.spans import cut_token, recognize_spans, token_spans

from documents import canonical, detect, detect_pages, expand_geometry, geometry as _geometry, cell as _cell, line as _line, label as _label, noise as _noise, published as _published, refused as _refused, references as _references


FIXTURE = Path(__file__).parent / "fixtures" / "values" / "span-oracle.json"
DETECTOR_FIXTURE = Path(__file__).parent / "fixtures" / "values" / "detector-cases.json"


def detect_model(model, *, tables=None, diagnostics=None):
    """Detection over a prepared geometry model, as the tests read it."""
    result = detect(model, tables=tables)
    if diagnostics is not None:
        diagnostics.update(result.diagnostics)
    return result


def _detect(pages):
    """Published texts, everything refused, and the diagnostics."""
    result = detect_pages(pages)
    return _published(result), _refused(result), result.diagnostics

class ContractVocabularyTests(unittest.TestCase):
    """The producer's vocabulary and the contract's closed enums are one thing.

    The viewer already checks this from its side; a detector that publishes a
    kind the contract does not define would otherwise be caught only there,
    after the span had been silently dropped by the decoder.
    """

    CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "document-values-v1.json"

    def _definitions(self) -> dict:
        return json.loads(self.CONTRACT.read_text(encoding="utf-8"))["definitions"]

    def test_every_vocabulary_matches_the_contract(self) -> None:
        definitions = self._definitions()
        for name, field, published in (
            ("Reference", "kind", categories.REFERENCE_KINDS),
            ("Structure", "kind", categories.STRUCTURE_KINDS),
            ("Noise", "reason", categories.NOISE_REASONS),
        ):
            with self.subTest(definition=name):
                self.assertEqual(
                    sorted(definitions[name]["properties"][field]["enum"]),
                    sorted(published),
                )

    def test_noise_is_never_clickable_by_construction(self) -> None:
        definitions = self._definitions()
        self.assertEqual(definitions["Noise"]["properties"]["clickable"], {
            "const": False,
            "description": definitions["Noise"]["properties"]["clickable"]["description"],
        })
        for kind in categories.NOISE_REASONS:
            self.assertFalse(categories.is_clickable(categories.NOISE, kind))

    def test_clickability_is_stated_for_every_category(self) -> None:
        self.assertEqual(
            sorted(categories.CLICKABLE_BY_DEFAULT), sorted(categories.CATEGORIES)
        )


class SpanOracleTests(unittest.TestCase):
    def test_recognizer_matches_oracle(self) -> None:
        cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(text=case["text"]):
                actual = []
                for span in recognize_spans(case["text"]):
                    item = {"kind": span.kind, "text": span.text}
                    if span.normalized_value:
                        item["normalizedValue"] = span.normalized_value
                    if span.currency:
                        item["currency"] = span.currency
                    if span.magnitude:
                        item["magnitude"] = span.magnitude
                    if span.date_precision:
                        item["datePrecision"] = span.date_precision
                    if span.date_order == "ambiguous":
                        item["dateOrder"] = span.date_order
                    actual.append(item)
                self.assertEqual(actual, case["spans"])

    def test_dates_win_over_the_numbers_inside_them(self) -> None:
        spans = recognize_spans("At December 31, 2025, cash was $ 1,200 or 4.5%.")
        self.assertEqual([span.kind for span in spans], ["date", "number", "percent"])


class DetectorTests(unittest.TestCase):
    def test_builds_bounds_and_page_context(self) -> None:
        text = "Amounts in millions USD  December 31, 2025  $ 1,200"
        chars = [
            {"char": char, "x": 0.02 + index * 0.008, "y": 0.10, "width": 0.008, "height": 0.02, "lineIndex": 0}
            for index, char in enumerate(text)
        ]
        model = detect_model({"version": 1, "coordinateSpace": "normalized", "pages": [{"pageIndex": 0, "characters": chars}]})
        self.assertEqual(model["documentContext"]["currency"], "USD")
        self.assertEqual(model["pages"][0]["context"]["scale"], 1_000_000)
        self.assertEqual([value["kind"] for value in model["pages"][0]["values"]], ["date", "number"])
        self.assertEqual(model["pages"][0]["values"][1]["normalizedValue"], "1200")
        self.assertGreater(model["pages"][0]["values"][1]["bounds"]["width"], 0)

    def test_attaches_a_wrapped_modifier_across_pages(self) -> None:
        def characters(text: str, line_index: int = 0) -> list[dict]:
            return [
                {"char": char, "x": 0.02 + index * 0.01, "y": 0.10, "width": 0.01, "height": 0.02, "lineIndex": line_index}
                for index, char in enumerate(text)
            ]

        model = detect_model({
            "version": 1,
            "coordinateSpace": "normalized",
            "pages": [
                {"pageIndex": 0, "characters": characters("$1.0")},
                {"pageIndex": 1, "characters": characters("million in revenue")},
            ],
        })
        value = model["pages"][0]["values"][0]
        self.assertEqual(value["text"], "$1.0 million")
        self.assertEqual(value["normalizedValue"], "1000000")
        self.assertEqual(value["magnitude"], 1_000_000)
        self.assertEqual([segment["pageIndex"] for segment in value["segments"]], [0, 1])

    def test_attaches_aligned_wrapped_date_headers(self) -> None:
        def placed(text: str, x: float, y: float, line_index: int) -> list[dict]:
            return [
                {
                    "char": char,
                    "x": x + index * 0.008,
                    "y": y,
                    "width": 0.008,
                    "height": 0.012,
                    "lineIndex": line_index,
                }
                for index, char in enumerate(text)
            ]

        characters = []
        for x, head, year in (
            (0.20, "September 27,", "2025"),
            (0.45, "September 28,", "2024"),
            (0.70, "September 30,", "2023"),
        ):
            characters.extend(placed(head, x, 0.10, 0))
            characters.extend(placed(year, x + 0.036, 0.11, 1))

        model = detect_model({
            "version": 1,
            "coordinateSpace": "normalized",
            "pages": [{"pageIndex": 0, "characters": characters}],
        })
        values = model["pages"][0]["values"]
        self.assertEqual([value["text"] for value in values], [
            "September 27, 2025",
            "September 28, 2024",
            "September 30, 2023",
        ])
        self.assertEqual(values[0]["normalizedValue"], "2025-09-27")
        self.assertEqual(len(values[0]["segments"]), 2)
        self.assertEqual(values[0]["bounds"], values[0]["segments"][0]["bounds"])

    def test_does_not_attach_a_year_without_wrapped_leading(self) -> None:
        chars = [
            {"char": char, "x": 0.2 + index * 0.008, "y": 0.10, "width": 0.008, "height": 0.012, "lineIndex": 0}
            for index, char in enumerate("September 27,")
        ] + [
            {"char": char, "x": 0.236 + index * 0.008, "y": 0.20, "width": 0.008, "height": 0.012, "lineIndex": 1}
            for index, char in enumerate("2025")
        ]
        model = detect_model({"pages": [{"pageIndex": 0, "characters": chars}]})
        self.assertNotIn("September 27, 2025", [value["text"] for value in model["pages"][0]["values"]])



class TokenAlignmentTests(unittest.TestCase):
    """A value has to claim whole tokens, not cut one in half."""

    def test_a_joined_identifier_yields_no_value(self) -> None:
        for text, reason in (
            ("123-456-7890", "identifier"),
            ("FORM 10-K", "identifier"),
            ("94-2404110", "identifier"),
            ("3:13 PM", "identifier"),
            ("ASU 2024-03", "identifier"),
            ("(1)Includes $4", "alphanumeric"),
        ):
            with self.subTest(text=text):
                refused: list = []
                spans = recognize_spans(text, rejected=refused)
                self.assertNotIn(reason, [span.text for span in spans])
                self.assertIn(reason, [token.reason for token in refused])

    def test_a_value_may_still_span_several_tokens(self) -> None:
        for text, expected in (
            ("$ 50.14", "$ 50.14"),
            ("December 31, 2025", "December 31, 2025"),
            ("1.0 million", "1.0 million"),
            ("0.2 percent", "0.2 percent"),
        ):
            with self.subTest(text=text):
                self.assertEqual([span.text for span in recognize_spans(text)], [expected])

    def test_sentence_punctuation_is_not_part_of_the_token(self) -> None:
        self.assertEqual([span.text for span in recognize_spans("was $1,234.")], ["$1,234"])

    def test_an_en_dash_separates_where_a_hyphen_binds(self) -> None:
        """No identifier is written with an en dash, so a range is two figures."""
        self.assertEqual(
            [span.text for span in recognize_spans("2024\u20132025")], ["2024", "2025"]
        )
        refused: list = []
        self.assertEqual([span.text for span in recognize_spans("2024-2025", rejected=refused)], [])
        self.assertEqual([token.reason for token in refused], ["identifier"])
        # Only between two figures. The same dash joining a figure to a word is
        # a sentence's punctuation, and the cross-reference is not the number 55.
        self.assertEqual(
            [span.text for span in recognize_spans("see page 55\u2014Entertainment")], []
        )

    def test_a_currency_printed_outside_the_bracket_stays_with_the_figure(self) -> None:
        """"$(1,234)" is one token, and it is the commonest negative in a statement."""
        for text in ("$(1,234)", "$ (1,234)", "($1,234)", "(1,234 USD)"):
            with self.subTest(text=text):
                spans = recognize_spans(text)
                self.assertEqual([span.normalized_value for span in spans], ["-1234"])
                self.assertEqual([span.currency for span in spans], ["USD"])

    def test_a_cut_token_is_reported_whole_and_once(self) -> None:
        refused: list = []
        recognize_spans("call 123-456-7890 today", rejected=refused)
        self.assertEqual([(token.text, token.reason) for token in refused], [("123-456-7890", "identifier")])

    def test_a_fragmented_figure_is_reported_rather_than_guessed(self) -> None:
        refused: list = []
        spans = recognize_spans("1,2 34", rejected=refused)
        self.assertEqual([span.text for span in spans], ["34"])
        self.assertEqual([(token.text, token.reason) for token in refused], [("1,2", "partial-token")])

    def test_alignment_allows_only_trimmable_leftovers(self) -> None:
        text = "par value: 50,400,000 shares"
        tokens = token_spans(text)
        start = text.index("50,400,000")
        self.assertIsNone(cut_token(text, tokens, start, start + len("50,400,000")))

    def test_a_form_number_is_a_reference_rather_than_a_value(self) -> None:
        result = detect_pages([_line("FORM 10-K", y=0.1, line_index=0)])
        self.assertEqual(_published(result), [])
        self.assertEqual(_noise(result), [])
        self.assertEqual(
            [(item["kind"], item["text"]) for item in _references(result)],
            [("identifier", "10-K")],
        )


class SuppressionTests(unittest.TestCase):
    def test_a_phone_number_never_becomes_a_negative(self) -> None:
        result = detect_pages([_line("Cupertino, California (408) 996-1010", y=0.1, line_index=0)])
        self.assertEqual(_published(result), [])
        self.assertIn("phone", {item["kind"] for item in _references(result)})

    def test_a_citation_year_is_not_a_period(self) -> None:
        published, _, _ = _detect([_line("of the Securities Exchange Act of 1934", y=0.1, line_index=0)])
        self.assertEqual(published, [])

    def test_a_year_reached_through_period_language_survives(self) -> None:
        published, _, _ = _detect([_line("for the fiscal year ended 2025", y=0.1, line_index=0)])
        self.assertEqual(published, ["2025"])

    def test_a_footnote_marker_set_smaller_than_its_page_is_dropped(self) -> None:
        page = _line("Total net sales were 1,234 in the period", y=0.10, line_index=0)
        page += _line("(1)", y=0.20, line_index=1, height=0.006)
        published, rejected, _ = _detect([page])
        self.assertEqual(published, ["1,234"])
        self.assertEqual([_label(item) for item in rejected], ["superscript"])

    def test_an_identifier_label_only_condemns_what_follows_it(self) -> None:
        published, _, _ = _detect([_line("Total 1,234 CUSIP 037833100", y=0.1, line_index=0)])
        self.assertEqual(published, ["1,234"])

    def test_a_number_mark_names_the_number_beside_it(self) -> None:
        """"#7" is refused on shape; "No. 7" is two tokens, so the mark is a cue."""
        result = detect_pages([_line("Invoice No. 00550 totalling 1,234", y=0.1, line_index=0)])
        self.assertEqual(_published(result), ["1,234"])
        self.assertEqual(
            [(item["kind"], item["text"]) for item in _references(result)],
            [("identifier", "00550")],
        )

    def test_a_number_mark_answers_only_for_the_number_after_it(self) -> None:
        published, _, _ = _detect([_line("Nos. 3 and 4 were 1,234", y=0.1, line_index=0)])
        self.assertEqual(published, ["4", "1,234"])

    def test_refusals_and_redirections_are_counted_apart(self) -> None:
        _, _, diagnostics = _detect([
            _line("FORM 10-K", y=0.1, line_index=0),
            _line("of the Securities Exchange Act of 1934", y=0.2, line_index=1),
        ])
        self.assertEqual(diagnostics["value_references"], 1)
        self.assertEqual(diagnostics["value_references_identifier"], 1)
        self.assertEqual(diagnostics["value_noise"], 1)
        self.assertEqual(diagnostics["value_noise_citation_year"], 1)

    def test_noise_is_carried_beside_the_values_it_was_kept_from(self) -> None:
        model = detect_model(_geometry([_line("1,2 34", y=0.1, line_index=0)]))
        page = model["pages"][0]
        # The readable half still publishes; the damaged half is reported rather
        # than silently dropped, which is what makes OCR fragmentation visible.
        self.assertEqual([value["text"] for value in page["values"]], ["34"])
        self.assertEqual(
            page["noise"],
            [{
                "id": page["noise"][0]["id"],
                "kind": "number",
                "clickable": False,
                "text": "1,2",
                "bounds": page["noise"][0]["bounds"],
                "reason": "partial-token",
            }],
        )

    def test_a_page_that_refuses_nothing_carries_no_noise_key(self) -> None:
        model = detect_model(_geometry([_line("Total 1,234", y=0.1, line_index=0)]))
        self.assertNotIn("noise", model["pages"][0])

    def test_ids_are_content_addressed_and_carry_their_category(self) -> None:
        pages = [_line("Total 1,234 of FORM 10-K", y=0.1, line_index=0)]
        model = detect_model(_geometry(pages))
        page = model["pages"][0]
        self.assertRegex(page["values"][0]["id"], r"^val-[0-9a-f]{16}$")
        self.assertRegex(page["references"][0]["id"], r"^ref-[0-9a-f]{16}$")
        # Content-addressed means a second reading of the same page agrees, and
        # an id survives a detector that publishes something new before it.
        again = detect_model(_geometry(pages))
        self.assertEqual(
            [value["id"] for value in again["pages"][0]["values"]],
            [value["id"] for value in page["values"]],
        )


class UnsupportedTests(unittest.TestCase):
    """A bare number in a sentence, with nothing about it saying it measures."""

    PROSE = "Indicate by check mark whether the Registrant is an issuer as defined in Rule 405"

    def test_a_number_a_sentence_needed_is_refused(self) -> None:
        published, rejected, _ = _detect([_line(self.PROSE, y=0.1, line_index=0)])
        self.assertEqual(published, [])
        self.assertEqual([_label(item) for item in rejected], ["unsupported"])

    def test_a_figure_written_as_a_figure_survives_in_prose(self) -> None:
        """Market value and par value are printed in sentences and are wanted."""
        for text, expected in (
            ("The aggregate market value of the shares held was $3,253,431 on that date", "$3,253,431"),
            ("Common Stock, $0.00001 par value per share, was registered under the Act", "$0.00001"),
            ("Net sales in the segment grew 5% compared with the prior fiscal year", "5%"),
            ("The Company repurchased 166,000 shares of its common stock in the period", "166,000"),
        ):
            with self.subTest(text=text):
                published, _, _ = _detect([_line(text, y=0.1, line_index=0)])
                self.assertEqual(published, [expected])

    def test_a_number_in_its_own_cell_is_never_unsupported(self) -> None:
        """A column keeps its figures even where the row carries a long label."""
        row = _cell("Weighted average shares outstanding, basic and diluted", y=0.1, line_index=0, x=0.02)
        row += _cell("166", y=0.1, line_index=0, x=0.70)
        published, rejected, _ = _detect([row])
        self.assertEqual(published, ["166"])
        self.assertEqual(rejected, [])

    def test_period_language_speaks_for_the_number_after_it(self) -> None:
        published, _, _ = _detect([
            _line("The notes issued under the indenture are maturing 5 years from the date", y=0.1, line_index=0)
        ])
        self.assertIn("5", published)

    def test_a_modifier_on_the_next_line_speaks_for_the_number_before_it(self) -> None:
        """The recognizer reads one line; the magnitude arrives on the next."""
        page = _line("wrapping number value modifiers. This is an example of a document with 1", y=0.10, line_index=0)
        page += _line("million modifiers. This is an example of a document with wrapping values", y=0.12, line_index=1)
        result = detect_model(_geometry([page]))
        value = result["pages"][0]["values"][0]
        self.assertEqual(value["text"], "1 million")
        self.assertEqual(value["normalizedValue"], "1000000")

    def test_a_bare_year_in_prose_is_left_alone(self) -> None:
        """Deliberately out of scope: a year in a sentence is often a period."""
        published, _, _ = _detect([
            _line("During 2025, the Company repurchased shares of its common stock", y=0.1, line_index=0)
        ])
        self.assertEqual(published, ["2025"])


class PageFurnitureTests(unittest.TestCase):
    @staticmethod
    def _pages(footer: str, *, copies_per_page: int = 1) -> list[list[dict]]:
        pages = []
        for page_index in range(4):
            characters = _line(f"Net sales of 1,{page_index}00 in the period", y=0.10, line_index=0)
            for copy in range(copies_per_page):
                characters += _line(footer, y=0.95 + copy * 0.01, line_index=1 + copy)
            pages.append(characters)
        return pages

    def test_a_running_footer_is_not_content(self) -> None:
        published, rejected, _ = _detect(self._pages("Apple Inc. | 2025 Form 10-K | 7"))
        self.assertNotIn("2025", published)
        self.assertEqual({_label(item) for item in rejected}, {"page-furniture"})

    def test_a_period_caption_survives_repeating_on_every_page(self) -> None:
        published, _, _ = _detect(self._pages("As of December 31, 2025"))
        self.assertEqual(published.count("December 31, 2025"), 4)

    def test_a_row_printed_twice_on_a_page_is_content_not_furniture(self) -> None:
        published, _, _ = _detect(self._pages("Deposit 50% Down Payment 1,500", copies_per_page=2))
        self.assertEqual(published.count("1,500"), 8)

    def test_a_column_of_bare_figures_does_not_convict_itself(self) -> None:
        # Every numeric cell normalizes to the same text; only position and the
        # surviving words may distinguish furniture from data.
        pages = [_line("4,058.00", y=0.95, line_index=0) for _ in range(4)]
        published, _, _ = _detect(pages)
        self.assertEqual(published, ["4,058.00"] * 4)



def _marked_row(
    marker: str,
    body: str,
    *,
    y: float,
    line_index: int,
    marker_x: float = 0.03,
    body_x: float = 0.15,
) -> list[dict]:
    """A list item as geometry delivers one: the ordinal alone in its own cell."""
    return [
        *_cell(marker, y=y, line_index=line_index, x=marker_x),
        *_cell(body, y=y, line_index=line_index + 1, x=body_x),
    ]


def _marked_rows(rows: list[tuple[str, str]], *, start: float = 0.10) -> list[dict]:
    characters: list[dict] = []
    for index, (marker, body) in enumerate(rows):
        characters.extend(
            _marked_row(marker, body, y=start + index * 0.05, line_index=index * 2)
        )
    return characters


class ListMarkerTests(unittest.TestCase):
    """An ordinal that numbers a list item is not a quantity."""

    def test_an_exhibit_column_is_refused_whole(self) -> None:
        published, noise, _ = _detect([_marked_rows([
            ("3.1", "Restated Articles of Incorporation"),
            ("3.2", "Restated Bylaws of the Company"),
            ("4.1", "Description of Registered Securities"),
            ("10.1", "Incentive Compensation Plan"),
        ])])
        self.assertEqual(published, [])
        self.assertEqual(
            [(_label(item), item["text"]) for item in noise],
            [
                ("list-marker", "3.1"),
                ("list-marker", "3.2"),
                ("list-marker", "4.1"),
                ("list-marker", "10.1"),
            ],
        )

    def test_a_column_that_only_ascends_stays_published(self) -> None:
        """Ages beside job titles look identical until you ask how they step."""
        published, _noise, _ = _detect([_marked_rows([
            ("48", "Senior Vice President and Chief Financial Officer"),
            ("50", "Chief Executive Officer of Web Services"),
            ("54", "Senior Vice President of Business Development"),
        ])])
        self.assertEqual(published, ["48", "50", "54"])

    def test_a_numbered_column_beside_figures_stays_published(self) -> None:
        """A marker leads prose. A cell whose neighbour is a figure leads nothing."""
        published, noise, _ = _detect([_marked_rows([
            ("1", "2,400"),
            ("2", "3,100"),
            ("3", "4,800"),
        ])])
        self.assertEqual(
            sorted(published), ["1", "2", "2,400", "3", "3,100", "4,800"]
        )
        self.assertEqual(noise, [])

    def test_an_inline_enumeration_is_refused(self) -> None:
        published, noise, _ = _detect([[
            *_line("Our competitors include: (1) online retailers of", y=0.1, line_index=0),
            *_line("physical goods; (2) publishers of digital media;", y=0.2, line_index=1),
            *_line("and (3) providers of commerce services.", y=0.3, line_index=2),
        ]])
        self.assertEqual(published, [])
        self.assertEqual(
            [_label(item) for item in noise], ["list-marker"] * 3
        )


class FootnoteTests(unittest.TestCase):
    """A footnote's ordinal, and the indicator that points at it."""

    @staticmethod
    def _page() -> list[dict]:
        return [
            *_line("Number of Securities Underlying Options (1)", y=0.10, line_index=0),
            *_line("Total compensation reported for the year (2)", y=0.16, line_index=1),
            *_marked_row(
                "(1)", "Amounts are stated before forfeitures", y=0.60, line_index=2
            ),
            *_marked_row(
                "(2)", "Amounts include the retention bonus paid", y=0.66, line_index=4
            ),
            *_marked_row(
                "(3)", "Amounts exclude the value of health cover", y=0.72, line_index=6
            ),
        ]

    def test_a_footnote_block_and_its_indicators_are_refused(self) -> None:
        published, noise, _ = _detect([self._page()])
        self.assertEqual(published, [])
        self.assertEqual(
            sorted((_label(item), item["text"]) for item in noise),
            [
                ("footnote-marker", "(1)"),
                ("footnote-marker", "(2)"),
                ("footnote-marker", "(3)"),
                ("footnote-reference", "(1)"),
                ("footnote-reference", "(2)"),
            ],
        )

    def test_an_indicator_without_a_footnote_block_stays_published(self) -> None:
        """Nothing may refuse a parenthesised figure on the strength of its shape."""
        published, _noise, _ = _detect([[
            *_line("Number of Securities Underlying Options (1)", y=0.10, line_index=0),
        ]])
        self.assertEqual(published, ["(1)"])

    def test_a_bracketed_negative_in_a_column_survives_the_footnotes(self) -> None:
        published, _noise, _ = _detect([[
            *self._page(),
            *_cell("Total state and local", y=0.30, line_index=8, x=0.03),
            *_cell("(1)", y=0.30, line_index=9, x=0.70),
            *_cell("(2)", y=0.36, line_index=10, x=0.70),
        ]])
        self.assertEqual(published, ["(1)", "(2)"])

    def test_a_bracketed_percentage_is_never_a_marker(self) -> None:
        published, _noise, _ = _detect([[
            *self._page(),
            *_line("Operating margin changed by (5)% over the year", y=0.24, line_index=8),
        ]])
        self.assertEqual(published, ["(5)%"])


def _annotated_row(
    label: str,
    figure: str,
    *,
    y: float,
    line_index: int,
    mark: str = "(1)",
    mark_height: float = 0.007,
) -> list[dict]:
    """A table row whose label carries a footnote mark in a cell of its own.

    `mark_height` is the whole test: a raised mark is an indicator, and the same
    mark at body size is a bracketed negative sitting in a column.
    """
    return [
        *_cell(label, y=y, line_index=line_index, x=0.03),
        *_cell(mark, y=y, line_index=line_index + 1, x=0.10, height=mark_height),
        *_cell(figure, y=y, line_index=line_index + 2, x=0.60),
    ]


class SharedFootnoteTests(unittest.TestCase):
    """One note may answer several rows, and then it has no chain to belong to."""

    @staticmethod
    def _page(*, mark_height: float = 0.007, rows: int = 2) -> list[dict]:
        characters: list[dict] = []
        for index in range(rows):
            characters.extend(
                _annotated_row(
                    "China",
                    "64,377",
                    y=0.10 + index * 0.15,
                    line_index=index * 3,
                    mark_height=mark_height,
                )
            )
        characters.extend(
            _marked_row(
                "(1)",
                "China includes Hong Kong and Taiwan",
                y=0.60,
                line_index=rows * 3,
            )
        )
        return characters

    def test_one_note_answers_every_mark_pointing_at_it(self) -> None:
        published, noise, _ = _detect([self._page()])
        self.assertEqual(published, ["64,377", "64,377"])
        self.assertEqual(
            sorted(_label(item) for item in noise),
            ["footnote-marker", "footnote-reference", "footnote-reference"],
        )

    def test_a_single_mark_is_enough(self) -> None:
        _published, noise, _ = _detect([self._page(rows=1)])
        self.assertEqual(
            sorted(_label(item) for item in noise),
            ["footnote-marker", "footnote-reference"],
        )

    def test_a_full_size_bracketed_figure_is_not_a_mark(self) -> None:
        """The guard: without a raised mark there is no note, and no note here
        means the column keeps its negatives and the paragraph keeps its number."""
        published, noise, _ = _detect([self._page(mark_height=0.012)])
        self.assertEqual(
            sorted(published), ["(1)", "(1)", "(1)", "64,377", "64,377"]
        )
        self.assertEqual(noise, [])

    def test_a_note_nothing_points_at_stays_published(self) -> None:
        published, noise, _ = _detect([_marked_row(
            "(1)", "China includes Hong Kong and Taiwan", y=0.60, line_index=0
        )])
        self.assertEqual(published, ["(1)"])
        self.assertEqual(noise, [])


class SharedDetectorCorpusTests(unittest.TestCase):
    """One geometry-to-model corpus, scored by both recognizers.

    The same file the TypeScript suite reads (`detector-cases.json`) is asserted
    here against the Python engine, so neither recognizer can drift from the
    other without a test on one side failing. The comparison drops derived ids
    and the detector version and materializes every optional layer, through
    `documents.canonical`, which the TypeScript runner mirrors.
    """

    def test_matches_the_shared_corpus(self) -> None:
        cases = json.loads(DETECTOR_FIXTURE.read_text(encoding="utf-8"))
        self.assertGreater(len(cases), 0)
        for case in cases:
            with self.subTest(case=case["name"]):
                model = detect_values(expand_geometry(case["geometry"]))
                self.assertEqual(canonical(model), case["expect"])
