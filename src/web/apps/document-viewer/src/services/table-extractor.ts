import type { CharacterEntry } from "./text-content-cache.js";
import type { NormalizedRect, TableGridData } from "../types/index.js";
import type { DetectedTable, TableRow } from "@talliark/shared";
import { normalizeExtractedZeroPlaceholder } from "@talliark/shared";

const MIN_CHAR_OVERLAP = 0.3;
const MIN_BOUNDARY_DISTANCE = 0.005;

function overlapsRect(entry: CharacterEntry, rect: NormalizedRect): boolean {
  const left = Math.max(entry.normLeft, rect.x);
  const top = Math.max(entry.normTop, rect.y);
  const right = Math.min(entry.normRight, rect.x + rect.width);
  const bottom = Math.min(entry.normBottom, rect.y + rect.height);
  if (left >= right || top >= bottom) return false;

  const area = (entry.normRight - entry.normLeft) * (entry.normBottom - entry.normTop);
  return area > 0 && ((right - left) * (bottom - top)) / area >= MIN_CHAR_OVERLAP;
}

function normalizeBoundaries(values: number[]): number[] {
  return values
    .filter((value) => Number.isFinite(value) && value > MIN_BOUNDARY_DISTANCE && value < 1 - MIN_BOUNDARY_DISTANCE)
    .sort((a, b) => a - b)
    .filter((value, index, all) => index === 0 || value - all[index - 1]! >= MIN_BOUNDARY_DISTANCE);
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const ordered = [...values].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 === 0
    ? (ordered[middle - 1]! + ordered[middle]!) / 2
    : ordered[middle]!;
}

interface VisualRow {
  entries: CharacterEntry[];
  top: number;
  bottom: number;
  center: number;
  medianHeight: number;
}

function summarizeRow(entries: CharacterEntry[]): VisualRow {
  return {
    entries,
    top: Math.min(...entries.map((entry) => entry.normTop)),
    bottom: Math.max(...entries.map((entry) => entry.normBottom)),
    center: median(entries.map((entry) => (entry.normTop + entry.normBottom) / 2)),
    medianHeight: median(entries.map((entry) => entry.normBottom - entry.normTop)),
  };
}

/**
 * Overlap has to dominate. A sparse band (for example a period header sitting over the
 * first data line) overlaps the line below it by about half a glyph height, which the
 * previous 0.4-or-close-centers rule merged away. Fragments of one visual row overlap
 * almost completely instead.
 *
 * Mirrors `_merge_same_baseline` in python/engines/table/layout.py — keep the two in step, or
 * the extracted grid stops agreeing with the detector's suggested rows.
 */
function shouldMergeRows(first: VisualRow, second: VisualRow): boolean {
  const overlap = Math.max(0, Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top));
  const smallerHeight = Math.min(first.bottom - first.top, second.bottom - second.top);
  if (smallerHeight > 0) return overlap / smallerHeight >= 0.7;
  const centerTolerance = Math.max(first.medianHeight, second.medianHeight) * 0.25;
  return Math.abs(first.center - second.center) <= centerTolerance;
}

/**
 * Builds actual visual rows rather than trusting source lineIndex one-for-one. OCR and
 * native PDF text can contain whitespace-only lines or split one visual row across source
 * lines; both previously generated empty Excel rows.
 */
function buildVisualRows(entries: CharacterEntry[]): VisualRow[] {
  const bySourceLine = new Map<number, CharacterEntry[]>();
  for (const entry of entries) {
    if (entry.char.trim().length === 0) continue;
    const line = bySourceLine.get(entry.lineIndex) ?? [];
    line.push(entry);
    bySourceLine.set(entry.lineIndex, line);
  }

  const sourceRows = Array.from(bySourceLine.values())
    .filter((line) => line.length > 0)
    .map((line) => summarizeRow(line))
    .sort((a, b) => a.center - b.center || a.top - b.top);

  const rows: VisualRow[] = [];
  for (const sourceRow of sourceRows) {
    const previous = rows[rows.length - 1];
    if (previous && shouldMergeRows(previous, sourceRow)) {
      rows[rows.length - 1] = summarizeRow(previous.entries.concat(sourceRow.entries));
    } else {
      rows.push(sourceRow);
    }
  }
  return rows;
}

function detectRowBoundaries(rows: VisualRow[], rect: NormalizedRect): number[] {
  const boundaries: number[] = [];
  for (let index = 1; index < rows.length; index++) {
    const previous = rows[index - 1]!;
    const current = rows[index]!;
    // Centers guarantee every detected text row lands in a non-empty output band even
    // when glyph boxes overlap vertically.
    const pagePosition = (previous.center + current.center) / 2;
    boundaries.push((pagePosition - rect.y) / rect.height);
  }
  return normalizeBoundaries(boundaries);
}

interface HorizontalGap {
  left: number;
  right: number;
  width: number;
}

function findRowGaps(row: VisualRow, minimumWidth: number): HorizontalGap[] {
  const entries = [...row.entries].sort((a, b) => a.normLeft - b.normLeft);
  const gaps: HorizontalGap[] = [];
  let occupiedRight = entries[0]?.normRight ?? 0;

  for (let index = 1; index < entries.length; index++) {
    const entry = entries[index]!;
    const width = entry.normLeft - occupiedRight;
    if (width >= minimumWidth) {
      gaps.push({ left: occupiedRight, right: entry.normLeft, width });
    }
    occupiedRight = Math.max(occupiedRight, entry.normRight);
  }
  return gaps;
}

interface GutterSample {
  position: number;
  support: number;
  clearance: number;
}

/**
 * Finds vertical whitespace corridors shared by several visual rows. Unlike midpoint
 * clustering, this still identifies a column when the left-hand labels have very different
 * lengths or the right-hand values are aligned to a common edge.
 */
function detectColumnBoundaries(rows: VisualRow[], rect: NormalizedRect): number[] {
  const visibleChars = rows.flatMap((row) => row.entries);
  const medianWidth = median(visibleChars.map((entry) => entry.normRight - entry.normLeft));
  if (medianWidth <= 0 || rect.width <= 0 || rows.length === 0) return [];

  const minimumGap = medianWidth * (rows.length === 1 ? 4 : 1.5);
  const gapsByRow = rows.map((row) => findRowGaps(row, minimumGap));
  const requiredSupport = rows.length === 1 ? 1 : Math.max(2, Math.ceil(rows.length * 0.45));
  const sampleCount = 800;
  const samples: GutterSample[] = [];

  for (let index = 1; index < sampleCount; index++) {
    const relativePosition = index / sampleCount;
    const pagePosition = rect.x + relativePosition * rect.width;
    let support = 0;
    let clearance = 0;

    for (const gaps of gapsByRow) {
      const gap = gaps.find((candidate) => pagePosition >= candidate.left && pagePosition <= candidate.right);
      if (!gap) continue;
      support++;
      clearance += Math.min(pagePosition - gap.left, gap.right - pagePosition);
    }

    if (support >= requiredSupport) {
      samples.push({ position: relativePosition, support, clearance });
    }
  }

  const regions: GutterSample[][] = [];
  for (const sample of samples) {
    const region = regions[regions.length - 1];
    if (!region || sample.position - region[region.length - 1]!.position > 1.5 / sampleCount) {
      regions.push([sample]);
    } else {
      region.push(sample);
    }
  }

  const boundaries = regions.map((region) => {
    const maxSupport = Math.max(...region.map((sample) => sample.support));
    const strongest = region.filter((sample) => sample.support === maxSupport);
    const maxClearance = Math.max(...strongest.map((sample) => sample.clearance));
    const safest = strongest.filter((sample) => Math.abs(sample.clearance - maxClearance) < 1e-9);
    return safest.reduce((sum, sample) => sum + sample.position, 0) / safest.length;
  });

  return normalizeBoundaries(boundaries);
}

function findBand(position: number, boundaries: number[]): number {
  for (let index = 0; index < boundaries.length; index++) {
    if (position < boundaries[index]!) return index;
  }
  return boundaries.length;
}

function joinCellEntries(entries: CharacterEntry[]): string {
  if (entries.length === 0) return "";
  entries.sort((a, b) => a.lineIndex - b.lineIndex || a.normLeft - b.normLeft);
  const spacesPrecomputed = entries[0]?.spacesPrecomputed === true;
  let result = "";
  let previous: CharacterEntry | null = null;

  for (const entry of entries) {
    if (previous && entry.lineIndex !== previous.lineIndex) {
      result += " ";
    } else if (!spacesPrecomputed && previous && entry.itemIndex !== previous.itemIndex) {
      const charWidth = previous.normRight - previous.normLeft;
      if (entry.normLeft - previous.normRight > charWidth) result += " ";
    }
    result += entry.char;
    previous = entry;
  }
  return normalizeExtractedZeroPlaceholder(result.trim());
}

export function extractTableCells(
  entries: CharacterEntry[] | null,
  rect: NormalizedRect,
  columnBoundaries: number[],
  rowBoundaries: number[],
): string[][] {
  const included = (entries ?? []).filter((entry) => overlapsRect(entry, rect));
  return extractIncludedTableCells(included, rect, columnBoundaries, rowBoundaries);
}

function extractIncludedTableCells(
  included: CharacterEntry[],
  rect: NormalizedRect,
  columnBoundaries: number[],
  rowBoundaries: number[],
): string[][] {
  const columns = normalizeBoundaries(columnBoundaries);
  const rows = normalizeBoundaries(rowBoundaries);
  const buckets: CharacterEntry[][][] = Array.from(
    { length: rows.length + 1 },
    () => Array.from({ length: columns.length + 1 }, () => []),
  );

  for (const entry of included) {
    const x = ((entry.normLeft + entry.normRight) / 2 - rect.x) / rect.width;
    const y = ((entry.normTop + entry.normBottom) / 2 - rect.y) / rect.height;
    buckets[findBand(y, rows)]![findBand(x, columns)]!.push(entry);
  }

  return buckets.map((row) => row.map((cell) => joinCellEntries(cell)));
}

export function detectTableGrid(
  entries: CharacterEntry[] | null,
  rect: NormalizedRect,
  detectedTable?: DetectedTable | null,
): TableGridData {
  if (detectedTable) return gridFromTableStructure(detectedTable, rect, entries);
  const included = (entries ?? []).filter((entry) => overlapsRect(entry, rect));
  const visualRows = buildVisualRows(included);
  const columnBoundaries = detectColumnBoundaries(visualRows, rect);
  const rowBoundaries = detectRowBoundaries(visualRows, rect);
  return {
    columnBoundaries,
    rowBoundaries,
    cells: extractIncludedTableCells(included, rect, columnBoundaries, rowBoundaries),
  };
}

function relativeX(pageX: number, rect: NormalizedRect): number {
  return (pageX - rect.x) / rect.width;
}

function relativeY(pageY: number, rect: NormalizedRect): number {
  return (pageY - rect.y) / rect.height;
}

function sourceLineBoundaries(rows: TableRow[], rect: NormalizedRect): number[] {
  const lines = rows.flatMap((row) => row.textLines).sort((a, b) => a.y0 - b.y0);
  const boundaries: number[] = [];
  for (let index = 1; index < lines.length; index++) {
    const pagePosition = (lines[index - 1]!.y1 + lines[index]!.y0) / 2;
    boundaries.push(relativeY(pagePosition, rect));
  }
  return normalizeBoundaries(boundaries);
}

/**
 * Returns the row bands the user sees and edits.
 *
 * The detector retains each physical header line as a row so its evidence is not
 * lost, while the table editor presents those lines as one multiline header row.
 * Consumers that draw the editable grid must use these groups rather than the raw
 * detector rows or they will expose boundaries that disappear during creation.
 */
export function groupDisplayedTableRows(table: DetectedTable): TableRow[][] {
  const headerRowCount = Math.min(Math.max(table.header?.rowCount ?? 0, 0), table.rows.length);
  const displayedGroups: TableRow[][] = [];
  if (headerRowCount > 0) displayedGroups.push(table.rows.slice(0, headerRowCount));
  for (const row of table.rows.slice(headerRowCount)) displayedGroups.push([row]);
  return displayedGroups;
}

/** Converts absolute page table bands into the existing rectangle-relative grid shape. */
export function gridFromTableStructure(
  table: DetectedTable,
  rect: NormalizedRect,
  entries: CharacterEntry[] | null = null,
): TableGridData {
  const columnBoundaries = normalizeBoundaries(
    table.columns.slice(0, -1).map((column) => relativeX(column.x1, rect)),
  );
  const headerRowCount = Math.min(table.header?.rowCount ?? 0, table.rows.length);
  const displayedGroups = groupDisplayedTableRows(table);

  const rowBoundaries = normalizeBoundaries(
    displayedGroups.slice(0, -1).map((group) => relativeY(group[group.length - 1]!.y1, rect)),
  );
  const textLineBoundaries = displayedGroups.map((group) => sourceLineBoundaries(group, rect));
  const included = (entries ?? []).filter((entry) => overlapsRect(entry, rect));
  return {
    columnBoundaries,
    rowBoundaries,
    cells: extractIncludedTableCells(included, rect, columnBoundaries, rowBoundaries),
    ...(headerRowCount > 0 ? { headerRowCount } : {}),
    ...(textLineBoundaries.some((boundaries) => boundaries.length > 0)
      ? { textLineBoundaries }
      : {}),
  };
}

/**
 * Detects only the row layout for a copied table, preserving the source columns and
 * extracting its cells in the same pass. Copying does not need target-page column
 * detection because its columns are intentionally fixed to the source rectangle.
 */
export function detectCopiedTable(
  entries: CharacterEntry[] | null,
  rect: NormalizedRect,
  sourceColumnBoundaries: number[],
): TableGridData {
  const included = (entries ?? []).filter((entry) => overlapsRect(entry, rect));
  const rowBoundaries = detectRowBoundaries(buildVisualRows(included), rect);
  const columnBoundaries = normalizeBoundaries(sourceColumnBoundaries);
  return {
    columnBoundaries,
    rowBoundaries,
    cells: extractIncludedTableCells(included, rect, columnBoundaries, rowBoundaries),
  };
}

export function withExtractedTableCells(
  entries: CharacterEntry[] | null,
  rect: NormalizedRect,
  table: TableGridData,
): TableGridData {
  const columnBoundaries = normalizeBoundaries(table.columnBoundaries);
  const rowBoundaries = normalizeBoundaries(table.rowBoundaries);
  const included = (entries ?? []).filter((entry) => overlapsRect(entry, rect));
  return {
    columnBoundaries,
    rowBoundaries,
    cells: extractIncludedTableCells(included, rect, columnBoundaries, rowBoundaries),
    ...(table.headerRowCount ? { headerRowCount: table.headerRowCount } : {}),
    ...(table.textLineBoundaries
      ? { textLineBoundaries: table.textLineBoundaries.map((row) => [...row]) }
      : {}),
  };
}
