"""Rebuild the project-authored PDF fixtures in mixed-pdf-purposes."""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "sample-document-corpus" / "mixed-pdf-purposes"
INK = (0.12, 0.16, 0.22)
MUTED = (0.38, 0.43, 0.50)
ACCENT = (0.12, 0.38, 0.62)


def _save(document: fitz.Document, name: str) -> Path:
    path = OUTPUT / name
    temporary = path.with_suffix(path.suffix + ".tmp")
    document.set_metadata(
        {
            "title": name.removesuffix(".pdf"),
            "author": "Talliark project",
            "creator": "scripts/make_local_corpus_fixtures.py",
            "producer": "PyMuPDF",
        }
    )
    document.save(temporary, garbage=3, deflate=True, reproducible=True)
    document.close()
    temporary.replace(path)
    return path


def _text(page: fitz.Page, x: float, y: float, value: str, size: float = 9, *, bold: bool = False, color=INK) -> None:
    page.insert_text((x, y), value, fontsize=size, fontname="hebo" if bold else "helv", color=color)


def _rule(page: fitz.Page, y: float, x0: float = 54, x1: float = 558, width: float = 0.7) -> None:
    page.draw_line((x0, y), (x1, y), color=MUTED, width=width)


def _right(page: fitz.Page, right: float, y: float, value: str, size: float = 9, *, bold: bool = False) -> None:
    font = "hebo" if bold else "helv"
    width = fitz.get_text_length(value, fontname=font, fontsize=size)
    _text(page, right - width, y, value, size, bold=bold)


def period_header_fixture() -> Path:
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    _text(page, 54, 58, "NORTHSTAR COMPONENTS", 12, bold=True, color=ACCENT)
    _text(page, 54, 82, "Warranty reserve activity", 18, bold=True)
    _text(page, 54, 101, "Synthetic regression fixture — amounts in thousands", 8, color=MUTED)
    _text(page, 330, 137, "Years ended December 31,", 9, bold=True)
    for x, label in ((360, "2025"), (452, "2024"), (530, "2023¹")):
        _text(page, x, 158, label, 9, bold=True)
    _rule(page, 166)
    rows = [
        ("Balance at beginning of year", "1,240", "1,105", "980"),
        ("Provision charged to expense", "465", "410", "390"),
        ("Claims paid", "(338)", "(275)", "(265)"),
        ("Balance at end of year", "1,367", "1,240", "1,105"),
    ]
    y = 190
    for index, row in enumerate(rows):
        _text(page, 54, y, row[0], 9, bold=index == len(rows) - 1)
        for x, value in zip((370, 462, 540), row[1:]):
            _text(page, x, y, value, 9, bold=index == len(rows) - 1)
        if index == len(rows) - 2:
            _rule(page, y + 9)
        y += 28
    _rule(page, y - 18, width=1.2)
    _text(page, 54, y + 12, "¹ 2023 excludes the acquired Orion product line before its purchase date.", 8, color=MUTED)
    return _save(document, "21-period-header-footnote.pdf")


def invoice_fixture() -> Path:
    document = fitz.open()
    line_items = [
        ("Implementation workshop", "3", "425.00"),
        ("Document processing seats", "12", "89.00"),
        ("Archive migration", "18", "72.50"),
        ("Quality review hours", "14", "115.00"),
        ("Training materials", "6", "48.00"),
        ("Support retainer", "1", "950.00"),
        ("Sandbox storage (GB)", "250", "0.38"),
        ("Custom export mapping", "4", "160.00"),
        ("On-site travel allowance", "1", "780.00"),
        ("Compliance review", "5", "132.00"),
        ("Data validation hours", "16", "97.50"),
        ("Reconciliation templates", "8", "54.00"),
    ]
    for page_number in range(2):
        page = document.new_page(width=612, height=792)
        _text(page, 54, 58, "TALLIARK LABS", 13, bold=True, color=ACCENT)
        _text(page, 54, 78, "Synthetic document services", 8, color=MUTED)
        _text(page, 430, 58, "INVOICE", 22, bold=True)
        _text(page, 430, 82, "TL-2026-0041", 10, bold=True)
        if page_number == 0:
            _text(page, 54, 126, "Bill to", 8, bold=True, color=MUTED)
            _text(page, 54, 145, "Northstar Components Ltd.", 10, bold=True)
            _text(page, 54, 162, "100 Test Fixture Way", 9)
            _text(page, 54, 178, "Example City, CA 90000", 9)
            for y, label, value in ((126, "Invoice date", "September 7, 2026"), (146, "Due date", "October 7, 2026"), (166, "Purchase order", "PO-48317")):
                _text(page, 360, y, label, 8, color=MUTED)
                _text(page, 446, y, value, 8, bold=True)
            start = 228
        else:
            _text(page, 54, 124, "Invoice TL-2026-0041 — continued", 11, bold=True)
            start = 166
        _text(page, 54, start, "DESCRIPTION", 8, bold=True, color=MUTED)
        _text(page, 386, start, "QTY", 8, bold=True, color=MUTED)
        _text(page, 454, start, "RATE", 8, bold=True, color=MUTED)
        _text(page, 516, start, "AMOUNT", 8, bold=True, color=MUTED)
        _rule(page, start + 9)
        page_items = line_items[page_number * 6 : (page_number + 1) * 6]
        y = start + 35
        page_total = 0.0
        for description, quantity, rate in page_items:
            amount = int(quantity) * float(rate)
            page_total += amount
            _text(page, 54, y, description, 9)
            _right(page, 414, y, quantity, 9)
            _right(page, 490, y, f"${float(rate):,.2f}", 9)
            _right(page, 558, y, f"${amount:,.2f}", 9)
            y += 34
        _rule(page, y - 14)
        if page_number == 0:
            _right(page, 490, y + 10, "Page subtotal", 9, bold=True)
            _right(page, 558, y + 10, f"${page_total:,.2f}", 9, bold=True)
        else:
            first_total = sum(int(q) * float(r) for _, q, r in line_items[:6])
            subtotal = first_total + page_total
            tax = subtotal * 0.0825
            for label, amount, bold in (
                ("Subtotal", subtotal, False),
                ("Sales tax (8.25%)", tax, False),
                ("Amount due", subtotal + tax, True),
            ):
                _right(page, 490, y + 10, label, 9, bold=bold)
                _right(page, 558, y + 10, f"${amount:,.2f}", 9, bold=bold)
                y += 24
            _text(page, 54, 642, "Payment reference", 8, color=MUTED)
            _text(page, 54, 660, "ACH-8842-7719  •  Account ending 4026", 9, bold=True)
            _text(page, 54, 704, "This invoice is fictional and exists only as a Talliark regression fixture.", 8, color=MUTED)
        _text(page, 520, 758, f"Page {page_number + 1} of 2", 8, color=MUTED)
    return _save(document, "invoice-0-4 (1).pdf")


def scanned_table_fixture() -> Path:
    source = fitz.open()
    page = source.new_page(width=612, height=792)
    _text(page, 54, 60, "WAREHOUSE CYCLE COUNT", 16, bold=True)
    _text(page, 54, 82, "Synthetic scan — September 2026", 9, color=MUTED)
    columns = (54, 164, 344, 425, 500, 558)
    top, row_height = 124, 42
    rows = [
        ("BIN", "ITEM", "SYSTEM", "COUNTED", "VARIANCE"),
        ("A-104", "Blue fastener kit", "128", "126", "(2)"),
        ("A-219", "Drive coupling", "84", "84", "0"),
        ("B-031", "Sensor bracket", "215", "218", "3"),
        ("C-117", "Control cable", "63", "61", "(2)"),
        ("D-008", "Packing sleeve", "340", "340", "0"),
    ]
    for x in columns:
        page.draw_line((x, top), (x, top + row_height * len(rows)), color=INK, width=0.8)
    for index in range(len(rows) + 1):
        page.draw_line((columns[0], top + index * row_height), (columns[-1], top + index * row_height), color=INK, width=0.8)
    for row_index, row in enumerate(rows):
        y = top + row_index * row_height + 25
        for x, value in zip((62, 172, 360, 443, 518), row):
            _text(page, x, y, value, 8, bold=row_index == 0)
    _text(page, 54, 420, "Reviewed by: ____________________", 9)
    _text(page, 350, 420, "Date: 09/07/2026", 9)

    pixmap = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), colorspace=fitz.csGRAY, alpha=False)
    source.close()
    document = fitz.open()
    scanned_page = document.new_page(width=612, height=792)
    scanned_page.insert_image(scanned_page.rect, stream=pixmap.tobytes("png"))
    return _save(document, "scanned pdf table.pdf")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for path in (period_header_fixture(), invoice_fixture(), scanned_table_fixture()):
        print(path)


if __name__ == "__main__":
    main()
