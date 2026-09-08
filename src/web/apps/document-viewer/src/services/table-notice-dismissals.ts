const STORAGE_KEY = "talliark.document-viewer.table-notice-dismissals.v1";

type NoticeStorage = Pick<Storage, "getItem" | "setItem">;

/**
 * Persistent acknowledgement state for detected-table notices.
 *
 * A count is stored rather than a boolean so a later OCR result that discovers
 * additional tables can announce them without bringing the notice back merely
 * because the user linked or removed tables they had already seen.
 */
export class TableNoticeDismissals {
  private readonly _storage: NoticeStorage | null;
  private readonly _dismissedAt = new Map<string, number>();

  constructor(storage: NoticeStorage | null = TableNoticeDismissals.browserStorage()) {
    this._storage = storage;
    this._mergeStoredDismissals();
  }

  shouldShow(pdfId: string, remainingTables: number): boolean {
    this._mergeStoredDismissals();
    return remainingTables > (this._dismissedAt.get(pdfId) ?? 0);
  }

  dismiss(pdfId: string, remainingTables: number): void {
    if (!pdfId) return;

    // Another viewer window may have acknowledged a different document since
    // this instance was created. Merge before writing so its state is retained.
    this._mergeStoredDismissals();
    this._dismissedAt.set(
      pdfId,
      Math.max(this._dismissedAt.get(pdfId) ?? 0, remainingTables, 0),
    );
    this._persist();
  }

  private static browserStorage(): NoticeStorage | null {
    try {
      return globalThis.localStorage;
    } catch {
      return null;
    }
  }

  private _mergeStoredDismissals(): void {
    if (!this._storage) return;

    try {
      const stored = this._storage.getItem(STORAGE_KEY);
      if (!stored) return;
      const parsed: unknown = JSON.parse(stored);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return;

      for (const [pdfId, count] of Object.entries(parsed)) {
        if (pdfId && typeof count === "number" && Number.isFinite(count) && count >= 0) {
          this._dismissedAt.set(pdfId, Math.max(this._dismissedAt.get(pdfId) ?? 0, count));
        }
      }
    } catch {
      // Notification state is optional UI state. Restricted or corrupt browser
      // storage must never prevent the document viewer from opening.
    }
  }

  private _persist(): void {
    if (!this._storage) return;

    try {
      this._storage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(this._dismissedAt)));
    } catch {
      // Keep the in-memory state for this viewer session when storage is denied.
    }
  }
}
