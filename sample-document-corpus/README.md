# Sample document corpus

The documents the engines are measured against. Every score in the engine docs,
every golden under `src/python/tests/fixtures/`, and every threshold that had a
measurement behind it came from a file in this folder.

Scripts take a corpus path as their first argument:

```powershell
py scripts/score_tables.py "sample-document-corpus/financial-statements/apple 10k.pdf" --write-report output/report.json
py scripts/score_values.py "sample-document-corpus/financial-statements/apple 10k.pdf" --write-report output/fs.json
py scripts/score_reconcile.py "sample-document-corpus/financial-statements/apple 10k.pdf"
```

The financial reports and public OCR resources are local development inputs and
are not committed to this repository. Fetch them directly from their sources
after cloning:

```powershell
.\scripts\setup-development-corpus.ps1
```

The script pins and verifies every downloaded source by SHA-256, then assembles
the image-derived PDFs locally. Only project-authored regression fixtures and
the small importer fixtures remain in the repository.

| Folder | Holds | Exercises |
| --- | --- | --- |
| `financial-statements/` | 8 real filings and reports, 573 pages | Table detection, value recognition, the sum tree, footing and cross-footing |
| `mixed-pdf-purposes/` | 20 downloaded/generated public PDFs and 4 project fixtures | OCR, page geometry, rotation and skew, scanned tables |
| `supported/` | 30 files across every importable format | The conversion path — one file per format the importer accepts |
| `unsupported/` | 8 stub files | The refusal path; each is a few hundred bytes, contents irrelevant |
| `folder-drop/` | A small nested folder tree | Folder import: recursion, mixed types, a subfolder |

## `financial-statements/`

The measurement corpus. Its PDFs are gitignored and populated by
`scripts/setup-development-corpus.ps1`. `apple 10k.pdf` is the primary benchmark
— most numbers quoted in the engine docs are its numbers — and the others exist
to keep a threshold tuned on one document from being mistaken for a threshold
that works.

| File | What it is | Pages | Text layer | Why it is here |
| --- | --- | ---: | --- | --- |
| `apple 10k.pdf` | Apple Inc. Form 10-K, fiscal year ended September 27, 2025 | 80 | Native throughout | The primary benchmark: clean typography, deep statements, dense notes |
| `disney 10-k.pdf` | The Walt Disney Company Form 10-K, fiscal year ended September 27, 2025 | 128 | Native; image cover and section dividers | A second large filing with a different house style |
| `quest 10k.pdf` | Quest Form 10-K | 90 | Native; no text on the cover | Tests that detection does not assume page 1 carries text |
| `Amazon_AR.pdf` | Amazon.com 2017 Annual Report | 89 | Native | Annual report wrapper rather than a bare filing; shareholder letter before the statements |
| `Boeing-2023-Annual-Report.pdf` | The Boeing Company 2023 Annual Report | 152 | Native | The largest document in the corpus; heavy design, charts beside tables |
| `RoyCarver-2014-Rpt-Final.pdf` | Roy J. Carver Charitable Trust, financial report April 30, 2014 | 18 | Native; some scanned pages | A small audited non-profit report: auditor's report, statements, notes, supplementary book-to-GAAP schedules |
| `cafr1112bfs.pdf` | County of San Diego, Comprehensive Annual Financial Report for the year ended June 30, 2012 — basic financial statements extract | 13 | Native | Governmental reporting: fund columns, component units, statement of net assets |
| `Sample-Financial-Statements-1.pdf` | A teaching example — income statement, retained earnings, balance sheet for a fictional company | 3 | Native | The smallest end-to-end case; every total is checkable by eye |

The setup script downloads each document from its publisher or issuer-hosted
investor-relations CDN. These exact URLs and hashes were verified on 2026-09-07:

| File | Publisher source |
| --- | --- |
| `apple 10k.pdf` | [Apple investor relations](https://d18rn0p25nwr6d.cloudfront.net/CIK-0000320193/c24e7a28-5254-4dfa-9447-62aaa3c24bb1.pdf) |
| `disney 10-k.pdf` | [The Walt Disney Company investor relations](https://investors.thewaltdisneycompany.com/files/doc_financials/2025/ar/2025-Annual-Report.pdf) |
| `quest 10k.pdf` | [Quest Resource Holding Corporation investor relations](https://s29.q4cdn.com/924252060/files/doc_financials/2025/ar/d494cf61-2aaf-485f-b90e-ecc0853ca722.pdf) |
| `Amazon_AR.pdf` | [Amazon investor relations](https://s2.q4cdn.com/299287126/files/doc_financials/annual/Amazon_AR.PDF) |
| `Boeing-2023-Annual-Report.pdf` | [Boeing investor relations](https://s2.q4cdn.com/661678649/files/doc_financials/2023/ar/Boeing-2023-Annual-Report.pdf) |
| `RoyCarver-2014-Rpt-Final.pdf` | [Roy J. Carver Charitable Trust](https://carvertrust.org/wp-content/uploads/2017/03/RoyCarver-2014-Rpt-Final.pdf) |
| `cafr1112bfs.pdf` | [County of San Diego Auditor and Controller](https://www.sandiegocounty.gov/content/dam/sdc/auditor/annual_report12/pdf/cafr1112bfs.pdf) |
| `Sample-Financial-Statements-1.pdf` | [The College of New Jersey, Mayo Business Plan Competition](https://mbpc.tcnj.edu/wp-content/uploads/sites/148/2021/10/Sample-Financial-Statements-1.pdf) |

The repository does not redistribute these reports. Developers download them
from their publishers for local testing and remain responsible for complying
with the terms at each source.

### SHA-256

```text
108590052c3ba5400c63660d787fe7ed4e43868292946d7a7facebe9ab7d1aab  apple 10k.pdf
4567eb113db33ae6061dd047743093365b7aa875f9b2908c25bc044c389da5fe  disney 10-k.pdf
c374675aac0f31fee5972319e487fbd918af88b0c0c5c626cf9e6fac67508335  quest 10k.pdf
7ad17bf1634f10ea777505d5c35de1735bc3e2944c614e1a80c68360c942d3ad  Amazon_AR.pdf
663a413e937a8596f39f6443ee9ea761b1124b500aee6feda1d19b5f5f9195b5  Boeing-2023-Annual-Report.pdf
f912cf0136af5cab9e898b0eedb0b2d04c1242d08ba97226f9c77993eeff6f5d  RoyCarver-2014-Rpt-Final.pdf
524419643cd63f23a2f65ee4292b2ac2353c0924852bcb709e67c01d6c2eaf1d  cafr1112bfs.pdf
3c424fe06ddfe8ce9291ce10cfd2f7633ceb23dfc448a920b538584b0ec8023a  Sample-Financial-Statements-1.pdf
```

## `mixed-pdf-purposes/`

Files `01`–`20` are a public scanned-PDF OCR corpus collected on 2026-08-16: 20
PDFs, 35 pages. They are gitignored and populated by
`scripts/setup-development-corpus.ps1`; every direct download and source image
is SHA-256 pinned. Files `21` and the three unnumbered files are small,
project-authored fixtures that remain committed.

### Public OCR resources (`01`–`12`)

Upstream directory:
https://github.com/ocrmypdf/OCRmyPDF/tree/main/tests/resources

License metadata:
https://github.com/ocrmypdf/OCRmyPDF/blob/main/REUSE.toml

| Local file | Upstream file | Primary test case | Pages | Existing text |
| --- | --- | --- | ---: | ---: |
| `01-difficult-illustrated-book-page.pdf` | `c02-22.pdf` | Degraded historical page, decorative font, illustration | 1 | None |
| `02-two-column-scan.pdf` | `linn.pdf` | Dense two-column technical page | 1 | None |
| `03-ccitt-monochrome-scan.pdf` | `ccitt.pdf` | CCITT monochrome image encoding | 1 | None |
| `04-skewed-scan.pdf` | `skew.pdf` | Skewed page geometry | 1 | None |
| `05-rotated-skewed-scan.pdf` | `rotated_skew.pdf` | PDF rotation plus skew | 1 | None |
| `06-multiple-page-rotations.pdf` | `cardinal.pdf` | Four pages at different orientations | 4 | None |
| `07-high-dpi-typewriter.pdf` | `2400dpi.pdf` | Noisy typewriter text at extreme source DPI | 1 | None |
| `08-nonsquare-dpi-scan.pdf` | `aspect.pdf` | Different horizontal and vertical DPI | 1 | None |
| `09-french-diacritics.pdf` | `francais.pdf` | Accents and non-ASCII Latin text | 1 | None |
| `10-image-in-form-xobject.pdf` | `formxobject.pdf` | Raster image nested in a Form XObject | 1 | Partial |
| `11-multipage-mixed.pdf` | `multipage.pdf` | Mixed historical scan, graphics, text, and columns | 6 | Partial |
| `12-jbig2-monochrome-scan.pdf` | `jbig2.pdf` | JBIG2 monochrome image encoding | 1 | None |

### Table-oriented scans (`13`–`20`)

Files 13-19 come from the Apache-2.0-licensed Google Cloud Document AI samples.
Files 17-20 are assembled during setup from the cited public raster images at
200 DPI without an OCR text layer, so they exercise the same image-only import
path as a scanner-generated PDF.

Google source repository:
https://github.com/GoogleCloudPlatform/document-ai-samples

Google repository license:
https://github.com/GoogleCloudPlatform/document-ai-samples/blob/main/LICENSE

Library of Congress ledger record and rights statement:
https://www.loc.gov/item/mgw500001/
https://www.loc.gov/collections/george-washington-papers/about-this-collection/rights-and-access/

| Local file | Public source | Primary test case | Pages | Existing text |
| --- | --- | --- | ---: | ---: |
| `13-scanned-line-item-invoice.pdf` | `community/codelabs/docai-specialized-processors/google_invoice.pdf` | Raster invoice with line-item columns and totals | 1 | None |
| `14-stained-energy-invoice.pdf` | `web-app-pix2info-python/src/samples/4-INVOICE_PROCESSOR/a. Energy invoice (stained).pdf` | Stained, dense, multi-page utility invoice with charts and tables | 2 | None |
| `15-mixed-procurement-packet.pdf` | `community/codelabs/docai-specialized-processors/procurement_multi_document.pdf` | Invoice, photographed receipt, stained form, statement, and narrow receipt | 5 | None |
| `16-scanned-form-with-table.pdf` | `community/codelabs/docai-form-parser/form_with_tables.pdf` | Form labels plus bordered two-column table | 1 | None |
| `17-photographed-grocery-receipt.pdf` | `web-app-pix2info-python/src/samples/3-EXPENSE_PROCESSOR/a. Receipt.png` | Low-contrast photographed thermal receipt with show-through | 1 | None |
| `18-photographed-ticket-receipt.pdf` | `web-app-pix2info-python/src/samples/3-EXPENSE_PROCESSOR/b. Enrichments.jpg` | Perspective-skewed landscape ticket receipt | 1 | None |
| `19-scanned-table-layouts.pdf` | `web-app-pix2info-python/src/samples/2-FORM_PARSER_PROCESSOR/{c. Table without borders.png,d. Table with borders.png}` | Same table as bordered and borderless scans | 2 | None |
| `20-historical-general-ledger.pdf` | George Washington, *General Ledger A*, source images 0250 and 0600 | Dense handwritten debit/credit ledger pages with ruled columns | 2 | None |

### Project-authored fixtures

Not part of the downloaded corpus. These contain only fictional/project-created
material and may be redistributed with this repository. Three are reproducibly
generated by `scripts/make_local_corpus_fixtures.py`; `Wrapping Number
Modifiers.pdf` is an earlier project-authored fixture.

| Local file | Primary test case | Pages | Existing text |
| --- | --- | ---: | ---: |
| `21-period-header-footnote.pdf` | Synthetic period header and footnote marker in the same column band | 1 | Native |
| `Wrapping Number Modifiers.pdf` | A modifier that wraps onto the following line, away from the number it qualifies | 2 | Native |
| `invoice-0-4 (1).pdf` | Synthetic born-digital invoice with line items | 2 | Native |
| `scanned pdf table.pdf` | Synthetic scanned table with no text layer at all | 1 | None |

### Licenses

Applies to the upstream inputs for files `01`–`20` only. The repository records
their provenance but does not redistribute their bytes.

- `c02-22.pdf` and `multipage.pdf`: public domain.
- `aspect.pdf`, `francais.pdf`, and `formxobject.pdf`: CC-BY-SA-4.0.
- `linn.pdf` and its CCITT, skew, rotation, cardinal, and JBIG2 derivatives:
  GFDL-1.2-or-later or CC-BY-SA-3.0.
- `2400dpi.pdf`: GFDL-1.2-or-later or one of CC-BY-SA-1.0 through
  CC-BY-SA-3.0, as recorded by the upstream project.
- Files 13-19: Apache-2.0, from the Google Cloud Document AI samples.
- File 20: Library of Congress George Washington Papers; see the linked Rights
  and Access statement for permitted use and reuse guidance.

### SHA-256

Files 01-16 are direct downloads, so their output hashes are fixed. Files 17-20
are locally assembled PDFs whose container bytes may vary with Pillow; the
setup script instead pins every source image hash shown after this list.

```text
ae6a3bec3809e1540911bda42dabb42ffbd63cfda17e74a5c3e9dcd87129462a  01-difficult-illustrated-book-page.pdf
e923f6e8e036185f8f2aae5f7fdeefd8ac658d627cebd4ebf630de4cbf0a2d64  02-two-column-scan.pdf
5f4b129bf0eb0d32358a917cd1754c6fd68cac589ad79076b6d0191ebe84f0f1  03-ccitt-monochrome-scan.pdf
6be6b54d49df71351974774404e299b756d8d3cbeb2c4a31f15fae8dd983d72f  04-skewed-scan.pdf
5122c07d05a61219eb9f8305776ef16def6b34c29358e05c5ae2e884a351438f  05-rotated-skewed-scan.pdf
ebd7b2233aea5f320562df9635901bbd8e734cb42c85defa1059163d4b1d6ccb  06-multiple-page-rotations.pdf
98cf83efd35673301dbfcbfa900e42a31f8ea90cb16ffd9b8be2aa28394ef142  07-high-dpi-typewriter.pdf
1585370a7897e0cbef3af440f4c107b5ee2a31d208fad794ad225d856b460628  08-nonsquare-dpi-scan.pdf
600c27f8dd2ef085a94d3500f6ccdb3b37f4b89aa8ffdf9a00a06a1367887e8b  09-french-diacritics.pdf
bd9060153923c1b716ddb2d3408d686145a9d896bfa6b7c8155f7a1f64776066  10-image-in-form-xobject.pdf
1cf372a2b66c3be729b853bb19a0361244feec0e39f31eeff548a00ffcd7ab70  11-multipage-mixed.pdf
210b845b62eab7118bc1e6076a3235a37eb947218254198c6102d06422f35037  12-jbig2-monochrome-scan.pdf
fc38c1d68a565e1ef56f43de99dbe2c4effed643dfa0e8d3b9d345b33010ed8f  13-scanned-line-item-invoice.pdf
010d1f502d442271c8cee72cfd9f5c5e1e3ab800aba7300a5354440c22d64cee  14-stained-energy-invoice.pdf
29aec93511c025d18afe6bd74d39c2657f34694e6efa4a03c0727e48978f4abe  15-mixed-procurement-packet.pdf
86c26db043a60fa04b15882ac7674d222ef75176af9de747635bce78e46f96e3  16-scanned-form-with-table.pdf
```

Source-image SHA-256 values for files 17-20:

```text
852e08ae5c6c8b79713e6ca1894415cef785e6c47bafbffd22804d1870a24ecc  a. Receipt.png
01f14c656aeeda50afc6ebf51bf8bc522f2ad35041509540a0a84347a5697db6  b. Enrichments.jpg
391c664245fca1e90b87a7ef971ec212f5df2cd658cd3f43382debc3e5a68962  c. Table without borders.png
a695364379bdb98bbbc52e58e078029dd25ffd65056005304ec38acbf1128a6b  d. Table with borders.png
d30c2bd183f629af47a012e6b53e72345cccc1e16ba1b5c7270c6fb422834f3d  General Ledger A image 0250
fec8a4a64224bc7cc481df58aac7838edf47f49de303e6aee6ad8ec1b970c8d3  General Ledger A image 0600
```

The project-authored fixture hashes are recorded separately because those
files are committed:

```text
8c5daf1d005ab540358b5d886786156d4c1da1beec8ceb8c50ff10c3aeabe797  21-period-header-footnote.pdf
a85d70d27640686d2360ac8122449412da08050e8b46f2630b69b3a7db6a25d2  Wrapping Number Modifiers.pdf
9b7f782526c74a044882f3739ac1a323156f266992c271d5bdef563791215a0e  invoice-0-4 (1).pdf
80b1419e99e3458664013e159e454adfaf73c662cb83996a2422e89b565c6ce3  scanned pdf table.pdf
```

## `supported/`

One file per format the importer accepts, so a regression in the conversion path
shows up as a specific format failing rather than as a vague import bug. Covers
office documents (`.docx`, `.xlsx`, `.xls`, `.pptx`, `.ppsx`), OpenDocument
(`.odt`, `.ods`, `.odp`), raster and vector images (`.png`, `.jpg`, `.bmp`,
`.dib`, `.gif`, `.webp`, `.tiff`, `.ico`, `.svg`), mail (`.eml`, `.mht`), ebooks
(`.epub`, `.fb2`, `.cbz`), text and data (`.txt`, `.md`, `.csv`, `.log`,
`.rtf`, `.html`), and a PDF that is already a PDF. `accents-bom.txt` carries a
byte-order mark and non-ASCII text; `scan-3page.tiff` is multi-page;
`transparent-badge.png` has an alpha channel.

## `unsupported/`

Formats the importer must refuse cleanly. Every file is a few hundred bytes and
its contents do not matter — what is being tested is the refusal and its message,
not the parse. An extensionless `LICENSE` is included because a file with no
extension is its own case.

## `folder-drop/`

A small nested tree for folder import: mixed file types at the top level, a `Q1`
subfolder one level down, and a spreadsheet, a scanned PNG and a `.docx` beside
the PDFs. Exercises recursion, ordering, and mixed-type handling in one drop.
