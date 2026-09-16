"""The review tool's pairing, classification and edit decisions.

The script is a development tool, but the decisions it makes about a golden are
the ones that decide whether the corpus bar means anything, so they are pinned
here without rendering or scanning.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import review_reconcile_golden as review  # noqa: E402


def _cell(cell_id: str, row: int, column: int, value: str, label: str = "Total") -> dict:
    return {
        "id": cell_id,
        "rowIndex": row,
        "columnIndex": column,
        "text": value,
        "rowLabel": label,
        "normalizedValue": value,
        "bounds": {"x": 0.1, "y": 0.1, "width": 0.05, "height": 0.02},
    }


def _model(*, confirmed: bool = True) -> dict:
    cells = [
        _cell("t-r0-c1", 0, 1, "40"),
        _cell("t-r1-c1", 1, 1, "60"),
        _cell("t-r2-c1", 2, 1, "100"),
    ]
    total = {
        "id": "t-t1-r2",
        "cellId": "t-r2-c1",
        "rowIndex": 2,
        "columnIndex": 1,
        "axis": "vertical",
        "outcome": "confirmed" if confirmed else "unresolved",
        "signals": [{"name": "label-total"}],
    }
    if confirmed:
        total["resolution"] = {
            "basis": "leaves",
            "addendCellIds": ["t-r0-c1", "t-r1-c1"],
            "negatedAddendCellIds": [],
        }
    return {
        "tables": [
            {
                "id": "t",
                "pageIndex": 0,
                "cells": cells,
                "totals": [total],
                "headerLabels": [
                    {
                        "columnIndex": 1,
                        "text": "2025",
                        "isPeriodColumn": True,
                        "isTotalColumn": False,
                    }
                ],
            }
        ]
    }


def _golden_entry(outcome: str = "confirmed", addends=None, label: str = "Total") -> dict:
    entry = {
        "page": 1,
        "column": "2025",
        "label": label,
        "value": "100",
        "outcome": outcome,
    }
    if outcome == "confirmed":
        entry["sum"] = "100"
        entry["delta"] = "0"
        entry["basis"] = "leaves"
        entry["addends"] = ["40", "60"] if addends is None else addends
        entry["negatedAddends"] = []
    return entry


class PageSetTests(unittest.TestCase):
    def test_parses_lists_and_ranges(self) -> None:
        self.assertEqual(review._page_set(["22,25,26-28"]), {22, 25, 26, 27, 28})
        self.assertEqual(review._page_set(["1-3", "3,5"]), {1, 2, 3, 5})
        self.assertIsNone(review._page_set(None))


class ClassificationTests(unittest.TestCase):
    def test_an_engine_miss_is_not_an_assertion(self) -> None:
        # The engine's own limitation is grey when the golden says nothing;
        # it is a missed confirmation only when the golden confirmed it.
        self.assertEqual(review._classify(None, None), "unresolved")
        self.assertEqual(review._classify(None, _golden_entry()), "missed")
        unresolved = {"outcome": "unresolved"}
        self.assertEqual(review._classify(unresolved, None), "unresolved")
        self.assertEqual(review._classify(unresolved, _golden_entry()), "missed")

    def test_confirmation_comparison(self) -> None:
        engine = {"outcome": "confirmed", "addends": ["40", "60"], "negatedAddends": []}
        self.assertEqual(review._classify(engine, None), "unapproved")
        self.assertEqual(review._classify(engine, _golden_entry()), "match")
        self.assertEqual(
            review._classify(engine, _golden_entry(addends=["100"])),
            "wrong-addends",
        )
        self.assertEqual(
            review._classify(engine, _golden_entry(outcome="break")),
            "outcome-differs",
        )

    def test_breaks_are_distinguished(self) -> None:
        engine = {"outcome": "break"}
        self.assertEqual(
            review._classify(engine, _golden_entry(outcome="break")), "golden-break"
        )
        self.assertEqual(
            review._classify(engine, _golden_entry()), "unexpected-break"
        )


class ReviewPairingTests(unittest.TestCase):
    def test_review_pairs_the_engine_and_the_golden(self) -> None:
        golden = {"totals": [_golden_entry()]}
        rows = review.review(_model(), golden)

        self.assertEqual([row.classification for row in rows], ["match"])
        self.assertIsNotNone(rows[0].cell)
        self.assertEqual(rows[0].locator, "1|Total|100")

    def test_a_confirmed_golden_the_engine_missed_is_drawn_in_place(self) -> None:
        golden = {"totals": [_golden_entry()]}
        rows = review.review(_model(confirmed=False), golden)

        self.assertEqual([row.classification for row in rows], ["missed"])
        self.assertEqual(rows[0].cell["id"], "t-r2-c1")

    def test_an_engine_confirmation_the_golden_lacks_is_flagged(self) -> None:
        rows = review.review(_model(), {"totals": []})

        self.assertEqual([row.classification for row in rows], ["unapproved"])


class EditTests(unittest.TestCase):
    def test_find_and_drop_by_locator(self) -> None:
        golden = {"totals": [_golden_entry()]}
        entry = review._find_golden_entry(golden, "1|Total|100")
        self.assertIs(entry, golden["totals"][0])
        self.assertIsNone(review._find_golden_entry(golden, "1|Other|100"))

    def test_accept_engine_replaces_the_working(self) -> None:
        golden = {"totals": [_golden_entry(addends=["999"])]}
        label = review._accept_engine(golden, _model(), "1|Total|100")

        self.assertEqual(label, "Total")
        self.assertEqual(golden["totals"][0]["addends"], ["40", "60"])
        self.assertEqual(golden["totals"][0]["outcome"], "confirmed")

    def test_accept_missing_adds_only_confirmed_engine_totals_on_the_pages(self) -> None:
        golden = {"totals": []}
        added = review._accept_missing(golden, _model(), {1})
        self.assertEqual(added, ["1|Total|100"])
        self.assertEqual(len(golden["totals"]), 1)

        golden = {"totals": []}
        self.assertEqual(review._accept_missing(golden, _model(), {2}), [])
        self.assertEqual(golden["totals"], [])

        # An unresolved engine total is a scan limit, not a golden entry.
        golden = {"totals": []}
        self.assertEqual(
            review._accept_missing(golden, _model(confirmed=False), {1}), []
        )


if __name__ == "__main__":
    unittest.main()
