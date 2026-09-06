"""Grid x text geometry x values -> the cell layer.

Reconcile's first job is the one nothing else in the system does: connect a
value to a cell. `document-values-v1` carries bounds and no membership -- no
table id, no row, no column, because the values engine's test for a figure
standing in a column is a geometric proxy that deliberately works whether or not
a table was detected around it. And `table-structure-v1` carries grid geometry
and header labels but no cell text. Neither can answer "what is in row 4 of
column 2", so this module builds the join.

The join is derived per scan, over the grid the scan itself re-detected, and is
never published back into `document-values-v1`. That is what keeps the
membership a description of the grid the analysis actually used, which the cache
build's grid may no longer be.

R-1 fills this in.
"""
from __future__ import annotations


# --- R-1 -------------------------------------------------------------------
# build_cells(table, page_geometry, values_page) -> list[dict]
#   * intersect the table's column extents and row bands with text geometry for
#     cell text;
#   * intersect the same grid with document-values-v1 spans for the cell value,
#     keyed by spanId so a finding can name it;
#   * take the row label from the leading cell and hand it to labels.normalize.
#
# A dash alone in a cell is an addend worth zero and is published with
# dash: true. A cell with no text at all is NOT published: its absence is the
# fact a run search reads, because a blank ends a candidate rather than
# contributing to it.
