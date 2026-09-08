"""The private financial-table pass layered over general table recognition."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import pymupdf

from engines.financial_table.detector import (
    DETECTOR_VERSION,
    detect_financial_tables,
)
from engines.geometry_engine import extract_text_geometry
from engines.table.detector import detect_tables
from engines.table.handoff import build_analysis_handoff, table_geometry_digest
from engines.values.detector import detect_values
from schemas.models import Stage


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tables" / "synthetic"
PYTHON_ROOT = Path(__file__).resolve().parents[1]


def _document() -> bytes:
    document = pymupdf.open(str(FIXTURES / "ruled-grid.pdf"))
    try:
        return document.tobytes()
    finally:
        document.close()


class FinancialTableScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pdf = _document()
        cls.geometry = extract_text_geometry(cls.pdf)
        cls.values = detect_values(cls.geometry)
        cls.tables = detect_tables(cls.pdf, cls.geometry, budget_ms=0)

    def test_the_general_model_is_an_input_and_is_not_rewritten(self) -> None:
        before = copy.deepcopy(self.tables)
        before_bytes = json.dumps(self.tables, separators=(",", ":")).encode("utf-8")

        scan = detect_financial_tables(self.geometry, self.values, self.tables)

        self.assertEqual(self.tables, before)
        self.assertEqual(
            json.dumps(self.tables, separators=(",", ":")).encode("utf-8"),
            before_bytes,
        )
        self.assertGreater(scan.lattice_blocks, 0)
        self.assertEqual(
            scan.grid_fallbacks,
            sum(len(page["tables"]) for page in self.tables["pages"]),
        )

    def test_the_pass_has_its_own_version_and_progress_stage(self) -> None:
        stages: list[str] = []

        scan = detect_financial_tables(
            self.geometry,
            self.values,
            self.tables,
            progress_callback=lambda _message, stage, **_kw: stages.append(stage),
        )

        self.assertEqual(DETECTOR_VERSION, "financial-table-detector-2")
        self.assertEqual(scan.detector_version, DETECTOR_VERSION)
        self.assertEqual(len(scan.pages), len(self.geometry["pages"]))
        self.assertIn(Stage.RECONCILE_TABLES, stages)
        self.assertNotIn(Stage.TABLE_STRUCTURE, stages)

    def test_missing_general_tables_still_allows_financial_lattice_recovery(self) -> None:
        scan = detect_financial_tables(
            self.geometry,
            self.values,
            {"detectorVersion": "unavailable", "pages": []},
        )

        self.assertGreater(scan.lattice_blocks, 0)
        self.assertEqual(scan.grid_fallbacks, 0)
        self.assertTrue(all(
            block.provenance == "lattice"
            for page in scan.pages
            for block in page.lattice_blocks
        ))

    def test_handoff_is_recursively_read_only(self) -> None:
        handoff = build_analysis_handoff(self.geometry, self.tables)
        table = next(page.tables[0] for page in handoff.pages if page.tables)

        with self.assertRaises(TypeError):
            table["bounds"]["x"] = 0.25

    def test_every_grid_fallback_pins_exact_general_geometry(self) -> None:
        scan = detect_financial_tables(self.geometry, self.values, self.tables)
        tables_by_page = {
            int(page["pageIndex"]): page.get("tables", [])
            for page in self.tables["pages"]
        }

        for page in scan.pages:
            source = tables_by_page.get(page.page_index, [])
            self.assertEqual(len(source), len(page.grid_fallbacks))
            for table, fallback in zip(source, page.grid_fallbacks):
                self.assertEqual(fallback.source_general_table_id, table["id"])
                self.assertEqual(
                    fallback.source_general_geometry_digest,
                    table_geometry_digest(table),
                )
                self.assertEqual(fallback.bounds, table["bounds"])
                self.assertEqual(fallback.column_count, len(table["columns"]))
                self.assertEqual(
                    fallback.row_count,
                    sum(row["kind"] == "body" for row in table["rows"]),
                )

    def test_boundary_disagreement_is_evidence_not_a_grid_rewrite(self) -> None:
        shifted = copy.deepcopy(self.tables)
        table = next(
            table
            for page in shifted["pages"]
            for table in page["tables"]
            if len(table.get("columns", [])) > 1
        )
        columns = table["columns"]
        table["columns"] = [
            columns[0],
            {"x0": columns[1]["x0"], "x1": columns[-1]["x1"]},
        ]
        before = copy.deepcopy(shifted)

        scan = detect_financial_tables(self.geometry, self.values, shifted)

        self.assertEqual(shifted, before)
        self.assertGreater(scan.boundary_disagreements, 0)
        disagreement = scan.disagreements[0]
        self.assertEqual(disagreement.general_table_id, table["id"])
        self.assertIsNotNone(disagreement.financial_block_id)


class EngineBoundaryTests(unittest.TestCase):
    def test_general_table_engine_does_not_depend_on_financial_analysis(self) -> None:
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((PYTHON_ROOT / "engines" / "table").glob("*.py"))
        )

        self.assertNotIn("engines.financial_table", sources)
        self.assertNotIn("engines.reconcile", sources)

    def test_financial_table_engine_does_not_depend_on_reconcile(self) -> None:
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(
                (PYTHON_ROOT / "engines" / "financial_table").glob("*.py")
            )
        )

        self.assertNotIn("engines.reconcile", sources)

    def test_financial_table_uses_only_the_supported_general_handoff(self) -> None:
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(
                (PYTHON_ROOT / "engines" / "financial_table").glob("*.py")
            )
        )

        self.assertNotIn("engines.table.layout", sources)
        self.assertNotIn("engines.table.grid", sources)


if __name__ == "__main__":
    unittest.main()
