"""The drift report compares evidence without turning counts into an oracle."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from audit_table_engine_drift import build_report  # noqa: E402


def _document(name: str, general_tables: int, disagreements: int) -> dict:
    return {
        "document": name,
        "controls": {
            "generalModelUnchanged": True,
            "oneGridFallbackPerGeneralTable": True,
            "fallbackLineageValidated": True,
        },
        "generalDetectorVersion": "general-1",
        "financialTableDetectorVersion": "financial-1",
        "pages": 2,
        "generalTables": general_tables,
        "latticeBlocks": 3,
        "latticeOnlyBlocks": 1,
        "corroboratedLatticeBlocks": 2,
        "gridFallbacks": general_tables,
        "boundaryDisagreements": disagreements,
        "generalTablesWithoutLattice": 1,
    }


class DriftReportTests(unittest.TestCase):
    def test_metric_changes_are_reported_but_do_not_fail_controls(self) -> None:
        baseline = build_report([_document("statement.pdf", 4, 1)])
        current = build_report([_document("statement.pdf", 3, 2)], baseline)

        self.assertTrue(current["controlsPassed"])
        delta = current["comparison"]["documents"][0]["delta"]
        self.assertEqual(delta["generalTables"], -1)
        self.assertEqual(delta["boundaryDisagreements"], 1)

    def test_an_invariant_failure_fails_the_report(self) -> None:
        document = _document("statement.pdf", 4, 1)
        document["controls"]["generalModelUnchanged"] = False

        self.assertFalse(build_report([document])["controlsPassed"])


if __name__ == "__main__":
    unittest.main()
