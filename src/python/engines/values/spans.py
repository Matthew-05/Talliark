"""Recognize financial value spans without depending on PDF or OCR libraries."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .categories import ALPHANUMERIC, IDENTIFIER, PARTIAL_TOKEN
from .config import (
    CONFIDENCE,
    CURRENCY_CODE_PATTERN,
    CURRENCY_CODES,
    CURRENCY_SYMBOL_PATTERN,
    CURRENCY_SYMBOLS,
    MAGNITUDE_PATTERN,
    MAGNITUDES,
    MONTH_PATTERN,
    MONTHS,
)


@dataclass(frozen=True)
class RecognizedSpan:
    start: int
    end: int
    kind: str
    text: str
    confidence: float
    normalized_value: str = ""
    currency: str = ""
    date_precision: str = ""
    date_order: str = ""
    magnitude: int = 0


@dataclass(frozen=True)
class TextFragment:
    start: int
    end: int
    text: str


_DATE_PATTERNS = (
    (re.compile(rf"\b(?P<month>{MONTH_PATTERN})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s+(?P<year>(?:19|20)\d{{2}})\b", re.I), "mdy"),
    (re.compile(rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month>{MONTH_PATTERN})\.?\s*,?\s+(?P<year>(?:19|20)\d{{2}})\b", re.I), "dmy"),
    (re.compile(r"\b(?P<year>(?:19|20)\d{2})[-/.](?P<month>0?[1-9]|1[0-2])[-/.](?P<day>0?[1-9]|[12]\d|3[01])\b"), "ymd"),
    (re.compile(r"(?<![\d.])(?P<a>0?[1-9]|[12]\d|3[01])[/.-](?P<b>0?[1-9]|[12]\d|3[01])[/.-](?P<year>(?:19|20)?\d{2})(?![\d.])"), "numeric"),
    (re.compile(rf"\b(?P<month>{MONTH_PATTERN})\.?\s+(?P<year>(?:19|20)\d{{2}})\b", re.I), "month"),
    (re.compile(r"\b(?:FY\s*)?(?P<year>(?:19|20)\d{2})\s*(?:Q(?P<q1>[1-4]))\b|\bQ(?P<q2>[1-4])\s*(?:FY\s*)?(?P<year2>(?:19|20)\d{2})\b", re.I), "quarter"),
    (re.compile(r"\b(?:FY\s*)?(?P<year>(?:19|20)\d{2})\b", re.I), "year"),
)

_WRAPPED_MONTH_DAY_RE = re.compile(
    rf"\b(?:{MONTH_PATTERN})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?"
    rf"(?!\s*,?\s*(?:19|20)\d{{2}}\b)\s*,?",
    re.I,
)
_WRAPPED_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

_NUMBER_RE = re.compile(
    r"(?<![\w\d])"
    # A currency mark is printed outside the bracket as often as inside it:
    # "$(1,234)" and "$ (1,234)" are the same amount as "($1,234)".
    rf"(?:(?P<lead_code>{CURRENCY_CODE_PATTERN})\s*|(?P<lead_symbol>{CURRENCY_SYMBOL_PATTERN})\s*)?"
    r"(?P<open>\()?\s*"
    rf"(?:(?P<code>{CURRENCY_CODE_PATTERN})\s*|(?P<symbol>{CURRENCY_SYMBOL_PATTERN})\s*)?"
    # A sign binds to the figure it signs. Nothing may stand between them: a
    # dash set off by a space is separating two figures, and "8.5% - 9.0%" is a
    # range whose second half is positive nine.
    r"(?P<sign>[+\-\u2212])?"
    r"(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)"
    # A bracketed negative percentage closes before its sign: "(4)%". The
    # lookahead keeps this alternative from claiming the ordinary "(1,234)"
    # closer, which the group at the end of the pattern still owns.
    r"\s*(?P<close_percent>\)(?=\s*(?:%|percent\b)))?"
    r"\s*(?P<percent>%|percent\b)?\s*"
    rf"(?P<magnitude>{MAGNITUDE_PATTERN})?\s*"
    # A code printed after the figure. Case-sensitive here, unlike the leading
    # position, because "12 CAD" is Canadian dollars and "12 cad" is a word the
    # sentence needed; and never when a hyphen makes it a compound adjective,
    # which would otherwise cut "USD-denominated" in half and lose the figure.
    rf"(?:(?P<trail_code>(?-i:{CURRENCY_CODE_PATTERN}))\b(?!-)\s*)?"
    r"(?P<close>\))?"
    r"(?![\w\d])",
    re.I,
)

_MAGNITUDE_PREFIX_RE = re.compile(rf"^\s*(?P<magnitude>{MAGNITUDE_PATTERN})\b", re.I)

# A hyphen, slash or colon binding two alphanumeric runs together makes an
# identifier, not an arithmetic expression: "10-K", "001-36743", "3:13 PM",
# "ASU 2024-03", "123-456-7890".
_JOIN_CHARACTERS = "-/:"

# Punctuation that may sit between a value and the edge of its token without
# being part of it: sentence punctuation, quotes and footnote marks. Anything
# else left over means the match cut a token in half.
_TRIMMABLE = frozenset(".,;:!?\"'\u2018\u2019\u201c\u201d()[]*\u2020\u2021")

_TOKEN_RE = re.compile(r"\S+")

# An en or em dash standing between two figures separates them, and a hyphen
# never does: no identifier is written with an en dash, so "2024–2025" and "7%–9%"
# are two figures each while "10-K" and "001-36743" stay one token and stay
# refused. Between a figure and a word the same dash is punctuation holding a
# sentence together -- "page 55—Entertainment" -- and the token stays whole, so the
# cross-reference is not read as the quantity fifty-five.
_FIGURE_DASH = re.compile(
    rf"(?<=[\d%)])[\u2013\u2014](?=[\d({CURRENCY_SYMBOL_PATTERN[1:-1]}])"
)


@dataclass(frozen=True)
class RejectedToken:
    """A token that held something value-shaped but did not parse in full."""

    start: int
    end: int
    text: str
    reason: str


def token_spans(text: str) -> list[tuple[int, int]]:
    """The units a value must align to.

    Runs of non-whitespace, split again wherever a dash stands between two
    figures rather than binding one token together.
    """
    spans: list[tuple[int, int]] = []
    for match in _TOKEN_RE.finditer(text):
        start = match.start()
        for dash in _FIGURE_DASH.finditer(match.group(0)):
            spans.append((start, match.start() + dash.start()))
            start = match.start() + dash.end()
        spans.append((start, match.end()))
    return spans


# "#7" and "№7" name a thing rather than count one.
_REFERENCE_MARKS = "#\u2116"

# The two characters a true minus sign is written with.
_MINUS = frozenset("-\u2212")


def token_shape(token: str) -> str:
    """Why a token could never be a value, from its shape alone."""
    if any(character in _REFERENCE_MARKS for character in token):
        return IDENTIFIER
    for index, character in enumerate(token):
        if (
            character in _JOIN_CHARACTERS
            and index > 0
            and token[index - 1].isalnum()
            and index + 1 < len(token)
            and token[index + 1].isalnum()
        ):
            return IDENTIFIER
    if any(c.isalpha() for c in token) and any(c.isdigit() for c in token):
        return ALPHANUMERIC
    return PARTIAL_TOKEN


def cut_token(
    text: str, tokens: list[tuple[int, int]], start: int, end: int
) -> tuple[int, int] | None:
    """The first token this match cuts through, or None when it aligns.

    A value has to claim whole tokens. "$1,234." aligns because only a full stop
    is left over; "123" inside "123-456-7890" does not, and that is what keeps a
    phone number, a form number and an accounting standard from being read as
    arithmetic.
    """
    for token_start, token_end in tokens:
        if token_end <= start or token_start >= end:
            continue
        outside = text[token_start:max(token_start, start)] + text[min(token_end, end):token_end]
        if any(character not in _TRIMMABLE for character in outside):
            return (token_start, token_end)
    return None


def _month_number(value: str) -> int:
    return MONTHS[value.rstrip(".").lower()]


def _valid_date(year: int, month: int, day: int) -> bool:
    try:
        calendar.monthrange(year, month)[1]
        return 1 <= day <= calendar.monthrange(year, month)[1]
    except (ValueError, IndexError):
        return False


def _date_span(match: re.Match[str], mode: str) -> RecognizedSpan | None:
    groups = match.groupdict()
    if mode == "quarter":
        year = int(groups.get("year") or groups.get("year2") or 0)
        quarter = int(groups.get("q1") or groups.get("q2") or 0)
        normalized = f"{year:04d}-Q{quarter}"
        precision, order = "quarter", "ymd"
    elif mode == "year":
        year = int(groups["year"])
        normalized, precision, order = f"{year:04d}", "year", "ymd"
    elif mode == "month":
        year, month = int(groups["year"]), _month_number(groups["month"])
        normalized, precision, order = f"{year:04d}-{month:02d}", "month", "mdy"
    elif mode == "numeric":
        first, second = int(groups["a"]), int(groups["b"])
        year_text = groups["year"]
        year = int(year_text) + (2000 if len(year_text) == 2 and int(year_text) < 70 else 1900 if len(year_text) == 2 else 0)
        if first <= 12 and second <= 12:
            normalized, order = "", "ambiguous"
        elif first <= 12:
            month, day, order = first, second, "mdy"
            if not _valid_date(year, month, day):
                return None
            normalized = f"{year:04d}-{month:02d}-{day:02d}"
        else:
            day, month, order = first, second, "dmy"
            if not _valid_date(year, month, day):
                return None
            normalized = f"{year:04d}-{month:02d}-{day:02d}"
        precision = "day"
    else:
        year = int(groups["year"])
        month = int(groups["month"]) if groups["month"].isdigit() else _month_number(groups["month"])
        day = int(groups["day"])
        if not _valid_date(year, month, day):
            return None
        normalized, precision, order = f"{year:04d}-{month:02d}-{day:02d}", "day", mode
    return RecognizedSpan(
        match.start(), match.end(), "date", match.group(0),         CONFIDENCE["dateDay"] if precision == "day" else CONFIDENCE["dateOther"],
        normalized, date_precision=precision, date_order=order,
    )


def _overlaps(start: int, end: int, occupied: list[tuple[int, int]]) -> bool:
    return any(start < right and end > left for left, right in occupied)


def _canonical_decimal(text: str, negative: bool) -> str:
    try:
        value = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return ""
    if negative:
        value = -abs(value)
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _scaled_decimal(text: str, negative: bool, magnitude: int) -> str:
    canonical = _canonical_decimal(text, negative)
    if not canonical or not magnitude:
        return canonical
    return _canonical_decimal(str(Decimal(canonical) * magnitude), negative=False)


def recognize_magnitude_prefix(text: str) -> tuple[int, str, int] | None:
    """Return the first magnitude token when it begins the supplied line."""
    match = _MAGNITUDE_PREFIX_RE.match(text)
    if match is None:
        return None
    modifier = match.group("magnitude")
    return match.end(), modifier, MAGNITUDES[modifier.lower()]


def wrapped_date_heads(text: str) -> list[TextFragment]:
    """Month/day fragments that still need a year from the line below."""
    return [
        TextFragment(match.start(), match.end(), match.group(0).strip())
        for match in _WRAPPED_MONTH_DAY_RE.finditer(text)
    ]


def wrapped_date_years(text: str) -> list[TextFragment]:
    """Four-digit year fragments that can finish a wrapped month/day."""
    return [TextFragment(match.start(), match.end(), match.group(0)) for match in _WRAPPED_YEAR_RE.finditer(text)]


def recognize_wrapped_date(head: str, year: str) -> RecognizedSpan | None:
    """Build the ordinary date span produced when two visual fragments are joined."""
    combined = f"{head.rstrip()} {year.strip()}"
    return next(
        (span for span in recognize_spans(combined) if span.kind == "date" and span.date_precision == "day"),
        None,
    )


def recognize_spans(
    text: str,
    *,
    rejected: list[RejectedToken] | None = None,
) -> list[RecognizedSpan]:
    """Return non-overlapping date/percent/number spans in source order.

    A span must align to token boundaries. Pass `rejected` to collect the tokens
    that held something value-shaped and were refused for cutting across one --
    a phone number, a form number, an OCR-fragmented figure. Each offending
    token is reported once, whole, rather than once per sub-match.
    """
    results: list[RecognizedSpan] = []
    occupied: list[tuple[int, int]] = []
    tokens = token_spans(text)
    reported: set[tuple[int, int]] = set()

    def cuts_a_token(start: int, end: int) -> bool:
        token = cut_token(text, tokens, start, end)
        if token is None:
            return False
        if rejected is not None and token not in reported:
            reported.add(token)
            body = text[token[0]:token[1]]
            rejected.append(RejectedToken(token[0], token[1], body, token_shape(body)))
        occupied.append((start, end))
        return True

    for pattern, mode in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            if _overlaps(match.start(), match.end(), occupied):
                continue
            span = _date_span(match, mode)
            if span is None:
                continue
            if cuts_a_token(span.start, span.end):
                continue
            results.append(span)
            occupied.append((span.start, span.end))

    for match in _NUMBER_RE.finditer(text):
        start, end = match.span()
        if _overlaps(start, end, occupied):
            continue
        open_paren = match.group("open")
        close_paren = match.group("close") or match.group("close_percent")
        if bool(open_paren) != bool(close_paren):
            # Trim unmatched optional punctuation rather than claiming prose parens.
            if open_paren:
                # The opener belongs to the sentence, not to the figure:
                # "($748 million after tax)" closes long after the amount ends.
                # The currency printed inside it still belongs to the figure.
                start = min(
                    position
                    for position in (match.start("code"), match.start("symbol"), match.start("number"))
                    if position >= 0
                )
            else:
                # A closer with no opener still leaves the percentage behind it.
                end = match.end("percent") if match.group("percent") else match.end("number")
        number_text = match.group("number")
        negative = match.group("sign") in _MINUS or bool(open_paren and close_paren)
        magnitude_text = match.group("magnitude") or ""
        magnitude = MAGNITUDES.get(magnitude_text.lower(), 0)
        normalized = _scaled_decimal(number_text, negative, magnitude)
        if not normalized:
            continue
        # Whichever position it was printed in, there is one currency here.
        code = (match.group("code") or match.group("lead_code") or match.group("trail_code") or "").upper()
        symbol = match.group("symbol") or match.group("lead_symbol") or ""
        currency = CURRENCY_CODES.get(code, "") or CURRENCY_SYMBOLS.get(symbol, "")
        percent = bool(match.group("percent"))
        if percent and magnitude:
            # A magnitude following "percent" belongs to surrounding prose, not the percentage.
            magnitude = 0
            normalized = _canonical_decimal(number_text, negative)
            end = match.end("percent")
        if cuts_a_token(start, end):
            continue
        confidence = (
            CONFIDENCE["numberPercentOrCurrency"] if percent or currency
            else CONFIDENCE["numberComma"] if "," in number_text
            else CONFIDENCE["numberDecimal"] if "." in number_text
            else CONFIDENCE["numberBare"]
        )
        results.append(RecognizedSpan(
            start, end, "percent" if percent else "number", text[start:end].strip(), confidence,
            normalized, magnitude=magnitude, currency=currency,
        ))
        occupied.append((start, end))

    return sorted(results, key=lambda span: (span.start, span.end))
