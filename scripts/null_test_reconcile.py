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
import sys, json, random, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"/"python")); sys.path.insert(0, str(ROOT/"scripts"))
from table_corpus import geometry_for
from engines.values.lines import prepare as prepare_lines
from engines.table.detector import detect_tables
from engines.financial.detector import detect_financial_structure
from engines.values.detector import detect_values
import engines.reconcile.detector as D

RNG = random.Random()

def permute(built):
    by_column = {}
    for cell in built.published:
        if "normalizedValue" in cell:
            by_column.setdefault(cell["columnIndex"], []).append(cell)
    for cells in by_column.values():
        if len(cells) < 2:
            continue
        payload = [(c["text"], c["normalizedValue"], c.get("decimals", 0)) for c in cells]
        for _ in range(20):                      # a derangement: no cell keeps its own value
            order = RNG.sample(range(len(payload)), len(payload))
            if all(order[i] != i for i in range(len(payload))):
                break
        for cell, index in zip(cells, order):
            cell["text"], cell["normalizedValue"], cell["decimals"] = payload[index]

_original = D._reconcile_block
def patched(built, **kw):
    permute(built)
    return _original(built, **kw)

def scan(pdf):
    data = pdf.read_bytes()
    geometry = geometry_for(data)
    document = prepare_lines(geometry)
    tables = detect_tables(data, geometry)
    st = detect_financial_structure(document, tables=tables)
    values = detect_values(document, claims=st.spans)
    return D.detect_reconcile(data, geometry, document_id="0"*8+"-0000-0000-0000-"+"0"*12,
                              values=values, financial=st.model, tables=tables, diagnostics={})

if __name__ == "__main__":
    label = sys.argv[1]
    seeds = [int(x) for x in sys.argv[2].split(",")]
    docs = sys.argv[3:] or ["apple 10k.pdf", "disney 10-k.pdf", "Amazon_AR.pdf",
                            "RoyCarver-2014-Rpt-Final.pdf", "cafr1112bfs.pdf"]
    report = {}
    for seed in seeds:
        for name in docs:
            RNG.seed(seed * 7919 + hash(name) % 1000)
            D._reconcile_block = patched
            m = scan(ROOT/"sample-document-corpus/financial-statements"/name)
            D._reconcile_block = _original
            s = m["summary"]
            cells = {c["id"]: c for t in m["tables"] for c in t["cells"]}
            false_ties = []
            for t in m["tables"]:
                for x in t["totals"]:
                    if x["outcome"] != "confirmed":
                        continue
                    r = x["resolution"]
                    false_ties.append({
                        "page": t["pageIndex"] + 1,
                        "signals": [g["name"] for g in x["signals"]],
                        "basis": r["basis"],
                        "addends": len(r["addendCellIds"]),
                        "negated": len(r["negatedAddendCellIds"]),
                        "value": cells[x["cellId"]].get("normalizedValue"),
                        "run": " + ".join(cells[i]["text"].strip() for i in r["addendCellIds"])[:70],
                    })
            report[f"{name}|seed{seed}"] = {
                "nominated": s["totalsNominated"], "confirmed": s["confirmed"],
                "breaks": s["breaks"], "falseTies": false_ties,
            }
            print(f"seed {seed} {name:34s} nominated {s['totalsNominated']:5d}  FALSE TIES {s['confirmed']:4d}  breaks {s['breaks']:3d}", flush=True)
            json.dump(report, open(ROOT/"output"/f"null-{label}.json", "w"), indent=1)
    tot_n = sum(v["nominated"] for v in report.values())
    tot_c = sum(v["confirmed"] for v in report.values())
    print(f"\n{label}: {tot_c} false ties / {tot_n} nominations = {100*tot_c/max(1,tot_n):.2f}%")
