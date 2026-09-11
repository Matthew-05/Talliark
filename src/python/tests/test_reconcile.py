"""What a Reconcile scan publishes, and the decisions it stands on.

Three layers are asserted here. The envelope is what storage, staleness and the
window are built against. The cell layer is checked end to end against a drawn
fixture, because the join it makes -- grid to text geometry to values -- is the
one thing nothing else in the system does. And the sum tree is checked against
hand-built cell layers, because the shapes that matter are ones no fixture
reliably contains: a total that foots on its subtotals, a run that hides a
subtotal inside itself, a near miss that reads as a transposition.

Two assertions are guards rather than tests of behaviour. The stage-order
assertion fails if the closed ProgressStage enum drifts between the worker
protocol contract and the code that emits it; the contract says the two must be
changed together, and this is what enforces it.
"""
from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

import pymupdf

from engines.geometry_engine import extract_text_geometry
from engines.financial_table import cells, labels
from engines.financial_table.cells import (
    TableCells,
    _label_for,
    decimals_of,
    is_label_column,
)
from engines.financial_table.detector import DETECTOR_VERSION as FINANCIAL_TABLE_VERSION
from engines.reconcile import findings, nominate, propagate, structures, sums
from engines.reconcile.detector import (
    DETECTOR_VERSION,
    _composed_parallel_total_ids,
    _is_supported_composed_total,
    _reconcile_block,
    _select_grid_totals,
    detect_reconcile,
    geometry_fingerprint,
)
from schemas.models import STAGE_ORDER, Stage
from engines.table.detector import detect_tables
from engines.values.detector import detect_values


CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tables" / "synthetic"


def _document(name: str = "ruled-grid.pdf") -> bytes:
    source = pymupdf.open(str(FIXTURES / name))
    try:
        return source.tobytes()
    finally:
        source.close()


def _table(rows: list[tuple[str, ...]], *, table_id: str = "t") -> TableCells:
    """A cell layer built by hand, in the shape `build_table_cells` publishes.

    Each row is its label followed by one printed cell per value column; an
    empty string is a cell that was never published, which is what a blank in
    the page looks like to the run search. Written out this way because the
    shapes the sum tree turns on -- a subtotal inside a run, a column that
    changes decimal specificity halfway down -- are ones a drawn fixture
    contains only by luck.
    """
    built = TableCells(
        table_id=table_id,
        page_index=0,
        column_count=max(len(row) for row in rows),
        row_count=len(rows),
        row_labels=[row[0] for row in rows],
    )
    for row_index, row in enumerate(rows):
        for column_index, printed in enumerate(row):
            if not printed:
                continue
            cell = {
                "id": f"{table_id}-r{row_index}-c{column_index}",
                "rowIndex": row_index,
                "columnIndex": column_index,
                "text": printed,
                "bounds": {"x": 0.1, "y": 0.1 + row_index / 100, "width": 0.05, "height": 0.01},
            }
            if column_index:
                stripped = printed.replace(",", "").replace("$", "").strip()
                if stripped in ("—", "-"):
                    cell["dash"] = True
                else:
                    negative = stripped.startswith("(")
                    digits = stripped.strip("()")
                    cell["spanId"] = f"val-{row_index:08d}{column_index:08d}"
                    cell["normalizedValue"] = ("-" if negative else "") + digits
                    cell["decimals"] = decimals_of(printed)
                cell["rowLabel"] = row[0]
            built.published.append(cell)
            built.by_position[(row_index, column_index)] = cell
            built.by_id[cell["id"]] = cell
    return built


def _resolve(built: TableCells, column: int = 1) -> list[dict]:
    """Nominate and foot one column, the way the detector does."""
    nominations = [
        nomination
        for nomination in nominate.nominate(built)
        if nomination.column_index == column
    ]
    nominations.sort(key=lambda nomination: nomination.row_index)
    nominated_rows = {nomination.row_index for nomination in nominations}
    jump: dict[int, int | None] = {}
    resolved = []
    for nomination in nominations:
        outcome = sums.resolve(
            built, nomination.cell, len(nomination.signals), nominated_rows, jump
        )
        resolution = outcome.get("resolution")
        if resolution:
            addends = [built.by_id[cell_id] for cell_id in resolution["addendCellIds"]]
            jump[nomination.row_index] = sums.block_top(addends, jump, nominated_rows)
        resolved.append({"label": nomination.cell["rowLabel"], **outcome})
    return resolved


class ProgressVocabulary(unittest.TestCase):
    """The enum is closed and ordered; two copies of it must not drift."""

    def test_stage_order_matches_the_worker_contract(self) -> None:
        contract = json.loads((CONTRACTS / "python-worker-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(
            tuple(contract["definitions"]["ProgressStage"]["enum"]),
            STAGE_ORDER,
        )

    def test_the_webview_contract_mirrors_the_same_vocabulary(self) -> None:
        contract = json.loads((CONTRACTS / "webview-messages-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(
            tuple(contract["definitions"]["ProgressStage"]["enum"]),
            STAGE_ORDER,
        )

    def test_the_scan_stages_run_after_values(self) -> None:
        # A consumer places a stage on a bar from this order. The scan is the
        # pipeline's tail, so its stages sit after every cache-build stage and
        # before the host's own result-transfer.
        self.assertLess(STAGE_ORDER.index(Stage.VALUES), STAGE_ORDER.index(Stage.RECONCILE_TABLES))
        self.assertLess(STAGE_ORDER.index(Stage.RECONCILE_TABLES), STAGE_ORDER.index(Stage.RECONCILE))
        self.assertLess(STAGE_ORDER.index(Stage.RECONCILE), STAGE_ORDER.index(Stage.RESULT_TRANSFER))


class ScanEnvelope(unittest.TestCase):
    """The model a scan stores, which staleness and the window are built on."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.pdf = _document()
        cls.geometry = extract_text_geometry(cls.pdf)
        cls.values = detect_values(cls.geometry)
        cls.tables = detect_tables(cls.pdf, cls.geometry, budget_ms=0)
        cls.stages: list[str] = []
        cls.diagnostics: dict = {}
        cls.model = detect_reconcile(
            cls.pdf,
            cls.geometry,
            document_id="7c9e6679-7425-40de-944b-e07fc1f90ae7",
            values=cls.values,
            tables=cls.tables,
            progress_callback=lambda message, stage, **kw: cls.stages.append(stage),
            diagnostics=cls.diagnostics,
        )

    def test_the_model_validates_against_its_contract(self) -> None:
        try:
            import jsonschema
        except ImportError:  # pragma: no cover — jsonschema is not a worker dependency
            self.skipTest("jsonschema not installed")
        schema = json.loads((CONTRACTS / "reconcile-v1.json").read_text(encoding="utf-8"))
        # The contract's $id is a urn, which older resolvers try to dereference
        # as a URL. Dropping it makes "#/definitions/..." resolve against the
        # document itself, which is what those refs mean.
        schema.pop("$id", None)
        jsonschema.validate(self.model, schema)

    def test_the_envelope_names_what_produced_it(self) -> None:
        self.assertEqual(self.model["version"], 1)
        self.assertEqual(self.model["coordinateSpace"], "normalized")
        self.assertEqual(self.model["detectorVersion"], DETECTOR_VERSION)

    def test_the_source_records_what_staleness_is_decided_from(self) -> None:
        source = self.model["source"]
        self.assertEqual(source["documentId"], "7c9e6679-7425-40de-944b-e07fc1f90ae7")
        self.assertEqual(source["versionId"], source["documentId"])
        self.assertEqual(source["pageCount"], len(self.geometry["pages"]))
        self.assertEqual(source["geometryFingerprint"], geometry_fingerprint(self.geometry))
        self.assertTrue(source["tableDetectorVersion"])
        self.assertEqual(source["valueDetectorVersion"], "document-values-detector-3")
        # The financial tier publishes nothing for a document with no apparatus,
        # and its absence is recorded as absence rather than as an empty string.
        self.assertNotIn("financialStructureDetectorVersion", source)

    def test_every_table_examined_is_published_even_when_it_yields_nothing(self) -> None:
        # A table with nothing nominated is still the record that the table was
        # looked at, which is what lets the window say it found nothing to check
        # rather than implying the document passed.
        summary = self.model["summary"]
        self.assertGreater(summary["tablesExamined"], 0)
        self.assertEqual(len(self.model["tables"]), summary["tablesExamined"])

    def test_a_document_with_no_totals_asserts_nothing_about_it(self) -> None:
        # The fixture is an order table: items, quantities and amounts, with no
        # row that announces itself as a total. Silence here is the correct
        # answer, and it must not be worded or counted as a pass.
        self.assertEqual(self.model["summary"]["totalsNominated"], 0)
        self.assertEqual(self.model["findings"], [])

    def test_the_cell_layer_joins_the_grid_to_the_text_on_the_page(self) -> None:
        table = self.model["tables"][0]
        self.assertGreater(table["rowCount"], 0)
        self.assertGreater(table["columnCount"], 1)
        placed = {(cell["rowIndex"], cell["columnIndex"]): cell["text"] for cell in table["cells"]}
        # Cell text is the join nothing else in the system makes:
        # `document-values-v1` carries no membership and `table-structure-v1`
        # carries no text.
        self.assertTrue(any(text for text in placed.values()))
        self.assertEqual(
            sorted(placed), sorted(set(placed)), "a cell may be published only once"
        )
        for cell in table["cells"]:
            self.assertLess(cell["rowIndex"], table["rowCount"])
            self.assertLess(cell["columnIndex"], table["columnCount"])
            self.assertGreater(cell["bounds"]["width"], 0)
            self.assertGreater(cell["bounds"]["height"], 0)

    def test_an_empty_cell_is_absent_rather_than_published_blank(self) -> None:
        # Absence is the fact the run search reads: a blank ends a candidate
        # rather than contributing a zero to it.
        for table in self.model["tables"]:
            for cell in table["cells"]:
                self.assertTrue(cell["text"].strip(), cell)

    def test_reconcile_reads_the_header_labels_and_interprets_them_itself(self) -> None:
        table = self.model["tables"][0]
        self.assertIn("headerLabels", table)
        for label in table["headerLabels"]:
            self.assertIn("isTotalColumn", label)
            self.assertIn("isPeriodColumn", label)

    def test_every_nominated_total_lands_in_exactly_one_outcome(self) -> None:
        summary = self.model["summary"]
        self.assertEqual(
            summary["confirmed"] + summary["breaks"] + summary["unresolved"],
            summary["totalsNominated"],
        )

    def test_the_scan_reports_its_own_stages_not_the_cache_build_s(self) -> None:
        # Financial table interpretation follows the cache-build table stage;
        # reporting it as `table-structure` would run a progress bar backwards.
        self.assertIn(Stage.RECONCILE_TABLES, self.stages)
        self.assertIn(Stage.RECONCILE, self.stages)
        self.assertNotIn(Stage.TABLE_STRUCTURE, self.stages)

    def test_diagnostics_separate_financial_table_recognition_from_arithmetic(self) -> None:
        self.assertEqual(self.diagnostics["reconcile_detector_version"], DETECTOR_VERSION)
        self.assertEqual(
            self.diagnostics["financial_table_detector_version"],
            FINANCIAL_TABLE_VERSION,
        )
        self.assertGreater(self.diagnostics["financial_table_lattice_blocks"], 0)
        self.assertGreater(self.diagnostics["financial_table_grid_fallbacks"], 0)
        self.assertIn("reconcile_table_detection_ms", self.diagnostics)
        self.assertIn("reconcile_ms", self.diagnostics)
        self.assertIn("reconcile_structure_truncated_blocks", self.diagnostics)
        self.assertIn("reconcile_candidates_withheld", self.diagnostics)

    def test_the_fingerprint_moves_when_the_geometry_does(self) -> None:
        # Span ids survive a detector upgrade and not a re-OCR that moves
        # bounds. This is the fact that lets a stored result notice.
        moved = json.loads(json.dumps(self.geometry))
        for page in moved["pages"]:
            for character in page.get("characters", []):
                character["y"] = round(character["y"] + 0.01, 6)
            break
        self.assertNotEqual(geometry_fingerprint(moved), geometry_fingerprint(self.geometry))


class Decisions(unittest.TestCase):
    """The rules R-1 and R-2 are built on, asserted where they are declared."""

    def test_a_total_announces_itself_in_its_opening_phrase(self) -> None:
        for label in ("Total net sales", "NET INCOME", "Balance at December 31, 2025",
                      "(1) Total revenue", "Subtotal"):
            self.assertTrue(labels.is_total_label(label), label)

    def test_a_label_that_merely_mentions_a_total_is_not_one(self) -> None:
        for label in (
            "Cost of sales",
            "the total of these amounts",
            "Percentage of total",
            "Gross benefits paid",
            "Gross carrying amount",
        ):
            self.assertFalse(labels.is_total_label(label), label)
        self.assertTrue(labels.is_total_label("Gross profit"))

    def test_the_enabled_signals_are_the_five_that_have_been_measured(self) -> None:
        self.assertEqual(
            nominate.ENABLED_SIGNALS,
            (
                "label-total",
                "column-corroboration",
                "total-column",
                "ruling-above",
                "double-rule-below",
            ),
        )

    def test_a_ruled_grid_lends_no_row_the_convention(self) -> None:
        # Every row bordered is a grid, not a statement marking its totals, and
        # a rule that is drawn everywhere says nothing about anywhere.
        built = cells.TableCells(
            table_id="t", page_index=0, column_count=2, row_count=4
        )
        for row in range(4):
            cell = {
                "id": f"t-r{row}-c1", "rowIndex": row, "columnIndex": 1, "text": "1",
                "bounds": {"x": 0.5, "y": 0.10 + row * 0.02, "width": 0.05, "height": 0.011},
            }
            built.published.append(cell)
            built.by_position[(row, 1)] = cell
            built.by_id[cell["id"]] = cell
        cells.mark_rules_above(built, [0.1125, 0.1325, 0.1525])
        self.assertEqual(built.ruled_above, frozenset())
        cells.mark_rules_above(built, [0.1525])
        self.assertEqual(built.ruled_above, frozenset({3}))

    def test_a_double_rule_is_two_rules_and_a_single_rule_is_not(self) -> None:
        built = cells.TableCells(
            table_id="t", page_index=0, column_count=2, row_count=3
        )
        for row, y in ((0, 0.10), (1, 0.14), (2, 0.18)):
            cell = {
                "id": f"t-r{row}-c1", "rowIndex": row, "columnIndex": 1, "text": "1",
                "bounds": {"x": 0.5, "y": y, "width": 0.05, "height": 0.011},
            }
            built.published.append(cell)
            built.by_position[(row, 1)] = cell
            built.by_id[cell["id"]] = cell
        # A single rule under row 0, a double rule under row 2.
        cells.mark_double_rules(built, [0.1125, 0.1925, 0.1950])
        self.assertEqual(built.double_ruled, frozenset({2}))

    def test_a_row_does_not_borrow_the_rule_of_the_row_beneath_it(self) -> None:
        built = cells.TableCells(
            table_id="t", page_index=0, column_count=2, row_count=2
        )
        for row, y in ((0, 0.100), (1, 0.115)):
            cell = {
                "id": f"t-r{row}-c1", "rowIndex": row, "columnIndex": 1, "text": "1",
                "bounds": {"x": 0.5, "y": y, "width": 0.05, "height": 0.011},
            }
            built.published.append(cell)
            built.by_position[(row, 1)] = cell
            built.by_id[cell["id"]] = cell
        # Row 0's own rule, then row 1's -- two rules, but not one double rule,
        # because row 1's glyphs stand between them.
        cells.mark_double_rules(built, [0.1120, 0.1270])
        self.assertEqual(built.double_ruled, frozenset())

    def test_a_reversal_found_among_many_candidates_is_not_evidence(self) -> None:
        # A discovered sign reversal is worth what the search space it came from
        # is worth. Every real signed confirmation on the corpus was found among
        # seven candidates or fewer; every false one among 210 or more.
        self.assertEqual(sums.MAX_SIGN_CANDIDATES, 20)
        from math import comb
        self.assertLessEqual(comb(7, 1), sums.MAX_SIGN_CANDIDATES)
        self.assertGreater(comb(10, 4), sums.MAX_SIGN_CANDIDATES)

    def test_a_run_that_subtracts_a_figure_it_also_adds_is_cancellation(self) -> None:
        def cell(index, value):
            return {
                "id": f"c{index}", "rowIndex": index, "columnIndex": 1,
                "text": value, "normalizedValue": value, "decimals": 0,
            }
        run = sums.Run(
            "leaves",
            tuple(cell(index, value) for index, value in enumerate(
                ("500", "10558", "933", "10558")
            )),
            Decimal("11991"),
            0,
        )
        # Subtracting the 10,558 the run also adds says nothing: the pair
        # cancels, and whatever ties is the shorter run underneath -- which the
        # search can find on its own without inventing a sign to get there.
        self.assertTrue(sums.double_counts(run.with_negated((3,))))
        # The same members with their printed signs are an ordinary run.
        self.assertFalse(sums.double_counts(run))

    def test_a_total_proposed_by_a_rule_alone_never_accuses(self) -> None:
        # The published rule positions carry no extent, so the signal says a
        # double rule is under the row, not under this column. Thin evidence
        # confirms and stays quiet otherwise.
        signal = nominate.double_rule_below(
            {"rowIndex": 4}, type("B", (), {"double_ruled": frozenset({4})})()
        )
        self.assertEqual(signal["name"], "double-rule-below")
        self.assertIsNone(
            nominate.double_rule_below(
                {"rowIndex": 3}, type("B", (), {"double_ruled": frozenset({4})})()
            )
        )

    def test_the_total_column_signal_nominates_only_across(self) -> None:
        # It is the only thing that may propose a cross-foot, and it proposes
        # nothing vertically: a cell is not a column total because its own
        # column header says Total.
        self.assertIsNotNone(nominate.total_column({"isTotalColumn": True, "text": "Total"}))
        self.assertIsNone(nominate.total_column({"isTotalColumn": False, "text": "Europe"}))
        self.assertIsNone(nominate.total_column(None))

    def test_two_period_headers_disable_cross_footing_for_the_table(self) -> None:
        comparative = [
            {"columnIndex": 1, "text": "2025", "isPeriodColumn": True, "isTotalColumn": False},
            {"columnIndex": 2, "text": "2024", "isPeriodColumn": True, "isTotalColumn": False},
            {"columnIndex": 3, "text": "Total", "isPeriodColumn": False, "isTotalColumn": True},
        ]
        self.assertEqual(nominate.cross_foot_columns(comparative), [])

    def test_a_cross_foot_confirms_and_never_accuses(self) -> None:
        # A row carries one signal -- the word in its own column header -- and
        # nothing corroborates it, so a miss is recorded and not spoken.
        self.assertFalse(sums.CROSS_FOOT_MAY_BREAK)

    def test_an_unresolved_rule_probe_is_not_published_as_not_checked(self) -> None:
        built = _table([
            ("Alpha", "10"),
            ("Beta", "20"),
            ("Gamma", "30"),
            ("Ordinary row", "99"),
        ])
        built.ruled_above = frozenset({3})

        model, diagnostics = _reconcile_block(built, page_index=0, findings=[])

        self.assertEqual(model["totals"], [])
        self.assertEqual(diagnostics["withheld"], {"speculative-rule": 1})

    def test_an_exact_rule_probe_is_still_published(self) -> None:
        built = _table([
            ("Alpha", "10"),
            ("Beta", "20"),
            ("Gamma", "40"),
            ("Operating income", "70"),
        ])
        built.ruled_above = frozenset({3})

        model, diagnostics = _reconcile_block(built, page_index=0, findings=[])

        self.assertEqual([total["outcome"] for total in model["totals"]], ["confirmed"])
        self.assertEqual(diagnostics["withheld"], {})

    def test_an_opening_balance_without_addends_is_not_not_checked(self) -> None:
        built = _table([("Balance at January 1, 2025", "100")])

        model, diagnostics = _reconcile_block(built, page_index=0, findings=[])

        self.assertEqual(model["totals"], [])
        self.assertEqual(diagnostics["withheld"], {"opening-balance": 1})

    def test_a_total_in_a_non_additive_column_is_not_not_checked(self) -> None:
        built = _table([
            ("Alpha", "10.00"),
            ("Beta", "20.00"),
            ("Gamma", "30.00"),
            ("Total", "99.00"),
        ])
        built.header_labels = [{
            "columnIndex": 1,
            "text": "Weighted Average Exercise Price Per Share",
            "isPeriodColumn": False,
            "isTotalColumn": False,
        }]

        model, diagnostics = _reconcile_block(built, page_index=0, findings=[])

        self.assertEqual(model["totals"], [])
        self.assertEqual(diagnostics["withheld"], {"non-additive-column": 1})

    def test_a_noncontrolling_allocation_is_not_a_total_nomination(self) -> None:
        built = _table([
            ("Income before allocation", "100"),
            ("Net income attributable to noncontrolling interests", "5"),
        ])

        model, diagnostics = _reconcile_block(built, page_index=0, findings=[])

        self.assertFalse(model["totals"])
        self.assertEqual(diagnostics["withheld"]["allocation-component"], 1)

    def test_a_net_named_tax_or_actuarial_component_is_not_not_checked(self) -> None:
        for label in (
            "Net operating losses and tax credit carryforwards",
            "Net actuarial loss (gain)",
        ):
            with self.subTest(label=label):
                built = _table([("Earlier component", "100"), (label, "5")])

                model, diagnostics = _reconcile_block(
                    built, page_index=0, findings=[]
                )

                self.assertFalse(model["totals"])
                self.assertEqual(diagnostics["withheld"], {"net-component": 1})

    def test_a_net_result_carried_into_a_new_statement_is_not_not_checked(self) -> None:
        built = _table([
            ("Net income", "100"),
            ("Depreciation and amortization", "20"),
            ("Other adjustment", "5"),
        ])

        model, diagnostics = _reconcile_block(built, page_index=0, findings=[])

        self.assertFalse(model["totals"])
        self.assertEqual(diagnostics["withheld"], {"carried-result": 1})

    def test_any_total_carried_in_as_the_first_row_is_not_not_checked(self) -> None:
        built = _table([
            ("Total revenue", "100"),
            ("Cost of revenue", "60"),
            ("Operating expense", "20"),
        ])

        model, diagnostics = _reconcile_block(built, page_index=0, findings=[])

        self.assertFalse(model["totals"])
        self.assertEqual(diagnostics["withheld"], {"carried-result": 1})

    def test_cash_flow_activity_rows_are_peers_in_a_summary_table(self) -> None:
        built = _table([
            ("Net cash provided by (used in) operating activities", "100"),
            ("Net cash provided by (used in) investing activities", "20"),
            ("Net cash provided by (used in) financing activities", "5"),
            ("Net increase in cash and cash equivalents", "125"),
        ])

        model, diagnostics = _reconcile_block(built, page_index=0, findings=[])

        unresolved_labels = {
            built.by_id[total["cellId"]]["rowLabel"]
            for total in model["totals"]
            if total["outcome"] == "unresolved"
        }
        self.assertFalse(any("activities" in label for label in unresolved_labels))
        self.assertEqual(
            diagnostics["withheld"],
            {"cash-flow-summary-peer": 3},
        )

    def test_common_cash_flow_result_phrasings_name_the_same_activity(self) -> None:
        for label, activity in (
            ("Net cash used by financing activities", "financing"),
            ("Net cash (used)/provided by investing activities", "investing"),
            ("Net cash provided/(used) by operating activities", "operating"),
        ):
            with self.subTest(label=label):
                self.assertEqual(labels.cash_flow_activity(label), activity)

    def test_net_loss_is_a_movement_in_an_equity_rollforward(self) -> None:
        built = _table([
            ("Opening", "100"),
            ("Net loss", "5"),
        ])
        built.header_labels = [
            {"columnIndex": 1, "text": "Accumulated Deficit", "isPeriodColumn": False, "isTotalColumn": False},
            {"columnIndex": 2, "text": "Total Stockholders' Equity", "isPeriodColumn": False, "isTotalColumn": True},
        ]

        model, diagnostics = _reconcile_block(built, page_index=0, findings=[])

        self.assertFalse(model["totals"])
        self.assertEqual(diagnostics["withheld"], {"equity-movement": 1})

    def test_period_headers_do_not_hide_equity_rollforward_row_context(self) -> None:
        built = _table([
            ("Total shareholders' equity, beginning balances", "100"),
            ("Common stock repurchased", "20"),
            ("Dividends and dividend equivalents declared", "10"),
            ("Net income", "5"),
            ("Ending balances", "25"),
        ])
        built.header_labels = [{
            "columnIndex": 1,
            "text": "2025",
            "isPeriodColumn": True,
            "isTotalColumn": False,
        }]

        model, diagnostics = _reconcile_block(built, page_index=0, findings=[])

        self.assertFalse(any(
            built.by_id[total["cellId"]].get("rowLabel") == "Net income"
            for total in model["totals"]
        ))
        self.assertEqual(diagnostics["withheld"]["equity-movement"], 1)

    def test_net_income_after_opening_cash_is_a_cash_flow_input(self) -> None:
        built = _table([
            ("Cash, beginning of year", "50"),
            ("Net income", "100"),
            ("Depreciation", "20"),
            ("Cash provided by operations", "120"),
        ])

        model, diagnostics = _reconcile_block(built, page_index=0, findings=[])

        self.assertFalse(any(
            built.by_id[total["cellId"]].get("rowLabel") == "Net income"
            for total in model["totals"]
        ))
        self.assertEqual(diagnostics["withheld"]["carried-result"], 1)

    def test_a_credible_labelled_total_remains_not_checked(self) -> None:
        built = _table([
            ("Alpha", "10"),
            ("Beta", "20"),
            ("Gamma", "30"),
            ("Total liabilities", "99"),
        ])

        model, diagnostics = _reconcile_block(built, page_index=0, findings=[])

        self.assertEqual([total["outcome"] for total in model["totals"]], ["unresolved"])
        self.assertEqual(diagnostics["withheld"], {})

    def test_a_result_header_checks_a_two_addend_row(self) -> None:
        built = _table([
            ("Director A", "40", "60", "100"),
            ("Director B", "30", "20", "50"),
        ])
        built.header_labels = [
            {"columnIndex": 1, "text": "Cash", "isPeriodColumn": False, "isTotalColumn": False},
            {"columnIndex": 2, "text": "Stock", "isPeriodColumn": False, "isTotalColumn": False},
            {"columnIndex": 3, "text": "Total", "isPeriodColumn": False, "isTotalColumn": True},
        ]

        model, _ = _reconcile_block(built, page_index=0, findings=[])

        cross = [total for total in model["totals"] if total.get("axis") == "cross"]
        self.assertEqual(len(cross), 2)
        self.assertTrue(all(total["outcome"] == "confirmed" for total in cross))
        self.assertTrue(all(len(total["resolution"]["addendCellIds"]) == 2 for total in cross))

    def test_an_isolated_two_addend_cross_foot_is_not_published(self) -> None:
        built = _table([("Director A", "40", "60", "100")])
        built.header_labels = [
            {"columnIndex": 1, "text": "Cash", "isPeriodColumn": False, "isTotalColumn": False},
            {"columnIndex": 2, "text": "Stock", "isPeriodColumn": False, "isTotalColumn": False},
            {"columnIndex": 3, "text": "Total", "isPeriodColumn": False, "isTotalColumn": True},
        ]

        model, _ = _reconcile_block(built, page_index=0, findings=[])

        self.assertFalse([total for total in model["totals"] if total.get("axis") == "cross"])

    def test_a_net_header_checks_a_subtractive_row(self) -> None:
        built = _table([
            ("Customer relationships", "5", "20,685", "17,979", "2,706"),
            ("Software", "7", "5,740", "1,586", "4,154"),
        ])
        built.header_labels = [
            {"columnIndex": 1, "text": "Estimated useful life", "isPeriodColumn": False, "isTotalColumn": False},
            {"columnIndex": 2, "text": "Gross carrying amount", "isPeriodColumn": False, "isTotalColumn": False},
            {"columnIndex": 3, "text": "Accumulated amortization", "isPeriodColumn": False, "isTotalColumn": False},
            {"columnIndex": 4, "text": "Net", "isPeriodColumn": False, "isTotalColumn": True},
        ]

        model, _ = _reconcile_block(built, page_index=0, findings=[])

        cross = [total for total in model["totals"] if total.get("axis") == "cross"]
        self.assertEqual(len(cross), 2)
        self.assertTrue(all(total["outcome"] == "confirmed" for total in cross))
        self.assertEqual(
            cross[0]["resolution"]["negatedAddendCellIds"],
            ["t-r0-c3"],
        )

    def test_corporate_is_a_column_of_amounts_and_not_a_rate(self) -> None:
        # "rate" inside "Corporate" is the expensive false match: Corporate is a
        # real segment column in every segment schedule a filing prints.
        for header in ("Corporate", "Separate accounts", "Incorporated"):
            self.assertFalse(labels.is_non_additive_column(header), header)
        for header in (
            "Effective Rate",
            "Interest Rates",
            "Weighted-Average",
            "% of total",
            "Estimated Useful Life",
        ):
            self.assertTrue(labels.is_non_additive_column(header), header)

    def test_a_consumed_block_s_own_caption_is_not_a_boundary(self) -> None:
        # A caption directly above a block taken whole is that block's heading.
        # Every other caption still ends the run.
        self.assertTrue(sums.STEP_OVER_SECTION_CAPTIONS)
        self.assertFalse(sums.SKIP_CAPTION_ROWS)

    def test_corroboration_makes_two_addend_runs_publishable(self) -> None:
        # The structure search still supplies the signal per candidate: a
        # label-only nomination does not inherit it merely because it is enabled.
        self.assertTrue(nominate.two_addend_runs_publishable())

    def test_the_first_pass_confirms_on_exact_ties_only(self) -> None:
        self.assertEqual(sums.TOLERANCE, Decimal(0))

    def test_a_display_rounding_difference_is_named_but_never_confirmed(self) -> None:
        built = _table([
            ("Domestic", "59.3"),
            ("International", "72.4"),
            ("Disney+", "131.6"),
        ])
        built.double_ruled = frozenset({2})

        resolved = _resolve(built)

        self.assertEqual(resolved[0]["outcome"], "unresolved")
        self.assertEqual(
            resolved[0]["unresolvedReason"],
            "rounding-indeterminate",
        )

    def test_whole_number_near_misses_are_not_assumed_to_be_rounded(self) -> None:
        built = _table([
            ("Domestic", "59"),
            ("International", "72"),
            ("Total", "130"),
        ])

        self.assertNotEqual(
            _resolve(built)[0]["unresolvedReason"],
            "rounding-indeterminate",
        )

    def test_a_delta_divisible_by_nine_reads_as_a_transposition(self) -> None:
        self.assertTrue(sums.is_transposition(Decimal("9")))
        self.assertTrue(sums.is_transposition(Decimal("-18")))
        self.assertFalse(sums.is_transposition(Decimal("7")))
        self.assertFalse(sums.is_transposition(Decimal("0")))

    def test_a_wrong_sign_diagnosis_uses_the_direction_of_the_delta(self) -> None:
        built = _table([("Revenue", "10"), ("Cost", "5"), ("Total", "5")])
        run = sums.Run(
            "leaves",
            (built.cell(0, 1), built.cell(1, 1)),
            Decimal("5"),
            0,
        )
        self.assertEqual(run.delta, Decimal("-10"))
        self.assertEqual(sums.diagnose(run)["kind"], "sign")
        self.assertEqual(sums.diagnose(run)["cellId"], "t-r1-c1")

    def test_no_finding_kind_words_an_unresolved_total_as_a_failure(self) -> None:
        self.assertIn("footing-unresolved", findings.KINDS)


class CellValues(unittest.TestCase):
    """What a cell carries, and what it deliberately does not."""

    def test_decimal_specificity_is_read_from_the_printed_form(self) -> None:
        # Not from `normalizedValue`: that is canonical decimal and strips
        # trailing zeros, so "1.50" arrives as "1.5" and a column of two-decimal
        # amounts would disagree with itself.
        self.assertEqual(decimals_of("1,234.56"), 2)
        self.assertEqual(decimals_of("$ 404,035"), 0)
        self.assertEqual(decimals_of("(1.50)"), 2)
        self.assertEqual(decimals_of("12,393"), 0)


class LabelColumns(unittest.TestCase):
    """Which column labels a cell, when it is not the first one.

    The case is the two-panel balance sheet: assets down the left, liabilities
    and equity down the right, printed as one grid. Reading the fourth column's
    figures against the first produces nonsense with a straight face -- "Total
    current assets" beside the balance of income taxes payable -- and it is a
    break reported in a statement that foots perfectly.
    """

    def test_a_column_of_words_carrying_no_figures_labels_the_one_beside_it(self) -> None:
        self.assertTrue(
            is_label_column(["Accounts payable", "Interest payable", "Wages payable"], valued=0)
        )

    def test_a_column_of_figures_is_never_a_label_column(self) -> None:
        self.assertFalse(is_label_column(["1,550", "770", "40"], valued=3))

    def test_a_floated_currency_column_is_not_a_label_column(self) -> None:
        # It carries text and no letters. Treating it as a label column would
        # quietly retitle every row to its right, and every total to its right
        # would stop being nominated with nothing to say why.
        self.assertFalse(is_label_column(["$", "$", "$"], valued=0))

    def test_one_cell_of_words_is_not_yet_a_column(self) -> None:
        self.assertFalse(is_label_column(["Note 4"], valued=0))

    def test_a_cell_takes_the_nearest_label_column_to_its_left(self) -> None:
        texts = ["Total current assets", "2,360", "Income taxes payable", "405"]
        self.assertEqual(_label_for(texts, 1, [0, 2]), "Total current assets")
        self.assertEqual(_label_for(texts, 3, [0, 2]), "Income taxes payable")


class SumTree(unittest.TestCase):
    """What the arithmetic makes of what structure nominated."""

    def test_a_run_of_three_or_more_confirms_on_the_label_alone(self) -> None:
        built = _table([
            ("Americas", "167,045"),
            ("Europe", "101,328"),
            ("Greater China", "66,952"),
            ("Total net sales", "335,325"),
        ])
        resolved = _resolve(built)
        self.assertEqual([entry["outcome"] for entry in resolved], ["confirmed"])
        self.assertEqual(resolved[0]["resolution"]["delta"], "0")
        self.assertEqual(resolved[0]["resolution"]["basis"], "leaves")

    def test_a_run_of_two_is_not_published_while_one_signal_is_enabled(self) -> None:
        # A pair that happens to sum is nearly evidence-free, so it stays
        # unresolved until a second signal exists -- not confirmed quietly.
        built = _table([("Products", "10"), ("Services", "20"), ("Total", "30")])
        resolved = _resolve(built)
        self.assertEqual(resolved[0]["outcome"], "unresolved")
        self.assertEqual(resolved[0]["unresolvedReason"], "run-too-short")

    def test_a_dash_is_an_addend_worth_zero(self) -> None:
        built = _table([
            ("Money market funds", "10"),
            ("Mutual funds", "—"),
            ("Corporate debt", "5"),
            ("Subtotal", "15"),
        ])
        resolved = _resolve(built)
        self.assertEqual(resolved[0]["outcome"], "confirmed")
        self.assertEqual(len(resolved[0]["resolution"]["addendCellIds"]), 3)

    def test_a_blank_ends_the_bounded_run_but_a_crossing_reading_may_confirm(self) -> None:
        built = _table([
            ("Cash", "10"),
            ("Receivables", "20"),
            ("Deferred tax assets:", ""),
            ("Inventories", "5"),
            ("Other", "5"),
            ("Total", "40"),
        ])
        # The bounded walk stops at the caption and holds only 5 + 5, a
        # fragment it may never accuse from. The crossing reading reaches
        # 10 + 20 + 5 + 5 = 40 and confirms on the exact tie -- and because it
        # crossed a gap it may confirm and may never accuse, which is what keeps
        # reading through a caption safe.
        resolved = _resolve(built)[0]
        self.assertEqual(resolved["outcome"], "confirmed")
        self.assertEqual(resolved["resolution"]["basis"], "leaves")

    def test_a_crossing_run_that_misses_never_accuses(self) -> None:
        built = _table([
            ("Earlier section", "100"),
            ("Missing amount", ""),
            ("Alpha", "10"),
            ("Beta", "20"),
            ("Gamma", "30"),
            ("Total", "999"),
        ])
        # Neither the bounded fragment (10 + 20 + 30) nor the crossing reading
        # (100 + 10 + 20 + 30) ties, and a run whose extent crossed a gap may
        # not accuse the page of an error.
        resolved = _resolve(built)[0]
        self.assertEqual(resolved["outcome"], "unresolved")
        self.assertNotIn(resolved["unresolvedReason"], {"break"})

    def test_a_run_fragment_cut_off_by_a_blank_never_accuses(self) -> None:
        built = _table([
            ("Earlier section", "100"),
            ("Missing amount", ""),
            ("Alpha", "10"),
            ("Beta", "20"),
            ("Gamma", "30"),
            ("Total", "65"),
        ])
        resolved = _resolve(built)
        self.assertEqual(resolved[0]["outcome"], "unresolved")
        self.assertEqual(resolved[0]["unresolvedReason"], "no-plausible-run")

    def test_a_total_foots_on_its_subtotals_rather_than_on_the_leaves(self) -> None:
        built = _table([
            ("Fixed-rate notes", "12,393"),
            ("Floating-rate notes", "10,078"),
            ("Other notes", "9,300"),
            ("Total term debt principal", "31,771"),
            ("Unamortized discount", "(309)"),
            ("Hedge adjustments", "(294)"),
            ("Total term debt", "31,168"),
        ])
        resolved = _resolve(built)
        self.assertEqual([entry["outcome"] for entry in resolved], ["confirmed", "confirmed"])
        # The subtotals are the tree the statement is asserting; adding the
        # leaves as well would count the same principal twice.
        self.assertEqual(resolved[1]["resolution"]["basis"], "subtotals")
        self.assertEqual(len(resolved[1]["resolution"]["addendCellIds"]), 3)

    def test_a_run_that_hides_a_subtotal_is_refused_rather_than_reported(self) -> None:
        # Apple's commercial-paper table: proceeds, repayments, and the net of
        # the two, whose label opens with "Proceeds" so `label-total` cannot see
        # it. Adding all three double-counts and reports a break in a table that
        # foots perfectly. Refuting a run is not nominating one.
        built = _table([
            ("Proceeds from commercial paper", "—"),
            ("Repayments of commercial paper", "(2,645)"),
            ("Proceeds from/(Repayments of) commercial paper, net", "(2,645)"),
            ("Total proceeds from/(repayments of) commercial paper, net", "(3,978)"),
        ])
        resolved = _resolve(built)
        self.assertEqual(resolved[0]["outcome"], "unresolved")
        self.assertEqual(resolved[0]["unresolvedReason"], "no-plausible-run")

    def test_a_run_holding_a_subtotal_and_its_addends_collapses_to_the_tree(self) -> None:
        # Apple's commercial-paper note again, but read from the opening
        # balance. The net row (3,788) equals the two rows above it
        # (5,836 - 2,048), and the grand total is the opening balance
        # (-5,820) plus that net. A walk that does not know the net is a
        # subtotal double-counts it; collapsing the subtotal with its own
        # addends recovers -5,820 + 3,788 = -2,032 exactly.
        built = _table([
            ("Opening balance", "-5,820"),
            ("Proceeds from commercial paper", "5,836"),
            ("Repayments of commercial paper", "(2,048)"),
            ("Proceeds from/(Repayments of) commercial paper, net", "3,788"),
            ("Total proceeds from/(repayments of) commercial paper, net", "-2,032"),
        ])
        resolved = _resolve(built)
        self.assertEqual(resolved[0]["outcome"], "confirmed")
        self.assertEqual(resolved[0]["resolution"]["basis"], "subtotals")
        self.assertEqual(resolved[0]["resolution"]["sum"], "-2032")
        self.assertEqual(
            resolved[0]["resolution"]["addendCellIds"],
            ["t-r0-c1", "t-r3-c1"],
        )

    def test_a_collapsed_run_needs_two_figures(self) -> None:
        # "778 + dash = 778" proves nothing on its own, and a crossing reading
        # that reaches only one figure is exactly that: it may not confirm.
        built = _table([
            ("Earlier", "778"),
            ("Heading", ""),
            ("Total", "778"),
        ])
        resolved = _resolve(built)
        self.assertEqual(resolved[0]["outcome"], "unresolved")

    def test_a_near_miss_is_a_break_carrying_the_delta_and_its_diagnosis(self) -> None:
        built = _table([
            ("Americas", "178,353"),
            ("Europe", "111,032"),
            ("Greater China", "64,377"),
            ("Total net sales", "353,771"),
        ])
        resolved = _resolve(built)
        self.assertEqual(resolved[0]["outcome"], "break")
        run = resolved[0]["resolution"]
        self.assertEqual(run["sum"], "353762")
        self.assertEqual(run["delta"], "9")
        self.assertEqual(run["diagnosis"]["kind"], "transposition")

    def test_a_miss_larger_than_the_total_is_a_scan_limit_not_an_accusation(self) -> None:
        built = _table([
            ("Alpha", "1,000"),
            ("Beta", "2,000"),
            ("Gamma", "3,000"),
            ("Total", "12"),
        ])
        resolved = _resolve(built)
        self.assertEqual(resolved[0]["outcome"], "unresolved")
        self.assertEqual(resolved[0]["unresolvedReason"], "no-plausible-run")

    def test_a_column_changing_decimal_specificity_ends_the_run(self) -> None:
        # A per-share figure kept out of a column of whole millions, without the
        # module knowing what "per share" means.
        built = _table([
            ("Basic earnings per share", "6.11"),
            ("Products", "10"),
            ("Services", "20"),
            ("Other", "30"),
            ("Total", "66.11"),
        ])
        self.assertEqual(_resolve(built)[0]["outcome"], "unresolved")

    def test_addition_keeps_the_decimal_specificity_the_page_printed(self) -> None:
        built = _table([
            ("Operating leases", "1.50"),
            ("Finance leases", "2.50"),
            ("Other", "0.10"),
            ("Total lease liabilities", "4.10"),
        ])
        run = _resolve(built)[0]["resolution"]
        # "4.1" would be the same number and the wrong answer: specificity is
        # what a near-miss diagnosis reads.
        self.assertEqual(run["sum"], "4.10")
        self.assertEqual(run["delta"], "0.00")

    def test_a_total_may_reverse_one_printed_sign(self) -> None:
        built = _table([
            ("Revenue", "100"),
            ("Other", "5"),
            ("Cost of sales", "40"),
            ("Gross margin", "65"),
        ])
        run = _resolve(built)[0]["resolution"]
        self.assertEqual(run["sum"], "65")
        self.assertEqual(run["delta"], "0")
        self.assertEqual(run["negatedAddendCellIds"], ["t-r2-c1"])

    def test_the_first_member_of_a_run_keeps_its_printed_sign(self) -> None:
        # The same four figures with the subtraction in the middle:
        # 100 - 40 + 5 = 65 is arithmetic the search can reach and a shape no
        # statement lays out. Reversing an arbitrary subset is 2^n readings and
        # finds an exact tie in almost any column; a statement writes *A less B
        # less C*, and on the corpus every one of the 55 real signed
        # confirmations reverses a contiguous suffix.
        built = _table([
            ("Revenue", "100"),
            ("Cost of sales", "40"),
            ("Other", "5"),
            ("Gross margin", "65"),
        ])
        resolved = _resolve(built)
        self.assertEqual(resolved[0]["outcome"], "unresolved")

    def test_a_reversal_is_a_suffix_and_never_a_scattered_subset(self) -> None:
        from decimal import Decimal as _D
        def cell(index, value):
            return {
                "id": f"c{index}", "rowIndex": index, "columnIndex": 1,
                "text": value, "normalizedValue": value, "decimals": 0,
            }
        run = sums.Run(
            "leaves",
            tuple(cell(i, v) for i, v in enumerate(("10", "3", "4", "5"))),
            _D("8"),
            0,
        )
        # 10 - 3 + 4 - 5 = 6 and 10 + 3 - 4 - 5 = 4; only the suffix reading
        # 10 + 3 + 4 - 5 = 12 and 10 + 3 - 4 - 5 are candidates at all, and the
        # one that ties is a suffix.
        for variant in sums.tied_variants(run):
            negated = set(variant.negated_indices)
            self.assertNotIn(0, negated)
            self.assertEqual(negated, set(range(min(negated), len(run.cells))))

    def test_a_total_may_reverse_multiple_printed_signs(self) -> None:
        # Returns, discounts and allowances are a contiguous suffix.
        built = _table([
            ("Revenue", "100"),
            ("Returns", "10"),
            ("Discounts", "5"),
            ("Allowances", "2"),
            ("Net revenue", "83"),
        ])
        run = _resolve(built)[0]["resolution"]
        self.assertEqual(run["sum"], "83")
        self.assertEqual(
            run["negatedAddendCellIds"],
            ["t-r1-c1", "t-r2-c1", "t-r3-c1"],
        )

    def test_nothing_above_a_total_is_recorded_without_accusing_the_document(self) -> None:
        built = _table([("Total net sales", "100"), ("Cost of sales", "40")])
        resolved = _resolve(built)
        self.assertEqual(resolved[0]["outcome"], "unresolved")
        self.assertEqual(resolved[0]["unresolvedReason"], "no-candidate-run")

    def test_the_leading_column_is_labels_and_is_never_nominated(self) -> None:
        built = _table([("2023", "10"), ("2024", "20"), ("Total", "30")])
        self.assertFalse([n for n in nominate.nominate(built) if n.column_index == 0])


class Findings(unittest.TestCase):
    """The sentence is the deliverable."""

    def test_a_break_restates_as_a_sentence_a_reviewer_can_check_by_hand(self) -> None:
        built = _table([
            ("Americas", "178,353"),
            ("Europe", "111,032"),
            ("Greater China", "64,377"),
            ("Total net sales", "353,771"),
        ])
        resolved = _resolve(built)[0]
        total = built.cell(3, 1)
        addends = [built.by_id[cell_id] for cell_id in resolved["resolution"]["addendCellIds"]]
        finding = findings.build_break(
            total_cell=total,
            run=resolved["resolution"],
            addends=addends,
            table_id="t",
            total_id="t-t1-0",
            page_index=23,
            decimals=0,
        )
        self.assertEqual(finding["kind"], "footing-break")
        self.assertIn("page 24", finding["sentence"])
        self.assertIn("Total net sales", finding["sentence"])
        self.assertIn("353,762", finding["sentence"])
        self.assertIn("353,771", finding["sentence"])
        self.assertIn("two adjacent digits swapped", finding["sentence"])
        self.assertEqual([span["role"] for span in finding["spans"]][0], "total")
        self.assertEqual(len(finding["spans"]), 4)

    def test_a_finding_id_is_derived_from_what_it_says_not_from_its_order(self) -> None:
        # A re-scan that reaches the same conclusion reaches the same id, which
        # is what a disposition will eventually anchor to.
        first = findings.finding_id("footing-break", "t", "t-t1-0", "600|9")
        again = findings.finding_id("footing-break", "t", "t-t1-0", "600|9")
        other = findings.finding_id("footing-break", "t", "t-t1-0", "600|8")
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)

    def test_the_scan_stays_quiet_about_totals_it_simply_could_not_reach(self) -> None:
        # `no-candidate-run` and `run-too-short` describe the scan, not the
        # document, and a filing yields hundreds of them. They are counted in
        # the summary and carried on the total; they are not spoken as findings.
        for reason in (
            "no-candidate-run",
            "run-too-short",
            "mixed-decimals",
            "rounding-indeterminate",
        ):
            self.assertNotIn(reason, findings.SPOKEN_UNRESOLVED)
        self.assertIn("no-plausible-run", findings.SPOKEN_UNRESOLVED)


class ParallelColumnCorroboration(unittest.TestCase):
    """A column is judged only on arithmetic evidence outside itself."""

    def test_two_independent_columns_publish_two_addend_totals(self) -> None:
        built = _table([
            ("Opening", "10", "7"),
            ("Movement", "5", "9"),
            ("Ending", "15", "16"),
        ])
        resolved, diagnostics = structures.resolve(built)
        self.assertEqual([entry["outcome"] for entry in resolved], ["confirmed", "confirmed"])
        self.assertEqual(diagnostics["hypotheses"], 1)
        self.assertTrue(all(len(entry["resolution"]["addendCellIds"]) == 2 for entry in resolved))

    def test_parallel_columns_corroborate_the_same_subtractive_rows(self) -> None:
        built = _table([
            ("Net sales", "100", "90", "80"),
            ("Cost of sales", "40", "35", "30"),
            ("Gross margin", "60", "55", "50"),
        ])
        resolved, _ = structures.resolve(built)
        self.assertEqual([entry["outcome"] for entry in resolved], [
            "confirmed", "confirmed", "confirmed",
        ])
        self.assertTrue(all(
            entry["resolution"]["negatedAddendCellIds"] == [
                f"t-r1-c{entry['cell']['columnIndex']}"
            ]
            for entry in resolved
        ))

    def test_sibling_subtotals_build_derived_totals_across_captioned_blocks(self) -> None:
        built = _table([
            ("Products", "70", "60", "50"),
            ("Services", "30", "30", "30"),
            ("Total net sales", "100", "90", "80"),
            ("Cost of sales:", "", "", ""),
            ("Products", "30", "25", "20"),
            ("Services", "10", "10", "10"),
            ("Total cost of sales", "40", "35", "30"),
            ("Gross margin", "60", "55", "50"),
            ("Operating expenses:", "", "", ""),
            ("Research", "8", "7", "6"),
            ("Administrative", "2", "3", "4"),
            ("Total operating expenses", "10", "10", "10"),
            ("Operating income", "50", "45", "40"),
        ])
        resolved, _ = structures.resolve(built)
        by_label = {}
        for entry in resolved:
            by_label.setdefault(entry["cell"].get("rowLabel"), []).append(entry)
        for label in ("Gross margin", "Operating income"):
            self.assertEqual(
                [entry["outcome"] for entry in by_label[label]],
                ["confirmed", "confirmed", "confirmed"],
            )
            self.assertTrue(all(entry["resolution"]["basis"] == "subtotals" for entry in by_label[label]))

    def test_a_bad_column_cannot_veto_its_own_examination(self) -> None:
        built = _table([
            ("Opening", "10", "7", "20"),
            ("Movement", "5", "9", "30"),
            ("Ending", "15", "16", "59"),
        ])
        resolved, _ = structures.resolve(built)
        bad = next(entry for entry in resolved if entry["cell"]["columnIndex"] == 3)
        self.assertEqual(bad["outcome"], "break")
        self.assertEqual(bad["resolution"]["delta"], "9")
        self.assertEqual(bad["resolution"]["diagnosis"]["kind"], "transposition")

    def test_proportional_columns_count_as_one_piece_of_evidence(self) -> None:
        built = _table([
            ("Opening", "10", "20"),
            ("Movement", "5", "10"),
            ("Ending", "15", "30"),
        ])
        resolved, _ = structures.resolve(built)
        self.assertEqual(resolved, [])

    def test_proportional_labelled_columns_do_not_corroborate_each_other(self) -> None:
        built = _table([
            ("Opening", "10", "20"),
            ("Movement", "5", "10"),
            ("Total", "15", "30"),
        ])
        resolved, _ = structures.resolve(built)
        self.assertEqual(resolved, [])

    def test_zeroes_do_not_change_a_shared_signed_row_pattern(self) -> None:
        built = _table([
            ("Revenue", "100", "90"),
            ("Returns", "40", "—"),
            ("Discounts", "10", "30"),
            ("Net revenue", "50", "60"),
        ])
        resolved, _ = structures.resolve(built)
        self.assertEqual([entry["outcome"] for entry in resolved], [
            "confirmed", "confirmed",
        ])
        self.assertEqual(
            resolved[1]["resolution"]["negatedAddendCellIds"],
            ["t-r2-c2"],
        )

    def test_a_total_never_counts_a_subtotal_and_part_of_its_block(self) -> None:
        built = _table([
            ("Entertainment", "-1155", "-977"),
            ("Sports", "-3", "-10"),
            ("Domestic", "-5271", "-2710"),
            ("International", "-1158", "-949"),
            ("Total Experiences", "-6429", "-3659"),
            ("Corporate", "-437", "-766"),
            ("Total investments", "-8024", "-5412"),
        ])
        model, _ = _reconcile_block(built, page_index=0, findings=[])
        totals = [total for total in model["totals"] if total["rowIndex"] == 6]
        self.assertEqual([total["outcome"] for total in totals], [
            "confirmed", "confirmed",
        ])
        self.assertTrue(all(total["resolution"]["basis"] == "subtotals" for total in totals))
        self.assertTrue(all(len(total["resolution"]["addendCellIds"]) == 4 for total in totals))

    def test_a_nested_structure_propagates_the_deepest_owned_top(self) -> None:
        built = _table([
            ("Equipment", "82", "76"),
            ("Accumulated depreciation", "(49)", "(45)"),
            ("Property and equipment", "33", "31"),
            ("Projects", "7", "5"),
            ("Land", "1", "1"),
            ("Property, projects and land", "41", "37"),
        ])

        resolved, _ = structures.resolve(built)

        parents = [entry for entry in resolved if entry["cell"]["rowIndex"] == 5]
        self.assertEqual([entry["top"] for entry in parents], [0, 0])

    def test_total_assets_is_a_boundary_not_an_addend_of_the_other_side(self) -> None:
        built = _table([
            ("Total assets", "100", "110"),
            ("Current liabilities", "40", "50"),
            ("Long-term liabilities", "20", "20"),
            ("Total liabilities", "60", "70"),
            ("Total equity", "40", "40"),
            ("Total liabilities and equity", "100", "110"),
        ])

        model, _ = _reconcile_block(built, page_index=0, findings=[])

        by_id = {cell["id"]: cell for cell in model["cells"]}
        totals = [
            total
            for total in model["totals"]
            if by_id[total["cellId"]].get("rowLabel")
            == "Total liabilities and equity"
        ]
        self.assertEqual([total["outcome"] for total in totals], [
            "confirmed",
            "confirmed",
        ])
        self.assertTrue(all(
            "t-r0-c1" not in total["resolution"]["addendCellIds"]
            and "t-r0-c2" not in total["resolution"]["addendCellIds"]
            for total in totals
        ))

    def test_one_unlabelled_witness_cannot_accuse_an_established_column(self) -> None:
        built = _table([
            ("A", "1", "2"),
            ("B", "2", "5"),
            ("First ending", "3", "7"),
            ("C", "10", "4"),
            ("D", "20", "6"),
            ("Second ending", "31", "10"),
        ])
        resolved, _ = structures.resolve(built)
        accused = [
            entry for entry in resolved
            if entry["cell"]["rowIndex"] == 5
            and entry["cell"]["columnIndex"] == 1
        ]
        self.assertEqual(accused, [])

    def test_an_exact_permitted_structure_defeats_a_different_missing_one(self) -> None:
        built = _table([
            ("Opening", "1", "2", "5", "8"),
            ("First movement", "2", "3", "2", "4"),
            ("Second movement", "4", "6", "3", "7"),
            ("Total", "7", "11", "5", "11"),
        ])
        resolved, _ = structures.resolve(built)
        totals = [entry for entry in resolved if entry["cell"]["rowIndex"] == 3]
        self.assertEqual(len(totals), 4)
        self.assertTrue(all(entry["outcome"] == "confirmed" for entry in totals))
        self.assertEqual(
            len(next(entry for entry in totals if entry["cell"]["columnIndex"] == 3)["resolution"]["addendCellIds"]),
            2,
        )

    def test_the_hypothesis_cap_reduces_lookback_not_later_row_coverage(self) -> None:
        built = _table([
            ("A", "100"),
            ("B", "100"),
            ("C", "100"),
            ("D", "2"),
            ("E", "3"),
            ("Ending", "5"),
        ])
        original = structures.MAX_HYPOTHESES_PER_BLOCK
        structures.MAX_HYPOTHESES_PER_BLOCK = 4
        try:
            discovered, tested = structures.discover(built)
        finally:
            structures.MAX_HYPOTHESES_PER_BLOCK = original
        self.assertEqual(tested, 4)
        self.assertIn(5, [structure.total_row for structure in discovered])

    def test_rate_headers_are_not_accused(self) -> None:
        built = _table([
            ("Opening", "10", "7", "20"),
            ("Movement", "5", "9", "30"),
            ("Ending", "15", "16", "59"),
        ])
        built.header_labels = [{
            "columnIndex": 3, "text": "Average rate", "isTotalColumn": False,
            "isPeriodColumn": False,
        }]
        resolved, _ = structures.resolve(built)
        self.assertNotIn(3, [entry["cell"]["columnIndex"] for entry in resolved])

    def test_a_label_alone_cannot_accuse_from_the_looser_lattice(self) -> None:
        built = _table([
            ("Alpha", "10"),
            ("Beta", "20"),
            ("Gamma", "30"),
            ("Total", "51"),
        ])
        built.provenance = "lattice"
        built.bounds = {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}
        model, _ = _reconcile_block(built, page_index=0, findings=[])
        self.assertEqual(model["totals"][0]["outcome"], "unresolved")

    def test_a_resolved_grid_total_replaces_its_unresolved_lattice_duplicate(self) -> None:
        cell = {"id": "lattice-cell", "spanId": "shared-span"}
        lattice = {
            "cells": [cell],
            "totals": [{"id": "lattice-total", "cellId": cell["id"], "outcome": "unresolved"}],
        }
        grid_cell = {"id": "grid-cell", "spanId": "shared-span"}
        grid_total = {"id": "grid-total", "cellId": grid_cell["id"], "outcome": "confirmed"}
        selected, replaced = _select_grid_totals(
            {"cells": [grid_cell], "totals": [grid_total]},
            [lattice],
        )
        self.assertEqual(selected, [grid_total])
        self.assertEqual(replaced, {"lattice-total"})
        self.assertEqual(lattice["totals"], [])

    def test_an_exact_grid_total_replaces_a_lattice_break(self) -> None:
        cell = {"id": "lattice-cell", "spanId": "shared-span"}
        lattice = {
            "cells": [cell],
            "totals": [{"id": "lattice-total", "cellId": cell["id"], "outcome": "break"}],
        }
        grid_cell = {"id": "grid-cell", "spanId": "shared-span"}
        grid_total = {"id": "grid-total", "cellId": grid_cell["id"], "outcome": "confirmed"}
        selected, replaced = _select_grid_totals(
            {"cells": [grid_cell], "totals": [grid_total]},
            [lattice],
        )
        self.assertEqual(selected, [grid_total])
        self.assertEqual(replaced, {"lattice-total"})
        self.assertEqual(lattice["totals"], [])

    def test_a_composed_tie_requires_independent_column_structure(self) -> None:
        exact = {"id": "single", "outcome": "confirmed", "signals": [{"name": "label-total"}]}
        supported = {
            "id": "corroborated",
            "outcome": "confirmed",
            "signals": [{"name": "column-corroboration"}],
        }

        self.assertFalse(_is_supported_composed_total(exact))
        self.assertTrue(_is_supported_composed_total(supported))
        self.assertFalse(_is_supported_composed_total({
            "id": "miss",
            "outcome": "unresolved",
            "signals": [{"name": "column-corroboration"}],
        }))

    def test_parallel_exact_columns_support_a_composed_total(self) -> None:
        block = {
            "cells": [
                {"id": "a1", "rowIndex": 0, "normalizedValue": "40"},
                {"id": "b1", "rowIndex": 1, "normalizedValue": "60"},
                {"id": "a2", "rowIndex": 0, "normalizedValue": "45"},
                {"id": "b2", "rowIndex": 1, "normalizedValue": "65"},
            ],
            "totals": [
                {
                    "id": "t1", "rowIndex": 2, "columnIndex": 1,
                    "axis": "vertical", "outcome": "confirmed",
                    "resolution": {"addendCellIds": ["a1", "b1"], "negatedAddendCellIds": []},
                },
                {
                    "id": "t2", "rowIndex": 2, "columnIndex": 2,
                    "axis": "vertical", "outcome": "confirmed",
                    "resolution": {"addendCellIds": ["a2", "b2"], "negatedAddendCellIds": []},
                },
            ],
        }

        supported = _composed_parallel_total_ids(block)

        self.assertEqual(supported, frozenset({"t1", "t2"}))


class VerifiedStructurePropagation(unittest.TestCase):
    """A confirmed total is evidence for the same row in other columns."""

    def _nomination(self, built, row, column):
        return nominate.Nomination(
            cell=built.cell(row, column),
            signals=({"name": "label-total", "evidence": "test"},),
        )

    def _confirmed(self, built, column, addend_rows):
        return {
            "outcome": "confirmed",
            "resolution": {
                "basis": "leaves",
                "addendCellIds": [built.cell(row, column)["id"] for row in addend_rows],
                "negatedAddendCellIds": [],
                "sum": "0",
                "delta": "0",
            },
        }

    def test_a_structure_proved_in_two_columns_confirms_the_third(self) -> None:
        built = _table([
            ("Opening", "10", "20", "30"),
            ("Movement", "5", "6", "7"),
            ("Ending", "15", "26", "37"),
        ])
        # Columns 1 and 2 prove the structure [Opening, Movement] -> Ending.
        # Column 3 is left unresolved on purpose, to isolate the propagation:
        # it must confirm from the verified structure rather than its own walk.
        column_results = {
            1: [(self._nomination(built, 2, 1), self._confirmed(built, 1, (0, 1)))],
            2: [(self._nomination(built, 2, 2), self._confirmed(built, 2, (0, 1)))],
            3: [(
                self._nomination(built, 2, 3),
                {"outcome": "unresolved", "unresolvedReason": "run-too-short"},
            )],
        }

        confirmed = propagate.propagate(built, column_results)

        self.assertEqual(confirmed, {built.cell(2, 3)["id"]})
        outcome = column_results[3][0][1]
        self.assertEqual(outcome["outcome"], "confirmed")
        self.assertEqual(
            outcome["resolution"]["addendCellIds"],
            [built.cell(0, 3)["id"], built.cell(1, 3)["id"]],
        )
        self.assertEqual(outcome["signals"][0]["name"], "column-corroboration")

    def test_a_single_proof_does_not_propagate(self) -> None:
        built = _table([
            ("Opening", "10", "20", "30"),
            ("Movement", "5", "6", "7"),
            ("Ending", "15", "26", "37"),
        ])
        column_results = {
            1: [(self._nomination(built, 2, 1), self._confirmed(built, 1, (0, 1)))],
            3: [(
                self._nomination(built, 2, 3),
                {"outcome": "unresolved", "unresolvedReason": "run-too-short"},
            )],
        }

        self.assertEqual(propagate.propagate(built, column_results), set())
        self.assertEqual(column_results[3][0][1]["outcome"], "unresolved")

    def test_a_propagated_rounding_miss_is_recorded_and_not_confirmed(self) -> None:
        built = _table([
            ("Domestic", "59.3", "60.1", "61.2"),
            ("International", "72.3", "70.1", "69.4"),
            ("Total", "131.6", "130.2", "130.5"),
        ])
        # Columns 1 and 2 tie exactly; column 3 is 0.1 low, inside the interval
        # the printed tenths permit. The structure is proved, so the miss is
        # worth recording, but it is not proof and must not confirm.
        column_results = {
            1: [(self._nomination(built, 2, 1), self._confirmed(built, 1, (0, 1)))],
            2: [(self._nomination(built, 2, 2), self._confirmed(built, 2, (0, 1)))],
            3: [(
                self._nomination(built, 2, 3),
                {"outcome": "unresolved", "unresolvedReason": "run-too-short"},
            )],
        }
        misses: list[dict] = []

        self.assertEqual(propagate.propagate(built, column_results, miss_log=misses), set())
        self.assertEqual(column_results[3][0][1]["outcome"], "unresolved")
        self.assertEqual(len(misses), 1)
        self.assertTrue(misses[0]["withinRounding"])


class HeaderSemantics(unittest.TestCase):
    """Reconcile owns what a label means; table detection owns what it says."""

    def test_a_column_naming_a_total_is_the_only_cross_foot_target(self) -> None:
        self.assertTrue(labels.is_total_column("Total"))
        self.assertTrue(labels.is_total_column("Consolidated"))
        self.assertFalse(labels.is_total_column("Percentage of total"))
        # A result can be additive or subtractive; the cross-foot search handles
        # both while the period guard still refuses comparative year columns.
        self.assertTrue(labels.is_total_column("Net"))

    def test_a_period_column_is_recognized_however_the_filing_writes_it(self) -> None:
        for label in ("2025", "FY 2024", "Q1 2025", "September 28, 2024"):
            self.assertTrue(labels.is_period_column(label), label)
        self.assertFalse(labels.is_period_column("Adjusted Cost"))

    def test_amount_text_resists_a_concatenated_rate_header(self) -> None:
        self.assertFalse(labels.is_clearly_non_additive_column(
            "2024 Amount Effective (in millions) Interest Rate"
        ))
        self.assertTrue(labels.is_clearly_non_additive_column(
            "Weighted Average Exercise Price Per Share"
        ))
        self.assertTrue(labels.is_clearly_non_additive_column(
            "Maximum Number of Shares that May Yet Be Purchased"
        ))
        self.assertTrue(labels.is_clearly_non_additive_column(
            "Approximate Dollar Value of Shares That May Yet Be Purchased"
        ))


if __name__ == "__main__":
    unittest.main()
