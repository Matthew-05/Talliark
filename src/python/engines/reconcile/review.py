"""Adapt immutable scan evidence and apply explicit, revisioned human review.

The detector and its goldens remain unchanged. Everything from it begins as an
unreviewed suggestion. This module never changes the source analysis artifacts.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json

from engines.binary_codec import json_from_base64, json_to_base64
from schemas.reconcile_review import validate
from .evaluate import decimal_value, evaluate


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]


def _cell(page, cell, column=""):
    return {
        "id": "cell-" + _digest([page, cell["bounds"], cell.get("text", ""), cell.get("normalizedValue", ""), bool(cell.get("dash"))]),
        "pageIndex": page, "bounds": deepcopy(cell["bounds"]), "text": cell.get("text", ""),
        "label": cell.get("rowLabel", ""), "column": column,
        "value": str(cell.get("normalizedValue") or ("0" if cell.get("dash") else "")),
        "dash": bool(cell.get("dash")), "origin": "scan",
    }


def _effective(cell, corrections):
    return corrections.get(cell["id"], cell["value"])


def refresh(workspace):
    cells = {cell["id"]: cell for cell in workspace["evidence"]}
    corrections = {item["cellId"]: item["value"] for item in workspace["corrections"]}
    for equation in workspace["equations"]:
        target = cells[equation["targetId"]]
        operands = [cells[term["cellId"]] for term in equation["terms"]]
        values = [_effective(cell, corrections) for cell in [target, *operands]]
        signature = _digest([equation["axis"], target, equation["terms"], operands, values])
        if signature != equation["signature"]:
            equation["decision"] = "unreviewed"
            equation["issue"] = "open"
        equation["signature"] = signature
        equation["evaluation"] = evaluate(values[0], [(value, term["coefficient"]) for value, term in zip(values[1:], equation["terms"])])


def _equation(draft, origin):
    return {
        "id": "eq-" + _digest([draft["targetId"], draft["axis"], draft["terms"]]),
        **deepcopy(draft), "origin": origin, "decision": "unreviewed",
        "evaluation": {"state": "not-evaluable", "sum": "", "delta": "", "reason": ""},
        "issue": "open", "note": "", "signature": "",
    }


def from_scan(model, values=None):
    if model.get("version") != 1 or model.get("coordinateSpace") != "normalized":
        raise ValueError("Unsupported Reconcile scan. The original payload was preserved.")
    source = model["source"]
    workspace = {
        "version": 1, "documentId": source["documentId"], "scanId": _digest([model, values]),
        "geometryFingerprint": source["geometryFingerprint"], "pageCount": source["pageCount"],
        "revision": 0, "evidence": [], "regions": [], "equations": [],
        "corrections": [], "history": [], "archives": [], "reviewedPages": [],
    }
    cells, regions, equations = {}, {}, {}
    for page in range(workspace["pageCount"]):
        key = f"page-{page}"
        regions[key] = {"id": key, "pageIndex": page, "bounds": {"x": 0, "y": 0, "width": 1, "height": 1}, "label": f"Page {page + 1} · manual review"}
    for table in model["tables"]:
        page = table["pageIndex"]
        region_id = "region-" + _digest([page, table["bounds"]])
        regions[region_id] = {"id": region_id, "pageIndex": page, "bounds": table["bounds"], "label": f"Page {page + 1} · {table['rowCount']} rows"}
        aliases = {}
        for cell in table["cells"]:
            if "normalizedValue" not in cell and not cell.get("dash"):
                # Targets with unreadable values remain editable review targets.
                if not any(total["cellId"] == cell["id"] for total in table["totals"]):
                    continue
            column = next((header.get("period") or header["text"] for header in table.get("headerLabels", []) if header["columnIndex"] == cell["columnIndex"]), "")
            evidence = _cell(page, cell, column)
            cells.setdefault(evidence["id"], evidence)
            aliases[cell["id"]] = evidence["id"]
        for total in table["totals"]:
            resolution = total.get("resolution", {})
            negated = set(resolution.get("negatedAddendCellIds", []))
            draft = {"regionId": region_id, "targetId": aliases[total["cellId"]], "axis": total.get("axis", "vertical"),
                     "terms": [{"cellId": aliases[item], "coefficient": -1 if item in negated else 1} for item in resolution.get("addendCellIds", [])]}
            equation = _equation(draft, "detected")
            equations.setdefault(equation["id"], equation)
    # Manual sums must reach recognized numbers outside the detector's blocks.
    for page in (values or {}).get("pages", []):
        for span in page.get("values", []):
            if span.get("kind") != "number" or "normalizedValue" not in span or not span.get("clickable", True):
                continue
            evidence = _cell(page["pageIndex"], span)
            # The same span may already have a lattice cell with row/column labels.
            cells.setdefault(evidence["id"], evidence)
    workspace["evidence"] = list(cells.values())
    workspace["regions"] = list(regions.values())
    workspace["equations"] = list(equations.values())
    refresh(workspace)
    validate(workspace, "ReviewWorkspace")
    return workspace


def align(model, values, previous):
    return _align_workspace(from_scan(model, values), previous)


def _align_workspace(current, previous):
    if not previous:
        return deepcopy(current)
    validate(previous, "ReviewWorkspace")
    if previous["documentId"] != current["documentId"]:
        raise ValueError("Review belongs to another document snapshot.")
    if previous["scanId"] == current["scanId"]:
        return deepcopy(previous)
    current = deepcopy(current)
    current["revision"] = previous["revision"] + 1
    current["archives"] = deepcopy(previous["archives"]) + [
        {key: deepcopy(previous[key]) for key in ("scanId", "geometryFingerprint", "revision", "evidence", "regions", "equations", "corrections", "history", "reviewedPages")}
    ]
    # Only exactly unchanged evidence may retain decisions/corrections. Re-OCR
    # is explicitly a re-review event, even when some numbers stayed in place.
    if previous["geometryFingerprint"] == current["geometryFingerprint"]:
        cells = {cell["id"]: cell for cell in current["evidence"]}
        old_cells = {cell["id"]: cell for cell in previous["evidence"]}
        retained = {key for key in cells.keys() & old_cells.keys() if cells[key] == old_cells[key]}
        current["corrections"] = [deepcopy(item) for item in previous["corrections"] if item["cellId"] in retained]
        old_eqs = {eq["id"]: eq for eq in previous["equations"]}
        refresh(current)
        for equation in current["equations"]:
            old = old_eqs.get(equation["id"])
            if old and old["signature"] == equation["signature"]:
                for key in ("decision", "issue", "note"):
                    equation[key] = old[key]
        # Reviewer-created/edited equations are retained only if every anchor
        # and region is unchanged; all others remain in the historical scan.
        ids = {eq["id"] for eq in current["equations"]}
        region_ids = {region["id"] for region in current["regions"]}
        for equation in previous["equations"]:
            if equation["id"] not in ids and equation["origin"] == "manual" and equation["regionId"] in region_ids and {equation["targetId"], *(term["cellId"] for term in equation["terms"])} <= retained:
                current["equations"] = [eq for eq in current["equations"] if not (eq["targetId"] == equation["targetId"] and eq["axis"] == equation["axis"] and eq["origin"] == "detected")]
                current["equations"].append(deepcopy(equation))
        refresh(current)
    validate(current, "ReviewWorkspace")
    return current


@lru_cache(maxsize=1)
def _scan_workspace(model_base64, values_base64):
    """Keep one immutable scan substrate per worker; source changes miss the cache.

    Review alignment always copies before edits. The cache holds no decisions
    and is keyed by the complete scan and selectable-value payloads.
    """
    model = json_from_base64(model_base64)
    values = json_from_base64(values_base64) if values_base64 else None
    return from_scan(model, values)


def _snapshot(workspace):
    return {"revision": workspace["revision"], "at": datetime.now(timezone.utc).isoformat(),
            **{key: deepcopy(workspace[key]) for key in ("equations", "corrections", "evidence", "reviewedPages")}}


def _check_equation(workspace, draft, replacing=""):
    validate(draft, "ReviewDraft")
    cells = {cell["id"]: cell for cell in workspace["evidence"]}
    if draft["regionId"] not in {region["id"] for region in workspace["regions"]}:
        raise ValueError("Select a review region.")
    ids = [term["cellId"] for term in draft["terms"]]
    if draft["targetId"] not in cells or any(item not in cells for item in ids):
        raise ValueError("An equation references unavailable evidence.")
    if len(ids) < 2 or len(ids) != len(set(ids)) or draft["targetId"] in ids:
        raise ValueError("Select at least two distinct operands; the result cannot be its own operand.")
    # Shared operands are legitimate; cycles among result relationships are not.
    equations = [eq for eq in workspace["equations"] if eq["id"] != replacing and eq["decision"] == "accepted"]
    if any(eq["targetId"] == draft["targetId"] and eq["axis"] == draft["axis"] for eq in equations):
        raise ValueError("This result already has an approved relationship on this axis. Edit that relationship first.")
    edges = {}
    for equation in [*equations, draft]:
        edges.setdefault(equation["targetId"], set()).update(term["cellId"] for term in equation["terms"])
    def visit(node, active):
        if node in active:
            raise ValueError("This equation would create a circular relationship.")
        for child in edges.get(node, ()):
            visit(child, active | {node})
    for target in edges:
        visit(target, set())
    def descendants(node, seen):
        children = edges.get(node, set()) - seen
        return children | set().union(*(descendants(child, seen | {node}) for child in children)) if children else set()
    # Check existing parents too: accepting a child must not retroactively make
    # an already-approved parent count that child's components twice.
    for equation in [*equations, draft]:
        operands = {term["cellId"] for term in equation["terms"]}
        for operand in operands:
            if (operands - {operand}) & descendants(operand, set()):
                raise ValueError("The equation counts a reviewed subtotal and its component operands together.")


def apply_operations(workspace, operations):
    working = deepcopy(workspace)
    for operation in operations:
        validate(operation, "ReviewOperation")
        kind = operation["kind"]
        equations = {eq["id"]: eq for eq in working["equations"]}
        if kind == "equation":
            old_id = operation.get("equationId", "")
            if old_id and old_id not in equations:
                raise ValueError("The equation no longer exists.")
            draft = operation["equation"]
            _check_equation(working, draft, old_id)
            equation = _equation(draft, "manual")
            if equation["id"] in equations and equation["id"] != old_id:
                raise ValueError("This equation already exists.")
            working["equations"] = [eq for eq in working["equations"] if eq["id"] != old_id] + [equation]
            working["reviewedPages"] = []
        elif kind == "decision":
            for key in operation["equationIds"]:
                eq = equations.get(key)
                if eq is None:
                    raise ValueError("A selected equation no longer exists.")
                if operation["decision"] == "accepted" and eq["evaluation"]["state"] == "not-evaluable":
                    raise ValueError("Repair unavailable operands before accepting this relationship.")
                if operation["decision"] == "accepted":
                    _check_equation(working, {name: eq[name] for name in ("regionId", "targetId", "terms", "axis")}, eq["id"])
                eq["decision"] = operation["decision"]
        elif kind == "correction":
            correction = operation["correction"]
            if not correction["reason"].strip():
                raise ValueError("Record why the recognized value is being corrected.")
            if correction["cellId"] not in {cell["id"] for cell in working["evidence"]}:
                raise ValueError("The selected value no longer exists.")
            if correction["value"]:
                decimal_value(correction["value"])
            working["corrections"] = [item for item in working["corrections"] if item["cellId"] != correction["cellId"]]
            if correction["value"]:
                working["corrections"].append(deepcopy(correction))
            working["reviewedPages"] = []
        elif kind == "value":
            cell = deepcopy(operation["cell"])
            decimal_value(cell["value"])
            if not cell["text"].strip() or not cell["label"].strip():
                raise ValueError("Record the printed figure and why it is being added.")
            if cell["pageIndex"] >= working["pageCount"] or cell["bounds"]["x"] + cell["bounds"]["width"] > 1.000001 or cell["bounds"]["y"] + cell["bounds"]["height"] > 1.000001:
                raise ValueError("The value must be anchored inside a source page.")
            cell["origin"] = "manual"
            cell["id"] = "manual-" + _digest([cell["pageIndex"], cell["bounds"], cell["text"], cell["value"]])
            if cell["id"] not in {item["id"] for item in working["evidence"]}:
                working["evidence"].append(cell)
            working["reviewedPages"] = []
        elif kind == "issue":
            eq = equations[operation["equationId"]]
            if eq["decision"] != "accepted" or eq["evaluation"]["state"] != "difference":
                raise ValueError("Only a reviewed difference has an issue disposition.")
            note = operation.get("note", "").strip()
            if operation["issue"] != "open" and not note:
                raise ValueError("Record an explanation for this difference.")
            eq["issue"], eq["note"] = operation["issue"], note
        elif kind == "restore":
            prior = next((item for item in working["history"] if item["revision"] == operation["revision"]), None)
            if prior is None:
                raise ValueError("The selected review revision is unavailable.")
            for key in ("equations", "corrections", "evidence", "reviewedPages"):
                working[key] = deepcopy(prior[key])
        elif kind == "page":
            page = operation["pageIndex"]
            if page >= working["pageCount"]:
                raise ValueError("The page is outside this document.")
            working["reviewedPages"] = sorted((set(working["reviewedPages"]) | {page}) if operation["reviewed"] else (set(working["reviewedPages"]) - {page}))
        refresh(working)
    validate(working, "ReviewWorkspace")
    return working


def handle_job(job):
    validate(job, "ReviewWorkerJob")
    request = job["request"]
    response = {"type": "reconcile-review-response", "version": 1, "requestId": request["requestId"], "pdfId": request["pdfId"], "status": "error"}
    try:
        if request["mode"] == "save-workbook":
            raise ValueError("Workbook Save is handled only by the Excel host.")
        previous = json_from_base64(job["review_base64"]) if job["review_base64"] else None
        workspace = _align_workspace(_scan_workspace(job["model_base64"], job["values_base64"]), previous)
        if workspace["documentId"] != request["pdfId"]:
            raise ValueError("The source document was replaced.")
        mode = request["mode"]
        if mode != "load":
            if request["scanId"] != workspace["scanId"] or request["expectedRevision"] != workspace["revision"]:
                raise ValueError("This review changed. Reload it before applying your draft.")
            if not request["operations"]:
                raise ValueError("No review operation was provided.")
            working = apply_operations(workspace, request["operations"])
            if mode == "commit":
                working["history"].append(_snapshot(workspace))
                working["revision"] += 1
            workspace = working
        elif request["operations"]:
            raise ValueError("Loading review cannot apply changes.")
        response.update(status={"load": "loaded", "preview": "preview", "commit": "updated"}[mode], workspace=workspace)
        encoded = (job["review_base64"] if mode == "load" and workspace == previous else json_to_base64(workspace)) if mode != "preview" else ""
    except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as exc:
        response["error"] = str(exc)
        encoded = ""
    validate(response, "ReviewResponse")
    return {"job_id": job["job_id"], "status": "success", "response": response, "review_base64": encoded}
