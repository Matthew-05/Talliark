from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf


REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT, REPO_ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts.render_table_overlay import render_overlay  # noqa: E402
from table_corpus import detect, geometry_for  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tables" / "synthetic"


def _source() -> Path:
    # A local corpus document when one is offered, otherwise the committed
    # fixture, so the renderer is exercised on every machine.
    override = os.environ.get("TALLIARK_OVERLAY_PDF")
    return Path(override) if override else FIXTURES / "two-schemas.pdf"


class TableOverlayTests(unittest.TestCase):
    def test_render_overlay_writes_one_page_per_detected_table_page(self) -> None:
        source = _source()
        pdf_bytes = source.read_bytes()
        model, _diagnostics, _elapsed = detect(pdf_bytes, geometry_for(pdf_bytes))
        expected_page_count = sum(bool(page["tables"]) for page in model["pages"])
        self.assertGreater(expected_page_count, 0, f"{source.name} contains no detected tables")

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "table-overlay.pdf"
            result = render_overlay(source, output)

            self.assertEqual(output, result)
            rendered = pymupdf.open(output)
            try:
                self.assertEqual(expected_page_count, rendered.page_count)
            finally:
                rendered.close()

    def test_rejected_candidates_are_reported_with_a_reason(self) -> None:
        # A numbered list produces a fitted candidate and publishes none of it,
        # so it is the clearest check that rejections are surfaced rather than
        # silent. Bullet markers are filtered before candidate generation.
        source = FIXTURES / "negative-numbered-list.pdf"
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "overlay.pdf"
            report = Path(temporary_directory) / "overlay.json"
            with self.assertRaises(ValueError):
                render_overlay(source, output, show_rejected=False, report=report)
            written = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(written["document"], source.name)

            render_overlay(source, output, show_rejected=True, report=report)
            written = json.loads(report.read_text(encoding="utf-8"))
            rejected = [entry for page in written["pages"] for entry in page["rejected"]]
            self.assertTrue(rejected)
            self.assertTrue(all(entry["reason"] for entry in rejected))

    def test_page_selection_limits_what_is_rendered(self) -> None:
        source = FIXTURES / "two-schemas.pdf"
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "overlay.pdf"
            render_overlay(source, output, pages={1})
            rendered = pymupdf.open(output)
            try:
                self.assertEqual(rendered.page_count, 1)
            finally:
                rendered.close()
            with self.assertRaises(ValueError):
                render_overlay(source, output, pages={2})


if __name__ == "__main__":
    unittest.main()
