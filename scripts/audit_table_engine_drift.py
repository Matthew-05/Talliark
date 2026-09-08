"""Compare general table recognition with private financial-table hypotheses.

This report is intentionally not a golden and disagreement is not failure.  It
records where the conservative public grid and the statement-oriented lattice
diverge so an engine change can be reviewed across publishers and layout
families without teaching either engine to copy the other.

With no PDF arguments, every PDF in the local financial-statement corpus is
scanned.  A previous JSON report may be supplied to show metric deltas.  The
command exits non-zero only when an ownership invariant is violated or a file
cannot be scanned; ordinary metric drift remains visible for human review.

    py scripts/audit_table_engine_drift.py
    py scripts/audit_table_engine_drift.py path/to/statement.pdf --write-report output/drift.json
    py scripts/audit_table_engine_drift.py --baseline output/before.json
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = Path(__file__).resolve().parent
for entry in (str(ROOT / "src" / "python"), str(SCRIPT_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from engines.financial.detector import detect_financial_structure  # noqa: E402
from engines.financial_table.detector import detect_financial_tables  # noqa: E402
from engines.table.handoff import artifact_digest  # noqa: E402
from engines.values.detector import detect_values  # noqa: E402
from engines.values.lines import prepare as prepare_lines  # noqa: E402
from table_corpus import detect, geometry_for  # noqa: E402


CORPUS = ROOT / "sample-document-corpus" / "financial-statements"
METRICS = (
    "pages",
    "generalTables",
    "latticeBlocks",
    "latticeOnlyBlocks",
    "corroboratedLatticeBlocks",
    "gridFallbacks",
    "boundaryDisagreements",
    "generalTablesWithoutLattice",
)


def scan_document(pdf: Path) -> dict:
    """Run both recognition layers and return reviewable drift evidence."""
    started = time.perf_counter()
    data = pdf.read_bytes()
    geometry = geometry_for(data)
    geometry_ms = int((time.perf_counter() - started) * 1000)

    tables, table_diagnostics, table_seconds = detect(data, geometry)
    document = prepare_lines(geometry)
    financial = detect_financial_structure(document, tables=tables)
    values = detect_values(document, claims=financial.spans)

    source_before = artifact_digest(tables)
    scan = detect_financial_tables(geometry, values, tables)
    source_after = artifact_digest(tables)

    general_by_page = {
        int(page["pageIndex"]): list(page.get("tables", []))
        for page in tables.get("pages", [])
    }
    page_reports = []
    general_without_lattice = 0
    for page in scan.pages:
        general = general_by_page.get(page.page_index, [])
        referenced = {
            block.source_general_table_id
            for block in page.lattice_blocks
            if block.source_general_table_id is not None
        }
        unmatched = [
            str(table["id"])
            for table in general
            if str(table["id"]) not in referenced
        ]
        general_without_lattice += len(unmatched)
        if not (general or page.lattice_blocks or page.disagreements):
            continue
        page_reports.append(
            {
                "pageIndex": page.page_index,
                "generalTableIds": [str(table["id"]) for table in general],
                "gridFallbackSourceIds": [
                    block.source_general_table_id for block in page.grid_fallbacks
                ],
                "latticeBlocks": [
                    {
                        "id": block.table_id,
                        "provenance": block.provenance,
                        "sourceGeneralTableId": block.source_general_table_id,
                    }
                    for block in page.lattice_blocks
                ],
                "generalTableIdsWithoutLattice": unmatched,
                "boundaryDisagreements": [
                    _camel_disagreement(asdict(item)) for item in page.disagreements
                ],
            }
        )

    lattice_blocks = [
        block for page in scan.pages for block in page.lattice_blocks
    ]
    return {
        "document": pdf.name,
        "path": str(pdf.resolve()),
        "generalDetectorVersion": tables.get("detectorVersion", "unavailable"),
        "financialTableDetectorVersion": scan.detector_version,
        "generalModelDigest": source_before,
        "controls": {
            "generalModelUnchanged": source_before == source_after,
            "oneGridFallbackPerGeneralTable": scan.grid_fallbacks
            == sum(len(page.get("tables", [])) for page in tables.get("pages", [])),
            "fallbackLineageValidated": True,
        },
        "pages": len(geometry.get("pages", [])),
        "generalTables": sum(
            len(page.get("tables", [])) for page in tables.get("pages", [])
        ),
        "latticeBlocks": len(lattice_blocks),
        "latticeOnlyBlocks": sum(
            block.provenance == "lattice" for block in lattice_blocks
        ),
        "corroboratedLatticeBlocks": sum(
            block.provenance == "lattice+table" for block in lattice_blocks
        ),
        "gridFallbacks": scan.grid_fallbacks,
        "boundaryDisagreements": scan.boundary_disagreements,
        "generalTablesWithoutLattice": general_without_lattice,
        "tableScanTruncated": bool(tables.get("truncated")),
        "pagesWithEvidence": page_reports,
        "timings": {
            "geometryMs": geometry_ms,
            "generalTableMs": int(table_seconds * 1000),
            "financialTableMs": scan.elapsed_ms,
            "totalMs": int((time.perf_counter() - started) * 1000),
        },
        "generalDiagnostics": table_diagnostics,
    }


def build_report(documents: list[dict], baseline: dict | None = None) -> dict:
    report = {
        "reportVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "interpretation": (
            "Counts and disagreements are review signals, not accuracy labels. "
            "Only controls are executable invariants."
        ),
        "documents": documents,
        "aggregate": {
            metric: sum(int(document[metric]) for document in documents)
            for metric in METRICS
        },
        "controlsPassed": all(
            all(document["controls"].values()) for document in documents
        ),
    }
    if baseline is not None:
        report["comparison"] = compare_reports(report, baseline)
    return report


def compare_reports(current: dict, baseline: dict) -> dict:
    """Expose per-document metric deltas without declaring them regressions."""
    previous = {
        document["document"]: document for document in baseline.get("documents", [])
    }
    now = {document["document"]: document for document in current["documents"]}
    shared = sorted(previous.keys() & now.keys())
    return {
        "addedDocuments": sorted(now.keys() - previous.keys()),
        "removedDocuments": sorted(previous.keys() - now.keys()),
        "documents": [
            {
                "document": name,
                "detectorVersionsChanged": (
                    previous[name].get("generalDetectorVersion")
                    != now[name].get("generalDetectorVersion")
                    or previous[name].get("financialTableDetectorVersion")
                    != now[name].get("financialTableDetectorVersion")
                ),
                "delta": {
                    metric: int(now[name][metric]) - int(previous[name][metric])
                    for metric in METRICS
                },
            }
            for name in shared
        ],
    }


def _camel_disagreement(item: dict) -> dict:
    return {
        "pageIndex": item["page_index"],
        "generalTableId": item["general_table_id"],
        "financialBlockId": item["financial_block_id"],
        "kind": item["kind"],
        "generalColumnIndex": item["general_column_index"],
        "generalColumnX0": item["general_column_x0"],
        "generalColumnX1": item["general_column_x1"],
        "latticeEdges": item["lattice_edges"],
    }


def _default_pdfs() -> list[Path]:
    return sorted(CORPUS.glob("*.pdf"), key=lambda path: path.name.casefold())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="*", type=Path)
    parser.add_argument("--baseline", type=Path, help="show deltas from an earlier report")
    parser.add_argument("--write-report", type=Path)
    args = parser.parse_args()

    pdfs = args.pdf or _default_pdfs()
    if not pdfs:
        parser.error(
            "no PDFs supplied and the local financial-statement corpus is empty"
        )
    missing = [str(pdf) for pdf in pdfs if not pdf.is_file()]
    if missing:
        parser.error("PDF not found: " + ", ".join(missing))

    baseline = (
        json.loads(args.baseline.read_text(encoding="utf-8"))
        if args.baseline is not None
        else None
    )
    report = build_report([scan_document(pdf) for pdf in pdfs], baseline)
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    print(rendered)
    if args.write_report is not None:
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        args.write_report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["controlsPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
