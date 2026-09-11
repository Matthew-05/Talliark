"""How often does this detector confirm a total that is not there?

The instrument: keep every structural signal exactly as the page prints it --
the labels, the rules, the captions, the grid, which cells hold figures and
which hold dashes -- and permute the *values* within each column. Every real
arithmetic relationship is destroyed; every reason the engine had to propose a
total survives. Anything it confirms on that page is a coincidence, and the
count is the false-tie rate of the whole nomination-and-search machine.

Permuting within a column rather than across the table is deliberate: a
coincidence has to beat figures of the same magnitude, which is the hard case.
"""
import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

from table_corpus import geometry_for
from engines.values.lines import prepare as prepare_lines
from engines.table.detector import detect_tables
from engines.financial.detector import detect_financial_structure
from engines.values.detector import detect_values
import engines.reconcile.detector as D

RNG = random.Random()


def _derangement(size: int) -> list[int]:
    """A random permutation in which no value remains in its original cell."""
    while True:
        order = RNG.sample(range(size), size)
        if all(order[index] != index for index in range(size)):
            return order


def permute(built):
    by_column = {}
    for cell in built.published:
        if "normalizedValue" in cell:
            by_column.setdefault(cell["columnIndex"], []).append(cell)
    for cells in by_column.values():
        if len(cells) < 2:
            continue
        payload = [(c["text"], c["normalizedValue"], c.get("decimals", 0)) for c in cells]
        order = _derangement(len(payload))
        for cell, index in zip(cells, order):
            cell["text"], cell["normalizedValue"], cell["decimals"] = payload[index]

_original = D._reconcile_block
_original_admission = D._withheld_unresolved_reason


def patched(built, **kw):
    permute(built)
    return _original(built, **kw)


def admit_every_internal_candidate(*_args, **_kwargs):
    """Keep the null denominator on nomination, not presentation policy.

    The user-facing admission pass can remove only unresolved results. Leaving
    it enabled after permutation would remove denominator cells while every
    chance confirmation necessarily survives, making the arithmetic false-tie
    rate look worse without changing the search. The null instrument therefore
    observes the complete internal nomination machine.
    """
    return None


def scan(pdf):
    data = pdf.read_bytes()
    geometry = geometry_for(data)
    document = prepare_lines(geometry)
    tables = detect_tables(data, geometry)
    st = detect_financial_structure(document, tables=tables)
    values = detect_values(document, claims=st.spans)
    return D.detect_reconcile(
        data,
        geometry,
        document_id="0" * 8 + "-0000-0000-0000-" + "0" * 12,
        values=values,
        financial=st.model,
        tables=tables,
        diagnostics={},
    )


def _document_seed(seed: int, name: str) -> int:
    """Stable across Python processes and machines for reproducible runs."""
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return seed * 7919 + int.from_bytes(digest[:4], "big")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label", help="name used for output/null-<label>.json")
    parser.add_argument("seeds", help="comma-separated integer seeds")
    parser.add_argument(
        "documents",
        nargs="*",
        default=[
            "apple 10k.pdf",
            "disney 10-k.pdf",
            "Amazon_AR.pdf",
            "RoyCarver-2014-Rpt-Final.pdf",
            "cafr1112bfs.pdf",
        ],
        help="financial-statement corpus filenames",
    )
    args = parser.parse_args()
    seeds = [int(value) for value in args.seeds.split(",")]
    report = {}
    for seed in seeds:
        for name in args.documents:
            RNG.seed(_document_seed(seed, name))
            D._reconcile_block = patched
            D._withheld_unresolved_reason = admit_every_internal_candidate
            try:
                model = scan(ROOT / "sample-document-corpus" / "financial-statements" / name)
            finally:
                D._reconcile_block = _original
                D._withheld_unresolved_reason = _original_admission
            summary = model["summary"]
            cells = {
                cell["id"]: cell
                for table in model["tables"]
                for cell in table["cells"]
            }
            false_ties = []
            for table in model["tables"]:
                for total in table["totals"]:
                    if total["outcome"] != "confirmed":
                        continue
                    resolution = total["resolution"]
                    false_ties.append({
                        "page": table["pageIndex"] + 1,
                        "signals": [signal["name"] for signal in total["signals"]],
                        "basis": resolution["basis"],
                        "addends": len(resolution["addendCellIds"]),
                        "negated": len(resolution["negatedAddendCellIds"]),
                        "value": cells[total["cellId"]].get("normalizedValue"),
                        "run": " + ".join(
                            cells[cell_id]["text"].strip()
                            for cell_id in resolution["addendCellIds"]
                        )[:70],
                    })
            report[f"{name}|seed{seed}"] = {
                "nominated": summary["totalsNominated"],
                "confirmed": summary["confirmed"],
                "breaks": summary["breaks"],
                "falseTies": false_ties,
            }
            print(
                f"seed {seed} {name:34s} nominated {summary['totalsNominated']:5d}  "
                f"FALSE TIES {summary['confirmed']:4d}  breaks {summary['breaks']:3d}",
                flush=True,
            )
            target = ROOT / "output" / f"null-{args.label}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report, indent=1), encoding="utf-8")
    total_nominated = sum(value["nominated"] for value in report.values())
    total_confirmed = sum(value["confirmed"] for value in report.values())
    print(
        f"\n{args.label}: {total_confirmed} false ties / {total_nominated} nominations = "
        f"{100 * total_confirmed / max(1, total_nominated):.2f}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
