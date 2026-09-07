"""Score a Reconcile scan against hand-approved goldens, and report what it did.

The scorer is not a phase that follows the engine. It is what decides whether
any further nomination signal gets enabled, and every phase after it is gated on
a number rather than on judgement. So it measures two different things:

* **Against a golden**, the hard bar and the soft one. Zero false ties anywhere
  in the document is the hard number -- a single confidently wrong finding costs
  more than ten missed totals. Every labelled total in the primary statements
  nominated and confirmed is the soft one, and recall is expected to lag.
* **Without a golden**, the watch lists. Where a run was found, rejected, and
  came close enough that a looser guard would have turned it into a break; where
  a run was refused for containing its own subtotal; and how the unresolved
  totals divide by reason, which is what says whether the next signal to enable
  is `ruling-above` or `outdent`.

The scorer never blesses its own output. `--propose-golden` writes candidates
marked unapproved, and scoring refuses a golden that a person has not marked
approved.

    py scripts/score_reconcile.py "test-imports.local/financial-statements/apple 10k.pdf"
    py scripts/score_reconcile.py "…/apple 10k.pdf" --propose-golden src/python/tests/fixtures/reconcile/apple-10k.json
    py scripts/score_reconcile.py "…/apple 10k.pdf" --golden src/python/tests/fixtures/reconcile/apple-10k.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = Path(__file__).resolve().parent
for entry in (str(ROOT / "src" / "python"), str(SCRIPT_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from engines.financial.detector import detect_financial_structure  # noqa: E402
from engines.reconcile.detector import DETECTOR_VERSION, detect_reconcile  # noqa: E402
from engines.table.detector import detect_tables  # noqa: E402
from engines.values.detector import detect_values  # noqa: E402
from engines.values.lines import prepare as prepare_lines  # noqa: E402


# A golden entry is keyed by what a reviewer can see on the page: the page it is
# printed on, the label of its row, and the figure it reads. Nothing positional,
# because a detector upgrade renumbers rows and a golden that renumbers with it
# is not an independent oracle.
def key_of(page_index: int, label: str, value: str, column: str = "") -> str:
    parts = (
        str(page_index + 1),
        " ".join((column or "").split()),
        " ".join((label or "").split()),
        value,
    )
    return "|".join(parts)


def scan(pdf: Path) -> tuple[dict, dict, dict]:
    """Run the whole pipeline the way a scan runs it, and return its model."""
    from table_corpus import geometry_for

    data = pdf.read_bytes()
    timings: dict = {}
    started = time.perf_counter()
    geometry = geometry_for(data)
    timings["geometry_ms"] = int((time.perf_counter() - started) * 1000)

    document = prepare_lines(geometry)
    tables = detect_tables(data, geometry)
    structure = detect_financial_structure(document, tables=tables)
    values = detect_values(document, claims=structure.spans)
    timings["cache_build_ms"] = int((time.perf_counter() - started) * 1000)

    diagnostics: dict = {}
    model = detect_reconcile(
        data,
        geometry,
        document_id="00000000-0000-0000-0000-000000000000",
        values=values,
        financial=structure.model,
        tables=tables,
        diagnostics=diagnostics,
    )
    timings["total_ms"] = int((time.perf_counter() - started) * 1000)
    return model, diagnostics, timings


def observed(model: dict) -> dict[str, dict]:
    """Every nominated total, keyed the way a golden entry is keyed."""
    cells = {cell["id"]: cell for table in model["tables"] for cell in table["cells"]}
    found: dict[str, dict] = {}
    for table in model["tables"]:
        for total in table["totals"]:
            cell = cells[total["cellId"]]
            entry = {
                "page": table["pageIndex"] + 1,
                "column": next(
                    (
                        header.get("period") or header["text"]
                        for header in table.get("headerLabels", [])
                        if header["columnIndex"] == total["columnIndex"]
                    ),
                    "",
                ),
                "label": cell.get("rowLabel", cell["text"]),
                "value": cell["normalizedValue"],
                "outcome": total["outcome"],
            }
            resolution = total.get("resolution")
            if resolution:
                entry["sum"] = resolution["sum"]
                entry["delta"] = resolution["delta"]
                entry["basis"] = resolution["basis"]
                entry["addends"] = [
                    cells[cell_id]["normalizedValue"]
                    for cell_id in resolution["addendCellIds"]
                    if cell_id in cells and "normalizedValue" in cells[cell_id]
                ]
                entry["negatedAddends"] = [
                    cells[cell_id]["normalizedValue"]
                    for cell_id in resolution["negatedAddendCellIds"]
                    if cell_id in cells and "normalizedValue" in cells[cell_id]
                ]
            if total.get("unresolvedReason"):
                entry["unresolvedReason"] = total["unresolvedReason"]
            found[
                key_of(table["pageIndex"], entry["label"], entry["value"], entry["column"])
            ] = entry
    return found


def propose(model: dict, pdf: Path) -> dict:
    """A golden a person can approve, never one the scorer has approved itself.

    `approved` is false and `pages` is empty on purpose. A reviewer reads each
    entry against the printed page, fixes what is wrong, lists the pages they
    checked in full, and only then sets `approved`. A golden derived from
    detector output and blessed by the same detector measures nothing.
    """
    entries = sorted(
        observed(model).values(),
        key=lambda entry: (entry["page"], entry["label"], entry["column"]),
    )
    return {
        "document": pdf.name,
        "detectorVersion": model["detectorVersion"],
        "approved": False,
        "pages": [],
        "note": (
            "Candidate, not a golden. Check every entry against the printed page, "
            "correct the outcomes the scan got wrong, list in `pages` the page "
            "numbers you checked in full, then set `approved` to true. Any total "
            "confirmed on a listed page that is absent from `totals` scores as a "
            "false tie, which is what makes the hard bar mean anything."
        ),
        "totals": entries,
    }


def score(model: dict, golden: dict) -> dict:
    """The hard bar, the soft bar, and what fell outside the golden's reach."""
    if not golden.get("approved"):
        raise SystemExit(
            "This golden is not approved. The scorer never blesses its own output: "
            "review the entries against the document and set \"approved\": true."
        )
    covered = {int(page) for page in golden.get("pages", [])}
    expected = {
        key_of(
            int(entry["page"]) - 1, entry["label"], entry["value"], entry.get("column", "")
        ): entry
        for entry in golden["totals"]
    }
    actual = observed(model)

    false_ties: list[dict] = []
    wrong_addends: list[dict] = []
    unexpected_breaks: list[dict] = []
    missed: list[dict] = []
    unscored: list[dict] = []

    for key, entry in sorted(actual.items()):
        want = expected.get(key)
        if want is None:
            if entry["page"] in covered and entry["outcome"] != "unresolved":
                # A covered page is one a reviewer checked in full, so anything
                # asserted on it that the golden does not know about is an
                # assertion nobody approved.
                false_ties.append({**entry, "why": "not in an approved page's golden"})
            elif entry["outcome"] != "unresolved":
                unscored.append(entry)
            continue
        if entry["outcome"] == "confirmed" and want["outcome"] != "confirmed":
            false_ties.append({**entry, "expected": want["outcome"]})
        elif entry["outcome"] == "confirmed" and want.get("addends") is not None:
            if entry.get("addends") != want["addends"] or (
                want.get("negatedAddends") is not None
                and entry.get("negatedAddends") != want["negatedAddends"]
            ):
                wrong_addends.append({**entry, "expectedAddends": want["addends"]})
        elif entry["outcome"] == "break" and want["outcome"] != "break":
            unexpected_breaks.append({**entry, "expected": want["outcome"]})

    for key, want in sorted(expected.items()):
        got = actual.get(key)
        if want["outcome"] == "confirmed" and (got is None or got["outcome"] != "confirmed"):
            missed.append({**want, "got": (got or {}).get("outcome", "not nominated")})

    scored = [entry for key, entry in expected.items()]
    return {
        "goldenPages": sorted(covered),
        "goldenTotals": len(scored),
        "falseTies": false_ties,
        "wrongAddends": wrong_addends,
        "unexpectedBreaks": unexpected_breaks,
        "missedTotals": missed,
        "recall": round(
            (sum(1 for entry in scored if entry["outcome"] == "confirmed") - len(missed))
            / max(1, sum(1 for entry in scored if entry["outcome"] == "confirmed")),
            4,
        ),
        "assertedOutsideTheGolden": unscored,
    }


def watch_lists(model: dict) -> dict:
    """What to look at when there is no golden yet, or after one passes.

    `nearFalseBreaks` is the list that matters most. Each entry is a total with a
    complete, decimal-agreeing block above it that missed by more than the
    plausibility guard allows -- so it was recorded as a limit of the scan rather
    than said out loud. Every one of them is a break waiting to appear the moment
    that constant moves, which is why the constant should move on this list
    rather than on a judgement.
    """
    cells = {cell["id"]: cell for table in model["tables"] for cell in table["cells"]}
    near: list[dict] = []
    for table in model["tables"]:
        for total in table["totals"]:
            if total.get("unresolvedReason") != "no-plausible-run":
                continue
            cell = cells[total["cellId"]]
            near.append(
                {
                    "page": table["pageIndex"] + 1,
                    "label": cell.get("rowLabel", cell["text"]),
                    "value": cell["normalizedValue"],
                    "header": next(
                        (
                            header["text"]
                            for header in table.get("headerLabels", [])
                            if header["columnIndex"] == total["columnIndex"]
                        ),
                        "",
                    ),
                }
            )
    return {
        "nearFalseBreaks": near,
        "confirmedByBasis": dict(
            Counter(
                total["resolution"]["basis"]
                for table in model["tables"]
                for total in table["totals"]
                if total["outcome"] == "confirmed"
            )
        ),
        "addendCounts": dict(
            sorted(
                Counter(
                    len(total["resolution"]["addendCellIds"])
                    for table in model["tables"]
                    for total in table["totals"]
                    if total.get("resolution")
                ).items()
            )
        ),
    }


def report_for(pdf: Path, golden: Path | None) -> dict:
    model, diagnostics, timings = scan(pdf)
    summary = model["summary"]
    report = {
        "document": pdf.name,
        "detectorVersion": DETECTOR_VERSION,
        "pages": model["source"]["pageCount"],
        **summary,
        "unresolvedByReason": {
            key[len("reconcile_unresolved_") :].replace("_", "-"): value
            for key, value in sorted(diagnostics.items())
            if key.startswith("reconcile_unresolved_")
            and key != "reconcile_unresolved"
        },
        "findings": [
            {"kind": finding["kind"], "sentence": finding["sentence"]}
            for finding in model["findings"]
        ],
        "watch": watch_lists(model),
        "timings": timings,
        "storedBytes": len(model_bytes(model)),
    }
    if golden is not None:
        report["score"] = score(model, json.loads(golden.read_text(encoding="utf-8")))
    return report, model


def model_bytes(model: dict) -> bytes:
    """What the workbook has to carry, which is an open question the plan flags.

    A whole sum tree over an 80-page filing, gzipped, in a workbook that already
    embeds the PDFs. Measured here so the storage shape is settled on a number.
    """
    from engines.binary_codec import json_to_base64

    return json_to_base64(model).encode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="+", type=Path)
    parser.add_argument("--golden", type=Path, help="score against this hand-approved golden")
    parser.add_argument("--propose-golden", type=Path, help="write candidate goldens for review")
    parser.add_argument("--write-report", type=Path)
    args = parser.parse_args()

    reports = []
    failed = False
    for pdf in args.pdf:
        report, model = report_for(pdf, args.golden)
        reports.append(report)
        if args.propose_golden is not None:
            target = args.propose_golden
            if len(args.pdf) > 1:
                target = target.with_name(f"{pdf.stem}{target.suffix}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(propose(model, pdf), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"wrote candidate golden: {target}", file=sys.stderr)
        scored = report.get("score")
        if scored and (scored["falseTies"] or scored["unexpectedBreaks"] or scored["wrongAddends"]):
            failed = True

    for report in reports:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.write_report is not None:
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        args.write_report.write_text(
            json.dumps(reports if len(reports) > 1 else reports[0], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
