"""Review a Reconcile golden against the document, then approve it.

The golden workflow has three jobs a person does by hand: check every entry
against the printed page, list the pages checked in full, and set `approved`.
The scorer refuses to bless its own output, so none of that can be automated --
but all of it can be made faster.

This tool renders a **review PDF**: every page that carries a golden entry or an
engine result, with the total cell and its addends outlined and captioned by
what the comparison found. Green agrees, blue is a confirmation the engine did
not reach, red is an engine assertion the golden does not know about, orange is
a confirmation reached through different addends. The same facts are printed as
a terminal sheet and written as JSON.

Approval is non-interactive and explicit. `--pages` records what was checked in
full, `--accept-engine` replaces one entry with the engine's result,
`--accept-missing` adds the engine's confirmations on the checked pages, and
`--approve` sets the flag -- refusing to do so while any false tie, wrong
addend set or unexpected break is outstanding unless `--force` is given.

    py scripts/review_reconcile_golden.py "sample-document-corpus/financial-statements/apple 10k.pdf" `
      --golden src/python/tests/fixtures/reconcile/apple-10k.json

    py scripts/review_reconcile_golden.py "…/apple 10k.pdf" `
      --golden src/python/tests/fixtures/reconcile/apple-10k.json `
      --pages 22,25,26 --accept-missing --approve
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
for entry in (str(ROOT / "src" / "python"), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import pymupdf  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from score_reconcile import key_of, scan, score  # noqa: E402


# One colour per comparison result, and the words that go with it. A reviewer
# reads the sheet by colour first: what agrees, what needs a decision.
MATCH = (0, 150, 0, 255)
MISSED = (0, 102, 255, 255)
UNAPPROVED = (220, 40, 40, 255)
WRONG_ADDENDS = (255, 140, 0, 255)
UNEXPECTED_BREAK = (200, 0, 200, 255)
GOLDEN_BREAK = (110, 110, 110, 255)

COLORS = {
    "match": MATCH,
    "missed": MISSED,
    "unapproved": UNAPPROVED,
    "outcome-differs": UNAPPROVED,
    "wrong-addends": WRONG_ADDENDS,
    "unexpected-break": UNEXPECTED_BREAK,
    "break": GOLDEN_BREAK,
    "golden-break": GOLDEN_BREAK,
}

LEGEND = (
    ("match", "engine and golden agree"),
    ("missed", "golden confirmation the engine did not reach"),
    ("unapproved", "engine assertion absent from the golden"),
    ("wrong-addends", "confirmed, but through different addends"),
    ("unexpected-break", "engine break the golden does not expect"),
)


def _font() -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", 13)
    except Exception:
        return ImageFont.load_default()


def _rect(bounds: dict, size: tuple[int, int]) -> tuple[float, float, float, float]:
    width, height = size
    left = float(bounds["x"]) * width
    top = float(bounds["y"]) * height
    return (
        left,
        top,
        (float(bounds["x"]) + float(bounds["width"])) * width,
        (float(bounds["y"]) + float(bounds["height"])) * height,
    )


def _outline(
    draw: ImageDraw.ImageDraw,
    bounds: dict,
    size: tuple[int, int],
    color,
    *,
    width: int,
) -> tuple[float, float, float, float]:
    box = _rect(bounds, size)
    draw.rectangle(box, outline=color, width=width)
    return box


@dataclass
class ReviewEntry:
    """One figure the golden and the engine have something to say about."""

    page: int
    column: str
    label: str
    value: str
    classification: str
    engine: dict | None = None
    golden: dict | None = None
    cell: dict | None = None

    @property
    def locator(self) -> str:
        return f"{self.page}|{self.label}|{self.value}"

    def as_dict(self) -> dict:
        entry = {
            "page": self.page,
            "column": self.column,
            "label": self.label,
            "value": self.value,
            "classification": self.classification,
        }
        if self.engine:
            entry["engine"] = {
                key: value
                for key, value in self.engine.items()
                if key not in ("cell", "addendCells", "table", "total")
            }
        if self.golden:
            entry["golden"] = {
                key: value
                for key, value in self.golden.items()
                if key not in ("page",)
            }
        return entry


def engine_index(model: dict) -> list[dict]:
    """Every engine total with the geometry needed to draw it.

    `score_reconcile.observed` carries the scorer's key and outcome; this keeps
    the cells as well, because the review PDF draws the total and its addends
    and the scorer never needs to.
    """
    cells = {cell["id"]: cell for table in model["tables"] for cell in table["cells"]}
    found: list[dict] = []
    for table in model["tables"]:
        for total in table["totals"]:
            cell = cells[total["cellId"]]
            column = next(
                (
                    header.get("period") or header["text"]
                    for header in table.get("headerLabels", [])
                    if header["columnIndex"] == total["columnIndex"]
                ),
                "",
            )
            entry = {
                "page": table["pageIndex"] + 1,
                "column": column,
                "label": cell.get("rowLabel", cell["text"]),
                "value": cell.get("normalizedValue"),
                "outcome": total["outcome"],
                "axis": total.get("axis", "vertical"),
                "cell": cell,
                "table": table,
                "total": total,
            }
            resolution = total.get("resolution")
            if resolution:
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
                entry["addendCells"] = [
                    cells[cell_id]
                    for cell_id in resolution["addendCellIds"]
                    if cell_id in cells
                ]
            if total.get("unresolvedReason"):
                entry["unresolvedReason"] = total["unresolvedReason"]
            found.append(entry)
    return found


def _locate_golden_cell(model: dict, golden: dict) -> dict | None:
    """Find the printed cell a golden entry names, even where nothing nominated it.

    The review sheet draws a missed confirmation in place, so the reviewer can
    see which figure the worklist is about without opening the PDF by hand.
    """
    page = int(golden["page"]) - 1
    wanted_label = " ".join(str(golden.get("label", "")).split())
    wanted_value = str(golden.get("value", ""))
    fallback: dict | None = None
    for table in model["tables"]:
        if table["pageIndex"] != page:
            continue
        for cell in table["cells"]:
            if str(cell.get("normalizedValue", "")) != wanted_value:
                continue
            label = " ".join(str(cell.get("rowLabel", "")).split())
            if label == wanted_label:
                return cell
            if fallback is None and (
                label.endswith(wanted_label) or wanted_label.endswith(label)
            ):
                fallback = cell
    return fallback


def _classify(engine: dict | None, golden: dict | None) -> str:
    """What the comparison found for one figure.

    An engine miss is not an assertion, so it is only "missed" when the golden
    confirmed the figure and the engine did not; otherwise it is the engine's
    own limitation and stays grey.
    """
    if engine is None or engine["outcome"] == "unresolved":
        if golden is not None and golden.get("outcome") == "confirmed":
            return "missed"
        return "unresolved"
    if golden is None:
        return "unapproved"
    if engine["outcome"] == "confirmed" and golden["outcome"] == "confirmed":
        if engine.get("addends") == golden.get("addends") and (
            golden.get("negatedAddends") is None
            or engine.get("negatedAddends") == golden.get("negatedAddends")
        ):
            return "match"
        return "wrong-addends"
    if engine["outcome"] == "confirmed":
        return "outcome-differs"
    if engine["outcome"] == "break" and golden["outcome"] != "break":
        return "unexpected-break"
    if engine["outcome"] == "break":
        return "golden-break"
    return "unresolved"


def _preferred_axis(golden: dict) -> dict[str, str]:
    """The axis each golden entry is about, so a dual-axis cell compares right."""
    return {
        key_of(
            int(entry["page"]) - 1,
            entry["label"],
            entry["value"],
            entry.get("column", ""),
        ): ("cross" if entry.get("basis") == "row" else "vertical")
        for entry in golden["totals"]
        if entry.get("basis")
    }


def review(model: dict, golden: dict) -> list[ReviewEntry]:
    """Pair the engine's totals with the golden's, entry by entry."""
    engine_entries = engine_index(model)
    preferred = _preferred_axis(golden)
    by_key: dict[str, dict] = {}
    for entry in engine_entries:
        key = key_of(
            entry["page"] - 1, entry["label"], entry["value"], entry["column"]
        )
        prior = by_key.get(key)
        wanted = preferred.get(key)
        if (
            prior is None
            or wanted is None
            or entry["axis"] == wanted
            or prior.get("axis") != wanted
        ):
            by_key[key] = entry
    expected: dict[str, dict] = {}
    for entry in golden["totals"]:
        expected[
            key_of(
                int(entry["page"]) - 1,
                entry["label"],
                entry["value"],
                entry.get("column", ""),
            )
        ] = entry

    rows: list[ReviewEntry] = []
    for key, engine in by_key.items():
        want = expected.get(key)
        rows.append(
            ReviewEntry(
                page=engine["page"],
                column=engine["column"],
                label=engine["label"],
                value=str(engine["value"]),
                classification=_classify(engine, want),
                engine=engine,
                golden=want,
                cell=engine["cell"],
            )
        )
    for key, want in expected.items():
        if key in by_key or want.get("outcome") != "confirmed":
            continue
        cell = _locate_golden_cell(model, want)
        rows.append(
            ReviewEntry(
                page=int(want["page"]),
                column=want.get("column", ""),
                label=want["label"],
                value=str(want["value"]),
                classification="missed",
                golden=want,
                cell=cell,
            )
        )
    rows.sort(key=lambda row: (row.page, row.label, row.value))
    return rows


def _overlaps(first: tuple, second: tuple) -> bool:
    return not (
        first[2] <= second[0]
        or first[0] >= second[2]
        or first[3] <= second[1]
        or first[1] >= second[3]
    )


def _caption(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    text: str,
    color,
    placed: list[tuple],
    height: int,
) -> None:
    """Draw a caption and nudge it clear of the ones already on the page.

    Totals cluster at the bottom of a statement, so captions drawn blindly sit
    on top of each other. A caption is worth more than the pixels it covers.
    """
    font = _font()
    box = draw.textbbox((x, y), text, font=font)
    rect = (box[0] - 2.0, box[1] - 1.0, box[2] + 2.0, box[3] + 1.0)
    step = (box[3] - box[1]) + 3.0
    for _ in range(60):
        if not any(_overlaps(rect, other) for other in placed):
            break
        if rect[3] + step > height:
            rect = (rect[0], rect[1] - step, rect[2], rect[3] - step)
        else:
            rect = (rect[0], rect[1] + step, rect[2], rect[3] + step)
    draw.rectangle(rect, fill=(255, 255, 255, 235))
    draw.text((rect[0] + 2, rect[1] + 1), text, fill=color, font=font)
    placed.append(rect)


def _render(
    pdf: Path,
    rows: list[ReviewEntry],
    out_pdf: Path,
    *,
    dpi: int,
    pages: set[int] | None,
) -> Path:
    wanted = sorted(
        {
            row.page
            for row in rows
            if pages is None or row.page in pages
        }
    )
    if not wanted:
        raise ValueError("No review entries to render")
    document = pymupdf.open(str(pdf))
    images: list[Image.Image] = []
    try:
        for page_number in wanted:
            page_index = page_number - 1
            if not 0 <= page_index < document.page_count:
                continue
            pixmap = document.load_page(page_index).get_pixmap(dpi=dpi, alpha=False)
            image = Image.frombytes(
                "RGB", (pixmap.width, pixmap.height), pixmap.samples
            )
            draw = ImageDraw.Draw(image)
            placed: list[tuple] = []
            for row in (item for item in rows if item.page == page_number):
                color = COLORS.get(row.classification, GOLDEN_BREAK)
                if row.cell is not None:
                    box = _outline(
                        draw, row.cell["bounds"], image.size, color, width=3
                    )
                    if row.classification not in ("match", "golden-break"):
                        caption = (
                            f"{row.classification} · {row.label[:40]} = {row.value} · "
                            f"{row.engine['outcome'] if row.engine else 'no engine result'}"
                        )
                        _caption(
                            draw,
                            box[0] + 2,
                            max(2.0, box[1] - 16),
                            caption,
                            color,
                            placed,
                            image.size[1],
                        )
                if row.engine and row.engine.get("addendCells"):
                    for cell in row.engine["addendCells"]:
                        _outline(
                            draw, cell["bounds"], image.size, color, width=2
                        )
            if page_number == wanted[0]:
                for offset, (kind, meaning) in enumerate(LEGEND):
                    _caption(
                        draw,
                        6,
                        6 + offset * 16,
                        f"{kind}: {meaning}",
                        COLORS.get(kind, GOLDEN_BREAK),
                        placed,
                        image.size[1],
                    )
            images.append(image)
    finally:
        document.close()

    if not images:
        raise ValueError("No review entries to render")
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    try:
        images[0].save(
            out_pdf,
            "PDF",
            resolution=dpi,
            save_all=True,
            append_images=images[1:],
        )
    finally:
        for image in images:
            image.close()
    return out_pdf


def _page_set(values: list[str] | None) -> set[int] | None:
    if not values:
        return None
    pages: set[int] = set()
    for value in values:
        for part in str(value).split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start, end = part.split("-", 1)
                pages.update(range(int(start), int(end) + 1))
            else:
                pages.add(int(part))
    return pages


def _find_golden_entry(golden: dict, locator: str) -> dict | None:
    page, label, value = locator.split("|", 2)
    for entry in golden["totals"]:
        if (
            int(entry["page"]) == int(page)
            and entry.get("label") == label
            and str(entry.get("value")) == value
        ):
            return entry
    return None


def _accept_engine(golden: dict, model: dict, locator: str) -> str | None:
    """Replace one golden entry with the engine's result for the same figure.

    The column label is part of the scorer's key but not of the locator: an
    engine block can name the same column differently from the golden, and the
    reviewer pointing at a page, a label and a value means that figure. The
    exact key is tried first, then the column-agnostic match.
    """
    entry = _find_golden_entry(golden, locator)
    if entry is None:
        return None
    index = engine_index(model)
    key = key_of(
        int(entry["page"]) - 1,
        entry["label"],
        entry["value"],
        entry.get("column", ""),
    )
    # The scorer keeps the last entry for a key, so the review must too: the
    # same figure can be reached through more than one substrate, and the
    # working the comparison judges is the last one.
    matches = [
        item
        for item in index
        if key_of(item["page"] - 1, item["label"], item["value"], item["column"])
        == key
    ]
    if not matches:
        matches = [
            item
            for item in index
            if item["page"] == int(entry["page"])
            and item["label"] == entry["label"]
            and str(item["value"]) == str(entry["value"])
        ]
    if not matches:
        return None
    # A cell that is both a column total and a row total has two engine entries.
    # The golden's basis says which assertion it records, so replace that one.
    wanted = "cross" if entry.get("basis") == "row" else "vertical"
    preferred = [item for item in matches if item["axis"] == wanted]
    engine = (preferred or matches)[-1]
    entry["outcome"] = engine["outcome"]
    for field in ("sum", "delta", "basis", "addends", "negatedAddends"):
        entry.pop(field, None)
    for field in ("sum", "delta", "basis", "addends", "negatedAddends"):
        if field in engine:
            entry[field] = engine[field]
    entry.pop("unresolvedReason", None)
    if "unresolvedReason" in engine:
        entry["unresolvedReason"] = engine["unresolvedReason"]
    return entry.get("label", "")


def _accept_missing(golden: dict, model: dict, pages: set[int]) -> list[str]:
    """Add the engine's confirmations on the checked pages the golden lacks."""
    expected = {
        key_of(
            int(entry["page"]) - 1,
            entry["label"],
            entry["value"],
            entry.get("column", ""),
        )
        for entry in golden["totals"]
    }
    added: list[str] = []
    for engine in engine_index(model):
        if engine["outcome"] != "confirmed" or engine["page"] not in pages:
            continue
        key = key_of(
            engine["page"] - 1, engine["label"], engine["value"], engine["column"]
        )
        if key in expected:
            continue
        entry = {
            "page": engine["page"],
            "column": engine["column"],
            "label": engine["label"],
            "value": str(engine["value"]),
            "outcome": "confirmed",
        }
        for field in ("sum", "delta", "basis", "addends", "negatedAddends"):
            if field in engine:
                entry[field] = engine[field]
        golden["totals"].append(entry)
        expected.add(key)
        added.append(f"{entry['page']}|{entry['label']}|{entry['value']}")
    return added


def _summary_rows(rows: list[ReviewEntry]) -> dict[str, int]:
    return dict(Counter(row.classification for row in rows))


def _print_sheet(pdf: Path, golden_path: Path, golden: dict, rows, summary) -> None:
    print(f"{pdf.name} — {golden_path}")
    print(
        f"  approved={bool(golden.get('approved'))} "
        f"pagesChecked={len(golden.get('pages', []))} "
        f"goldenEntries={len(golden.get('totals', []))}"
    )
    print(
        "  match {match} · missed {missed} · unapproved {unapproved} · "
        "wrong-addends {wrong-addends} · outcome-differs {outcome-differs} · "
        "breaks {golden-break}".format(
            **{
                key: summary.get(key, 0)
                for key in (
                    "match",
                    "missed",
                    "unapproved",
                    "wrong-addends",
                    "outcome-differs",
                    "golden-break",
                )
            }
        )
    )
    for row in rows:
        if row.classification in ("match", "golden-break", "unresolved"):
            continue
        engine = row.engine or {}
        print(
            f"  [{row.classification}] p{row.page} {row.label!r} = {row.value} "
            f"engine={engine.get('outcome', '—')} "
            f"basis={engine.get('basis', '—')} "
            f"addends={engine.get('addends', '—')}"
        )


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument(
        "--out",
        type=Path,
        help="review PDF path (default output/pdf/reconcile-golden-review/<stem>-review.pdf)",
    )
    parser.add_argument(
        "--write-report",
        type=Path,
        help="review sheet JSON (default output/reconcile-golden-review/<stem>-review.json)",
    )
    parser.add_argument("--dpi", type=int, default=110)
    parser.add_argument(
        "--page",
        action="append",
        help="render only these 1-based pages, list or range; default every page with an entry",
    )
    parser.add_argument("--no-render", action="store_true", help="skip the review PDF")
    parser.add_argument(
        "--model",
        type=Path,
        help="load a previously written model instead of re-running the scan",
    )
    parser.add_argument(
        "--write-model",
        type=Path,
        help="save the model so a later review run need not re-scan",
    )
    parser.add_argument(
        "--pages",
        help="set the pages checked in full, e.g. 22,25,26-28",
    )
    parser.add_argument(
        "--add-pages",
        help="add pages to the checked-in-full list without replacing it",
    )
    parser.add_argument(
        "--accept-engine",
        action="append",
        default=[],
        metavar="PAGE|LABEL|VALUE",
        help="replace one golden entry with the engine's result for that figure",
    )
    parser.add_argument(
        "--accept-missing",
        action="store_true",
        help="add the engine's confirmations on the checked pages that the golden lacks",
    )
    parser.add_argument(
        "--drop",
        action="append",
        default=[],
        metavar="PAGE|LABEL|VALUE",
        help="remove a golden entry that is wrong",
    )
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--unapprove", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="approve even while false ties, wrong addends or unexpected breaks remain",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the edits without writing the golden",
    )
    args = parser.parse_args()

    if args.model is not None:
        model = json.loads(args.model.read_text(encoding="utf-8"))
    else:
        model, _diagnostics, _timings = scan(args.pdf)
    if args.write_model is not None:
        args.write_model.parent.mkdir(parents=True, exist_ok=True)
        args.write_model.write_text(
            json.dumps(model, ensure_ascii=False), encoding="utf-8"
        )

    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    rows = review(model, golden)
    summary = _summary_rows(rows)
    report = score(model, golden, require_approved=False)
    _print_sheet(args.pdf, args.golden, golden, rows, summary)

    stem = args.pdf.stem
    out_pdf = args.out or (
        ROOT / "output" / "pdf" / "reconcile-golden-review" / f"{stem}-review.pdf"
    )
    report_path = args.write_report or (
        ROOT / "output" / "reconcile-golden-review" / f"{stem}-review.json"
    )
    if not args.no_render:
        _render(
            args.pdf,
            rows,
            out_pdf,
            dpi=args.dpi,
            pages=_page_set(args.page),
        )
        print(f"review PDF: {out_pdf}")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "document": args.pdf.name,
                "golden": str(args.golden),
                "approved": bool(golden.get("approved")),
                "pages": golden.get("pages", []),
                "classifications": summary,
                "score": report,
                "entries": [row.as_dict() for row in rows],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"review sheet: {report_path}")

    edits: list[str] = []
    if args.pages is not None:
        golden["pages"] = sorted(_page_set([args.pages]) or set())
        edits.append(f"pages := {golden['pages']}")
    if args.add_pages:
        current = set(int(page) for page in golden.get("pages", []))
        current |= _page_set([args.add_pages]) or set()
        golden["pages"] = sorted(current)
        edits.append(f"pages := {golden['pages']}")
    for locator in args.accept_engine:
        label = _accept_engine(golden, model, locator)
        edits.append(
            f"accept-engine {locator}: {'replaced' if label else 'no such entry'}"
        )
    for locator in args.drop:
        entry = _find_golden_entry(golden, locator)
        if entry is not None:
            golden["totals"].remove(entry)
            edits.append(f"drop {locator}: removed")
        else:
            edits.append(f"drop {locator}: no such entry")
    if args.accept_missing:
        pages = set(int(page) for page in golden.get("pages", []))
        added = _accept_missing(golden, model, pages)
        edits.append(f"accept-missing: {len(added)} added")
        edits.extend(f"  + {locator}" for locator in added)
    if args.unapprove:
        golden["approved"] = False
        edits.append("approved := false")
    if edits:
        edited_rows = review(model, golden)
        print(
            "after edits: "
            + " · ".join(
                f"{kind} {count}"
                for kind, count in sorted(_summary_rows(edited_rows).items())
            )
        )
    if args.approve:
        # The guard judges the golden as it will be written, not as it was read:
        # the whole point of `--accept-missing` is to resolve the very false ties
        # the pre-edit comparison reports.
        after = score(model, golden, require_approved=False)
        if (
            after["falseTies"] or after["wrongAddends"] or after["unexpectedBreaks"]
        ) and not args.force:
            print(
                f"refusing to approve: {len(after['falseTies'])} false ties, "
                f"{len(after['wrongAddends'])} wrong addend sets, "
                f"{len(after['unexpectedBreaks'])} unexpected breaks. "
                "Resolve them or pass --force.",
                file=sys.stderr,
            )
            for item in after["wrongAddends"]:
                print(
                    f"  wrong-addends p{item['page']} {item.get('label')!r} = "
                    f"{item.get('value')} engine={item.get('addends')} "
                    f"golden={item.get('expectedAddends')}",
                    file=sys.stderr,
                )
            for item in after["falseTies"]:
                print(
                    f"  false-tie p{item['page']} {item.get('label')!r} = "
                    f"{item.get('value')}",
                    file=sys.stderr,
                )
            return 2
        golden["approved"] = True
        edits.append("approved := true")
    if args.dry_run:
        print("dry run; no golden written")
        for edit in edits:
            print(f"  {edit}")
        return 0
    if edits:
        golden["totals"].sort(
            key=lambda entry: (
                entry["page"],
                str(entry.get("label")),
                str(entry.get("value")),
            )
        )
        args.golden.write_text(
            json.dumps(golden, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"golden written: {args.golden}")
        for edit in edits:
            print(f"  {edit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
