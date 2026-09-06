"""Orchestration and the model envelope for one Reconcile scan.

A scan is not a background pass. The user selects a document and runs it, and it
arrives here as the tail of an `ocr` job that set its analysis flag -- one
command, one job id, one progress stream, one cancellation. A separate worker
command was considered and rejected: it would have duplicated the whole
pipeline's orchestration to add a stage at the end of it.

The scan re-runs table detection **unbudgeted and privately**, and this looks
wasteful until the reason is stated. Table detection in the cache build is tuned
for linking: it runs on every document, under a budget, and most of the time
nobody looks at its output except to place a rectangle. A sum tree cannot be
built on a truncated or approximate grid -- a missed column boundary does not
degrade a finding, it invents one. So the scan pays for detection again, without
the budget, and keeps the result to itself. The viewer keeps the model it has,
because linking accuracy is not what changed.

R-0 is the skeleton: this produces a valid, empty `reconcile-v1` model with a
complete source envelope, so storage, staleness and the window can be built and
exercised end to end before there is a sum tree to put in it. R-1 adds the cell
layer, R-2 nomination and the sum tree.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone

from engines.binary_codec import json_to_base64
from engines.table.detector import detect_tables
from schemas.models import Stage


DETECTOR_VERSION = "reconcile-detector-0"

# Zero disables the table detector's wall-clock budget entirely. This is R-5 in
# one argument: the scan is generous because the user chose to wait.
UNBUDGETED = 0


def geometry_fingerprint(geometry: dict) -> str:
    """A stable hash over the text geometry a scan read.

    Span ids are content-addressed and survive a detector upgrade, but not a
    re-OCR that moves geometry: rebuilt geometry moves bounds, and a moved span
    is a new id. This is what lets a stored result notice that its anchors no
    longer refer to the document the workbook now holds, so it can be shown as
    stale rather than silently recomputed.
    """
    payload = json.dumps(geometry, separators=(",", ":"), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _stage_as(progress_callback, stage: str):
    """Report another engine's progress under one of the scan's own stages.

    The re-detection is table detection's code but it is not the cache build's
    `table-structure` step, and a consumer placing stages on a bar would run the
    bar backwards if it were reported as one.
    """
    if progress_callback is None:
        return None

    def report(message: str, _stage: str, *, current=None, total=None, unit=None) -> None:
        progress_callback(message, stage, current=current, total=total, unit=unit)

    return report


def detect_reconcile(
    pdf_bytes: bytes,
    geometry: dict,
    *,
    document_id: str,
    values: dict | None = None,
    financial: dict | None = None,
    progress_callback=None,
    diagnostics: dict | None = None,
) -> dict:
    """Run one scan and return the `reconcile-v1` model.

    `values` is the `document-values-v1` model the same job just produced; the
    cell layer joins its spans to the grid by geometry, and R-6 of the plan --
    that Reconcile derives its own value-to-cell membership -- is why nothing
    here writes back into it.
    """
    table_started = time.perf_counter()
    if progress_callback:
        progress_callback("Finding tables for the scan…", Stage.RECONCILE_TABLES)
    table_diagnostics: dict = {}
    tables = detect_tables(
        pdf_bytes,
        geometry,
        progress_callback=_stage_as(progress_callback, Stage.RECONCILE_TABLES),
        diagnostics=table_diagnostics,
        budget_ms=UNBUDGETED,
    )
    table_detection_ms = int((time.perf_counter() - table_started) * 1000)

    analysis_started = time.perf_counter()
    if progress_callback:
        progress_callback("Reconciling totals…", Stage.RECONCILE)

    # R-1 builds the cell layer over `tables` here; R-2 nominates and foots it.
    # Until then the scan examines every detected table and nominates nothing,
    # which is a truthful empty result rather than a placeholder: a document
    # with nothing to check must produce a model that says so.
    published: list[dict] = []
    findings: list[dict] = []

    tables_examined = sum(len(page.get("tables", [])) for page in tables.get("pages", []))

    model = {
        "version": 1,
        "coordinateSpace": "normalized",
        "detectorVersion": DETECTOR_VERSION,
        "source": _source(
            document_id=document_id,
            geometry=geometry,
            tables=tables,
            values=values,
            financial=financial,
        ),
        "summary": {
            "tablesExamined": tables_examined,
            "totalsNominated": 0,
            "confirmed": 0,
            "breaks": 0,
            "unresolved": 0,
        },
        "tables": published,
        "findings": findings,
    }

    if diagnostics is not None:
        diagnostics["reconcile_detector_version"] = DETECTOR_VERSION
        diagnostics["reconcile_table_detection_ms"] = table_detection_ms
        diagnostics["reconcile_ms"] = int((time.perf_counter() - analysis_started) * 1000)
        summary = model["summary"]
        diagnostics["reconcile_tables_examined"] = summary["tablesExamined"]
        diagnostics["reconcile_totals_nominated"] = summary["totalsNominated"]
        diagnostics["reconcile_confirmed"] = summary["confirmed"]
        diagnostics["reconcile_breaks"] = summary["breaks"]
        diagnostics["reconcile_unresolved"] = summary["unresolved"]

    return model


def _source(
    *,
    document_id: str,
    geometry: dict,
    tables: dict,
    values: dict | None,
    financial: dict | None,
) -> dict:
    """What this result was produced from, for the staleness rule.

    Staleness is decided by these versions and the fingerprint, never by age. A
    result whose source no longer matches the workbook is shown labelled stale
    and only an explicit re-scan replaces it, because a reviewer who has read a
    finding should never find it quietly changed underneath them.
    """
    source = {
        "documentId": document_id,
        "pageCount": len(geometry.get("pages", [])),
        "geometryFingerprint": geometry_fingerprint(geometry),
        "tableDetectorVersion": tables.get("detectorVersion", ""),
        "valueDetectorVersion": (values or {}).get("detectorVersion", ""),
        "scannedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    financial_version = (financial or {}).get("detectorVersion", "")
    if financial_version:
        source["financialStructureDetectorVersion"] = financial_version
    return source


def reconcile_to_base64(model: dict) -> str:
    return json_to_base64(model)
