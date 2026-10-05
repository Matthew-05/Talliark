# Engines

One folder per algorithm package in `src/python/engines/`. Each folder is the
implementation record for that engine: what it decides, on what evidence, with
which constants, and where it is known to be wrong. Every engine doc follows the
same eight sections, so a reader who knows one knows where to look in all of
them.

| Engine | Source | Contract | What it decides |
| --- | --- | --- | --- |
| [values](values/README.md) | `engines/values/` | `document-values-v1` | Which spans on a page are values, references or noise, and what category each falls in |
| [table](table/README.md) | `engines/table/` | `table-structure-v1` | Where tables are and what their grid is |
| [financial](financial/README.md) | `engines/financial/` | `financial-structure-v1` | The apparatus a filing indexes itself by — headings, items, notes |
| [financial-table](financial-table/README.md) | `engines/financial_table/` | private analysis types | How general geometry and aligned values become statement-oriented blocks during a Reconcile scan |
| [reconcile](reconcile/README.md) | `engines/reconcile/` | `reconcile-v1`, `reconcile-review-v1` | Proposes printed totals and evaluates explicitly reviewed equations; acceptance and numerical agreement are separate |

The single-module engines beside the packages — `ocr_engine.py`,
`geometry_engine.py`, `conversion_engine.py`, `spreadsheet_engine.py`,
`text_lines.py`, `binary_codec.py`, `pdf_security.py`, `table_cell_engine.py`,
`table_date_engine.py` — have no folder yet. Give one a folder when its
behaviour stops fitting in its own docstring.

## Adding an engine

Copy the section skeleton from any existing `README.md` here, fill in the header
table, and add a row above. Keep the eight sections and their order even when a
section is empty; an empty **Tuning and thresholds** is itself information.
