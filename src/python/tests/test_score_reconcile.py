"""The scorer's key collisions and its approval gate.

A cell can be both a column total and a row total, and the two reach the same
key. The scorer must compare the assertion the golden recorded, not whichever
substrate happened to be examined last.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import score_reconcile  # noqa: E402


def _cell(cell_id: str, value: str) -> dict:
    return {
        "id": cell_id,
        "rowIndex": 2,
        "columnIndex": 1,
        "text": value,
        "rowLabel": "Total",
        "normalizedValue": value,
        "bounds": {"x": 0.1, "y": 0.1, "width": 0.05, "height": 0.02},
    }


def _model() -> dict:
    cells = [_cell("a", "100"), _cell("b", "40"), _cell("c", "60")]
    vertical = {
        "id": "v",
        "cellId": "a",
        "rowIndex": 2,
        "columnIndex": 1,
        "axis": "vertical",
        "outcome": "confirmed",
        "resolution": {
            "sum": "100",
            "delta": "0",
            "basis": "leaves",
            "addendCellIds": ["b", "c"],
            "negatedAddendCellIds": [],
        },
    }
    cross = {
        "id": "x",
        "cellId": "a",
        "rowIndex": 2,
        "columnIndex": 1,
        "axis": "cross",
        "outcome": "confirmed",
        "resolution": {
            "sum": "100",
            "delta": "0",
            "basis": "row",
            "addendCellIds": ["b", "c"],
            "negatedAddendCellIds": [],
        },
    }
    return {
        "tables": [
            {
                "id": "t",
                "pageIndex": 0,
                "cells": cells,
                "totals": [vertical, cross],
                "headerLabels": [
                    {
                        "columnIndex": 1,
                        "text": "Total",
                        "isPeriodColumn": False,
                        "isTotalColumn": True,
                    }
                ],
            }
        ]
    }


def _key() -> str:
    return score_reconcile.key_of(0, "Total", "100", "Total")


class ObservedCollisionTests(unittest.TestCase):
    def test_without_a_preference_the_last_substrate_wins(self) -> None:
        entry = score_reconcile.observed(_model())[_key()]
        self.assertEqual(entry["axis"], "cross")

    def test_a_preference_keeps_the_assertion_the_golden_is_about(self) -> None:
        actual = score_reconcile.observed(_model(), prefer_axis={_key(): "vertical"})
        self.assertEqual(actual[_key()]["axis"], "vertical")

        actual = score_reconcile.observed(_model(), prefer_axis={_key(): "cross"})
        self.assertEqual(actual[_key()]["axis"], "cross")


class ApprovalGateTests(unittest.TestCase):
    def test_unapproved_can_be_compared_but_not_scored(self) -> None:
        golden = {"approved": False, "totals": []}
        report = score_reconcile.score(_model(), golden, require_approved=False)
        self.assertEqual(report["falseTies"], [])
        with self.assertRaises(SystemExit):
            score_reconcile.score(_model(), golden)


if __name__ == "__main__":
    unittest.main()
