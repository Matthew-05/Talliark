"""Create visually faithful 10-K PDFs with one intentional footing error each.

The edits replace a confirmed vertical total with an adjacent-digit
transposition.  Keeping the character count, font, size, origin, and color the
same preserves the table geometry while making the arithmetic miss by a small,
known amount.  The generated manifest records the exact edits and hashes.

Run with the bundled worker Python so PyMuPDF is available:

    src/python/dist/worker/python.exe scripts/make_reconcile_break_fixtures.py
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pymupdf


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "sample-document-corpus" / "financial-statements"
OUTPUT_DIR = ROOT / "output" / "pdf" / "reconcile-break-fixtures"


@dataclass(frozen=True)
class Fixture:
    source: str
    output: str
    page: int
    printed_page: str
    row_label: str
    column_label: str
    original: str
    replacement: str
    expected_sum: str
    expected_delta: str


FIXTURES = (
    Fixture(
        source="apple 10k.pdf",
        output="apple-10k-intentional-footing-break.pdf",
        page=25,
        printed_page="22",
        row_label="Total net sales",
        column_label="2025",
        original="416,161",
        replacement="416,611",
        expected_sum="416161",
        expected_delta="450",
    ),
    Fixture(
        source="quest 10k.pdf",
        output="quest-10k-intentional-footing-break.pdf",
        page=29,
        printed_page="21",
        row_label="Total operating expenses",
        column_label="2025",
        original="48,701",
        replacement="48,071",
        expected_sum="48701",
        expected_delta="-630",
    ),
    Fixture(
        source="disney 10-k.pdf",
        output="disney-10k-intentional-footing-break.pdf",
        page=42,
        printed_page="38",
        row_label="Total revenues",
        column_label="2025",
        original="9,364",
        replacement="9,634",
        expected_sum="9364",
        expected_delta="270",
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _base14_font(span_font: str) -> str:
    lowered = span_font.lower()
    if "times" in lowered:
        if "bold" in lowered and "italic" in lowered:
            return "tibi"
        if "bold" in lowered:
            return "tibo"
        if "italic" in lowered:
            return "tiit"
        return "tiro"
    if "bold" in lowered and "italic" in lowered:
        return "hebi"
    if "bold" in lowered:
        return "hebo"
    if "italic" in lowered:
        return "heit"
    return "helv"


def _font_resource(page: pymupdf.Page, span_font: str) -> str:
    for font in page.get_fonts(full=True):
        pdf_base_font = font[3]
        base_font = pdf_base_font.split("+", 1)[-1]
        if base_font == span_font:
            # PyMuPDF can reuse embedded Type0 fonts. Some filing generators
            # expose non-embedded TrueType resources as ``n/a``; their metrics
            # cannot be loaded for insertion. Subset fonts also cannot encode
            # fresh Unicode reliably. In either case use the matching Base-14
            # face, whose Times / Helvetica digit metrics match these sources.
            if font[1] == "n/a" or "+" in pdf_base_font:
                return _base14_font(span_font)
            return font[4]
    return _base14_font(span_font)


def _target_span(page: pymupdf.Page, text: str, target: pymupdf.Rect) -> dict:
    matches = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if text not in span["text"]:
                    continue
                if pymupdf.Rect(span["bbox"]).intersects(target):
                    matches.append(span)
    if len(matches) != 1:
        raise ValueError(f"Expected one matching text span, found {len(matches)}")
    return matches[0]


def _create_fixture(spec: Fixture) -> dict:
    source = SOURCE_DIR / spec.source
    output = OUTPUT_DIR / spec.output
    document = pymupdf.open(source)
    page = document[spec.page - 1]

    targets = page.search_for(spec.original)
    if len(targets) != 1:
        raise ValueError(
            f"{spec.source} page {spec.page}: expected one {spec.original!r}, "
            f"found {len(targets)}"
        )
    target = targets[0]
    span = _target_span(page, spec.original, target)
    font_resource = _font_resource(page, span["font"])
    color = pymupdf.sRGB_to_pdf(span["color"])

    # Ignore line art so the underline / double-underline beneath the total is
    # preserved. Only the original glyphs inside the exact text rectangle go.
    page.add_redact_annot(target, fill=(1, 1, 1), cross_out=False)
    page.apply_redactions(images=0, graphics=0, text=0)
    page.insert_text(
        span["origin"],
        spec.replacement,
        fontsize=span["size"],
        fontname=font_resource,
        color=color,
        overlay=True,
    )

    temporary_output = output.with_suffix(".tmp.pdf")
    temporary_output.unlink(missing_ok=True)
    document.save(temporary_output, garbage=4, deflate=True)
    document.close()
    temporary_output.replace(output)

    written = pymupdf.open(output)
    with pymupdf.open(source) as source_document:
        source_page_count = source_document.page_count
    if written.page_count != source_page_count:
        raise ValueError(f"{spec.output}: page count changed")
    written_page = written[spec.page - 1]
    replacement_hits = written_page.search_for(spec.replacement)
    original_hits = written_page.search_for(spec.original)
    if len(replacement_hits) != 1 or original_hits:
        raise ValueError(
            f"{spec.output}: replacement validation failed "
            f"(new={len(replacement_hits)}, old={len(original_hits)})"
        )
    written.close()

    return {
        "source": str(source.relative_to(ROOT)).replace("\\", "/"),
        "sourceSha256": _sha256(source),
        "output": str(output.relative_to(ROOT)).replace("\\", "/"),
        "outputSha256": _sha256(output),
        "pdfPage": spec.page,
        "printedPage": spec.printed_page,
        "rowLabel": spec.row_label,
        "columnLabel": spec.column_label,
        "edit": {"from": spec.original, "to": spec.replacement},
        "expected": {
            "outcome": "break",
            "sum": spec.expected_sum,
            "delta": spec.expected_delta,
            "diagnosis": "transposition",
        },
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = [_create_fixture(spec) for spec in FIXTURES]
    manifest = {
        "purpose": (
            "Intentional near-miss fixtures for the reportable vertical footing "
            "break path. Horizontal cross-foot misses remain unresolved by design."
        ),
        "fixtures": entries,
    }
    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
