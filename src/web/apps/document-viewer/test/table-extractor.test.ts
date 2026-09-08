import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";
import type { DetectedTable } from "@talliark/shared";

const tableExtractorUrl = new URL("../src/services/table-extractor.ts", import.meta.url).href;
const zeroPlaceholderUrl = new URL(
  "../../../packages/shared/src/zero-placeholder.ts",
  import.meta.url,
).href;

// The workspace link is not resolvable from a bare `node --test` run, and pulling in the whole
// shared barrel would drag pdfjs along with it. Only the zero-placeholder helper is needed.
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@talliark/shared" && context.parentURL === tableExtractorUrl) {
      return { url: zeroPlaceholderUrl, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const { detectCopiedTable, detectTableGrid, gridFromTableStructure, groupDisplayedTableRows } =
  await import(tableExtractorUrl) as typeof import("../src/services/table-extractor.ts");

interface TestCharacter {
  char: string;
  normLeft: number;
  normTop: number;
  normRight: number;
  normBottom: number;
  lineIndex: number;
  itemIndex: number;
  spacesPrecomputed: boolean;
}

let itemIndex = 0;

function text(
  value: string,
  left: number,
  top: number,
  lineIndex: number,
  charWidth = 0.015,
): TestCharacter[] {
  return [...value].map((char, index) => ({
    char,
    normLeft: left + index * charWidth,
    normTop: top,
    normRight: left + (index + 1) * charWidth,
    normBottom: top + 0.025,
    lineIndex,
    itemIndex: itemIndex++,
    spacesPrecomputed: true,
  }));
}

const rect = { x: 0, y: 0, width: 1, height: 1 };

test("ignores whitespace-only source lines and anchors the first visible row at cells[0]", () => {
  const entries = [
    ...text("   ", 0.1, 0.05, 0),
    ...text("Header", 0.1, 0.2, 1),
    ...text("100", 0.65, 0.2, 1),
    ...text("Detail", 0.1, 0.4, 2),
    ...text("200", 0.65, 0.4, 2),
  ];

  const grid = detectTableGrid(entries, rect);

  assert.equal(grid.rowBoundaries.length, 1);
  assert.deepEqual(grid.cells, [["Header", "100"], ["Detail", "200"]]);
});

test("merges source line IDs that occupy the same visual row", () => {
  const entries = [
    ...text("Alpha", 0.1, 0.2, 1),
    ...text("10", 0.65, 0.201, 2),
    ...text("Beta", 0.1, 0.4, 3),
    ...text("20", 0.65, 0.4, 3),
  ];

  const grid = detectTableGrid(entries, rect);

  assert.equal(grid.rowBoundaries.length, 1);
  assert.deepEqual(grid.cells, [["Alpha", "10"], ["Beta", "20"]]);
});

test("finds shared column gutters when labels and values have uneven widths", () => {
  const entries = [
    ...text("Long description", 0.08, 0.15, 1),
    ...text("Q1", 0.48, 0.15, 1),
    ...text("100", 0.78, 0.15, 1),
    ...text("Short", 0.08, 0.35, 2),
    ...text("Quarter", 0.48, 0.35, 2),
    ...text("200", 0.78, 0.35, 2),
    ...text("Medium label", 0.08, 0.55, 3),
    ...text("Q3", 0.48, 0.55, 3),
    ...text("300", 0.78, 0.55, 3),
  ];

  const grid = detectTableGrid(entries, rect);

  assert.equal(grid.columnBoundaries.length, 2);
  assert.deepEqual(grid.cells, [
    ["Long description", "Q1", "100"],
    ["Short", "Quarter", "200"],
    ["Medium label", "Q3", "300"],
  ]);
});

test("re-detection resets manual columns and rows to best guesses", () => {
  const entries = [
    ...text("Alpha", 0.08, 0.15, 1),
    ...text("10", 0.65, 0.15, 1),
    ...text("Beta", 0.08, 0.45, 2),
    ...text("20", 0.65, 0.45, 2),
  ];
  const manuallyEdited = {
    columnBoundaries: [0.2, 0.4, 0.8],
    rowBoundaries: [0.38, 0.72],
  };

  const grid = detectTableGrid(entries, rect);

  assert.equal(grid.columnBoundaries.length, 1);
  assert.notDeepEqual(grid.columnBoundaries, manuallyEdited.columnBoundaries);
  assert.equal(grid.rowBoundaries.length, 1);
  assert.notDeepEqual(grid.rowBoundaries, manuallyEdited.rowBoundaries);
  assert.deepEqual(grid.cells, [
    ["Alpha", "10"],
    ["Beta", "20"],
  ]);
});

test("copied tables preserve source columns while detecting target rows", () => {
  const entries = [
    ...text("Alpha", 0.08, 0.15, 1),
    ...text("10", 0.65, 0.15, 1),
    ...text("Beta", 0.08, 0.45, 2),
    ...text("20", 0.65, 0.45, 2),
  ];
  const sourceColumns = [0.4, 0.8];

  const grid = detectCopiedTable(entries, rect, sourceColumns);

  assert.deepEqual(grid.columnBoundaries, sourceColumns);
  assert.equal(grid.rowBoundaries.length, 1);
  assert.deepEqual(grid.cells, [
    ["Alpha", "10", ""],
    ["Beta", "20", ""],
  ]);
});

test("keeps a sparse period band off the data line below it", () => {
  // Mirrors test_sparse_header_is_not_coalesced_with_lower_data_line in
  // python/tests/test_table_structure.py. The two merge rules must agree, or the
  // extracted grid stops matching the detector's suggested rows.
  const entries = [
    ...text("2025", 0.6, 0.1, 0),
    ...text("2024", 0.8, 0.1, 0),
    ...text("Income taxes payable", 0.1, 0.112, 1),
    ...text("13,016", 0.62, 0.112, 1),
    ...text("26,601", 0.82, 0.112, 1),
  ];

  const grid = detectTableGrid(entries, rect);

  assert.equal(grid.cells.length, 2);
  assert.ok(grid.cells[0]!.join("").includes("2025"));
  assert.ok(grid.cells[1]!.join("").includes("Income taxes payable"));
});

test("uses detected table bands and collapses a multiline header", () => {
  const table: DetectedTable = {
    id: "page-0-table-0",
    bounds: { x: 0.1, y: 0.1, width: 0.8, height: 0.7 },
    evidence: "ruled",
    confidence: 0.95,
    columns: [{ x0: 0.1, x1: 0.5 }, { x0: 0.5, x1: 0.9 }],
    rows: [
      { y0: 0.1, y1: 0.2, kind: "header", textLines: [{ y0: 0.12, y1: 0.16 }], merged: false, mergeConfidence: 1 },
      { y0: 0.2, y1: 0.3, kind: "header", textLines: [{ y0: 0.22, y1: 0.26 }], merged: false, mergeConfidence: 1 },
      { y0: 0.3, y1: 0.5, kind: "body", textLines: [{ y0: 0.35, y1: 0.39 }], merged: false, mergeConfidence: 1 },
      { y0: 0.5, y1: 0.8, kind: "body", textLines: [{ y0: 0.6, y1: 0.64 }], merged: false, mergeConfidence: 1 },
    ],
    header: { rowCount: 2, labels: ["Account name", "Current year"], groups: [] },
    rulings: { vertical: [0.1, 0.5, 0.9], horizontal: [0.1, 0.2, 0.3, 0.5, 0.8] },
  };

  const grid = gridFromTableStructure(table, table.bounds);
  const displayedRows = groupDisplayedTableRows(table);

  assert.deepEqual(grid.columnBoundaries, [0.5]);
  assert.ok(Math.abs(grid.rowBoundaries[0]! - 2 / 7) < 1e-12);
  assert.ok(Math.abs(grid.rowBoundaries[1]! - 4 / 7) < 1e-12);
  assert.equal(grid.headerRowCount, 2);
  assert.ok(grid.textLineBoundaries?.[0]?.length === 1);
  assert.equal(displayedRows.length, 3);
  assert.deepEqual(displayedRows.map((group) => group.map((row) => row.y1)), [
    [0.2, 0.3],
    [0.5],
    [0.8],
  ]);
});

test("a missing model retains the text heuristic fallback", () => {
  const entries = [
    ...text("Alpha", 0.1, 0.2, 1),
    ...text("10", 0.7, 0.2, 1),
    ...text("Beta", 0.1, 0.5, 2),
    ...text("20", 0.7, 0.5, 2),
  ];
  assert.deepEqual(detectTableGrid(entries, rect, null), detectTableGrid(entries, rect));
});

test("normalizes standalone em dash cells to zero like a single rectangle", () => {
  const entries = [
    ...text("Alpha", 0.08, 0.15, 1),
    ...text("100", 0.5, 0.15, 1),
    ...text("$ —", 0.78, 0.15, 1),
    ...text("Beta", 0.08, 0.35, 2),
    ...text("—", 0.5, 0.35, 2),
    ...text("Net — total", 0.78, 0.35, 2),
  ];

  const grid = detectTableGrid(entries, rect);

  assert.deepEqual(grid.cells, [
    ["Alpha", "100", "$ 0"],
    ["Beta", "0", "Net — total"],
  ]);
});
