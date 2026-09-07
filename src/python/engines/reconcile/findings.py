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
"""
from __future__ import annotations

import hashlib
from decimal import Decimal


# A nominated total with no candidate run is *unresolved, no candidate run*. It
# is not an accusation, and neither this module nor the window may word it as
# one.
KINDS: tuple[str, ...] = (
    "footing-break",
    "footing-unresolved",
    "cross-foot-break",
    "ruling-disagreement",
)

# Which unresolved totals are worth saying out loud. `no-candidate-run` and
# `run-too-short` describe what the scan could reach, not what the document did,
# and a filing yields hundreds of them -- surfacing them as findings would bury
# the breaks under the scan's own limits. `summary.unresolved` counts every one,
# and each total carries its own outcome, so nothing is hidden by staying quiet.
# `no-plausible-run` is different: a full, decimal-agreeing block stood above the
# cell and missed by more than the total itself, which is worth a look.
SPOKEN_UNRESOLVED: frozenset[str] = frozenset({"no-plausible-run"})


def finding_id(kind: str, table_id: str, total_id: str, arithmetic: str) -> str:
    """A finding's identity, derived rather than assigned.

    Hashed over what the finding says rather than the order it was found in, so
    a re-scan that reaches the same conclusion reaches the same id -- which is
    what a disposition will eventually anchor to.
    """
    key = "|".join((kind, table_id, total_id, arithmetic))
    return "fnd-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _amount(text: str, decimals: int) -> str:
    """A decimal set the way the page sets it, so a reviewer can match it."""
    value = Decimal(text)
    return f"{value:,.{decimals}f}"


def _column(label: str) -> str:
    """Which column the finding is about, when the table names its columns.

    A comparative statement prints the same labelled total once per period, so a
    sentence that names only the row says the same thing three times and leaves
    a reviewer to guess which figure it meant.
    """
    named = " ".join((label or "").split())
    return f", in the {named} column" if named else ""


def _rows(count: int) -> str:
    words = {
        2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
        8: "eight", 9: "nine", 10: "ten",
    }
    return f"{words.get(count, str(count))} rows"


def _span(cell: dict, page_index: int, role: str) -> dict | None:
    if "spanId" not in cell:
        return None
    return {
        "spanId": cell["spanId"],
        "pageIndex": page_index,
        "bounds": cell["bounds"],
        "role": role,
    }


def build_break(
    *,
    total_cell: dict,
    run: dict,
    addends: list[dict],
    table_id: str,
    total_id: str,
    page_index: int,
    decimals: int,
    column_label: str = "",
) -> dict | None:
    """A run that should have footed and did not.

    The sentence names the rows, the sum they make, what the total reads, and
    the difference -- in that order, because that is the order a reviewer checks
    them in.
    """
    label = total_cell.get("rowLabel") or total_cell.get("text", "")
    delta = Decimal(run["delta"])
    sentence = (
        f'On page {page_index + 1}{_column(column_label)}, the {_rows(len(addends))} above "{label}" sum to '
        f'{_amount(run["sum"], decimals)}, the total row reads '
        f'{_amount(total_cell["normalizedValue"], decimals)}, and the difference is '
        f'{_amount(str(delta.copy_abs()), decimals)}'
    )
    diagnosis = run.get("diagnosis")
    sentence += f' -- {diagnosis["detail"]}.' if diagnosis and diagnosis.get("detail") else "."

    spans = [_span(total_cell, page_index, "total")]
    spans += [_span(cell, page_index, "addend") for cell in addends]
    spans = [span for span in spans if span is not None]
    if not spans:
        return None
    return {
        "id": finding_id("footing-break", table_id, total_id, f'{run["sum"]}|{run["delta"]}'),
        "kind": "footing-break",
        "sentence": sentence,
        "tableId": table_id,
        "totalId": total_id,
        "pageIndex": page_index,
        "spans": spans,
    }


def build_unresolved(
    *,
    total_cell: dict,
    reason: str,
    table_id: str,
    total_id: str,
    page_index: int,
    column_label: str = "",
) -> dict | None:
    """A total the scan could not resolve, worded as the scan's limit.

    Never as a failure of the document: the sentence says what Reconcile could
    not do, and offers the reviewer the one thing it did establish.
    """
    if reason not in SPOKEN_UNRESOLVED:
        return None
    span = _span(total_cell, page_index, "total")
    if span is None:
        return None
    label = total_cell.get("rowLabel") or total_cell.get("text", "")
    sentence = (
        f'On page {page_index + 1}{_column(column_label)}, "{label}" reads as a total, but the rows above it do '
        f"not resemble its addends closely enough for Reconcile to check it. Nothing is "
        f"asserted about the figure either way."
    )
    return {
        "id": finding_id("footing-unresolved", table_id, total_id, reason),
        "kind": "footing-unresolved",
        "sentence": sentence,
        "tableId": table_id,
        "totalId": total_id,
        "pageIndex": page_index,
        "spans": [span],
    }
