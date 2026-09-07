/**
 * What the host tells the Reconcile window about the workbook.
 *
 * These mirror the Reconcile payloads in contracts/webview-messages-v1.json,
 * which is the source of the shapes. Nothing here is derived: staleness is
 * decided host-side, from the detector versions and the geometry a stored
 * result was produced from, and the app renders the answer rather than
 * recomputing it.
 */

/**
 * What the workbook holds for one document.
 *
 * `none` is not a pass. A document that has never been scanned must be shown as
 * unscanned, because a window that renders it the same as a clean scan is
 * telling the reviewer something it does not know.
 */
export type Staleness = "current" | "stale" | "none";

/** The counts a stored reconcile-v1 model's summary carries, copied verbatim. */
export interface ReconcileSummary {
  readonly tablesExamined: number;
  readonly totalsNominated: number;
  readonly confirmed: number;
  readonly breaks: number;
  readonly unresolved: number;
}

export interface ReconcileDocument {
  readonly id: string;
  readonly name: string;
  readonly folderId?: string | undefined;
  readonly pageCount?: number | undefined;
  readonly staleness: Staleness;
  /** Presentation only — staleness is never decided by age. */
  readonly scannedAt?: string | undefined;
  /** Absent when staleness is `none`. */
  readonly summary?: ReconcileSummary | undefined;
}

export interface FolderEntry {
  readonly id: string;
  readonly name: string;
}

/** One document's scan, as the host reports it. */
export type ScanStatus = "queued" | "scanning" | "complete" | "cancelled" | "error";

/**
 * Progress within a scan.
 *
 * `stage` is the machine-readable fact and `message` is prose beside it; the
 * app renders the stage's own label and never parses the message.
 * `current`/`total` count within the stage only — they are not progress through
 * the document.
 */
export interface ScanProgress {
  readonly message?: string | undefined;
  readonly stage?: string | undefined;
  readonly current?: number | undefined;
  readonly total?: number | undefined;
  readonly unit?: string | undefined;
}

export interface Bounds {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export type ReconcileOutcome = "confirmed" | "break" | "unresolved";

export interface ReconcileCell {
  readonly id: string;
  readonly rowIndex: number;
  readonly columnIndex: number;
  readonly text: string;
  readonly bounds: Bounds;
  readonly normalizedValue?: string | undefined;
  /** Printed decimal places. What the run agreed on, and what a sum is set at. */
  readonly decimals?: number | undefined;
  /** A dash alone: an addend worth zero, wherever in a run it appears. */
  readonly dash?: boolean | undefined;
  readonly rowLabel?: string | undefined;
}

/**
 * What Reconcile made of a column's header.
 *
 * `table-structure-v1` owns which bands are header and what they say as
 * printed; the reading of it is Reconcile's, and this is that reading.
 */
export interface ReconcileHeaderLabel {
  readonly columnIndex: number;
  readonly text: string;
  readonly isTotalColumn: boolean;
  readonly isPeriodColumn: boolean;
  readonly period?: string | undefined;
}

/** Why a cell was nominated. Structure nominates; arithmetic confirms. */
export interface ReconcileSignal {
  readonly name: string;
  readonly evidence: string;
}

export interface ReconcileSumRun {
  readonly basis: "subtotals" | "leaves";
  readonly addendCellIds: string[];
  /** Cells whose printed numeric sign is reversed in this arithmetic run. */
  readonly negatedAddendCellIds: string[];
  readonly sum: string;
  readonly delta: string;
  readonly diagnosis?: {
    readonly kind: "transposition" | "single-glyph" | "sign" | "omitted-addend";
    readonly detail?: string | undefined;
    readonly cellId?: string | undefined;
  } | undefined;
}

export interface ReconcileTotal {
  readonly id: string;
  readonly cellId: string;
  readonly rowIndex: number;
  readonly columnIndex: number;
  readonly axis?: "vertical" | "cross" | undefined;
  readonly outcome: ReconcileOutcome;
  readonly signals?: ReconcileSignal[] | undefined;
  readonly resolution?: ReconcileSumRun | undefined;
  /**
   * The same total resolved against the leaf rows instead of the subtotals.
   * Never the tree — the subtotals are what the statement is asserting.
   */
  readonly leafResolution?: ReconcileSumRun | undefined;
  readonly unresolvedReason?: string | undefined;
}

export interface ReconcileTable {
  readonly id: string;
  readonly pageIndex: number;
  readonly bounds: Bounds;
  /** Value lattice alone, or a lattice block corroborated by table detection. */
  readonly provenance: "lattice" | "lattice+table" | "table";
  readonly columnCount: number;
  readonly rowCount: number;
  readonly headerLabels?: ReconcileHeaderLabel[] | undefined;
  readonly cells: ReconcileCell[];
  readonly totals: ReconcileTotal[];
}

export interface ReconcileFinding {
  readonly id: string;
  readonly kind:
    | "footing-break"
    | "footing-unresolved"
    | "cross-foot-break"
    | "ruling-disagreement";
  readonly sentence: string;
  readonly tableId: string;
  readonly totalId: string;
  readonly pageIndex?: number | undefined;
}

export interface ReconcileModel {
  readonly version: 1;
  readonly coordinateSpace: "normalized";
  readonly detectorVersion: string;
  readonly source: {
    readonly documentId: string;
    readonly pageCount: number;
    readonly scannedAt?: string | undefined;
  };
  readonly summary: ReconcileSummary;
  readonly tables: ReconcileTable[];
  readonly findings: ReconcileFinding[];
}
