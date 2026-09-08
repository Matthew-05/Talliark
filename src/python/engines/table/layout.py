"""Normalized page layout: characters -> tokens -> visual lines -> logical rows.

Every later detection stage reads the page through this module, so they all share
one interpretation of where the words are and what kind of word each one is.
Nothing here decides whether something is a table; it only describes the page.

Coordinates stay in the normalized displayed-page 0-1 space of text-geometry-v1,
and the original character dictionaries are carried on every token so extraction
can still work from real glyph geometry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median

from engines.text_lines import line_bounds, line_groups


# A run of glyphs is one word when the gap to the next glyph is smaller than this
# share of a typical character; it is a new segment (a cell-sized island) when the
# gap is at least `_SEGMENT_GAP_CHARS` characters wide. Segment gaps are what a
# column boundary is made of, so the threshold also has a floor in page terms —
# a proportional font at 6pt would otherwise call every inter-word space a gutter.
_WORD_GAP_CHARS = 0.55
_SEGMENT_GAP_CHARS = 2.5
_MIN_SEGMENT_GAP = 0.012

# A trailing footnote mark rides along with the value it annotates: an exhibit
# number reads "10.1*" and a filing amount reads "1,234†". Without this the token
# has no alphabetic character and no numeric shape, and falls through to "symbol" —
# which then looks like a column of list markers.
_NUMBER = re.compile(
    r"^[(\[]?(?:[-+]?[$€£¥₹]?|[$€£¥₹][-+]?)"
    r"\d[\d, ']*(?:\.\d+)?[)\]]?[*†‡§¹²³]?$"
)
_PERCENT = re.compile(r"^[(\[]?[-+]?\d[\d,]*(?:\.\d+)?[)\]]?\s*%[*†‡§]?$")
_CURRENCY = re.compile(r"^[$€£¥₹]+$")
_PERIOD = re.compile(
    r"^(?:(?:19|20)\d{2}|FY\s*\d{2,4}|Q[1-4](?:\s*(?:19|20)\d{2})?)$",
    re.IGNORECASE,
)
_MONTH = re.compile(
    r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?$",
    re.IGNORECASE,
)
_DATE = re.compile(r"^\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}$")
_BULLET = re.compile(
    "^[•‣⁃▪▫●○■□·∙‐"
    "–—*◦❖➢>-]$"
)
_ORDINAL = re.compile(r"^\(?(?:\d{1,3}|[a-zA-Z]|[ivxlcdmIVXLCDM]{1,7})[.):]$")
_MARKER = re.compile(r"^[$€£¥%()\[\]*†‡§#]+$")

# Token kinds that carry a measurable value rather than a label.
VALUE_KINDS = frozenset({"numeric", "percent", "currency", "period", "date"})


def classify(text: str) -> str:
    """The role a single word plays, judged from its shape alone."""
    stripped = text.strip()
    if not stripped:
        return "blank"
    if _BULLET.match(stripped):
        return "bullet"
    if _CURRENCY.match(stripped):
        return "currency"
    if _PERIOD.match(stripped):
        return "period"
    if _MONTH.match(stripped) or _DATE.match(stripped):
        return "date"
    if _PERCENT.match(stripped):
        return "percent"
    if _NUMBER.match(stripped):
        return "numeric"
    # Ordinals are tested after numbers so that a plain "12" stays numeric and only
    # "12." or "(a)" — a list marker — is read as one.
    if _ORDINAL.match(stripped):
        return "ordinal"
    if _MARKER.match(stripped):
        return "marker"
    if any(character.isalpha() for character in stripped):
        return "word"
    return "symbol"


@dataclass
class TextToken:
    """One word, with the source characters it was built from."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    kind: str
    characters: list[dict] = field(default_factory=list, repr=False)

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def is_value(self) -> bool:
        return self.kind in VALUE_KINDS

    @property
    def is_marker(self) -> bool:
        return self.kind in ("currency", "marker")


@dataclass
class Segment:
    """A whitespace-delimited island of words: the smallest cell-shaped unit."""

    tokens: list[TextToken]

    @property
    def x0(self) -> float:
        return min(token.x0 for token in self.tokens)

    @property
    def x1(self) -> float:
        return max(token.x1 for token in self.tokens)

    @property
    def text(self) -> str:
        return " ".join(token.text for token in self.tokens)

    @property
    def is_marker_only(self) -> bool:
        return all(token.is_marker for token in self.tokens)

    @property
    def kind(self) -> str:
        """The dominant role of the island, ignoring currency and sign markers."""
        meaningful = [token for token in self.tokens if not token.is_marker]
        if not meaningful:
            return "marker"
        if all(token.is_value for token in meaningful):
            return "value"
        if len(meaningful) == 1:
            return meaningful[0].kind
        return "word"


@dataclass
class VisualLine:
    """Every fragment sharing one baseline, tokenized and split into segments."""

    index: int
    tokens: list[TextToken]
    segments: list[Segment]
    x0: float
    y0: float
    x1: float
    y1: float
    source_lines: list[int] = field(default_factory=list, repr=False)

    @property
    def center(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return max(1e-6, self.y1 - self.y0)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def segment_count(self) -> int:
        return len(self.segments)

    @property
    def text(self) -> str:
        return " ".join(segment.text for segment in self.segments)

    def gutters(self) -> list[tuple[float, float]]:
        """The whitespace corridors between this line's segments."""
        return [
            (self.segments[index].x1, self.segments[index + 1].x0)
            for index in range(len(self.segments) - 1)
        ]

    def crosses(self, boundary: float, *, slack: float = 0.0) -> bool:
        """Does a segment straddle this x position rather than sit either side?"""
        return any(
            segment.x0 + slack < boundary < segment.x1 - slack
            for segment in self.segments
        )


@dataclass
class LogicalRow:
    """One table row: a leading visual line plus the lines that wrap into it."""

    lines: list[VisualLine]
    cells: list[str] = field(default_factory=list)
    occupied: tuple[int, ...] = ()
    kind: str = "body"

    @property
    def y0(self) -> float:
        return min(line.y0 for line in self.lines)

    @property
    def y1(self) -> float:
        return max(line.y1 for line in self.lines)

    @property
    def merged(self) -> bool:
        return len(self.lines) > 1

    @property
    def anchor(self) -> VisualLine:
        return self.lines[0]


@dataclass
class PageLayout:
    """The whole page as lines, plus the scale constants every stage needs."""

    page_index: int
    lines: list[VisualLine]
    character_width: float
    line_height: float
    body_left: float
    body_right: float
    # Indices of every line that is part of a running paragraph — the full-width
    # lines and the short last line each paragraph ends on. Filled in by
    # `build_page_layout`, because deciding it needs the lines in order.
    prose_lines: set[int] = field(default_factory=set)

    @property
    def body_width(self) -> float:
        return max(1e-6, self.body_right - self.body_left)

    @property
    def row_gap(self) -> float:
        """How far apart two lines may sit and still belong to one block."""
        return max(0.018, self.line_height * 2.5)

    @property
    def min_gutter(self) -> float:
        return max(_MIN_SEGMENT_GAP, self.character_width * _SEGMENT_GAP_CHARS)

    def lines_within(self, bounds: dict) -> list[VisualLine]:
        top = bounds["y"]
        bottom = bounds["y"] + bounds["height"]
        return [line for line in self.lines if top <= line.center <= bottom]

    def is_prose(self, line: VisualLine) -> bool:
        """Is this line part of a paragraph rather than of a table?"""
        return line.index in self.prose_lines

    def is_paragraph_tail(self, line: VisualLine, previous: VisualLine | None) -> bool:
        """The short last line a paragraph ends on.

        "…was as follows (in" / "thousands, and per-share amounts):" is one
        sentence set across two lines. The second line is too short to read as
        running text on its own, so it used to survive as a caption and get pulled
        into the table below it. What gives it away is that it continues the line
        above at the same indent, on the same tight leading a paragraph uses
        between its own lines, and stops short of the right margin.
        """
        if previous is None or line.segment_count != 1:
            return False
        if line.y0 - previous.y1 > self.line_height * 0.8:
            return False
        if abs(line.x0 - previous.x0) > self.character_width * 2:
            return False
        return line.x1 < previous.x1 - self.character_width

    def is_block_prose(self, line: VisualLine) -> bool:
        """A running-text line that occupies the body column of the page.

        Prose is what ends a table, so the test is deliberately narrow: the line
        has to start where the page's body text starts, run most of the way
        across, carry several words, and read as one continuous run of text. A
        wrapped description inside a table starts at its own cell's indent, so it
        never qualifies — which is what keeps exhibit indexes intact — and a row
        whose second island sits across a wide corridor is a table row, however
        many words its first cell holds. An index of financial statements, whose
        titles fill the page width and whose page numbers hug the right margin,
        was read as prose until that second test was added.
        """
        if line.segment_count > 2:
            return False
        if line.segment_count == 2:
            gutter = max(end - start for start, end in line.gutters())
            # A short island across a wide corridor is an index value, code or
            # jurisdiction rather than the continuation of the sentence on the
            # left. Two substantial islands are instead the two synchronized
            # columns of an editorial page; refusing those here lets narrative
            # flow into candidate generation as if it were a table.
            right = line.segments[1]
            short_right_island = (
                len(right.tokens) <= 3
                and right.x1 - right.x0 <= self.body_width * 0.25
            )
            if gutter >= self.min_gutter * 3 and short_right_island:
                return False
        words = [token for token in line.tokens if token.kind in ("word", "ordinal")]
        if len(words) < 6:
            return False
        if line.x0 > self.body_left + self.character_width * 6:
            return False
        return line.width >= self.body_width * 0.45


def _visible(characters: list[dict]) -> list[dict]:
    return [character for character in characters if str(character.get("char", "")).strip()]


def _measurable(characters: list[dict]) -> list[dict]:
    """Every glyph with real geometry, blanks included.

    Blanks are kept because they are the reliable word separator: a proportional
    font sets the following glyph flush against the space it just drew, so the
    geometric gap between two words is frequently zero. Bounds are still measured
    from the visible glyphs only.
    """
    return [
        character
        for character in characters
        if float(character.get("width", 0.0)) > 0 and float(character.get("height", 0.0)) > 0
    ]


def _merge_same_baseline(records: list[dict]) -> list[dict]:
    """Combine source lines that share one visual baseline.

    Overlap has to dominate. A sparse band (for example a period header sitting
    over the first data line) overlaps the line below it by about half a glyph
    height; fragments of one visual row overlap almost completely. Mirrored in
    web/apps/document-viewer/src/services/table-extractor.ts (shouldMergeRows) —
    keep the two in step.
    """
    ordered = sorted(records, key=lambda item: ((item["y0"] + item["y1"]) / 2, item["x0"]))
    merged: list[dict] = []
    for record in ordered:
        previous = merged[-1] if merged else None
        if previous is not None:
            overlap = max(0.0, min(previous["y1"], record["y1"]) - max(previous["y0"], record["y0"]))
            smaller = min(previous["y1"] - previous["y0"], record["y1"] - record["y0"])
            larger = max(previous["y1"] - previous["y0"], record["y1"] - record["y0"])
            previous_center = (previous["y0"] + previous["y1"]) / 2
            current_center = (record["y0"] + record["y1"]) / 2
            same_row = (
                overlap / smaller >= 0.70
                if smaller > 0
                else abs(previous_center - current_center) <= larger * 0.25
            )
            if same_row:
                previous["x0"] = min(previous["x0"], record["x0"])
                previous["y0"] = min(previous["y0"], record["y0"])
                previous["x1"] = max(previous["x1"], record["x1"])
                previous["y1"] = max(previous["y1"], record["y1"])
                previous["characters"].extend(record["characters"])
                previous["sources"].extend(record["sources"])
                continue
        merged.append(record)
    return merged


def _tokenize(characters: list[dict], word_gap: float) -> list[TextToken]:
    ordered = sorted(characters, key=lambda item: float(item["x"]))
    groups: list[list[dict]] = []
    reach = None
    broken = True
    for character in ordered:
        left = float(character["x"])
        right = left + float(character["width"])
        if not str(character.get("char", "")).strip():
            # The space itself is the separator, and it occupies width, so the
            # next glyph would otherwise look adjacent to the previous word.
            reach = right if reach is None else max(reach, right)
            broken = True
            continue
        if broken or reach is None or left - reach >= word_gap:
            groups.append([character])
        else:
            groups[-1].append(character)
        broken = False
        reach = right if reach is None else max(reach, right)
    tokens: list[TextToken] = []
    for group in groups:
        text = "".join(str(item["char"]) for item in group)
        x0 = min(float(item["x"]) for item in group)
        x1 = max(float(item["x"]) + float(item["width"]) for item in group)
        y0 = min(float(item["y"]) for item in group)
        y1 = max(float(item["y"]) + float(item["height"]) for item in group)
        tokens.append(TextToken(text, x0, y0, x1, y1, classify(text), group))
    return tokens


def _segment(tokens: list[TextToken], segment_gap: float) -> list[Segment]:
    segments: list[Segment] = []
    reach = None
    for token in tokens:
        if reach is None or token.x0 - reach >= segment_gap:
            segments.append(Segment([token]))
        else:
            segments[-1].tokens.append(token)
        reach = token.x1 if reach is None else max(reach, token.x1)
    return segments


def build_page_layout(page_geometry: dict) -> PageLayout:
    """Normalize one text-geometry page into lines of classified tokens."""
    records: list[dict] = []
    for index, characters in enumerate(line_groups(page_geometry)):
        measurable = _measurable(characters)
        measured = line_bounds(measurable)
        if measured is None:
            continue
        x0, y0, x1, y1 = measured
        records.append(
            {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "characters": measurable, "sources": [index]}
        )
    merged = _merge_same_baseline(records)

    widths = [
        float(character["width"])
        for record in merged
        for character in _visible(record["characters"])
    ]
    character_width = median(widths) if widths else 0.005
    heights = [record["y1"] - record["y0"] for record in merged]
    line_height = median(heights) if heights else 0.012
    word_gap = max(character_width * _WORD_GAP_CHARS, 1e-4)
    segment_gap = max(_MIN_SEGMENT_GAP, character_width * _SEGMENT_GAP_CHARS)

    lines: list[VisualLine] = []
    for index, record in enumerate(merged):
        tokens = _tokenize(record["characters"], word_gap)
        if not tokens:
            continue
        lines.append(
            VisualLine(
                index=index,
                tokens=tokens,
                segments=_segment(tokens, segment_gap),
                x0=record["x0"],
                y0=record["y0"],
                x1=record["x1"],
                y1=record["y1"],
                source_lines=sorted(record["sources"]),
            )
        )

    if lines:
        lefts = sorted(line.x0 for line in lines)
        rights = sorted(line.x1 for line in lines)
        # Percentiles, not extremes: a page number or a stray mark must not define
        # where the body of the page begins.
        body_left = lefts[max(0, int(len(lefts) * 0.10))]
        body_right = rights[min(len(rights) - 1, int(len(rights) * 0.90))]
    else:
        body_left, body_right = 0.0, 1.0

    layout = PageLayout(
        page_index=int(page_geometry.get("pageIndex", 0)),
        lines=lines,
        character_width=character_width,
        line_height=line_height,
        body_left=body_left,
        body_right=body_right,
    )
    # Walk the page once to mark paragraphs. A tail is only recognized directly
    # below a full-width line, never below another tail, so a caption that happens
    # to follow a paragraph closely cannot be swallowed by the same rule.
    previous: VisualLine | None = None
    after_block = False
    for line in lines:
        if layout.is_block_prose(line):
            layout.prose_lines.add(line.index)
            after_block = True
        elif after_block and layout.is_paragraph_tail(line, previous):
            layout.prose_lines.add(line.index)
            after_block = False
        else:
            after_block = False
        previous = line
    return layout
