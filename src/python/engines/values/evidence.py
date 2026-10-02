"""Decide which of the three categories a recognized span belongs to.

`spans.py` answers "what shape is this text". This module answers the separate
question: is a well-formed number a value a reader would want to link, printed
data that identifies something, or an artifact of setting the page? A page
number, an area code, a statute year and a footnote marker are all well-formed
and none of them are values -- but only some of them are noise. An area code is
a phone number, and a phone number is printed data an auditor may want to
capture, so it is a reference and stays clickable.

Nothing here promotes; every rule either refuses a span or redirects it. The
verdict it returns travels into the model as the span's category, and the reason
or kind it carries is what the viewer's hover tip explains.
"""
from __future__ import annotations

import re

from .categories import (
    CITATION_YEAR,
    IDENTIFIER,
    NOISE,
    PHONE,
    REFERENCE,
    SUPERSCRIPT,
    UNSUPPORTED,
    VALUE,
)
from .config import (
    CITATION_CONTEXT_FRAGMENT,
    IDENTIFIER_CUES as _CUE_FRAGMENTS,
    NUMBER_MARK_FRAGMENT,
    PERIOD_CONTEXT_FRAGMENT,
    PROSE_WORDS,
)
from .profile import DocumentProfile
from .spans import RecognizedSpan


# A verdict is the category and, with it, the reference kind or the noise reason.
# A value carries neither, because there is nothing further to say about it.
Verdict = tuple[str, str]

KEEP: Verdict = (VALUE, "")


# Only the two phone shapes that cannot be mistaken for data: an explicit country
# code, or a parenthesised area code followed by a subscriber number.
_PHONE = re.compile(
    r"\+\d{1,3}[\s.\-]\d[\d\s.\-]{5,}\d"
    r"|\(\d{3}\)\s?\d{3}[\s.\-]\d{4}"
)

# Words that introduce a reference number rather than a quantity, each mapped to
# what the number it introduces actually identifies. Matched against the text
# before the span so a label cannot condemn the whole line.
_IDENTIFIER_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    *(
        (kind, re.compile(rf"\b(?:{fragment})\b[^.]{{0,30}}$", re.I))
        for kind, fragment in _CUE_FRAGMENTS
    ),
    # The number mark itself, standing apart from the number it names. "#7" is
    # one token and the recognizer refuses it on shape; "No. 7" and "# 7" are
    # two, so the mark has to be read as the cue it is. It must sit immediately
    # before the span -- a mark answers for the next number, not the sentence.
    (IDENTIFIER, re.compile(rf"(?:\b{NUMBER_MARK_FRAGMENT})\s*$", re.I)),
)

# What a figure written like a figure carries on its face: a comma group, a
# percent sign or a decimal fraction. Currency and magnitude are read off the
# span itself rather than its text.
_INTRINSIC_MARK = re.compile(r"[,%]|\.\d")

# Language that puts a number in a period rather than in a sentence.
_PERIOD_CONTEXT = re.compile(
    rf"\b(?:{PERIOD_CONTEXT_FRAGMENT})\b[^.]{{0,20}}$",
    re.I,
)

# How many words a line carries before it is prose rather than a row. A
# statement sets its figures on lines of no words at all -- 726 of 772 on the
# corpus CAFR, 89 of 97 on the invoice -- while a filing's narrative runs well
# past this. The threshold only has to separate those two populations, and the
# gap between them is wide enough that its exact value is not load-bearing.
_PROSE_WORDS = PROSE_WORDS
_WORD = re.compile(r"[^\W\d_]{2,}")

# A year reached through a citation is naming a law, not a period.
_CITATION_CONTEXT = re.compile(
    rf"\b(?:{CITATION_CONTEXT_FRAGMENT})\b[^.]{{0,25}}$",
    re.I,
)


def reference_kind(line: str, start: int, end: int) -> str:
    """The most specific thing a reference-shaped token could be identifying.

    A token the recognizer refused never reaches `classify` -- there is no span
    to classify -- so the cues that would have named it are read here instead.
    An area code inside a printed phone number is a phone, not an unlabelled
    identifier, and saying so is what makes the hover tip worth reading.
    """
    if any(
        start < match.end() and end > match.start()
        for match in _PHONE.finditer(line)
    ):
        return PHONE
    before = line[:start]
    for kind, cue in _IDENTIFIER_CUES:
        if cue.search(before):
            return kind
    return IDENTIFIER


def _unsupported(
    span: RecognizedSpan, *, line: str, start: int, isolated: bool, modifier_follows: bool
) -> bool:
    """Whether a figure has anything at all to say that it measures something.

    Every other rule here refuses a span for something it *is*. This one
    refuses it for what it lacks, so it is deliberately the narrowest: it
    reaches only a bare number -- no currency, no magnitude, no comma group, no
    decimal -- standing in a sentence, with nothing beside it to explain it.

    Four things speak for a number and any one of them is enough:

    * **How it is written.** `1,234`, `$5`, `4.5%` and `1.0 million` are set as
      quantities and are read as quantities wherever they appear -- including
      when the magnitude word wrapped onto the next line, which the caller
      reports as `modifier_follows`.
    * **Where it sits.** A number in its own island of whitespace is a cell in a
      column, whether or not table detection resolved the table around it. This
      is what keeps a statement's figures out of reach of the rule entirely.
    * **What precedes it.** Period language -- `due`, `ended`, `maturing` --
      puts a number in a period rather than in a sentence.

    What is left is `SECTION 13`, `Rule 405` and `See note 2 to the financial
    statements; table 3`: integers a sentence needed, printed in prose, that a
    reader would never link into a workpaper.

    Bare years are deliberately out of scope. `During 2025, the Company
    repurchased ...` is a period anchor and looks identical to a statute year;
    separating those needs the section classifier, not this rule.
    """
    if span.kind != "number":
        return False
    if span.currency or span.magnitude or _INTRINSIC_MARK.search(span.text):
        return False
    if modifier_follows:
        return False
    if isolated:
        return False
    if _PERIOD_CONTEXT.search(line[:start]):
        return False
    return len(_WORD.findall(line)) >= _PROSE_WORDS


def classify(
    span: RecognizedSpan,
    *,
    line: str,
    start: int,
    end: int,
    page_index: int,
    top: float,
    glyph_height: float,
    isolated: bool,
    modifier_follows: bool,
    profile: DocumentProfile,
) -> Verdict:
    """What this span is, and why.

    `start` and `end` locate the span within `line`. They are passed separately
    because a value wrapped across two lines carries the offsets of the fragment
    that appears on this one, not those of the logical span it belongs to.

    A token that could never be read as a value at all -- a form number, a
    product code -- is refused earlier, by `recognize_spans`, and reaches the
    model through its token shape rather than through this stage.

    `isolated` is whether the span sits in its own island of whitespace, which
    is geometry and so is measured by the caller, as `glyph_height` is.
    """
    furniture = profile.furniture_reason(line, page_index, top)
    if furniture:
        return (NOISE, furniture)
    if any(
        start < match.end() and end > match.start()
        for match in _PHONE.finditer(line)
    ):
        return (REFERENCE, PHONE)
    before = line[:start]
    for kind, cue in _IDENTIFIER_CUES:
        if cue.search(before):
            return (REFERENCE, kind)
    if profile.is_superscript(page_index, glyph_height):
        return (NOISE, SUPERSCRIPT)
    if (
        span.kind == "date"
        and span.date_precision == "year"
        and _CITATION_CONTEXT.search(before)
    ):
        return (NOISE, CITATION_YEAR)
    # Last, because it is the only rule that asks what a span lacks: everything
    # above has already had its say about what the span is.
    if _unsupported(
        span, line=line, start=start, isolated=isolated, modifier_follows=modifier_follows
    ):
        return (NOISE, UNSUPPORTED)
    return KEEP
