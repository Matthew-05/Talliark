"""Review invariants, source preservation and real NDJSON worker dispatch."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from engines.binary_codec import json_from_base64, json_to_base64
from engines.reconcile.evaluate import evaluate, decimal_value
from engines.reconcile.review import from_scan, align, apply_operations, handle_job
from schemas.reconcile_review import validate


def sample_scan():
    cells = [{"id": str(i), "bounds": {"x": .5, "y": .1 + i * .1, "width": .1, "height": .02},
              "text": value, "normalizedValue": value, "rowLabel": label, "columnIndex": 0}
             for i, (value, label) in enumerate([("10", "Sales"), ("-2", "Returns"), ("8", "Net sales"), ("1", "Other"), ("9", "Total")])]
    return {"version": 1, "coordinateSpace": "normalized", "detectorVersion": "test-1",
            "source": {"documentId": "doc", "geometryFingerprint": "geometry-1", "pageCount": 2},
            "tables": [{"id": "table", "pageIndex": 0, "bounds": {"x": .1, "y": .1, "width": .6, "height": .6},
                        "rowCount": 5, "cells": cells, "headerLabels": [], "totals": [
                            {"id": "net", "cellId": "2", "axis": "vertical", "resolution": {"addendCellIds": ["0", "1"]}},
                            {"id": "total", "cellId": "4", "axis": "vertical", "resolution": {"addendCellIds": ["2", "3"]}}]}]}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.model = sample_scan()
        self.workspace = from_scan(self.model)
        self.net, self.total = self.workspace["equations"]
        self.cells = {cell["text"]: cell["id"] for cell in self.workspace["evidence"]}

    def decision(self, *ids, decision="accepted"):
        return {"kind": "decision", "equationIds": list(ids), "decision": decision}

    def job(self, mode="load", operations=None, previous=None, **request_fields):
        return {"job_id": "test-job", "command": "reconcile-review", "model_base64": json_to_base64(self.model),
                "values_base64": "", "review_base64": json_to_base64(previous) if previous else "",
                "request": {"type": "reconcile-review-request", "version": 1, "requestId": "request", "pdfId": "doc",
                            "mode": mode, "scanId": self.workspace["scanId"], "expectedRevision": 0,
                            "operations": operations or [], **request_fields}}

    def test_exact_suggestions_are_unreviewed(self):
        self.assertTrue(all(eq["decision"] == "unreviewed" and eq["evaluation"]["state"] == "exact-match" for eq in self.workspace["equations"]))

    def test_printed_dash_is_zero(self):
        cell = self.model["tables"][0]["cells"][1]
        cell.update(text="—", normalizedValue="", dash=True)
        self.model["tables"][0]["cells"][2]["normalizedValue"] = "10"
        self.assertEqual(from_scan(self.model)["equations"][0]["evaluation"]["state"], "exact-match")

    def test_source_is_unchanged(self):
        original = deepcopy(self.model)
        apply_operations(self.workspace, [self.decision(self.net["id"])])
        self.assertEqual(original, self.model)
        self.assertEqual(self.net["decision"], "unreviewed")

    def test_preview_is_not_persisted(self):
        result = handle_job(self.job("preview", [self.decision(self.net["id"])], self.workspace))
        self.assertEqual(result["response"]["status"], "preview")
        self.assertEqual(result["review_base64"], "")
        self.assertEqual(result["response"]["workspace"]["revision"], 0)

    def test_commit_roundtrip_and_history(self):
        result = handle_job(self.job("commit", [self.decision(self.net["id"])], self.workspace))
        saved = json_from_base64(result["review_base64"])
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(saved["equations"][0]["decision"], "accepted")
        self.assertEqual(saved["history"][0]["equations"][0]["decision"], "unreviewed")
        loaded = handle_job(self.job(previous=saved))
        self.assertEqual(loaded["review_base64"], json_to_base64(saved))

    def test_undo_is_a_new_revision(self):
        saved = handle_job(self.job("commit", [self.decision(self.net["id"])], self.workspace))["response"]["workspace"]
        restored = handle_job(self.job("commit", [{"kind": "restore", "revision": 0}], saved, expectedRevision=1))["response"]["workspace"]
        self.assertEqual(restored["revision"], 2)
        self.assertEqual(restored["equations"][0]["decision"], "unreviewed")
        self.assertEqual(len(restored["history"]), 2)

    def test_bulk_is_atomic_on_failure(self):
        result = handle_job(self.job("commit", [self.decision(self.net["id"], "missing")], self.workspace))
        self.assertEqual(result["response"]["status"], "error")
        self.assertEqual(result["review_base64"], "")
        self.assertEqual(self.net["decision"], "unreviewed")

    def test_revision_conflict(self):
        result = handle_job(self.job("commit", [self.decision(self.net["id"])], self.workspace, expectedRevision=3))
        self.assertEqual(result["response"]["status"], "error")
        self.assertIn("changed", result["response"]["error"])

    def test_scan_conflict(self):
        result = handle_job(self.job("commit", [self.decision(self.net["id"])], self.workspace, scanId="old"))
        self.assertEqual(result["response"]["status"], "error")

    def test_reject_and_defer_persist(self):
        for decision in ("rejected", "deferred"):
            changed = apply_operations(self.workspace, [self.decision(self.net["id"], decision=decision)])
            self.assertEqual(changed["equations"][0]["decision"], decision)

    def test_correction_invalidates_only_dependencies(self):
        workspace = apply_operations(self.workspace, [self.decision(self.net["id"], self.total["id"])])
        changed = apply_operations(workspace, [{"kind": "correction", "correction": {"cellId": self.cells["10"], "value": "11", "reason": "Printed eleven"}}])
        self.assertEqual(changed["equations"][0]["decision"], "unreviewed")
        self.assertEqual(changed["equations"][1]["decision"], "accepted")
        self.assertEqual(changed["equations"][0]["evaluation"]["delta"], "-1")
        self.assertEqual(changed["evidence"], workspace["evidence"])

    def test_correction_remove_and_reason_required(self):
        correction = {"kind": "correction", "correction": {"cellId": self.cells["10"], "value": "11", "reason": "Eleven"}}
        changed = apply_operations(self.workspace, [correction])
        correction["correction"]["value"] = ""
        self.assertEqual(apply_operations(changed, [correction])["equations"][0]["evaluation"]["state"], "exact-match")
        correction["correction"]["reason"] = " "
        with self.assertRaises(ValueError): apply_operations(changed, [correction])

    def test_mismatch_can_be_approved_without_hiding_difference(self):
        self.model["tables"][0]["cells"][2]["normalizedValue"] = "7"
        workspace = from_scan(self.model)
        changed = apply_operations(workspace, [self.decision(workspace["equations"][0]["id"])])
        self.assertEqual(changed["equations"][0]["decision"], "accepted")
        self.assertEqual(changed["equations"][0]["evaluation"]["state"], "difference")
        self.assertEqual(changed["equations"][0]["issue"], "open")

    def test_issue_disposition_preserves_numeric_difference(self):
        workspace = apply_operations(self.workspace, [{"kind": "correction", "correction": {"cellId": self.cells["10"], "value": "11", "reason": "Printed"}}, self.decision(self.net["id"])])
        operation = {"kind": "issue", "equationId": self.net["id"], "issue": "explained", "note": "Rounding"}
        changed = apply_operations(workspace, [operation])
        self.assertEqual(changed["equations"][0]["evaluation"]["state"], "difference")
        self.assertEqual(changed["equations"][0]["issue"], "explained")
        operation["note"] = " "
        with self.assertRaises(ValueError): apply_operations(workspace, [operation])

    def test_unreadable_cannot_be_approved(self):
        self.model["tables"][0]["cells"][2].pop("normalizedValue")
        workspace = from_scan(self.model)
        with self.assertRaises(ValueError): apply_operations(workspace, [self.decision(workspace["equations"][0]["id"])])

    def test_edit_resets_approval_and_uses_printed_signs(self):
        workspace = apply_operations(self.workspace, [self.decision(self.net["id"])])
        draft = {key: deepcopy(self.net[key]) for key in ("targetId", "regionId", "terms", "axis")}
        draft["terms"][1]["coefficient"] = -1
        changed = apply_operations(workspace, [{"kind": "equation", "equationId": self.net["id"], "equation": draft}])
        edited = changed["equations"][-1]
        self.assertEqual(edited["decision"], "unreviewed")
        self.assertEqual(edited["evaluation"]["sum"], "12")

    def test_duplicate_self_and_too_few_operands_rejected(self):
        draft = {key: deepcopy(self.net[key]) for key in ("targetId", "regionId", "terms", "axis")}
        for terms in ([draft["terms"][0]], [draft["terms"][0]] * 2, [{"cellId": draft["targetId"], "coefficient": 1}, draft["terms"][0]]):
            with self.assertRaises(ValueError): apply_operations(self.workspace, [{"kind": "equation", "equationId": self.net["id"], "equation": {**draft, "terms": terms}}])

    def test_cycles_rejected(self):
        workspace = apply_operations(self.workspace, [self.decision(self.net["id"], self.total["id"])])
        draft = {"regionId": self.net["regionId"], "targetId": self.cells["10"], "axis": "manual",
                 "terms": [{"cellId": self.cells["9"], "coefficient": 1}, {"cellId": self.cells["1"], "coefficient": 1}]}
        with self.assertRaisesRegex(ValueError, "circular"): apply_operations(workspace, [{"kind": "equation", "equation": draft}])

    def test_subtotal_components_not_double_counted_in_either_approval_order(self):
        draft = {"regionId": self.net["regionId"], "targetId": self.cells["9"], "axis": "vertical",
                 "terms": [{"cellId": self.cells["8"], "coefficient": 1}, {"cellId": self.cells["10"], "coefficient": 1}]}
        workspace = apply_operations(self.workspace, [{"kind": "equation", "equationId": self.total["id"], "equation": draft}])
        parent = workspace["equations"][-1]["id"]
        for ids in ((self.net["id"], parent), (parent, self.net["id"])):
            with self.assertRaisesRegex(ValueError, "subtotal"): apply_operations(workspace, [self.decision(*ids)])

    def test_manual_value_validates_source_anchor(self):
        cell = {**deepcopy(self.workspace["evidence"][0]), "id": "", "pageIndex": 1, "value": "5", "text": "5"}
        changed = apply_operations(self.workspace, [{"kind": "value", "cell": cell}])
        self.assertEqual(changed["evidence"][-1]["origin"], "manual")
        cell["pageIndex"] = 2
        with self.assertRaises(ValueError): apply_operations(self.workspace, [{"kind": "value", "cell": cell}])
        cell["pageIndex"] = 1; cell["bounds"]["x"] = .95
        with self.assertRaises(ValueError): apply_operations(self.workspace, [{"kind": "value", "cell": cell}])

    def test_manual_value_requires_printed_text_and_reason(self):
        cell = deepcopy(self.workspace["evidence"][0]); cell["label"] = " "
        with self.assertRaises(ValueError): apply_operations(self.workspace, [{"kind": "value", "cell": cell}])

    def test_numbers_outside_detected_tables_are_available(self):
        values = {"pages": [{"pageIndex": 1, "values": [{"kind": "number", "bounds": {"x": .1, "y": .1, "width": .1, "height": .02}, "text": "3", "normalizedValue": "3"}]}]}
        self.assertEqual(from_scan(self.model, values)["evidence"][-1]["pageIndex"], 1)
        self.assertEqual(len(self.workspace["regions"]), 3)

    def test_value_catalogue_clickability_is_respected(self):
        values = {"pages": [{"pageIndex": 1, "values": [{"kind": "number", "clickable": False, "bounds": {"x": .1, "y": .1, "width": .1, "height": .02}, "text": "3", "normalizedValue": "3"}]}]}
        self.assertEqual(from_scan(self.model, values)["evidence"], self.workspace["evidence"])

    def test_page_review_is_explicit_and_edits_clear_it(self):
        workspace = apply_operations(self.workspace, [{"kind": "page", "pageIndex": 1, "reviewed": True}])
        self.assertEqual(workspace["reviewedPages"], [1])
        changed = apply_operations(workspace, [{"kind": "correction", "correction": {"cellId": self.cells["10"], "value": "11", "reason": "Printed"}}])
        self.assertEqual(changed["reviewedPages"], [])

    def test_same_scan_is_idempotent(self):
        reviewed = apply_operations(self.workspace, [self.decision(self.net["id"])])
        self.assertEqual(align(self.model, None, reviewed), reviewed)

    def test_detector_upgrade_retains_exact_evidence_only(self):
        reviewed = apply_operations(self.workspace, [self.decision(self.net["id"])])
        self.model["detectorVersion"] = "test-2"
        aligned = align(self.model, None, reviewed)
        self.assertEqual(aligned["equations"][0]["decision"], "accepted")
        self.assertEqual(aligned["archives"][0]["equations"], reviewed["equations"])
        self.assertNotEqual(aligned["scanId"], reviewed["scanId"])

    def test_reocr_archives_approvals(self):
        reviewed = apply_operations(self.workspace, [self.decision(self.net["id"])])
        self.model["source"]["geometryFingerprint"] = "geometry-2"
        aligned = align(self.model, None, reviewed)
        self.assertEqual(aligned["equations"][0]["decision"], "unreviewed")
        self.assertEqual(aligned["archives"][0]["equations"][0]["decision"], "accepted")

    def test_changed_evidence_never_keeps_approval(self):
        reviewed = apply_operations(self.workspace, [self.decision(self.net["id"])])
        self.model["tables"][0]["cells"][0]["normalizedValue"] = "11"
        self.assertEqual(align(self.model, None, reviewed)["equations"][0]["decision"], "unreviewed")

    def test_edited_equation_survives_upgrade_without_old_proposal(self):
        draft = {key: deepcopy(self.net[key]) for key in ("targetId", "regionId", "terms", "axis")}
        draft["terms"][1]["coefficient"] = -1
        reviewed = apply_operations(self.workspace, [{"kind": "equation", "equationId": self.net["id"], "equation": draft}])
        self.model["detectorVersion"] = "test-2"
        self.assertEqual(len(align(self.model, None, reviewed)["equations"]), 2)

    def test_unknown_contract_properties_rejected(self):
        with self.assertRaises(ValueError): validate({**self.workspace, "hidden": True}, "ReviewWorkspace")
        with self.assertRaises(ValueError): validate({"kind": "page", "pageIndex": True, "reviewed": True}, "ReviewOperation")

    def test_generated_schema_and_samples_match_contract(self):
        from schemas.reconcile_review_generated import SCHEMA
        root = Path(__file__).resolve().parents[3]
        self.assertEqual(SCHEMA, json.loads((root / "contracts/reconcile-review-v1.json").read_text()))
        sample = json.loads((root / "contracts/reconcile-review-v1.sample.json").read_text())
        validate(sample, "ReviewWorkspace")
        import xml.etree.ElementTree as ET
        stored = ET.parse(root / "contracts/talliark-storage-reconcile-review-v1.sample.xml")
        encoded = stored.find(".//{urn:talliark:schemas:storage:1:reconcile-review}ReviewBase64").text
        self.assertEqual(json_from_base64(encoded), sample)

    def test_other_document_rejected(self):
        previous = deepcopy(self.workspace); previous["documentId"] = "other"
        self.assertEqual(handle_job(self.job(previous=previous))["response"]["status"], "error")

    def test_real_worker_process_accepts_review_without_ocr(self):
        python_root = Path(__file__).resolve().parents[1]
        bootstrap = f"import sys, runpy; sys.path.insert(0, {str(python_root)!r}); runpy.run_path({str(python_root / 'worker.py')!r}, run_name='__main__')"
        jobs = [self.job(), self.job("commit", [self.decision(self.net["id"])], self.workspace)]
        result = subprocess.run([sys.executable, "-c", bootstrap], input="".join(json.dumps(job) + "\n" for job in jobs), text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([reply["response"]["status"] for reply in replies], ["loaded", "saved"])


class ReviewArithmeticTests(unittest.TestCase):
    def test_exact_decimal_arithmetic(self):
        self.assertEqual(evaluate("0.3", [("0.1", 1), ("0.2", 1)])["state"], "exact-match")

    def test_precision_beyond_default_decimal_context(self):
        value = "123456789012345678901234567890.123456789"
        self.assertEqual(evaluate(value, [(value, 1), ("0", 1)])["delta"], "0.000000000")

    def test_subtraction_of_printed_negative_value(self):
        self.assertEqual(evaluate("12", [("10", 1), ("-2", -1)])["state"], "exact-match")

    def test_missing_input_is_not_a_tie(self):
        self.assertEqual(evaluate("0", [("", 1), ("0", 1)])["state"], "not-evaluable")

    def test_invalid_manual_decimals_rejected(self):
        for value in ("NaN", "1e3", "1,000", "+1", "01", "9" * 257):
            with self.assertRaises(ValueError): decimal_value(value)

    def test_large_difference_is_never_suppressed(self):
        self.assertEqual(evaluate("1000", [("1", 1), ("2", 1)])["delta"], "997")


if __name__ == "__main__": unittest.main()
