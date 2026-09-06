"""Finding construction: the sentence, the arithmetic, and the spans.

Everything in the first pass serves one sentence: *these five rows sum to
12,345, the total row reads 12,354, and the difference of 9 is a digit
transposition.* A finding that cannot be restated that way does not ship.

A finding carries what raised it, the sentence, the arithmetic, and the spans --
each by `spanId`, with its page and bounds, so the window can take a reviewer to
it. Span ids are content-addressed, which is what lets a finding survive a
detector upgrade; the honest limit stands, that they are **not** stable across a
re-OCR that moves geometry.

Dispositions -- open, reviewed, explained, accepted -- are deliberately not here.
Where they are stored and how they survive a re-scan is the same question as the
staleness rule, and both belong to the home screen work.

R-2 fills this in.
"""
from __future__ import annotations


# A nominated total with no candidate run is *unresolved, no candidate run*. It
# is not an accusation, and neither this module nor the window may word it as
# one.
KINDS: tuple[str, ...] = (
    "footing-break",
    "footing-unresolved",
    "cross-foot-break",
    "ruling-disagreement",
)


# --- R-2 -------------------------------------------------------------------
# build(total, run, cells) -> dict   the finding, its sentence and its spans
