import type { DetectedTable } from "@talliark/shared";
import type { TableStructureCache } from "../../services/table-structure-cache.js";
import type { PdfViewer } from "./pdf-viewer.js";
import { ensureOverlayLayer } from "./page-renderer.js";
import { groupDisplayedTableRows } from "../../services/table-extractor.js";

const OVERLAY_CLASS = "table-suggestions";

export class TableSuggestionOverlay {
  private _visible = false;
  private _linked: ReadonlySet<string> = new Set();
  private readonly _callbacks: Array<(pdfId: string, pageIndex: number, table: DetectedTable) => void> = [];

  constructor(
    private readonly _viewer: PdfViewer,
    private readonly _cache: TableStructureCache,
  ) {
    this._viewer.onDocumentChanged(() => this.refresh());
  }

  /**
   * Tables that already have a link rectangle on them.
   *
   * A suggestion whose table has been linked has nothing left to suggest, so it
   * stops being drawn. Membership is recomputed from the current rectangles
   * rather than remembered, so deleting the link brings the suggestion back.
   */
  setLinkedTableIds(ids: ReadonlySet<string>): void {
    this._linked = ids;
    this.refresh();
  }

  onSuggestionClicked(callback: (pdfId: string, pageIndex: number, table: DetectedTable) => void): void {
    this._callbacks.push(callback);
  }

  show(): void {
    if (this._visible) return;
    this._visible = true;
    this._renderAll();
  }

  hide(): void {
    if (!this._visible) return;
    this._visible = false;
    this._clearAll();
  }

  refresh(): void {
    if (this._visible) this._renderAll();
    else this._clearAll();
  }

  private _renderAll(): void {
    const pdfId = this._viewer.getActivePdfId();
    if (!pdfId) return;
    for (const { pageNumber, wrapper } of this._viewer.getPageLayout()) {
      this._renderPage(wrapper, pdfId, pageNumber - 1);
    }
  }

  private _renderPage(wrapper: HTMLDivElement, pdfId: string, pageIndex: number): void {
    this._clearPage(wrapper);
    const tables = this._cache
      .tablesOnPage(pdfId, pageIndex)
      .filter((table) => !this._linked.has(table.id));
    if (tables.length === 0) return;
    const container = document.createElement("div");
    container.className = OVERLAY_CLASS;
    for (const table of tables) container.appendChild(this._createSuggestion(pdfId, pageIndex, table));
    ensureOverlayLayer(wrapper).appendChild(container);
  }

  /**
   * A suggested region is drawn, not clicked.
   *
   * The region spans the whole table, so making it interactive stole every drag
   * that started inside a table — exactly where someone wants to draw one by
   * hand. The region is inert; a small button in its corner is what creates the
   * link, and it is the only part in the tab order.
   */
  private _createSuggestion(pdfId: string, pageIndex: number, table: DetectedTable): HTMLDivElement {
    const bounds = table.bounds;
    const element = document.createElement("div");
    element.className = "table-suggestions__region";
    element.style.left = `${bounds.x * 100}%`;
    element.style.top = `${bounds.y * 100}%`;
    element.style.width = `${bounds.width * 100}%`;
    element.style.height = `${bounds.height * 100}%`;

    for (const column of table.columns.slice(0, -1)) {
      element.appendChild(this._line("column", (column.x1 - bounds.x) / bounds.width));
    }
    for (const group of groupDisplayedTableRows(table).slice(0, -1)) {
      const groupBottom = group[group.length - 1]!.y1;
      element.appendChild(this._line("row", (groupBottom - bounds.y) / bounds.height));
    }
    if (table.header) {
      const headerBottom = table.rows[Math.min(table.header.rowCount, table.rows.length) - 1]?.y1;
      if (headerBottom !== undefined) {
        const header = document.createElement("span");
        header.className = "table-suggestions__header";
        header.style.height = `${((headerBottom - bounds.y) / bounds.height) * 100}%`;
        element.appendChild(header);
      }
    }

    const action = document.createElement("button");
    action.type = "button";
    action.className = "table-suggestions__action";
    action.textContent = "+";
    action.title = `Create table link (${Math.round(table.confidence * 100)}% confidence)`;
    action.setAttribute("aria-label", "Create link from detected table");
    action.addEventListener("pointerdown", (event) => event.stopPropagation());
    action.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      for (const callback of this._callbacks) callback(pdfId, pageIndex, table);
    });
    element.appendChild(action);
    return element;
  }

  private _line(axis: "column" | "row", position: number): HTMLSpanElement {
    const line = document.createElement("span");
    line.className = `table-suggestions__line table-suggestions__line--${axis}`;
    if (axis === "column") line.style.left = `${position * 100}%`;
    else line.style.top = `${position * 100}%`;
    return line;
  }

  private _clearAll(): void {
    for (const { wrapper } of this._viewer.getPageLayout()) this._clearPage(wrapper);
  }

  private _clearPage(wrapper: HTMLDivElement): void {
    for (const element of Array.from(wrapper.querySelectorAll(`.${OVERLAY_CLASS}`))) element.remove();
  }
}
