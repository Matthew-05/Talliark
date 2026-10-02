"""Rebuild the shared value-detector corpus.

This is the cross-runtime golden: a set of geometry cases and the model the
reference (Python) engine publishes for each. Both the Python suite
(`tests/test_values.py`) and the TypeScript recognizer suite
(`src/web/packages/shared/test/`) score against the same file, so the port
cannot drift from the reference without failing a test on one side.

The expected models are generated here and hand-reviewed -- the generator never
blesses a case automatically; it only writes what the reference currently does.
Run from the repo root:

    py scripts/make_value_fixtures.py
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src" / "python"), str(ROOT / "src" / "python" / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from documents import canonical, cell, compact_geometry, expand_geometry, geometry, line  # noqa: E402
from engines.values.detector import detect_values  # noqa: E402

FIXTURE = ROOT / "src" / "python" / "tests" / "fixtures" / "values" / "detector-cases.json"


def placed(text: str, x: float, y: float, line_index: int) -> list[dict]:
    return [
        {"char": char, "x": x + index * 0.008, "y": y, "width": 0.008, "height": 0.012, "lineIndex": line_index}
        for index, char in enumerate(text)
    ]


def marked_row(marker: str, body: str, *, y: float, line_index: int, marker_x: float = 0.03, body_x: float = 0.15) -> list[dict]:
    return [*cell(marker, y=y, line_index=line_index, x=marker_x), *cell(body, y=y, line_index=line_index + 1, x=body_x)]


def marked_rows(rows: list[tuple[str, str]], *, start: float = 0.10) -> list[dict]:
    characters: list[dict] = []
    for index, (marker, body) in enumerate(rows):
        characters.extend(marked_row(marker, body, y=start + index * 0.05, line_index=index * 2))
    return characters


def annotated_row(label: str, figure: str, *, y: float, line_index: int, mark: str = "(1)", mark_height: float = 0.007) -> list[dict]:
    return [
        *cell(label, y=y, line_index=line_index, x=0.03),
        *cell(mark, y=y, line_index=line_index + 1, x=0.10, height=mark_height),
        *cell(figure, y=y, line_index=line_index + 2, x=0.60),
    ]


def footnote_page() -> list[dict]:
    return [
        *line("Number of Securities Underlying Options (1)", y=0.10, line_index=0),
        *line("Total compensation reported for the year (2)", y=0.16, line_index=1),
        *marked_row("(1)", "Amounts are stated before forfeitures", y=0.60, line_index=2),
        *marked_row("(2)", "Amounts include the retention bonus paid", y=0.66, line_index=4),
        *marked_row("(3)", "Amounts exclude the value of health cover", y=0.72, line_index=6),
    ]


def shared_footnote_page(*, mark_height: float = 0.007, rows: int = 2) -> list[dict]:
    characters: list[dict] = []
    for index in range(rows):
        characters.extend(annotated_row("China", "64,377", y=0.10 + index * 0.15, line_index=index * 3, mark_height=mark_height))
    characters.extend(marked_row("(1)", "China includes Hong Kong and Taiwan", y=0.60, line_index=rows * 3))
    return characters


def furniture_pages(footer: str, *, copies_per_page: int = 1) -> list[list[dict]]:
    pages: list[list[dict]] = []
    for page_index in range(4):
        characters = line(f"Net sales of 1,{page_index}00 in the period", y=0.10, line_index=0)
        for copy in range(copies_per_page):
            characters += line(footer, y=0.95 + copy * 0.01, line_index=1 + copy)
        pages.append(characters)
    return pages


def build_cases() -> list[dict]:
    cases: list[dict] = []

    def add(name: str, pages: list[list[dict]], note: str = "") -> None:
        cases.append({"name": name, "note": note, "geometry": compact_geometry(geometry(pages))})

    add("context-and-currency", [[
        *line("Amounts in millions USD  December 31, 2025  $ 1,200", y=0.10, line_index=0, height=0.02),
    ]], "A period and a quantity, and the currency and scale they imply.")

    add("wrapped-magnitude-across-pages", [
        [*line("$1.0", y=0.10, line_index=0)],
        [*line("million in revenue", y=0.10, line_index=0)],
    ], "A magnitude that wrapped onto the next page finishes the number before it.")

    wrapped_date = []
    for x, head, year in ((0.20, "September 27,", "2025"), (0.45, "September 28,", "2024"), (0.70, "September 30,", "2023")):
        wrapped_date.extend(placed(head, x, 0.10, 0))
        wrapped_date.extend(placed(year, x + 0.036, 0.11, 1))
    add("wrapped-date-headers", [wrapped_date], "A month/day head and the year beneath it join into one date.")

    add("distant-year-not-attached", [
        [*placed("September 27,", 0.2, 0.10, 0), *placed("2025", 0.236, 0.20, 1)],
    ], "A year too far below the head does not finish it.")

    add("phone-not-negative", [[*line("Cupertino, California (408) 996-1010", y=0.1, line_index=0)]], "An area code is a reference, never a bracketed negative.")
    add("citation-year", [[*line("of the Securities Exchange Act of 1934", y=0.1, line_index=0)]], "A year reached through a citation names a law, not a period.")
    add("period-year-survives", [[*line("for the fiscal year ended 2025", y=0.1, line_index=0)]], "Period language speaks for the year after it.")

    add("superscript-footnote", [[
        *line("Total net sales were 1,234 in the period", y=0.10, line_index=0),
        *line("(1)", y=0.20, line_index=1, height=0.006),
    ]], "A mark set smaller than its page is a superscript, not a negative.")

    add("identifier-label-scope", [[*line("Total 1,234 CUSIP 037833100", y=0.1, line_index=0)]], "A cue condemns only the number after it.")
    add("number-mark", [[*line("Invoice No. 00550 totalling 1,234", y=0.1, line_index=0)]], "A number mark names the number beside it.")
    add("number-mark-answers-only-next", [[*line("Nos. 3 and 4 were 1,234", y=0.1, line_index=0)]], "A mark answers for the next number, not the sentence.")

    add("unsupported-prose", [[*line("Indicate by check mark whether the Registrant is an issuer as defined in Rule 405", y=0.1, line_index=0)]], "A bare integer in a sentence, with nothing saying it measures.")
    add("figure-survives-prose", [[*line("The aggregate market value of the shares held was $3,253,431 on that date", y=0.1, line_index=0)]], "A figure written as a figure survives in prose.")
    add("isolated-cell", [[
        *cell("Weighted average shares outstanding, basic and diluted", y=0.1, line_index=0, x=0.02),
        *cell("166", y=0.1, line_index=0, x=0.70),
    ]], "A number in its own cell is never unsupported.")

    add("modifier-next-line", [[
        *line("wrapping number value modifiers. This is an example of a document with 1", y=0.10, line_index=0),
        *line("million modifiers. This is an example of a document with wrapping values", y=0.12, line_index=1),
    ]], "A modifier on the next line speaks for the number before it.")
    add("bare-year-in-prose", [[*line("During 2025, the Company repurchased shares of its common stock", y=0.1, line_index=0)]], "A bare year in prose stays a value.")

    add("page-furniture-footer", furniture_pages("Apple Inc. | 2025 Form 10-K | 7"), "A running footer is furniture, not content.")
    add("period-caption-survives", furniture_pages("As of December 31, 2025"), "A period caption repeating on every page must stay readable.")
    add("row-twice-on-page", furniture_pages("Deposit 50% Down Payment 1,500", copies_per_page=2), "A row printed twice on one page is content.")
    add("bare-figures-column", [[*line("4,058.00", y=0.95, line_index=0)] for _ in range(4)], "A column of bare figures does not convict itself.")

    add("exhibit-column", [marked_rows([
        ("3.1", "Restated Articles of Incorporation"),
        ("3.2", "Restated Bylaws of the Company"),
        ("4.1", "Description of Registered Securities"),
        ("10.1", "Incentive Compensation Plan"),
    ])], "An exhibit column is refused whole.")
    add("ascending-ages-stay-values", [marked_rows([
        ("48", "Senior Vice President and Chief Financial Officer"),
        ("50", "Chief Executive Officer of Web Services"),
        ("54", "Senior Vice President of Business Development"),
    ])], "A column that only ascends is not a list.")
    add("numbered-column-beside-figures", [marked_rows([
        ("1", "2,400"),
        ("2", "3,100"),
        ("3", "4,800"),
    ])], "A marker leads prose; a cell whose neighbour is a figure leads nothing.")
    add("inline-enumeration", [[
        *line("Our competitors include: (1) online retailers of", y=0.1, line_index=0),
        *line("physical goods; (2) publishers of digital media;", y=0.2, line_index=1),
        *line("and (3) providers of commerce services.", y=0.3, line_index=2),
    ]], "An inline enumeration is refused.")

    add("footnote-block", [footnote_page()], "A footnote block and its indicators are refused.")
    add("bracketed-negative-survives", [[
        *footnote_page(),
        *cell("Total state and local", y=0.30, line_index=8, x=0.03),
        *cell("(1)", y=0.30, line_index=9, x=0.70),
        *cell("(2)", y=0.36, line_index=10, x=0.70),
    ]], "A bracketed negative in a column survives the footnotes.")
    add("bracketed-percent-not-marker", [[
        *footnote_page(),
        *line("Operating margin changed by (5)% over the year", y=0.24, line_index=8),
    ]], "A bracketed percentage is never a marker.")

    add("shared-footnote", [shared_footnote_page()], "One note answers every mark pointing at it.")
    add("full-size-bracket-not-mark", [shared_footnote_page(mark_height=0.012)], "A full-size bracketed figure is not a mark.")

    add("form-number", [[*line("FORM 10-K", y=0.1, line_index=0)]], "A form number is a reference, not a value.")
    add("fragmented-figure", [[*line("1,2 34", y=0.1, line_index=0)]], "A fragmented figure is reported rather than guessed.")

    return cases


def serialize(cases: list[dict]) -> str:
    """One case per block, with the geometry inline and the expected model indented.

    The geometry is machine-generated and never read by hand, so keeping it on a
    single compact line stops the fixture from doubling in size; the expected
    model is what a reviewer reads.
    """
    blocks = []
    for case in cases:
        geometry_json = json.dumps(case["geometry"], separators=(",", ":"), ensure_ascii=False)
        expect_json = textwrap.indent(json.dumps(case["expect"], indent=2, ensure_ascii=False), "    ")
        blocks.append("\n".join([
            "  {",
            f'    "name": {json.dumps(case["name"], ensure_ascii=False)},',
            f'    "note": {json.dumps(case["note"], ensure_ascii=False)},',
            f'    "geometry": {geometry_json},',
            f'    "expect": {expect_json}',
            "  }",
        ]))
    return "[\n" + ",\n".join(blocks) + "\n]\n"


def main() -> int:
    cases = build_cases()
    for case in cases:
        case["expect"] = canonical(detect_values(expand_geometry(case["geometry"])))
    FIXTURE.write_text(serialize(cases), encoding="utf-8")
    print(f"[Talliark] wrote {len(cases)} value-detector cases to {FIXTURE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
