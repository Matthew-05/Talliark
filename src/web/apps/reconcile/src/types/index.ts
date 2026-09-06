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
