"""Score value detection: the span oracle, and a per-document report.

With no arguments this runs the span oracle -- the unit-level gate that pins the
exact spans a line of text must produce. Given a PDF it runs the detector over
real geometry and reports what was published, what was suppressed and why, so
two runs can be compared across a change.

    py scripts/score_values.py
    py scripts/score_values.py "test-imports.local/financial-statements/apple 10k.pdf" --write-report output/fs.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = Path(__file__).resolve().parent
for entry in (str(ROOT / "src" / "python"), str(SCRIPT_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from engines.values.spans import recognize_spans  # noqa: E402


ORACLE = ROOT / "src" / "python" / "tests" / "fixtures" / "values" / "span-oracle.json"

# A refusal must never discard a *well-formed value* carrying an unambiguous
# financial mark. Anything caught here is a recall bug, not a tuning question.
#
# The recognizer's own refusals are exempt: it rejects whole tokens, and a token
# like "5,200-acre" carries a comma without ever having been a value. So do the
# spans a tier above claimed whole, which carry whatever punctuation their
# sentence needs.
#
# The exemption is by shape, not by reason. A letter is what spoils "5,200-acre";
# a refusal made of digits and punctuation alone was a figure the recognizer
# could not read, and "$(1,234)" -- the commonest negative in a statement --
# hid behind a blanket partial-token exemption until this gate was narrowed.
_STRONG_MARKS = "$€£¥₹₩,%"

# Structure spans are printed apparatus, not refused values, so they carry
# whatever punctuation their heading or contents row carries. The gate that
# matters for them is that a real figure never lands here -- watched with the
# same currency-and-percent test the reference layer gets.
_STRUCTURE_MARKS = "$€£¥₹₩%"

# The sibling gate, and the reason it exists: categories can lose a value two
# ways now. Noise can swallow one, which the set above watches, and a cue rule
# can redirect one into the reference layer, where nothing would notice it. A
# reference is printed data, so a comma proves nothing -- an identifier is
# full of them -- but a currency symbol or a percent sign on a reference means a
# quantity was misread as a name for something.
_REFERENCE_MARKS = "$€£¥₹₩%"
# A citation is a claimed span rather than a refused value, so it quotes whatever
# the sentence it sits in quotes.
_CLAIMED_REFERENCE_KINDS = frozenset({"note", "item"})


def _spoiled_by_a_letter(candidate: dict) -> bool:
    """Whether a refused token was refused by a letter rather than by damage."""
    return candidate["reason"] == "partial-token" and any(
        character.isalpha() for character in candidate["text"]
    )


def _span_dict(span) -> dict:
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
    return item


def run_oracle() -> int:
    cases = json.loads(ORACLE.read_text(encoding="utf-8"))
    passed = 0
    for case in cases:
        actual = [_span_dict(span) for span in recognize_spans(case["text"])]
        if actual == case["spans"]:
            passed += 1
        else:
            print(json.dumps({"text": case["text"], "expected": case["spans"], "actual": actual}))
    print(json.dumps({"cases": len(cases), "passed": passed, "failed": len(cases) - passed}))
    return 0 if passed == len(cases) else 1


def score_document(pdf: Path) -> dict:
    from table_corpus import geometry_for

    from engines.financial.detector import detect_financial_structure
    from engines.values.detector import DETECTOR_VERSION, detect_values
    from engines.values.lines import prepare

    diagnostics: dict = {}
    document = prepare(geometry_for(pdf.read_bytes()))
    structure = detect_financial_structure(document)
    model = detect_values(document, claims=structure.spans, diagnostics=diagnostics)

    published = [value for page in model["pages"] for value in page["values"]]
    references = [item for page in model["pages"] for item in page.get("references", [])]
    structure_spans = [item for page in model["pages"] for item in page.get("structure", [])]
    rejected = [item for page in model["pages"] for item in page.get("noise", [])]
    page_count = len(model["pages"]) or 1
    suspicious = [
        {"category": "noise", "label": candidate["reason"], "text": candidate["text"]}
        for candidate in rejected
        if not _spoiled_by_a_letter(candidate)
        and any(mark in candidate["text"] for mark in _STRONG_MARKS)
    ] + [
        {"category": "reference", "label": reference["kind"], "text": reference["text"]}
        for reference in references
        if reference["kind"] not in _CLAIMED_REFERENCE_KINDS
        and any(mark in reference["text"] for mark in _REFERENCE_MARKS)
    ] + [
        {"category": "structure", "label": span["kind"], "text": span["text"]}
        for span in structure_spans
        if any(mark in span["text"] for mark in _STRUCTURE_MARKS)
    ]
    return {
        "document": pdf.name,
        "detectorVersion": DETECTOR_VERSION,
        "documentClass": structure.model["documentClass"],
        "pages": len(model["pages"]),
        "published": len(published),
        "publishedPerPage": round(len(published) / page_count, 2),
        "references": len(references),
        "structure": len(structure_spans),
        "rejected": len(rejected),
        "recognized": len(published) + len(references) + len(structure_spans) + len(rejected),
        "publishedByKind": dict(Counter(value["kind"] for value in published)),
        "referencesByKind": dict(Counter(item["kind"] for item in references)),
        "structureByKind": dict(Counter(item["kind"] for item in structure_spans)),
        "clickable": sum(
            1 for span in published + references + structure_spans if span["clickable"]
        ),
        "rejectedByReason": dict(Counter(candidate["reason"] for candidate in rejected)),
        "apparatus": structure.model["apparatus"],
        "notes": len(structure.model["notes"]),
        "noteReferences": len(structure.model["noteReferences"]),
        "items": len(structure.model["items"]),
        "itemReferences": len(structure.model["itemReferences"]),
        "lostValueCandidates": suspicious,
        "diagnostics": {key: value for key, value in sorted(diagnostics.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="*", type=Path, help="score these documents instead of the oracle")
    parser.add_argument("--write-report", type=Path)
    args = parser.parse_args()

    if not args.pdf:
        return run_oracle()

    reports = [score_document(pdf) for pdf in args.pdf]
    for report in reports:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.write_report is not None:
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        args.write_report.write_text(
            json.dumps(reports if len(reports) > 1 else reports[0], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return 1 if any(report["lostValueCandidates"] for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
