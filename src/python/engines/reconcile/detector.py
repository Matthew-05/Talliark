"""Orchestration and the model envelope for one Reconcile scan.

A scan is not a background pass. The user selects a document and runs it, and it
arrives here as the tail of an `ocr` job that set its analysis flag -- one
command, one job id, one progress stream, one cancellation. A separate worker
command was considered and rejected: it would have duplicated the whole
pipeline's orchestration to add a stage at the end of it.

The scan builds a page-local lattice from recognized value geometry. Detected
tables are optional corroboration for headers and boundaries; they are not the
substrate and are never re-run privately. Parallel columns nominate row
structures under leave-one-out evidence, while explicit total labels remain a
co-equal route for single-column statements.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from decimal import Decimal

from engines.binary_codec import json_to_base64
from engines.table.layout import build_page_layout
from schemas.models import Stage

from . import findings as findings_module
from . import nominate as nominate_module
from . import sums
from . import structures
from .cells import build_table_cells
from .lattice import build_page_lattice


DETECTOR_VERSION = "reconcile-detector-3-signed-runs"


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


def detect_reconcile(
    pdf_bytes: bytes,
    geometry: dict,
    *,
    document_id: str,
    values: dict | None = None,
    financial: dict | None = None,
    tables: dict | None = None,
    progress_callback=None,
    diagnostics: dict | None = None,
) -> dict:
    """Run one scan and return the `reconcile-v1` model.

    `values` is the `document-values-v1` model the same job just produced; the
    cell layer joins its spans to the grid by geometry, and R-6 of the plan --
    that Reconcile derives its own value-to-cell membership -- is why nothing
    here writes back into it.
    """
    analysis_started = time.perf_counter()
    if progress_callback:
        progress_callback("Building the footing lattice…", Stage.RECONCILE_TABLES)
        progress_callback("Reconciling totals…", Stage.RECONCILE)

    values_by_page = {
        int(page["pageIndex"]): page for page in (values or {}).get("pages", [])
    }

    table_model = tables or {"detectorVersion": "unavailable", "pages": []}
    detected_by_page = {
        int(page["pageIndex"]): list(page.get("tables", []))
        for page in table_model.get("pages", [])
    }
    published: list[dict] = []
    findings: list[dict] = []
    blocks_examined = 0
    hypotheses = 0
    disagreements = 0
    for position, page_geometry in enumerate(geometry.get("pages", [])):
        page_index = int(page_geometry["pageIndex"])
        if progress_callback:
            progress_callback(
                "Reconciling totals…",
                Stage.RECONCILE,
                current=position + 1,
                total=len(geometry.get("pages", [])),
                unit="pages",
            )
        layout = build_page_layout(page_geometry)
        blocks = build_page_lattice(
            layout,
            values_by_page.get(page_index),
            page_index=page_index,
            detected_tables=detected_by_page.get(page_index, []),
        )
        blocks_examined += len(blocks)
        disagreements += _boundary_disagreements(blocks, detected_by_page.get(page_index, []))
        for block in blocks:
            reconciled, block_diagnostics = _reconcile_block(
                block, page_index=page_index, findings=findings
            )
            hypotheses += block_diagnostics["hypotheses"]
            published.append(reconciled)

        # The ADR's bake-off gate chose the union fallback when pure lattice
        # recall trailed the grid corpus. Both substrates use the identical
        # corroboration policy; span identity removes duplicate totals.
        for table in detected_by_page.get(page_index, []):
            grid = build_table_cells(
                table, layout, values_by_page.get(page_index), page_index=page_index
            )
            grid_findings: list[dict] = []
            reconciled, grid_diagnostics = _reconcile_block(
                grid, page_index=page_index, findings=grid_findings
            )
            hypotheses += grid_diagnostics["hypotheses"]
            selected, replaced_ids = _select_grid_totals(reconciled, published)
            if not selected:
                continue
            selected_ids = {total["id"] for total in selected}
            reconciled["totals"] = selected
            published.append(reconciled)
            blocks_examined += 1
            if replaced_ids:
                findings[:] = [
                    finding for finding in findings
                    if finding["totalId"] not in replaced_ids
                ]
            findings.extend(
                finding for finding in grid_findings if finding["totalId"] in selected_ids
            )

    model = {
        "version": 1,
        "coordinateSpace": "normalized",
        "detectorVersion": DETECTOR_VERSION,
        "source": _source(
            document_id=document_id,
            geometry=geometry,
            tables=table_model,
            values=values,
            financial=financial,
        ),
        "summary": _summary(blocks_examined, published),
        "tables": published,
        "findings": findings,
    }

    if diagnostics is not None:
        diagnostics["reconcile_detector_version"] = DETECTOR_VERSION
        diagnostics["reconcile_table_detection_ms"] = 0
        diagnostics["reconcile_structure_hypotheses"] = hypotheses
        diagnostics["reconcile_substrate_disagreements"] = disagreements
        diagnostics["reconcile_ms"] = int((time.perf_counter() - analysis_started) * 1000)
        summary = model["summary"]
        diagnostics["reconcile_tables_examined"] = summary["tablesExamined"]
        diagnostics["reconcile_totals_nominated"] = summary["totalsNominated"]
        diagnostics["reconcile_confirmed"] = summary["confirmed"]
        diagnostics["reconcile_breaks"] = summary["breaks"]
        diagnostics["reconcile_unresolved"] = summary["unresolved"]
        # Why a total went unresolved separates a limit of the scan from
        # something worth looking at, and the split is what says whether the
        # next signal to enable is `ruling-above` or `outdent`.
        for reason, count in _unresolved_reasons(published).items():
            diagnostics[f"reconcile_unresolved_{reason.replace('-', '_')}"] = count

    return model


def _reconcile_block(
    built,
    *,
    page_index: int,
    findings: list[dict],
) -> tuple[dict, dict]:
    """One lattice or grid-fallback block: its cells, totals, and tree.

    Totals in a column are resolved from the top down, because a total that
    foots on the subtotals beneath it has to know where each of those subtotals
    began -- the row a subtotal's own run started at is where the run above it
    resumes, and stepping over that block is what keeps a subtotal's addends
    from being counted twice.
    """
    corroborated, structure_diagnostics = structures.resolve(built)
    claimed = {proposal["cell"]["id"] for proposal in corroborated}
    nominations = [nomination for nomination in nominate_module.nominate(built) if nomination.cell["id"] not in claimed]

    # A row the structure search corroborated is a subtotal as surely as a
    # label-nominated one, and it already knows the top of the block it
    # consumed. Both facts have to reach the run search or a total standing over
    # corroborated subtotals has no tree to foot on: the walk would take the
    # subtotal's own addends a second time, or -- because a corroborated row is
    # not in `nominated_rows` -- run straight through it. Apple's marketable-
    # securities note is the case: its two Subtotal rows are corroborated across
    # seven columns, and without this the Total above them resolves only in the
    # columns whose Cash row prints a dash.
    #
    # A corroborated break contributes the row and no top, which is exactly what
    # §7.2 asks for: a block whose own top is unknown is not stepped over.
    corroborated_blocks: dict[int, dict[int, int | None]] = {}
    for proposal in corroborated:
        cell = proposal["cell"]
        corroborated_blocks.setdefault(int(cell["columnIndex"]), {})[
            int(cell["rowIndex"])
        ] = int(proposal["top"]) if proposal["outcome"] == "confirmed" else None

    by_column: dict[int, list] = {}
    for nomination in nominations:
        by_column.setdefault(nomination.column_index, []).append(nomination)

    totals: list[dict] = []
    labelled = {
        header["columnIndex"]: header.get("period") or header["text"]
        for header in built.header_labels
    }
    for proposal in corroborated:
        cell = proposal["cell"]
        column_index = int(cell["columnIndex"])
        total_id = f"{built.table_id}-t{column_index}-r{cell['rowIndex']}"
        total = {
            "id": total_id,
            "cellId": cell["id"],
            "rowIndex": cell["rowIndex"],
            "columnIndex": column_index,
            "axis": "vertical",
            "signals": proposal["signals"],
            "outcome": proposal["outcome"],
            "resolution": proposal["resolution"],
        }
        totals.append(total)
        if proposal["outcome"] == "break":
            addends = [built.by_id[cell_id] for cell_id in proposal["resolution"]["addendCellIds"]]
            finding = findings_module.build_break(
                total_cell=cell, run=proposal["resolution"], addends=addends,
                table_id=built.table_id, total_id=total_id, page_index=page_index,
                decimals=int(cell.get("decimals", 0)), column_label=labelled.get(column_index, ""),
            )
            if finding is not None:
                findings.append(finding)

    # Resolved per column first, published afterwards, because whether a
    # two-addend run may be published is a fact about the other columns.
    column_results: dict[int, list[tuple[object, dict]]] = {}
    column_state: dict[int, tuple[set, dict]] = {}
    for column_index, column_nominations in sorted(by_column.items()):
        column_nominations.sort(key=lambda nomination: nomination.row_index)
        established = corroborated_blocks.get(column_index, {})
        nominated_rows = {nomination.row_index for nomination in column_nominations}
        nominated_rows |= set(established)
        jump: dict[int, int | None] = dict(established)
        results: list[tuple[object, dict]] = []
        for nomination in column_nominations:
            resolved = sums.resolve(
                built,
                nomination.cell,
                len(nomination.signals),
                nominated_rows,
                jump,
            )
            named = {signal["name"] for signal in nomination.signals}
            if resolved["outcome"] == "break" and (
                built.provenance.startswith("lattice") or "label-total" not in named
            ):
                # A label can nominate on its own, but the looser lattice may
                # have exposed only a fragment of the real block. Confirmation
                # is safe; accusation requires leave-one-out column evidence.
                #
                # A drawn rule is thinner still: the published rule positions
                # carry no extent, so `double-rule-below` says a double rule is
                # under the row and not that it is under this column. A total
                # holding it and no label confirms and stays quiet otherwise.
                resolved = {
                    "outcome": "unresolved",
                    "unresolvedReason": "no-plausible-run",
                }
            resolution = resolved.get("resolution")
            if resolution:
                addends = [
                    built.by_id[cell_id]
                    for cell_id in resolution["addendCellIds"]
                    if cell_id in built.by_id
                ]
                # The block this total consumed reaches above its own addends
                # wherever one of them is itself a subtotal, so the resume point
                # is the top of the deepest block beneath it.
                jump[nomination.row_index] = sums.block_top(addends, jump, nominated_rows)
            results.append((nomination, resolved))
        column_results[column_index] = results
        column_state[column_index] = (nominated_rows, jump)

    _corroborate_short_runs(built, column_results, column_state)

    for column_index, results in sorted(column_results.items()):
        column_label = labelled.get(column_index, "")
        for nomination, resolved in results:
            total_id = f"{built.table_id}-t{column_index}-r{nomination.row_index}"
            total = {
                "id": total_id,
                "cellId": nomination.cell["id"],
                "rowIndex": nomination.row_index,
                "columnIndex": column_index,
                "axis": "vertical",
                "signals": [dict(signal) for signal in resolved.get("signals", nomination.signals)],
                **{k: v for k, v in resolved.items() if k != "signals"},
            }
            totals.append(total)
            resolution = resolved.get("resolution")
            if resolution and resolved["outcome"] == "break":
                finding = findings_module.build_break(
                    total_cell=nomination.cell,
                    run=resolution,
                    addends=[
                        built.by_id[cell_id]
                        for cell_id in resolution["addendCellIds"]
                        if cell_id in built.by_id
                    ],
                    table_id=built.table_id,
                    total_id=total_id,
                    page_index=page_index,
                    decimals=int(nomination.cell.get("decimals", 0)),
                    column_label=column_label,
                )
                if finding is not None:
                    findings.append(finding)
            elif resolved["outcome"] == "unresolved" and any(
                signal["name"] == "label-total" for signal in nomination.signals
            ):
                # An unresolved total is spoken only where the document itself
                # called the row a total. A row proposed by a drawn rule alone
                # and not resolved is the scan reaching for something and
                # missing, which is not worth a sentence.
                finding = findings_module.build_unresolved(
                    total_cell=nomination.cell,
                    reason=resolved.get("unresolvedReason", ""),
                    table_id=built.table_id,
                    total_id=total_id,
                    page_index=page_index,
                    column_label=column_label,
                )
                if finding is not None:
                    findings.append(finding)

    # Cross-footing, secondary and narrow: a row is added against a column whose
    # own header names a total, and against nothing else. It runs after the
    # vertical pass and never feeds it -- a row that cross-foots is not thereby
    # a column total, and the tree stays the statement's own.
    cross_columns = frozenset(nominate_module.cross_foot_columns(built.header_labels))
    for nomination in nominate_module.nominate_cross(built):
        column_index = nomination.column_index
        resolved = sums.resolve_cross(
            built, nomination.cell, len(nomination.signals), cross_columns
        )
        if built.provenance.startswith("lattice") and resolved["outcome"] == "break":
            # The same rule the vertical pass keeps: the looser lattice may have
            # exposed a fragment of the row, so confirmation is safe and
            # accusation is not.
            resolved = {"outcome": "unresolved", "unresolvedReason": "no-plausible-run"}
        if resolved["outcome"] == "unresolved":
            # A row that is simply not additive across is the common case in a
            # table that has a Total column at all -- a per-share line, a rate, a
            # count -- and saying so once per row would drown the column totals.
            continue
        total_id = f"{built.table_id}-x{column_index}-r{nomination.row_index}"
        total = {
            "id": total_id,
            "cellId": nomination.cell["id"],
            "rowIndex": nomination.row_index,
            "columnIndex": column_index,
            "axis": "cross",
            "signals": [dict(signal) for signal in nomination.signals],
            **resolved,
        }
        totals.append(total)
        resolution = resolved.get("resolution")
        if resolution and resolved["outcome"] == "break":
            finding = findings_module.build_break(
                total_cell=nomination.cell,
                run=resolution,
                addends=[
                    built.by_id[cell_id]
                    for cell_id in resolution["addendCellIds"]
                    if cell_id in built.by_id
                ],
                table_id=built.table_id,
                total_id=total_id,
                page_index=page_index,
                decimals=int(nomination.cell.get("decimals", 0)),
                column_label=labelled.get(column_index, ""),
            )
            if finding is not None:
                findings.append(finding)

    totals.sort(key=lambda total: (total["rowIndex"], total["columnIndex"]))
    model = {
        "id": built.table_id,
        "pageIndex": page_index,
        "bounds": built.bounds,
        "provenance": built.provenance,
        "columnCount": built.column_count,
        "rowCount": built.row_count,
        "cells": built.published,
        "totals": totals,
    }
    if built.header_labels:
        model["headerLabels"] = built.header_labels
    return model, structure_diagnostics


def _corroborate_short_runs(built, column_results, column_state) -> None:
    """Publish a two-addend run only where the parallel columns say the same.

    A run of three or more addends may be confirmed on `label-total` alone. A
    run of two may not: a pair that happens to sum is nearly evidence-free, and
    the floor exists to say so. The second signal the floor waits for is the one
    the plan already names -- the same row structure footing independently in
    another value column -- and the walk has been run per column, so all that is
    missing is to compare the answers before publishing them.

    Apple's statement of comprehensive income is the case this was written for.
    *Total comprehensive income* is net income plus total other comprehensive
    income and nothing else, two addends with a section caption between them.
    The structure search cannot reach it, because a contiguous span containing
    that caption has a hole in it; the label walk reaches it in all three period
    columns and is refused in each for being two addends long. Three independent
    columns agreeing on the same two row positions is exactly the evidence that
    was missing.

    Only a total already resolved as `run-too-short` is reconsidered, and only
    ever upward: a two-addend run that misses stays unresolved and never becomes
    a break, because thin evidence may confirm and may not accuse.
    """
    candidates: dict[tuple[int, tuple[int, ...], tuple[int, ...]], list[tuple[int, int, dict]]] = {}
    for column_index, results in column_results.items():
        nominated_rows, jump = column_state[column_index]
        for position, (nomination, resolved) in enumerate(results):
            if resolved["outcome"] != "unresolved":
                continue
            if resolved.get("unresolvedReason") != "run-too-short":
                continue
            upgraded = sums.resolve(
                built,
                nomination.cell,
                len(nomination.signals),
                nominated_rows,
                jump,
                floor=nominate_module.MIN_ADDENDS,
            )
            if upgraded["outcome"] != "confirmed":
                continue
            resolution = upgraded["resolution"]
            addends = [
                built.by_id[cell_id]
                for cell_id in resolution["addendCellIds"]
                if cell_id in built.by_id
            ]
            if len(addends) != len(resolution["addendCellIds"]):
                continue
            # The signature is the row positions, and the positions reversed --
            # the same test parallel-column corroboration uses for a signed run,
            # so a coincidental sign choice cannot become a structural signal.
            signature = (
                nomination.row_index,
                tuple(int(cell["rowIndex"]) for cell in addends),
                tuple(
                    int(built.by_id[cell_id]["rowIndex"])
                    for cell_id in resolution["negatedAddendCellIds"]
                    if cell_id in built.by_id
                ),
            )
            candidates.setdefault(signature, []).append(
                (column_index, position, upgraded)
            )

    for entries in candidates.values():
        independent: list[list] = []
        agreeing: list[tuple[int, int, dict]] = []
        for column_index, position, upgraded in entries:
            values = [
                Decimal(built.by_id[cell_id]["normalizedValue"])
                if "normalizedValue" in built.by_id[cell_id]
                else Decimal(0)
                for cell_id in upgraded["resolution"]["addendCellIds"]
            ]
            if not any(sums.proportional_values(values, prior) for prior in independent):
                independent.append(values)
                agreeing.append((column_index, position, upgraded))
        if len(independent) < 2:
            continue
        columns = sorted({column_index for column_index, _, _ in entries})
        for column_index, position, upgraded in entries:
            others = [str(other) for other in columns if other != column_index]
            if not others:
                continue
            nomination, _ = column_results[column_index][position]
            published = dict(upgraded)
            published["signals"] = [dict(signal) for signal in nomination.signals] + [
                {
                    "name": "column-corroboration",
                    "evidence": (
                        "the same rows foot independently in column"
                        f"{'s' if len(others) != 1 else ''} {', '.join(others)}"
                    ),
                }
            ]
            column_results[column_index][position] = (nomination, published)


def _boundary_disagreements(blocks, tables: list[dict]) -> int:
    """Count confident grid columns whose right edge matches no lattice column."""
    count = 0
    for block in blocks:
        if block.provenance != "lattice+table":
            continue
        table = max(tables, key=lambda item: _bounds_overlap(block.bounds, item["bounds"]), default=None)
        if table is None:
            continue
        lattice_edges = [
            cell["bounds"]["x"] + cell["bounds"]["width"]
            for cell in block.published if "normalizedValue" in cell
        ]
        for column in table.get("columns", [])[1:]:
            edge = float(column["x1"])
            if not any(abs(edge - lattice) <= 0.01 for lattice in lattice_edges):
                count += 1
    return count


def _bounds_overlap(first: dict, second: dict) -> float:
    x = max(0.0, min(first["x"] + first["width"], second["x"] + second["width"]) - max(first["x"], second["x"]))
    y = max(0.0, min(first["y"] + first["height"], second["y"] + second["height"]) - max(first["y"], second["y"]))
    return x * y


def _span_for_total(block: dict, total: dict) -> str | None:
    """What identifies this total for deduplication, axis included.

    One printed figure can be both the total of its column and the total of its
    row, and those are two different assertions about it. Keying on the span
    alone would let whichever arrived first delete the other.
    """
    cell = next((cell for cell in block["cells"] if cell["id"] == total["cellId"]), None)
    if cell is None or "spanId" not in cell:
        return None
    return "{}:{}".format(total.get("axis", "vertical"), cell["spanId"])


def _select_grid_totals(grid: dict, published: list[dict]) -> tuple[list[dict], set[str]]:
    """Keep novel grid totals and replace weaker lattice duplicates.

    Span identity says two totals point at the same printed figure; it does not
    say their evidence is equal. A page-local lattice may be cut at a section
    gap while the detected grid still spans the full statement. In that case a
    grid confirmation or break replaces the lattice's unresolved result rather
    than being discarded merely because the lattice arrived first.
    """
    existing: dict[str, list[tuple[dict, dict]]] = {}
    for block in published:
        for total in block["totals"]:
            span = _span_for_total(block, total)
            if span is not None:
                existing.setdefault(span, []).append((block, total))

    selected: list[dict] = []
    replaced_ids: set[str] = set()
    rank = {"unresolved": 0, "break": 1, "confirmed": 1}
    for total in grid["totals"]:
        span = _span_for_total(grid, total)
        matches = existing.get(span, []) if span is not None else []
        if not matches:
            selected.append(total)
            continue
        strongest = max(rank[item["outcome"]] for _, item in matches)
        if rank[total["outcome"]] <= strongest:
            continue
        for block, prior in matches:
            block["totals"].remove(prior)
            replaced_ids.add(prior["id"])
        selected.append(total)
    return selected, replaced_ids


def _summary(tables_examined: int, published: list[dict]) -> dict:
    """The counts the home screen reads.

    Every nominated total lands in exactly one of confirmed, breaks and
    unresolved, so those three sum to `totalsNominated`. A clean scan is
    affirmative rather than empty, which is why `confirmed` is published beside
    the exceptions rather than inferred from their absence.
    """
    counts = {"confirmed": 0, "break": 0, "unresolved": 0}
    nominated = 0
    for table in published:
        for total in table["totals"]:
            nominated += 1
            counts[total["outcome"]] += 1
    return {
        "tablesExamined": tables_examined,
        "totalsNominated": nominated,
        "confirmed": counts["confirmed"],
        "breaks": counts["break"],
        "unresolved": counts["unresolved"],
    }


def _unresolved_reasons(published: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in published:
        for total in table["totals"]:
            if total["outcome"] != "unresolved":
                continue
            reason = total.get("unresolvedReason", "unknown")
            counts[reason] = counts.get(reason, 0) + 1
    return counts


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
