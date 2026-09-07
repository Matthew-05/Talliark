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
from engines.reconcile import findings, labels, nominate, structures, sums
from engines.reconcile.cells import (
    TableCells,
    _label_for,
    decimals_of,
    is_label_column,
)
from engines.reconcile.detector import (
    DETECTOR_VERSION,
    _reconcile_block,
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
        # The re-detection is table detection's code, but reporting it as
        # `table-structure` would run a consumer's progress bar backwards.
        self.assertIn(Stage.RECONCILE_TABLES, self.stages)
        self.assertIn(Stage.RECONCILE, self.stages)
        self.assertNotIn(Stage.TABLE_STRUCTURE, self.stages)

    def test_diagnostics_separate_the_re_detection_from_the_analysis(self) -> None:
        self.assertEqual(self.diagnostics["reconcile_detector_version"], DETECTOR_VERSION)
        self.assertIn("reconcile_table_detection_ms", self.diagnostics)
        self.assertIn("reconcile_ms", self.diagnostics)

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
        for label in ("Cost of sales", "the total of these amounts", "Percentage of total"):
            self.assertFalse(labels.is_total_label(label), label)

    def test_label_and_column_corroboration_are_enabled(self) -> None:
        self.assertEqual(nominate.ENABLED_SIGNALS, ("label-total", "column-corroboration"))

    def test_corroboration_makes_two_addend_runs_publishable(self) -> None:
        # The structure search still supplies the signal per candidate: a
        # label-only nomination does not inherit it merely because it is enabled.
        self.assertTrue(nominate.two_addend_runs_publishable())

    def test_the_first_pass_confirms_on_exact_ties_only(self) -> None:
        self.assertEqual(sums.TOLERANCE, Decimal(0))

    def test_a_delta_divisible_by_nine_reads_as_a_transposition(self) -> None:
        self.assertTrue(sums.is_transposition(Decimal("9")))
        self.assertTrue(sums.is_transposition(Decimal("-18")))
        self.assertFalse(sums.is_transposition(Decimal("7")))
        self.assertFalse(sums.is_transposition(Decimal("0")))

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

    def test_a_blank_ends_the_candidate_rather_than_contributing_a_zero(self) -> None:
        built = _table([
            ("Cash", "10"),
            ("Receivables", "20"),
            ("Deferred tax assets:", ""),
            ("Inventories", "5"),
            ("Other", "5"),
            ("Total", "40"),
        ])
        # 10 + 20 + 5 + 5 is 40, and reaching it would mean reading through a
        # caption row the statement put there to separate two blocks.
        self.assertEqual(_resolve(built)[0]["outcome"], "unresolved")

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
        for reason in ("no-candidate-run", "run-too-short", "mixed-decimals"):
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


class HeaderSemantics(unittest.TestCase):
    """Reconcile owns what a label means; table detection owns what it says."""

    def test_a_column_naming_a_total_is_the_only_cross_foot_target(self) -> None:
        self.assertTrue(labels.is_total_column("Total"))
        self.assertTrue(labels.is_total_column("Consolidated"))
        self.assertFalse(labels.is_total_column("Percentage of total"))
        # "Net" heading a column means net of something, not the sum of its
        # neighbours, which is why the column lexicon is narrower than the row's.
        self.assertFalse(labels.is_total_column("Net"))

    def test_a_period_column_is_recognized_however_the_filing_writes_it(self) -> None:
        for label in ("2025", "FY 2024", "Q1 2025", "September 28, 2024"):
            self.assertTrue(labels.is_period_column(label), label)
        self.assertFalse(labels.is_period_column("Adjusted Cost"))


if __name__ == "__main__":
    unittest.main()
