/** In-memory detected-table suggestion visibility, scoped to one PDF id. */
export class TableSuggestionVisibility {
  private readonly _enabledPdfIds = new Set<string>();

  isEnabled(pdfId: string | null | undefined): boolean {
    return Boolean(pdfId && this._enabledPdfIds.has(pdfId));
  }

  setEnabled(pdfId: string, enabled: boolean): void {
    if (!pdfId) return;
    if (enabled) this._enabledPdfIds.add(pdfId);
    else this._enabledPdfIds.delete(pdfId);
  }
}
