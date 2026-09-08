"""Build statement-oriented table blocks from general table geometry.

This engine is the seam between document-neutral recognition and financial
analysis.  It never publishes `table-structure-v1` and it never decides whether
arithmetic is correct.  It reads the cached general table model as corroborating
geometry, then builds two private views for Reconcile:

* a value-alignment lattice, which can recover statement structure when the
  conservative general detector missed or fragmented a table; and
* a cell join over every accepted general grid, retained as a precise fallback
  where the lattice lost a row or column.

Financial conventions such as period-column meaning, accounting dashes, and
subtotal rulings live in this package.  General table detection remains useful
without knowing any of them.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

from engines.table.handoff import (
    AnalysisHandoff,
    JsonMapping,
    artifact_digest,
    build_analysis_handoff,
    table_geometry_digest,
)
from schemas.models import Stage

from .cells import TableCells, build_table_cells
from .lattice import build_page_lattice


DETECTOR_VERSION = "financial-table-detector-2"


class FinancialTableInvariantError(RuntimeError):
    """The general/financial ownership boundary was violated."""


@dataclass(frozen=True)
class BoundaryDisagreement:
    """One explainable mismatch between general and lattice value columns."""

    page_index: int
    general_table_id: str
    financial_block_id: str
    kind: str
    general_column_index: int | None
    general_column_x0: float | None
    general_column_x1: float | None
    lattice_edges: tuple[float, ...]


@dataclass(frozen=True)
class FinancialTablePage:
    """The financial views of one page, before arithmetic evaluates them."""

    page_index: int
    lattice_blocks: tuple[TableCells, ...]
    grid_fallbacks: tuple[TableCells, ...]
    disagreements: tuple[BoundaryDisagreement, ...]


@dataclass(frozen=True)
class FinancialTableScan:
    """One private statement-table pass consumed by Reconcile."""

    detector_version: str
    pages: tuple[FinancialTablePage, ...]
    elapsed_ms: int

    @property
    def boundary_disagreements(self) -> int:
        return len(self.disagreements)

    @property
    def disagreements(self) -> tuple[BoundaryDisagreement, ...]:
        return tuple(
            disagreement
            for page in self.pages
            for disagreement in page.disagreements
        )

    @property
    def lattice_blocks(self) -> int:
        return sum(len(page.lattice_blocks) for page in self.pages)

    @property
    def grid_fallbacks(self) -> int:
        return sum(len(page.grid_fallbacks) for page in self.pages)


def detect_financial_tables(
    geometry: dict,
    values: dict | None,
    general_tables: dict | None,
    *,
    progress_callback=None,
) -> FinancialTableScan:
    """Interpret one general scan as financial-statement table blocks.

    The order is deliberate: the general table scan has already made its
    conservative publication decision.  This pass may recover a looser lattice
    for arithmetic, but it cannot write back into that artifact or make a
    financially inferred region appear to general consumers as a detected table.
    """
    started = time.perf_counter()
    values_by_page = {
        int(page["pageIndex"]): page for page in (values or {}).get("pages", [])
    }
    handoff = build_analysis_handoff(geometry, general_tables)

    pages: list[FinancialTablePage] = []
    if progress_callback:
        progress_callback(
            "Building financial statement tables…", Stage.RECONCILE_TABLES
        )
    for position, page in enumerate(handoff.pages):
        page_index = page.page_index
        if progress_callback:
            progress_callback(
                "Building financial statement tables…",
                Stage.RECONCILE_TABLES,
                current=position + 1,
                total=len(handoff.pages),
                unit="pages",
            )
        layout = page.layout
        tables = page.tables
        lattice = tuple(
            build_page_lattice(
                layout,
                values_by_page.get(page_index),
                page_index=page_index,
                detected_tables=tables,
            )
        )
        grids = tuple(
            build_table_cells(
                table,
                layout,
                values_by_page.get(page_index),
                page_index=page_index,
            )
            for table in tables
        )
        pages.append(
            FinancialTablePage(
                page_index=page_index,
                lattice_blocks=lattice,
                grid_fallbacks=grids,
                disagreements=_boundary_disagreements(
                    page_index, lattice, grids, tables
                ),
            )
        )

    scan = FinancialTableScan(
        detector_version=DETECTOR_VERSION,
        pages=tuple(pages),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )
    _assert_handoff_invariants(handoff, scan)
    if artifact_digest(general_tables) != handoff.source_digest:
        raise FinancialTableInvariantError(
            "financial-table analysis rewrote the general table artifact"
        )
    return scan


def _boundary_disagreements(
    page_index: int,
    blocks: tuple[TableCells, ...],
    grids: tuple[TableCells, ...],
    tables: tuple[JsonMapping, ...],
) -> tuple[BoundaryDisagreement, ...]:
    """Describe differing numeric-column partitions without choosing a winner."""
    disagreements: list[BoundaryDisagreement] = []
    tables_by_id = {str(table["id"]): table for table in tables}
    grids_by_id = {
        grid.source_general_table_id: grid
        for grid in grids
        if grid.source_general_table_id is not None
    }
    for block in blocks:
        if block.provenance != "lattice+table":
            continue
        table = tables_by_id.get(block.source_general_table_id or "")
        if table is None:
            continue
        columns = tuple(table.get("columns", ()))
        grid = grids_by_id.get(str(table["id"]))
        numeric_columns = {
            int(cell["columnIndex"])
            for cell in (grid.published if grid else ())
            if "normalizedValue" in cell or cell.get("dash")
        }
        mapped: dict[int, list[float]] = {}
        for edge in block.value_column_edges:
            matches = [
                index
                for index, column in enumerate(columns)
                if float(column["x0"]) - 0.002 <= edge <= float(column["x1"]) + 0.002
            ]
            if not matches:
                disagreements.append(
                    BoundaryDisagreement(
                        page_index=page_index,
                        general_table_id=str(table["id"]),
                        financial_block_id=block.table_id,
                        kind="lattice-edge-outside-general-grid",
                        general_column_index=None,
                        general_column_x0=None,
                        general_column_x1=None,
                        lattice_edges=(edge,),
                    )
                )
                continue
            chosen = min(
                matches,
                key=lambda index: abs(
                    edge
                    - (
                        float(columns[index]["x0"])
                        + float(columns[index]["x1"])
                    )
                    / 2
                ),
            )
            mapped.setdefault(chosen, []).append(edge)

        for column_index in sorted(numeric_columns | mapped.keys()):
            edges = tuple(mapped.get(column_index, ()))
            if column_index in numeric_columns and not edges:
                kind = "general-value-column-without-lattice"
            elif len(edges) > 1:
                kind = "multiple-lattice-alignments-in-general-column"
            else:
                continue
            column = columns[column_index]
            disagreements.append(
                BoundaryDisagreement(
                    page_index=page_index,
                    general_table_id=str(table["id"]),
                    financial_block_id=block.table_id,
                    kind=kind,
                    general_column_index=column_index,
                    general_column_x0=round(float(column["x0"]), 6),
                    general_column_x1=round(float(column["x1"]), 6),
                    lattice_edges=edges,
                )
            )
    return tuple(disagreements)


def _assert_handoff_invariants(
    handoff: AnalysisHandoff,
    scan: FinancialTableScan,
) -> None:
    """Fail closed if source lineage or page correspondence ever drifts."""
    if tuple(page.page_index for page in handoff.pages) != tuple(
        page.page_index for page in scan.pages
    ):
        raise FinancialTableInvariantError(
            "financial-table pages no longer correspond to general handoff pages"
        )

    for source_page, financial_page in zip(handoff.pages, scan.pages):
        expected = [
            (
                str(table["id"]),
                table_geometry_digest(table),
                dict(table.get("bounds", {})),
                len(table.get("columns", ())),
                sum(1 for row in table.get("rows", ()) if row.get("kind") == "body"),
            )
            for table in source_page.tables
        ]
        actual = [
            (
                block.source_general_table_id,
                block.source_general_geometry_digest,
                block.bounds,
                block.column_count,
                block.row_count,
            )
            for block in financial_page.grid_fallbacks
        ]
        if actual != expected:
            raise FinancialTableInvariantError(
                f"page {source_page.page_index}: grid fallback lineage or geometry drifted"
            )
        for block in financial_page.lattice_blocks:
            has_source = block.source_general_table_id is not None
            if (block.provenance == "lattice") == has_source:
                raise FinancialTableInvariantError(
                    f"page {source_page.page_index}: lattice provenance disagrees with source lineage"
                )
            if has_source:
                table = next(
                    (
                        item
                        for item in source_page.tables
                        if str(item["id"]) == block.source_general_table_id
                    ),
                    None,
                )
                if table is None or block.source_general_geometry_digest != table_geometry_digest(table):
                    raise FinancialTableInvariantError(
                        f"page {source_page.page_index}: lattice source geometry drifted"
                    )


__all__ = [
    "BoundaryDisagreement",
    "DETECTOR_VERSION",
    "FinancialTableInvariantError",
    "FinancialTablePage",
    "FinancialTableScan",
    "detect_financial_tables",
]
