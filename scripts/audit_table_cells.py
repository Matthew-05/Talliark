"""Check that published table geometry puts every glyph in the right cell.

The scorer measures where the boundaries went; this measures what the boundaries
*do*. It rebuilds every cell the way a consumer with only `table-structure-v1`
would — character centre inside a column band and a row band — and reports the
places where that disagrees with how the page reads.

The currency checks are the sharp ones. A floated "$" belongs to the amount it
marks, and the column boundary has to fall on the far side of it; when it does
not, the cell text a detector reports internally can still look right while every
consumer of the contract reads the symbol into the wrong column.

    py scripts/audit_table_cells.py "test-imports.local/financial-statements/apple 10k.pdf"
    py scripts/audit_table_cells.py <pdf> --periods --json output/cells.json

Exits non-zero when a marker lands in the wrong cell, so it can gate a change.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from table_corpus import detect, geometry_for  # noqa: E402

from engines.table.layout import build_page_layout  # noqa: E402


CURRENCY = ("$", "€", "£", "¥", "₹")
# Findings that mean a glyph is in the wrong cell, as opposed to text that no
# boundary could have placed correctly.
FAULTS = ("stray-marker", "trailing-marker", "split-value", "empty-column")


def _column_of(x: float, columns: list[dict]) -> int:
    for index, column in enumerate(columns):
        if column["x0"] <= x <= column["x1"]:
            return index
    return -1


def _cells(table: dict, characters: list[dict]) -> list[list[str]]:
    grid = [["" for _ in table["columns"]] for _ in table["rows"]]
    for character in characters:
        text = str(character.get("char", "")).strip()
        if not text:
            continue
        x = float(character["x"]) + float(character["width"]) / 2
        y = float(character["y"]) + float(character["height"]) / 2
        for row_index, row in enumerate(table["rows"]):
            if row["y0"] <= y <= row["y1"]:
                for column_index, column in enumerate(table["columns"]):
                    if column["x0"] <= x <= column["x1"]:
                        grid[row_index][column_index] += text
                break
    return grid


def audit_table(table: dict, characters: list[dict], lines) -> list[dict]:
    findings: list[dict] = []
    grid = _cells(table, characters)
    header_rows = int(table["header"]["rowCount"]) if table.get("header") else 0

    for row_index, row in enumerate(grid):
        for column_index, cell in enumerate(row):
            stripped = cell.strip()
            if not stripped:
                continue
            if stripped in CURRENCY:
                findings.append(
                    {"kind": "stray-marker", "row": row_index, "column": column_index, "cell": stripped}
                )
            elif stripped[-1] in CURRENCY:
                findings.append(
                    {"kind": "trailing-marker", "row": row_index, "column": column_index, "cell": stripped}
                )

    # A currency symbol and the value it marks have to land in one column.
    top = table["bounds"]["y"]
    bottom = top + table["bounds"]["height"]
    for line in lines:
        if not (top <= line.center <= bottom):
            continue
        tokens = line.tokens
        for index, token in enumerate(tokens):
            if token.kind != "currency":
                continue
            following = next(
                (other for other in tokens[index + 1 :] if other.kind != "currency"), None
            )
            if following is None:
                continue
            marker_column = _column_of(token.center, table["columns"])
            value_column = _column_of(following.center, table["columns"])
            if marker_column != value_column:
                findings.append(
                    {
                        "kind": "split-value",
                        "marker": token.text,
                        "markerColumn": marker_column,
                        "value": following.text[:16],
                        "valueColumn": value_column,
                    }
                )

    body = grid[header_rows:]
    for column_index in range(len(table["columns"])):
        if body and not any(row[column_index].strip() for row in body):
            findings.append({"kind": "empty-column", "column": column_index})

    # Text no boundary could place: a centred banner spanning several columns.
    for line in lines:
        if not (top <= line.center <= bottom):
            continue
        for token in line.tokens:
            for column in table["columns"][:-1]:
                if token.x0 < column["x1"] < token.x1:
                    findings.append({"kind": "spanning-text", "text": token.text[:20]})
                    break
    return findings


def audit(pdf: Path, *, periods: bool = False) -> dict:
    pdf_bytes = pdf.read_bytes()
    geometry = geometry_for(pdf_bytes)
    structure, _diagnostics, _elapsed = detect(
        pdf_bytes, geometry, periods=periods or None
    )
    by_page = {page["pageIndex"]: page for page in geometry["pages"]}
    report = {"document": pdf.name, "tables": 0, "counts": {}, "pages": []}
    for page in structure["pages"]:
        source = by_page.get(page["pageIndex"], {"characters": []})
        layout = build_page_layout(source)
        for table in page["tables"]:
            report["tables"] += 1
            findings = audit_table(table, source["characters"], layout.lines)
            for finding in findings:
                report["counts"][finding["kind"]] = report["counts"].get(finding["kind"], 0) + 1
            faults = [item for item in findings if item["kind"] in FAULTS]
            if faults:
                report["pages"].append(
                    {"page": page["pageIndex"] + 1, "table": table["id"], "findings": faults}
                )
    report["faults"] = sum(report["counts"].get(kind, 0) for kind in FAULTS)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--periods", action="store_true")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    report = audit(args.pdf, periods=args.periods)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    for entry in report["pages"]:
        for finding in entry["findings"]:
            detail = ", ".join(f"{key}={value!r}" for key, value in finding.items() if key != "kind")
            print(f"page {entry['page']} {entry['table']}: {finding['kind']} — {detail}")
    counts = ", ".join(f"{kind}={count}" for kind, count in sorted(report["counts"].items()))
    print(f"{report['tables']} tables audited; {counts or 'nothing found'}")
    print(f"cells in the wrong place: {report['faults']}")
    return 1 if report["faults"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
