"""What a Reconcile scan publishes, and the invariants that outlive R-0.

R-0 is the skeleton: the scan runs, examines every detected table, nominates
nothing, and stores a truthful empty model. So most of what can be asserted here
is the envelope and the decisions -- which is the point. The envelope is what
storage, staleness and the window are built against, and it has to be right
before there is a sum tree to put in it.

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
from engines.reconcile import findings, labels, nominate, sums
from engines.reconcile.detector import (
    DETECTOR_VERSION,
    detect_reconcile,
    geometry_fingerprint,
)
from schemas.models import STAGE_ORDER, Stage


CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tables" / "synthetic"


def _document() -> bytes:
    source = pymupdf.open(str(FIXTURES / "ruled-grid.pdf"))
    try:
        return source.tobytes()
    finally:
        source.close()


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
        cls.stages: list[str] = []
        cls.diagnostics: dict = {}
        cls.model = detect_reconcile(
            cls.pdf,
            cls.geometry,
            document_id="7c9e6679-7425-40de-944b-e07fc1f90ae7",
            values={"detectorVersion": "document-values-detector-3"},
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

    def test_a_first_pass_scan_examines_tables_and_nominates_nothing(self) -> None:
        summary = self.model["summary"]
        self.assertGreater(summary["tablesExamined"], 0)
        self.assertEqual(summary["totalsNominated"], 0)
        self.assertEqual(self.model["tables"], [])
        self.assertEqual(self.model["findings"], [])

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

    def test_only_the_label_signal_is_enabled_in_the_first_pass(self) -> None:
        self.assertEqual(nominate.ENABLED_SIGNALS, ("label-total",))

    def test_two_addend_runs_are_unpublishable_until_a_second_signal_exists(self) -> None:
        # A pair that happens to sum is nearly evidence-free, so this stays
        # false until `ruling-above` or `outdent` is enabled -- which makes one
        # of them a first-pass dependency rather than a later refinement.
        self.assertFalse(nominate.two_addend_runs_publishable())

    def test_the_first_pass_confirms_on_exact_ties_only(self) -> None:
        self.assertEqual(sums.TOLERANCE, Decimal(0))

    def test_a_delta_divisible_by_nine_reads_as_a_transposition(self) -> None:
        self.assertTrue(sums.is_transposition(Decimal("9")))
        self.assertTrue(sums.is_transposition(Decimal("-18")))
        self.assertFalse(sums.is_transposition(Decimal("7")))
        self.assertFalse(sums.is_transposition(Decimal("0")))

    def test_no_finding_kind_words_an_unresolved_total_as_a_failure(self) -> None:
        self.assertIn("footing-unresolved", findings.KINDS)


if __name__ == "__main__":
    unittest.main()
