"""Orchestration and the model envelope for one Reconcile scan.

A scan is not a background pass. The user selects a document and runs it, and it
arrives here as the tail of an `ocr` job that set its analysis flag -- one
command, one job id, one progress stream, one cancellation. A separate worker
command was considered and rejected: it would have duplicated the whole
pipeline's orchestration to add a stage at the end of it.

The financial-table sister engine builds page-local statement blocks from
recognized values and the cached general table model. Reconcile consumes those
blocks and owns only arithmetic interpretation: parallel columns nominate row
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
from engines.financial_table.detector import (
    DETECTOR_VERSION as FINANCIAL_TABLE_VERSION,
    detect_financial_tables,
)
from engines.financial_table import labels as financial_labels
from schemas.models import Stage

from . import findings as findings_module
from . import nominate as nominate_module
from . import sums
from . import structures


DETECTOR_VERSION = f"reconcile-detector-7+{FINANCIAL_TABLE_VERSION}"


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
    version_id: str = "",
    values: dict | None = None,
    financial: dict | None = None,
    tables: dict | None = None,
    progress_callback=None,
    diagnostics: dict | None = None,
) -> dict:
    """Run one scan and return the `reconcile-v1` model.

    `values` is the `document-values-v1` model the same job just produced; the
    financial-table engine joins its spans to statement blocks by geometry.
    Nothing writes that analysis-only membership back into a cache artifact.
    """
    del pdf_bytes  # the cached general scan has already read the source PDF
    analysis_started = time.perf_counter()
    table_model = tables or {"detectorVersion": "unavailable", "pages": []}
    financial_scan = detect_financial_tables(
        geometry,
        values,
        table_model,
        progress_callback=progress_callback,
    )
    if progress_callback:
        progress_callback("Reconciling totals…", Stage.RECONCILE)

    published: list[dict] = []
    findings: list[dict] = []
    blocks_examined = 0
    hypotheses = 0
    truncated_structure_blocks = 0
    withheld_candidates: dict[str, int] = {}
    for position, page_scan in enumerate(financial_scan.pages):
        page_index = page_scan.page_index
        if progress_callback:
            progress_callback(
                "Reconciling totals…",
                Stage.RECONCILE,
                current=position + 1,
                total=len(financial_scan.pages),
                unit="pages",
            )
        blocks = page_scan.lattice_blocks
        blocks_examined += len(blocks)
        for block in blocks:
            reconciled, block_diagnostics = _reconcile_block(
                block, page_index=page_index, findings=findings
            )
            hypotheses += block_diagnostics["hypotheses"]
            truncated_structure_blocks += int(block_diagnostics["truncated"])
            _merge_counts(withheld_candidates, block_diagnostics.get("withheld", {}))
            published.append(reconciled)

        # The ADR's bake-off gate chose the union fallback when a local lattice
        # block trailed a broader supported view. The composed lattice can prove
        # an exact relationship across statement captions; the grid can recover
        # a row or column the lattice lost. Both use the identical arithmetic
        # policy, and span identity admits only novel or stronger totals.
        fallbacks = (
            *((block, True) for block in page_scan.composed_lattice_fallbacks),
            *((block, False) for block in page_scan.grid_fallbacks),
        )
        for fallback, exact_only in fallbacks:
            fallback_findings: list[dict] = []
            reconciled, grid_diagnostics = _reconcile_block(
                fallback, page_index=page_index, findings=fallback_findings
            )
            hypotheses += grid_diagnostics["hypotheses"]
            truncated_structure_blocks += int(grid_diagnostics["truncated"])
            _merge_counts(withheld_candidates, grid_diagnostics.get("withheld", {}))
            if exact_only:
                # Composition deliberately removes the caption boundaries that
                # split the ordinary lattice. It may recover an exact proof,
                # but a miss in that broader view says nothing about the page
                # and must not enlarge the user's Not checked list.  An exact
                # coincidence is admitted only where independent parallel
                # columns also establish the row structure that was composed.
                parallel_ids = _composed_parallel_total_ids(reconciled)
                reconciled["totals"] = [
                    total
                    for total in reconciled["totals"]
                    if _is_supported_composed_total(total, parallel_ids)
                ]
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
                finding
                for finding in fallback_findings
                if finding["totalId"] in selected_ids
            )

    model = {
        "version": 1,
        "coordinateSpace": "normalized",
        "detectorVersion": DETECTOR_VERSION,
        "source": _source(
            document_id=document_id,
            version_id=version_id or document_id,
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
        diagnostics["financial_table_detector_version"] = (
            financial_scan.detector_version
        )
        diagnostics["financial_table_lattice_blocks"] = (
            financial_scan.lattice_blocks
        )
        diagnostics["financial_table_grid_fallbacks"] = (
            financial_scan.grid_fallbacks
        )
        diagnostics["financial_table_composed_lattice_fallbacks"] = (
            financial_scan.composed_lattice_fallbacks
        )
        diagnostics["reconcile_detector_version"] = DETECTOR_VERSION
        diagnostics["reconcile_table_detection_ms"] = financial_scan.elapsed_ms
        diagnostics["reconcile_structure_hypotheses"] = hypotheses
        diagnostics["reconcile_structure_truncated_blocks"] = (
            truncated_structure_blocks
        )
        diagnostics["reconcile_candidates_withheld"] = sum(
            withheld_candidates.values()
        )
        for reason, count in sorted(withheld_candidates.items()):
            diagnostics[f"reconcile_withheld_{reason.replace('-', '_')}"] = count
        diagnostics["reconcile_substrate_disagreements"] = (
            financial_scan.boundary_disagreements
        )
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
    withheld: dict[str, int] = {}
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
    #
    # And resolved more than once, because a total that foots on a subtotal
    # cannot be settled before that subtotal is. Corroboration can confirm a
    # short run only after every column has been walked, by which time the
    # totals standing above it have already been resolved without it -- Apple's
    # commercial paper note is two deep exactly this way, its second net line
    # confirmed by the parallel columns and its Total resting on that line. So
    # the pass repeats while it is still learning something, and stops as soon
    # as it is not.
    column_results: dict[int, list[tuple[object, dict]]] = {}
    column_state: dict[int, tuple[set, dict]] = {}
    seed = {
        column: dict(blocks) for column, blocks in corroborated_blocks.items()
    }
    # Each pass can only establish or extend a block boundary to an earlier row.
    # A block therefore converges in at most one pass per row; deriving this
    # guard from the input removes the former four-level nesting ceiling while
    # retaining a hard stop against a future non-monotone regression.
    for _ in range(max(1, built.row_count)):
        column_results, column_state = _resolve_columns(built, by_column, seed)
        _corroborate_short_runs(built, column_results, column_state)
        learned = {
            column: dict(blocks) for column, blocks in seed.items()
        }
        for column_index, results in column_results.items():
            for nomination, resolved in results:
                if resolved["outcome"] != "confirmed":
                    continue
                _, jump = column_state[column_index]
                learned.setdefault(column_index, {})[nomination.row_index] = jump.get(
                    nomination.row_index
                )
        if learned == seed:
            break
        seed = learned

    for column_index, results in sorted(column_results.items()):
        column_label = labelled.get(column_index, "")
        for nomination, resolved in results:
            withheld_reason = _withheld_unresolved_reason(
                built, nomination, resolved
            )
            if withheld_reason is not None:
                withheld[withheld_reason] = withheld.get(withheld_reason, 0) + 1
                continue
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
    cross_results = []
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
        cross_results.append((nomination, resolved))

    repeated_shapes: dict[tuple, int] = {}
    for nomination, resolved in cross_results:
        shape = _short_cross_shape(built, nomination, resolved)
        if shape is not None:
            repeated_shapes[shape] = repeated_shapes.get(shape, 0) + 1

    for nomination, resolved in cross_results:
        column_index = nomination.column_index
        if resolved["outcome"] == "unresolved":
            # A row that is simply not additive across is the common case in a
            # table that has a Total column at all -- a per-share line, a rate, a
            # count -- and saying so once per row would drown the column totals.
            continue
        shape = _short_cross_shape(built, nomination, resolved)
        if (
            shape is not None
            and repeated_shapes.get(shape, 0)
            < nominate_module.MIN_REPEATED_CROSS_ROWS
        ):
            # A single A +/- B coincidence is not enough to establish that the
            # result header means arithmetic for this table.  Repetition in a
            # second row supplies evidence independent of either row's values.
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
    structure_diagnostics["withheld"] = withheld
    return model, structure_diagnostics


def _withheld_unresolved_reason(built, nomination, resolved) -> str | None:
    """Why an internal candidate is not a user-worthy Not checked total.

    Arithmetic still examines every nomination. This admission policy runs only
    after an unresolved outcome, so it cannot create a confirmation or conceal
    a break. It separates speculative recognizer probes from totals the document
    credibly asserts should foot.
    """
    if resolved.get("outcome") != "unresolved":
        return None
    signals = {signal["name"] for signal in nomination.signals}
    if "label-total" not in signals:
        return "speculative-rule"

    label = nomination.cell.get("rowLabel", "")
    if financial_labels.is_noncontrolling_allocation_label(label):
        return "allocation-component"
    if financial_labels.is_equity_period_movement(
        label, built.header_labels, built.row_labels
    ):
        return "equity-movement"
    if financial_labels.is_non_total_net_measure(label):
        return "net-component"
    if (
        financial_labels.is_balance_state_label(label)
        and (
            resolved.get("unresolvedReason") == "no-candidate-run"
            or financial_labels.is_explicit_opening_state_label(label)
        )
    ):
        return "opening-balance"
    if _is_cash_flow_summary_peer(built, nomination):
        return "cash-flow-summary-peer"
    if (
        financial_labels.is_net_income_or_loss_label(label)
        and _is_statement_input_context(built)
    ):
        return "carried-result"
    if (
        resolved.get("unresolvedReason") == "no-candidate-run"
        and financial_labels.is_total_label(label)
        and _is_first_value_with_later_rows(built, nomination)
    ):
        return "carried-result"
    header = next(
        (
            item.get("text", "")
            for item in built.header_labels
            if int(item["columnIndex"]) == nomination.column_index
        ),
        "",
    )
    if financial_labels.is_clearly_non_additive_column(header):
        return "non-additive-column"
    return None


def _is_first_value_with_later_rows(built, nomination) -> bool:
    rows = sorted(
        int(cell["rowIndex"])
        for cell in built.published
        if int(cell["columnIndex"]) == nomination.column_index
        and "normalizedValue" in cell
    )
    return bool(
        rows
        and nomination.row_index == rows[0]
        and sum(row > nomination.row_index for row in rows) >= 2
    )


def _is_statement_input_context(built) -> bool:
    """Whether a block reuses Net income/loss as another statement's input."""
    labels = [financial_labels.normalize(label) for label in built.row_labels]
    cash_flow = any(
        financial_labels.cash_flow_activity(label) is not None
        or "cash generated by operating activities" in label
        or "cash provided by operations" in label
        for label in labels
    )
    comprehensive_income = (
        any(label.startswith("other comprehensive income") for label in labels)
        and any(label.startswith("comprehensive income") for label in labels)
    )
    return cash_flow or comprehensive_income


def _is_cash_flow_summary_peer(built, nomination) -> bool:
    """Whether activity totals are adjacent peer rows in a summary table."""
    if financial_labels.cash_flow_activity(
        nomination.cell.get("rowLabel", "")
    ) is None:
        return False
    peers = sorted(
        (
            int(cell["rowIndex"]),
            financial_labels.cash_flow_activity(cell.get("rowLabel", "")),
        )
        for cell in built.published
        if int(cell["columnIndex"]) == nomination.column_index
        and "normalizedValue" in cell
        and financial_labels.cash_flow_activity(cell.get("rowLabel", ""))
        is not None
    )
    rows = [row for row, _ in peers]
    activities = {activity for _, activity in peers}
    return (
        activities == {"operating", "investing", "financing"}
        and rows == list(range(rows[0], rows[0] + len(rows)))
    )


def _merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for key, count in source.items():
        target[key] = target.get(key, 0) + int(count)


def _short_cross_shape(built, nomination, resolved) -> tuple | None:
    """The structural equation repeated by a confirmed two-addend cross-foot."""
    if resolved.get("outcome") != "confirmed":
        return None
    resolution = resolved.get("resolution") or {}
    addend_ids = resolution.get("addendCellIds") or []
    if len(addend_ids) != nominate_module.MIN_ADDENDS:
        return None
    negated = frozenset(resolution.get("negatedAddendCellIds") or [])
    addend_columns = tuple(
        int(built.by_id[cell_id]["columnIndex"])
        for cell_id in addend_ids
        if cell_id in built.by_id
    )
    if len(addend_columns) != nominate_module.MIN_ADDENDS:
        return None
    negated_columns = tuple(
        int(built.by_id[cell_id]["columnIndex"])
        for cell_id in addend_ids
        if cell_id in negated and cell_id in built.by_id
    )
    return (nomination.column_index, addend_columns, negated_columns)


def _composed_parallel_total_ids(block: dict) -> frozenset[str]:
    """Exact totals whose row/sign structure repeats in independent columns."""
    cells = {cell["id"]: cell for cell in block.get("cells", [])}
    grouped: dict[tuple, list[tuple[dict, list[Decimal]]]] = {}
    for total in block.get("totals", []):
        if total.get("outcome") != "confirmed" or total.get("axis") == "cross":
            continue
        resolution = total.get("resolution") or {}
        addend_ids = resolution.get("addendCellIds") or []
        if not addend_ids or any(cell_id not in cells for cell_id in addend_ids):
            continue
        negated = frozenset(resolution.get("negatedAddendCellIds") or [])
        signature = (
            int(total["rowIndex"]),
            tuple(int(cells[cell_id]["rowIndex"]) for cell_id in addend_ids),
            tuple(
                int(cells[cell_id]["rowIndex"])
                for cell_id in addend_ids
                if cell_id in negated
            ),
        )
        values = [
            Decimal(cells[cell_id].get("normalizedValue", 0))
            for cell_id in addend_ids
        ]
        grouped.setdefault(signature, []).append((total, values))

    supported: set[str] = set()
    for entries in grouped.values():
        independent: list[list[Decimal]] = []
        columns: set[int] = set()
        for total, values in entries:
            column = int(total["columnIndex"])
            if column in columns:
                continue
            if any(sums.proportional_values(values, prior) for prior in independent):
                continue
            columns.add(column)
            independent.append(values)
        if len(independent) >= 2:
            # Once two columns independently agree on the structure, every
            # exact column carrying that same printed row pattern is supported.
            supported.update(total["id"] for total, _ in entries)
    return frozenset(supported)


def _is_supported_composed_total(
    total: dict,
    parallel_ids: frozenset[str] = frozenset(),
) -> bool:
    """Whether a caption-spanning exact tie has independent structure evidence."""
    return total.get("outcome") == "confirmed" and (
        total.get("id") in parallel_ids
        or any(
            signal.get("name") == "column-corroboration"
            for signal in total.get("signals", [])
        )
    )


# The signals strong enough for a nominated row to stop another total's walk.
# A label is the document's own word and a double rule is the grand-total
# convention drawn unambiguously; a rule above a row is neither, being equally
# the rule below the row before it.
BOUNDARY_SIGNALS = frozenset({"label-total", "double-rule-below"})


def _resolve_columns(built, by_column, seed):
    """Walk and resolve every label-route nomination, one column at a time.

    `seed` names the rows already known to be subtotals and, for each, the top
    of the block it consumed -- from the structure search, and from whatever an
    earlier pass confirmed. A row present with `None` is a subtotal whose own
    top is unknown, which §7.2 requires the walk to stop at rather than guess.
    """
    column_results: dict[int, list[tuple[object, dict]]] = {}
    column_state: dict[int, tuple[set, dict]] = {}
    for column_index, column_nominations in sorted(by_column.items()):
        column_nominations.sort(key=lambda nomination: nomination.row_index)
        established = seed.get(column_index, {})
        # Which rows the walk must stop at. A nominated total is a barrier
        # because stepping over one whose own block is unknown would count that
        # block's addends twice -- but only a row actually established as a
        # subtotal has a block to protect.
        #
        # `ruling-above` cannot tell a total from the row beneath one: the rule
        # under a subtotal is also the rule over the row that follows it, and
        # both readings look identical. That is fine for proposing -- the
        # arithmetic decides -- and ruinous for blocking, because a speculative
        # nomination that resolves to nothing would stop every real total above
        # it. Disney's segment expense note is the case: a rule sits under each
        # of the three segment subtotals, so the first row of the next segment
        # is nominated too, and treating it as a barrier hides *Total costs and
        # expenses* entirely.
        #
        # So a row proposed by a rule above and nothing else is resolved like any
        # other and blocks nothing until it resolves, at which point the next
        # pass admits it through the seed.
        nominated_rows = {
            nomination.row_index
            for nomination in column_nominations
            if not BOUNDARY_SIGNALS.isdisjoint(
                signal["name"] for signal in nomination.signals
            )
        }
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
                # carry no extent, so `double-rule-below` and `ruling-above` say
                # a rule is at the row and not that it crosses this column. A
                # total holding one of those and no label confirms and stays
                # quiet otherwise.
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
    return column_results, column_state


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
            nominated_rows, jump = column_state[column_index]
            # The upgraded run is a resolved block like any other, and the total
            # standing above it has to be able to step over it on the next pass.
            jump[nomination.row_index] = sums.block_top(
                [
                    built.by_id[cell_id]
                    for cell_id in upgraded["resolution"]["addendCellIds"]
                    if cell_id in built.by_id
                ],
                jump,
                nominated_rows,
            )
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
    # Any exact permitted resolution defeats a miss: by definition a break says
    # no permitted run ties.  Substrate order settles only equal outcomes.
    rank = {"unresolved": 0, "break": 1, "confirmed": 2}
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
    version_id: str,
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
        "versionId": version_id,
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
