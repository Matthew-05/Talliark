export interface TableStructure {
  version: 1;
  coordinateSpace: "normalized";
  /**
   * Which detector built this model.
   *
   * A structure is cached in the workbook, so it outlives the detector that
   * produced it. Carrying the version means a stale model can be recognized and
   * rebuilt rather than silently trusted. Absent in models written before the
   * field existed.
   */
  detectorVersion?: string;
  /** Detection stopped at its time budget; later pages were never examined. */
  truncated?: boolean;
  pages: PageTables[];
}

export interface PageTables {
  pageIndex: number;
  tables: DetectedTable[];
}

export interface TableBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface TableColumn {
  x0: number;
  x1: number;
}

export interface TableTextLine {
  y0: number;
  y1: number;
}

export interface TableRow {
  y0: number;
  y1: number;
  kind: "header" | "body";
  textLines: TableTextLine[];
  merged: boolean;
  mergeConfidence: number;
}

export interface TableHeader {
  rowCount: number;
  labels: string[];
  groups: TableHeaderGroup[];
}

export interface TableHeaderGroup {
  text: string;
  /** Zero-based inclusive final-column range governed by this label. */
  columnStart: number;
  columnEnd: number;
  /** Geometry of the printed label, which may sit above the table bounds. */
  bounds: TableBounds;
}

/**
 * What span of time the table's data covers.
 *
 * A statement dates each column; a stacked report labels the whole block once
 * and qualifies the dates separately. Those labels are not rows of the table and
 * are excluded from its bounds, so the period they carry is reported here.
 */
export interface TablePeriod {
  /** Period covering every column. Empty when it varies by column or row. */
  table: string;
  /** Period each column's data belongs to; empty where its label carries none. */
  columns: string[];
  /** Period each row's data belongs to; empty where its label carries none. */
  rows: string[];
  /** A phrase qualifying the dated columns, such as "Years ended". */
  qualifier: string;
  /**
   * Which way the periods run. A cell's period is its row's, else its column's,
   * else the table's.
   */
  axis: "columns" | "rows" | "both" | "none";
}

export interface DetectedTable {
  id: string;
  bounds: TableBounds;
  evidence: "ruled" | "whitespace" | "mixed";
  confidence: number;
  columns: TableColumn[];
  rows: TableRow[];
  header: TableHeader | null;
  period: TablePeriod | null;
  rulings: { vertical: number[]; horizontal: number[] };
}

// Coordinates are normalized, so a boundary may sit a rounding step outside the
// unit square or its parent box. Anything beyond this is malformed.
const EPSILON = 0.005;

const EVIDENCE = new Set(["ruled", "whitespace", "mixed"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isUnit(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value)
    && value >= -EPSILON && value <= 1 + EPSILON;
}

function parseBounds(value: unknown): TableBounds | null {
  if (!isRecord(value)) return null;
  const { x, y, width, height } = value;
  if (!isUnit(x) || !isUnit(y) || !isUnit(width) || !isUnit(height)) return null;
  if (width <= 0 || height <= 0) return null;
  if (x + width > 1 + EPSILON || y + height > 1 + EPSILON) return null;
  return { x, y, width, height };
}

function parseColumns(value: unknown, bounds: TableBounds): TableColumn[] | null {
  if (!Array.isArray(value) || value.length < 2) return null;
  const columns: TableColumn[] = [];
  let previous = bounds.x - EPSILON;
  for (const entry of value) {
    if (!isRecord(entry) || !isUnit(entry.x0) || !isUnit(entry.x1)) return null;
    const { x0, x1 } = entry as { x0: number; x1: number };
    // Columns must run left to right, not overlap, and stay inside the table.
    if (x1 <= x0 || x0 < previous - EPSILON || x1 > bounds.x + bounds.width + EPSILON) return null;
    previous = x1;
    columns.push({ x0, x1 });
  }
  return columns;
}

function parseTextLines(value: unknown): TableTextLine[] | null {
  if (!Array.isArray(value)) return null;
  const lines: TableTextLine[] = [];
  for (const entry of value) {
    if (!isRecord(entry) || !isUnit(entry.y0) || !isUnit(entry.y1)) return null;
    const { y0, y1 } = entry as { y0: number; y1: number };
    if (y1 < y0) return null;
    lines.push({ y0, y1 });
  }
  return lines;
}

function parseRows(value: unknown, bounds: TableBounds): TableRow[] | null {
  if (!Array.isArray(value) || value.length < 2) return null;
  const rows: TableRow[] = [];
  let previous = bounds.y - EPSILON;
  for (const entry of value) {
    if (!isRecord(entry) || !isUnit(entry.y0) || !isUnit(entry.y1)) return null;
    const { y0, y1 } = entry as { y0: number; y1: number };
    if (y1 <= y0 || y0 < previous - EPSILON || y1 > bounds.y + bounds.height + EPSILON) return null;
    if (entry.kind !== "header" && entry.kind !== "body") return null;
    if (typeof entry.merged !== "boolean" || !isUnit(entry.mergeConfidence)) return null;
    const textLines = parseTextLines(entry.textLines);
    if (textLines === null) return null;
    previous = y1;
    rows.push({
      y0,
      y1,
      kind: entry.kind,
      textLines,
      merged: entry.merged,
      mergeConfidence: entry.mergeConfidence,
    });
  }
  return rows;
}

function parseHeader(
  value: unknown,
  rowCount: number,
  columnCount: number,
): TableHeader | null | undefined {
  if (value === null || value === undefined) return null;
  if (!isRecord(value)) return undefined;
  const { rowCount: count, labels, groups } = value;
  if (typeof count !== "number" || !Number.isInteger(count) || count < 1 || count > rowCount) {
    return undefined;
  }
  if (!Array.isArray(labels) || labels.some((label) => typeof label !== "string")) return undefined;
  if (labels.length !== columnCount || !Array.isArray(groups)) return undefined;
  const parsedGroups: TableHeaderGroup[] = [];
  for (const group of groups) {
    if (!isRecord(group) || typeof group.text !== "string" || !group.text.trim()) return undefined;
    if (!Number.isInteger(group.columnStart) || !Number.isInteger(group.columnEnd)) return undefined;
    const columnStart = group.columnStart as number;
    const columnEnd = group.columnEnd as number;
    if (columnStart < 0 || columnEnd <= columnStart || columnEnd >= columnCount) return undefined;
    const bounds = parseBounds(group.bounds);
    if (bounds === null) return undefined;
    parsedGroups.push({ text: group.text, columnStart, columnEnd, bounds });
  }
  return { rowCount: count, labels: labels as string[], groups: parsedGroups };
}

const PERIOD_AXES = new Set(["columns", "rows", "both", "none"]);

function parsePeriod(
  value: unknown,
  columnCount: number,
  rowCount: number,
): TablePeriod | null | undefined {
  if (value === null || value === undefined) return null;
  if (!isRecord(value)) return undefined;
  const { table, columns, rows, qualifier, axis } = value;
  if (typeof table !== "string" || typeof qualifier !== "string") return undefined;
  if (typeof axis !== "string" || !PERIOD_AXES.has(axis)) return undefined;
  const strings = (input: unknown, length: number): string[] | null =>
    Array.isArray(input)
      && input.length === length
      && input.every((entry) => typeof entry === "string")
      ? (input as string[])
      : null;
  // One entry per column and per row, so a caller can index them alongside the
  // columns and rows arrays without bounds checks of its own.
  const byColumn = strings(columns, columnCount);
  const byRow = strings(rows, rowCount);
  if (byColumn === null || byRow === null) return undefined;
  return {
    table,
    columns: byColumn,
    rows: byRow,
    qualifier,
    axis: axis as TablePeriod["axis"],
  };
}

function parseRulings(value: unknown): { vertical: number[]; horizontal: number[] } {
  if (!isRecord(value)) return { vertical: [], horizontal: [] };
  const axis = (input: unknown): number[] =>
    Array.isArray(input) ? input.filter(isUnit) : [];
  return { vertical: axis(value.vertical), horizontal: axis(value.horizontal) };
}

function parseTable(value: unknown): DetectedTable | null {
  if (!isRecord(value)) return null;
  if (typeof value.id !== "string" || value.id.length === 0) return null;
  if (typeof value.evidence !== "string" || !EVIDENCE.has(value.evidence)) return null;
  if (!isUnit(value.confidence)) return null;
  const bounds = parseBounds(value.bounds);
  if (bounds === null) return null;
  const columns = parseColumns(value.columns, bounds);
  if (columns === null) return null;
  const rows = parseRows(value.rows, bounds);
  if (rows === null) return null;
  const header = parseHeader(value.header, rows.length, columns.length);
  if (header === undefined) return null;
  const period = parsePeriod(value.period, columns.length, rows.length);
  if (period === undefined) return null;
  return {
    id: value.id,
    bounds,
    evidence: value.evidence as DetectedTable["evidence"],
    confidence: value.confidence,
    columns,
    rows,
    header,
    period,
    rulings: parseRulings(value.rulings),
  };
}

/**
 * Validate a decoded payload instead of trusting it.
 *
 * The model is produced by a detector that evolves independently of this app and
 * arrives across a process boundary, so a cast would let a malformed or
 * out-of-order table reach the renderer and draw nonsense over the page. A table
 * that fails validation is dropped; the rest of the page still works. Only a
 * payload that is not a table model at all is rejected outright, which the cache
 * treats as "this PDF has no table structure".
 */
export function parseTableStructure(value: unknown): TableStructure {
  if (!isRecord(value) || value.version !== 1 || value.coordinateSpace !== "normalized") {
    throw new Error("Unsupported table structure payload");
  }
  if (!Array.isArray(value.pages)) throw new Error("Table structure has no pages");
  const pages: PageTables[] = [];
  for (const page of value.pages) {
    if (!isRecord(page)) continue;
    const { pageIndex, tables } = page;
    if (typeof pageIndex !== "number" || !Number.isInteger(pageIndex) || pageIndex < 0) continue;
    if (!Array.isArray(tables)) continue;
    const parsed: DetectedTable[] = [];
    for (const table of tables) {
      const detected = parseTable(table);
      if (detected !== null) parsed.push(detected);
    }
    pages.push({ pageIndex, tables: parsed });
  }
  return {
    version: 1,
    coordinateSpace: "normalized",
    ...(typeof value.detectorVersion === "string" ? { detectorVersion: value.detectorVersion } : {}),
    ...(value.truncated === true ? { truncated: true } : {}),
    pages,
  };
}

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

export async function decodeTableStructure(base64: string): Promise<TableStructure> {
  const compressed = base64ToBytes(base64);
  const stream = new Blob([compressed.buffer as ArrayBuffer])
    .stream()
    .pipeThrough(new DecompressionStream("gzip"));
  return parseTableStructure(JSON.parse(await new Response(stream).text()));
}
