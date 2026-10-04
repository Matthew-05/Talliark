import type { FileEntry, FolderEntry } from "../../types/index.js";
import {
  sendExportFile,
  sendOpenFileInViewer,
  sendRenameFile,
  sendRemoveFile,
  sendRowDragStarted,
  sendSelectFile,
} from "../../host-bridge.js";
import type { OcrProgress } from "../../host-bridge.js";
// Imported as its own entry point rather than through the package barrel:
// the barrel re-exports the pdf.js geometry module, whose top-level worker
// setup is a side effect that keeps all of pdf.js in whatever bundles it.
import { isTextEntryTarget } from "@talliark/shared/text-entry-target.js";
import { fileProgressFraction, stageLabel } from "@talliark/shared";

export interface FileTableOptions {
  onSelectionChange(selectedIds: string[]): void;
}

/** Tooltip shown on every control disabled by the OCR lock. */
const LOCKED_HINT = "Unavailable while OCR is running";

/**
 * Pointer travel, in CSS pixels, that turns a press on a row into a drag. Small enough that
 * a sloppy click still selects, large enough that the pointer wobble of a deliberate press
 * does not send a file to the folder list by accident.
 */
const DRAG_THRESHOLD = 5;

/** Gap kept between the context menu and the WebView viewport edges. */
const CONTEXT_MENU_VIEWPORT_MARGIN = 8;

type SortKey = "name" | "linkCount" | "status" | "fileSizeBytes" | "dateAdded" | "folder";
type SortDirection = "ascending" | "descending";

interface ColumnDefinition {
  className: string;
  label: string;
  sortKey: SortKey;
  minWidth: number;
}

const DATA_COLUMNS: ColumnDefinition[] = [
  { className: "col-name", label: "File Name", sortKey: "name", minWidth: 100 },
  { className: "col-links", label: "Links", sortKey: "linkCount", minWidth: 56 },
  { className: "col-status", label: "Status", sortKey: "status", minWidth: 92 },
  { className: "col-size", label: "Size", sortKey: "fileSizeBytes", minWidth: 62 },
  { className: "col-date", label: "Date Added", sortKey: "dateAdded", minWidth: 88 },
  { className: "col-folder", label: "Folder", sortKey: "folder", minWidth: 80 },
];

const TEXT_COLLATOR = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

export class FileTable {
  private readonly _root: HTMLElement;
  private readonly _table: HTMLTableElement;
  private readonly _columnElements = new Map<string, HTMLTableColElement>();
  private readonly _thead: HTMLTableSectionElement;
  private readonly _tbody: HTMLTableSectionElement;
  private readonly _contextMenu: HTMLDivElement;
  private _files: FileEntry[] = [];
  private _folders: FolderEntry[] = [];
  private _selectedFolderId: string | null = null;
  private _selectedIds: Set<string> = new Set();
  private _filterText = "";
  private _locked = false;
  private _contextMenuFile: FileEntry | null = null;
  private _contextMenuNameSpan: HTMLSpanElement | null = null;
  private _contextMenuRow: HTMLTableRowElement | null = null;
  private _isLoading = true;
  private readonly _ocrProgress = new Map<string, OcrProgress>();
  /**
   * Furthest point each file has reached, 0-1. The pipeline is adaptive and
   * its steps do not always arrive in declared order — an orientation check
   * can follow a retry — so the bar is held at its high-water mark. A bar
   * that slides backwards reads as a fault even when the work is fine.
   */
  private readonly _progressFloor = new Map<string, number>();
  private readonly _onSelectionChange: (ids: string[]) => void;
  private _sortKey: SortKey | null = "name";
  private _sortDirection: SortDirection = "ascending";
  /** Anchor row for shift-click range selection — the last row explicitly clicked. */
  private _lastClickedId: string | null = null;
  /**
   * Whether the anchor click checked (true) or unchecked (false) its row.
   * A shift-click range mirrors this direction, so unchecking a row and then
   * shift-clicking another deselects the whole range instead of selecting it.
   */
  private _lastClickedSelected = true;

  /**
   * A press that has not yet become a drag: the row it started on and where the pointer was
   * at the time. Null unless the left button is down on a row.
   */
  private _pressOrigin: { id: string; x: number; y: number } | null = null;

  /**
   * True from the moment a drag is reported to the host until the host answers
   * `row-drag-ended`. The host holds the mouse for the whole gesture, so the table is told
   * the drag is over from the host and from nowhere else — including the case where the host
   * refuses the drag outright and the release lands back here.
   */
  private _dragActive = false;

  constructor(container: HTMLElement, options: FileTableOptions) {
    this._onSelectionChange = options.onSelectionChange;

    this._root = document.createElement("div");
    this._root.className = "file-table-wrap";

    this._table = document.createElement("table");
    this._table.className = "file-table";
    this._buildColumnGroup();

    this._thead = document.createElement("thead");
    this._buildHeader();
    this._updateSortHeaders();

    const selectAllCb = this._thead.querySelector<HTMLInputElement>(".select-all-cb")!;
    selectAllCb.addEventListener("change", () => this._onSelectAll(selectAllCb.checked));

    this._tbody = document.createElement("tbody");

    this._table.appendChild(this._thead);
    this._table.appendChild(this._tbody);
    this._root.appendChild(this._table);

    // Create context menu
    this._contextMenu = document.createElement("div");
    this._contextMenu.className = "file-table__context-menu";
    this._contextMenu.innerHTML = `
      <button class="file-table__context-item" data-action="open-in-viewer">Open in Viewer</button>
      <span class="file-table__context-separator" aria-hidden="true"></span>
      <button class="file-table__context-item" data-action="export" title="Adds searchable text when OCR is available">Export PDF</button>
      <span class="file-table__context-separator" aria-hidden="true"></span>
      <button class="file-table__context-item" data-action="rename">Rename</button>
      <button class="file-table__context-item" data-action="delete">Delete</button>
    `;
    this._root.appendChild(this._contextMenu);

    // Hide context menu on outside click
    document.addEventListener("click", () => this._hideContextMenu());

    document.addEventListener("keydown", (e) => this._onDocumentKeyDown(e));

    // A drag is recognised from raw mouse travel rather than from HTML5 drag events, which
    // Chromium settles internally and never hands to the host. These two also cover a press
    // that ends outside the table, on the toolbar or the header, which would otherwise be left
    // waiting for a threshold that never arrives and would start a phantom drag on the next
    // pointer move anywhere in the window.
    document.addEventListener("mousemove", (e) => this._onDocumentMouseMove(e));
    document.addEventListener("mouseup", () => this._onDocumentMouseUp());

    container.appendChild(this._root);
  }

  private _buildColumnGroup(): void {
    const columnGroup = document.createElement("colgroup");
    for (const className of ["col-check", ...DATA_COLUMNS.map((column) => column.className)]) {
      const column = document.createElement("col");
      column.className = className;
      this._columnElements.set(className, column);
      columnGroup.appendChild(column);
    }
    this._table.appendChild(columnGroup);
  }

  private _buildHeader(): void {
    const row = document.createElement("tr");
    const checkboxHeader = document.createElement("th");
    checkboxHeader.className = "col-check";
    const selectAll = document.createElement("input");
    selectAll.type = "checkbox";
    selectAll.className = "select-all-cb";
    selectAll.title = "Select all (Ctrl+A)";
    checkboxHeader.appendChild(selectAll);
    row.appendChild(checkboxHeader);

    DATA_COLUMNS.forEach((column, index) => {
      const header = document.createElement("th");
      header.className = `${column.className} file-table__sortable-header`;
      header.dataset["sortKey"] = column.sortKey;
      header.setAttribute("aria-sort", "none");

      const button = document.createElement("button");
      button.type = "button";
      button.className = "file-table__sort-button";
      button.title = `Sort by ${column.label}`;

      const label = document.createElement("span");
      label.textContent = column.label;
      button.appendChild(label);

      const indicator = document.createElement("span");
      indicator.className = "file-table__sort-indicator";
      indicator.setAttribute("aria-hidden", "true");
      button.appendChild(indicator);

      button.addEventListener("click", () => this._setSort(column.sortKey));
      header.appendChild(button);

      if (index < DATA_COLUMNS.length - 1) {
        const resizeHandle = document.createElement("span");
        resizeHandle.className = "file-table__resize-handle";
        resizeHandle.setAttribute("role", "separator");
        resizeHandle.setAttribute("aria-orientation", "vertical");
        resizeHandle.setAttribute("aria-label", `Resize ${column.label} column`);
        resizeHandle.addEventListener("pointerdown", (event) => {
          this._startColumnResize(event, header, column);
        });
        header.appendChild(resizeHandle);
      }

      row.appendChild(header);
    });

    this._thead.appendChild(row);
  }

  private _setSort(sortKey: SortKey): void {
    if (this._sortKey === sortKey) {
      this._sortDirection = this._sortDirection === "ascending" ? "descending" : "ascending";
    } else {
      this._sortKey = sortKey;
      this._sortDirection = "ascending";
    }

    this._updateSortHeaders();
    this._render();
  }

  private _updateSortHeaders(): void {
    for (const header of this._thead.querySelectorAll<HTMLTableCellElement>("th[data-sort-key]")) {
      const active = header.dataset["sortKey"] === this._sortKey;
      const direction = active ? this._sortDirection : "none";
      header.setAttribute("aria-sort", direction);

      const button = header.querySelector<HTMLButtonElement>(".file-table__sort-button");
      if (button) {
        const label = button.querySelector("span")?.textContent ?? "column";
        button.title = active
          ? `Sort ${label} ${this._sortDirection === "ascending" ? "descending" : "ascending"}`
          : `Sort by ${label}`;
      }
    }
  }

  private _startColumnResize(
    event: PointerEvent,
    header: HTMLTableCellElement,
    column: ColumnDefinition,
  ): void {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();

    const columnElement = this._columnElements.get(column.className);
    const fillerDefinition = DATA_COLUMNS[DATA_COLUMNS.length - 1]!;
    const fillerColumn = this._columnElements.get(fillerDefinition.className);
    if (!columnElement || !fillerColumn) return;

    const headers = Array.from(
      this._thead.querySelectorAll<HTMLTableCellElement>("th"),
    );
    const headerWidths = headers.map((current) => current.getBoundingClientRect().width);
    const columns = Array.from(this._columnElements.values());
    for (let index = 0; index < columns.length; index++) {
      columns[index]!.style.width = `${headerWidths[index]}px`;
    }

    const handle = event.currentTarget as HTMLElement;
    const startX = event.clientX;
    const startWidth = header.getBoundingClientRect().width;
    const fillerStartWidth = headerWidths[headerWidths.length - 1]!;
    const startTableWidth = this._table.getBoundingClientRect().width;
    const currentMin = effectiveMinimumWidth(column.minWidth, startWidth);
    const fillerMin = effectiveMinimumWidth(fillerDefinition.minWidth, fillerStartWidth);
    this._table.style.width = `${startTableWidth}px`;

    this._root.classList.add("file-table-wrap--resizing");
    handle.classList.add("file-table__resize-handle--active");
    handle.setPointerCapture(event.pointerId);

    const onPointerMove = (moveEvent: PointerEvent): void => {
      const requestedWidth = startWidth + moveEvent.clientX - startX;
      const width = Math.max(currentMin, requestedWidth);
      const delta = width - startWidth;
      const fillerSlack = Math.max(0, fillerStartWidth - fillerMin);
      const absorbedDelta = delta > 0 ? Math.min(delta, fillerSlack) : delta;

      columnElement.style.width = `${width}px`;
      fillerColumn.style.width = `${fillerStartWidth - absorbedDelta}px`;
      this._table.style.width = `${startTableWidth + Math.max(0, delta - fillerSlack)}px`;
    };

    const finish = (): void => {
      handle.removeEventListener("pointermove", onPointerMove);
      handle.removeEventListener("pointerup", finish);
      handle.removeEventListener("pointercancel", finish);
      handle.removeEventListener("lostpointercapture", finish);
      handle.classList.remove("file-table__resize-handle--active");
      this._root.classList.remove("file-table-wrap--resizing");
    };

    handle.addEventListener("pointermove", onPointerMove);
    handle.addEventListener("pointerup", finish);
    handle.addEventListener("pointercancel", finish);
    handle.addEventListener("lostpointercapture", finish);
  }

  update(files: FileEntry[], selectedFolderId: string | null): void {
    this._isLoading = false;
    this._files = files;
    this._selectedFolderId = selectedFolderId;
    // Drop selections that no longer exist
    const fileIds = new Set(files.map((f) => f.id));
    for (const id of this._selectedIds) {
      if (!fileIds.has(id)) this._selectedIds.delete(id);
    }
    for (const id of this._ocrProgress.keys()) {
      const file = files.find((entry) => entry.id === id);
      if (
        !file ||
        (file.status !== "queued" &&
          file.status !== "processing" &&
          file.status !== "error")
      ) {
        this._ocrProgress.delete(id);
        this._progressFloor.delete(id);
      }
    }
    this._render();
  }

  /**
   * Repaints one row's status indicator in place, leaving every other row's DOM
   * untouched. OCR progress arrives one message at a time, and a full re-render
   * per message would rebuild every spinner element and restart its animation
   * from 0°, so the spinners would visibly stutter for the whole run.
   * Falls back to a full render when the row isn't currently on screen.
   */
  updateStatus(fileId: string, status: string, progress: OcrProgress = {}): void {
    const entry = this._files.find((f) => f.id === fileId);
    const previousStatus = entry?.status;
    if (entry) entry.status = status;

    if (
      status === "queued" ||
      status === "processing" ||
      (status === "error" && progress.message)
    ) {
      // A fresh run starts the bar over; within a run it only ever advances.
      if (status === "queued") this._progressFloor.delete(fileId);

      const fraction = fileProgressFraction(
        progress.stage, progress.current, progress.total,
      );
      if (fraction !== null) {
        const floor = this._progressFloor.get(fileId) ?? 0;
        this._progressFloor.set(fileId, Math.max(floor, fraction));
      }
      this._ocrProgress.set(fileId, progress);
    } else {
      this._ocrProgress.delete(fileId);
      this._progressFloor.delete(fileId);
    }

    if (this._sortKey === "status" && previousStatus !== status) {
      this._render();
      return;
    }

    const row = Array.from(this._tbody.rows).find(
      (r) => r.dataset["id"] === fileId
    );
    const cell = row?.querySelector<HTMLTableCellElement>("td.col-status");
    if (!cell) return;

    cell.replaceChildren(buildStatusIndicator(
      status, this._ocrProgress.get(fileId), this._progressFloor.get(fileId),
    ));
  }

  setFilter(text: string): void {
    this._filterText = text;
    this._selectedIds.clear();
    this._render();
    this._onSelectionChange([]);
  }

  getSelectedIds(): string[] {
    return Array.from(this._selectedIds);
  }

  clearSelection(): void {
    this._selectedIds.clear();
    this._lastClickedId = null;
    this._lastClickedSelected = true;
    this._render();
    this._onSelectionChange([]);
  }

  /**
   * Locks or unlocks all mutating interaction with the table while OCR runs:
   * row selection, inline rename (double-click, rename button) and the
   * right-click context menu. Locking immediately clears the current selection.
   * No-op if the state is unchanged.
   */
  setLocked(locked: boolean): void {
    if (this._locked === locked) return;
    this._locked = locked;
    if (locked) {
      this._selectedIds.clear();
      this._lastClickedId = null;
      this._lastClickedSelected = true;
      this._onSelectionChange([]);
      this._hideContextMenu();
      // The host ends a live drag when OCR takes the sidebar, so the flag would otherwise
      // still be set here, keeping the next click on a row from being a click.
      this._pressOrigin = null;
      this._dragActive = false;
    }
    this._render();
  }

  /** Clears row selection and the filter text. */
  reset(): void {
    this._filterText = "";
    this.clearSelection();
  }

  updateFolders(folders: FolderEntry[]): void {
    this._folders = folders;
    this._render();
  }

  private _visibleFiles(): FileEntry[] {
    let files = this._selectedFolderId === null
      ? this._files
      : this._files.filter((f) => (f.folderId ?? null) === this._selectedFolderId);

    if (this._filterText.trim() !== "") {
      const lower = this._filterText.toLowerCase();
      files = files.filter((f) => f.name.toLowerCase().includes(lower));
    }

    if (this._sortKey === null) return files;

    const sortKey = this._sortKey;
    const direction = this._sortDirection === "ascending" ? 1 : -1;
    return files
      .map((file, index) => ({ file, index }))
      .sort((left, right) => {
        const compared = this._compareFiles(left.file, right.file, sortKey);
        return compared === 0 ? left.index - right.index : compared * direction;
      })
      .map(({ file }) => file);
  }

  private _compareFiles(left: FileEntry, right: FileEntry, sortKey: SortKey): number {
    switch (sortKey) {
      case "linkCount":
        return (left.linkCount ?? 0) - (right.linkCount ?? 0);
      case "fileSizeBytes":
        return left.fileSizeBytes - right.fileSizeBytes;
      case "dateAdded": {
        const leftTime = Date.parse(left.dateAdded);
        const rightTime = Date.parse(right.dateAdded);
        return (Number.isNaN(leftTime) ? 0 : leftTime) - (Number.isNaN(rightTime) ? 0 : rightTime);
      }
      case "folder":
        return TEXT_COLLATOR.compare(this._folderName(left), this._folderName(right));
      case "status":
        return TEXT_COLLATOR.compare(formatStatusLabel(left.status), formatStatusLabel(right.status));
      case "name":
        return TEXT_COLLATOR.compare(left.name, right.name);
    }
  }

  private _folderName(file: FileEntry): string {
    if (!file.folderId) return "";
    return this._folders.find((folder) => folder.id === file.folderId)?.name ?? "";
  }

  /**
   * Ctrl+A (Cmd+A) selects every file the table is currently showing, which is
   * the header checkbox's job, so it goes through the same path rather than
   * touching the selection set itself — the folder and the filter narrow both
   * of them identically.
   *
   * It stands down inside a text field, where Ctrl+A belongs to the field, and
   * while OCR holds the table locked. Without preventDefault the browser would
   * also select the pane's text, leaving a blue wash over the rows.
   */
  private _onDocumentKeyDown(e: KeyboardEvent): void {
    if (e.key.toLowerCase() !== "a") return;
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
    if (this._locked || isTextEntryTarget(e.target)) return;

    e.preventDefault();
    this._onSelectAll(true);
  }

  /**
   * Turns a press that has travelled far enough into a drag of the current selection.
   *
   * The whole selection travels, not only the pressed row, so that checkboxes and
   * shift-click ranges mean what they look like they mean. A press on an unselected row
   * selects it first, which is what dragging one row out of a file list does everywhere
   * else. Past this point the table has no further part in the gesture: the host takes the
   * mouse and resolves the drop on the native folder list.
   */
  private _onDocumentMouseMove(e: MouseEvent): void {
    const origin = this._pressOrigin;
    if (origin === null) return;

    const travelled = Math.hypot(e.clientX - origin.x, e.clientY - origin.y);
    if (travelled < DRAG_THRESHOLD) return;

    this._pressOrigin = null;

    if (!this._selectedIds.has(origin.id)) {
      this._selectedIds.clear();
      this._selectedIds.add(origin.id);
      this._lastClickedId = origin.id;
      this._lastClickedSelected = true;
      this._onSelectionChange(this.getSelectedIds());
      this._render();
    }

    this._dragActive = true;
    for (const id of this._selectedIds) this._setRowDragging(id, true);
    sendRowDragStarted(this.getSelectedIds());
  }

  private _onDocumentMouseUp(): void {
    this._pressOrigin = null;
  }

  private _setRowDragging(id: string, dragging: boolean): void {
    const row = Array.from(this._tbody.rows).find((r) => r.dataset["id"] === id);
    row?.classList.toggle("is-dragging", dragging);
  }

  /**
   * Clears the drag highlight once the host reports the drag is over. Everything about the
   * outcome — which folder, whether the move happened — arrives separately as a file list,
   * so this only has to stop the rows looking held.
   */
  endRowDrag(): void {
    this._pressOrigin = null;
    this._dragActive = false;

    // Swept by class rather than by id: the host may have pushed a new file list, and the
    // rows that were marked are whatever was on screen when the drag started.
    for (const row of Array.from(this._tbody.rows)) row.classList.remove("is-dragging");
  }

  private _onSelectAll(checked: boolean): void {
    const visible = this._visibleFiles();
    if (checked) {
      for (const f of visible) this._selectedIds.add(f.id);
    } else {
      for (const f of visible) this._selectedIds.delete(f.id);
    }
    this._render();
    this._onSelectionChange(this.getSelectedIds());
  }

  private _onRowCheck(id: string, checked: boolean): void {
    if (checked) {
      this._selectedIds.add(id);
    } else {
      this._selectedIds.delete(id);
    }
    this._updateSelectAllCheckbox();
    this._onSelectionChange(this.getSelectedIds());
  }

  /**
   * Applies the last click's direction (select or unselect) to every visible
   * row between the anchor and `targetId` (inclusive), then re-renders. So if
   * the click right before the shift-click unchecked its row, the whole range
   * gets unselected instead of selected. The anchor itself is left unchanged
   * so repeated shift-clicks keep extending the range from the same origin,
   * matching Explorer/Finder behavior. Falls back to a plain select if there's
   * no anchor or it's no longer visible (e.g. filtered out).
   */
  private _selectRange(targetId: string): void {
    const visible = this._visibleFiles();
    const anchorIdx = this._lastClickedId
      ? visible.findIndex((f) => f.id === this._lastClickedId)
      : -1;
    const targetIdx = visible.findIndex((f) => f.id === targetId);

    if (anchorIdx === -1 || targetIdx === -1) {
      this._selectedIds.add(targetId);
      this._lastClickedId = targetId;
      this._lastClickedSelected = true;
    } else {
      const [start, end] = anchorIdx < targetIdx
        ? [anchorIdx, targetIdx]
        : [targetIdx, anchorIdx];
      for (let i = start; i <= end; i++) {
        const id = visible[i]!.id;
        if (this._lastClickedSelected) {
          this._selectedIds.add(id);
        } else {
          this._selectedIds.delete(id);
        }
      }
    }

    this._render();
    this._onSelectionChange(this.getSelectedIds());
  }

  private _updateSelectAllCheckbox(): void {
    const selectAllCb = this._thead.querySelector<HTMLInputElement>(".select-all-cb");
    if (!selectAllCb) return;
    const visible = this._visibleFiles();
    const checkedCount = visible.filter((f) => this._selectedIds.has(f.id)).length;
    selectAllCb.checked = visible.length > 0 && checkedCount === visible.length;
    selectAllCb.indeterminate = checkedCount > 0 && checkedCount < visible.length;
    selectAllCb.disabled = this._locked;
    selectAllCb.title = this._locked ? LOCKED_HINT : "Select all";
  }

  private _render(): void {
    this._tbody.innerHTML = "";
    const visible = this._visibleFiles();

    this._root.classList.toggle("file-table-wrap--locked", this._locked);
    this._updateSelectAllCheckbox();

    if (visible.length === 0) {
      const empty = document.createElement("tr");
      empty.className = "file-table__empty-row";
      empty.innerHTML = this._isLoading
        ? `<td colspan="7" class="file-table__empty">Talliark Initializing…</td>`
        : `<td colspan="7" class="file-table__empty">Add files to get started.</td>`;
      this._tbody.appendChild(empty);
      return;
    }

    for (const file of visible) {
      this._tbody.appendChild(this._buildRow(file));
    }
  }

  private _buildRow(file: FileEntry): HTMLTableRowElement {
    const tr = document.createElement("tr");
    tr.dataset["id"] = file.id;
    if (this._selectedIds.has(file.id)) tr.classList.add("is-selected");

    // Press on a row: the first move past the threshold hands the gesture to the host, which
    // owns the mouse from there and resolves the drop on the native folder list. Interactive
    // controls keep their own presses, and a locked table keeps all of them.
    tr.addEventListener("mousedown", (e) => {
      if (this._locked || e.button !== 0) return;
      const target = e.target as HTMLElement;
      if (target.closest("input, button")) return;
      this._pressOrigin = { id: file.id, x: e.clientX, y: e.clientY };
    });

    // Row-level right-click → context menu (suppressed entirely while locked)
    tr.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (this._locked) return;
      this._showContextMenu(file, nameSpan, tr, e.clientX, e.clientY);
    });

    // Checkbox cell
    const checkTd = document.createElement("td");
    checkTd.className = "col-check";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "row-cb";
    cb.checked = this._selectedIds.has(file.id);
    cb.disabled = this._locked;
    cb.title = this._locked ? LOCKED_HINT : "Select file";
    cb.addEventListener("click", (e) => {
      e.stopPropagation();
      if (e.shiftKey && this._lastClickedId) {
        // We manage range selection ourselves — suppress the native toggle
        // so this checkbox's own change doesn't also fire for it.
        e.preventDefault();
        this._selectRange(file.id);
      }
    });
    cb.addEventListener("change", () => {
      this._onRowCheck(file.id, cb.checked);
      tr.classList.toggle("is-selected", cb.checked);
      this._lastClickedId = file.id;
      this._lastClickedSelected = cb.checked;
      if (cb.checked) sendSelectFile(file.id);
    });
    checkTd.appendChild(cb);

    // Name cell — double-click to rename
    const nameTd = document.createElement("td");
    nameTd.className = "col-name";
    const nameSpan = document.createElement("span");
    nameSpan.className = "file-name";
    nameSpan.textContent = file.name;
    if (this._locked) {
      nameSpan.classList.add("file-name--locked");
      nameSpan.title = LOCKED_HINT;
    } else {
      nameSpan.title = "Double-click to rename";
      nameSpan.addEventListener("dblclick", (e) => {
        e.stopPropagation();
        this._startRename(file, nameSpan, tr);
      });
    }
    nameTd.appendChild(nameSpan);

    // Links cell
    const linksTd = document.createElement("td");
    linksTd.className = "col-links";
    const linkCount = file.linkCount ?? 0;
    linksTd.textContent = String(linkCount);
    if (linkCount === 0) linksTd.classList.add("col-links--none");

    // Status cell
    const statusTd = document.createElement("td");
    statusTd.className = "col-status";
    statusTd.appendChild(buildStatusIndicator(
      file.status, this._ocrProgress.get(file.id), this._progressFloor.get(file.id),
    ));

    // Size cell
    const sizeTd = document.createElement("td");
    sizeTd.className = "col-size";
    sizeTd.textContent = formatBytes(file.fileSizeBytes);

    // Date cell
    const dateTd = document.createElement("td");
    dateTd.className = "col-date";
    dateTd.textContent = formatDate(file.dateAdded);

    // Folder cell
    const folderTd = document.createElement("td");
    folderTd.className = "col-folder";
    if (file.folderId) {
      const folder = this._folders.find((f) => f.id === file.folderId);
      if (folder) folderTd.textContent = folder.name;
    }

    tr.appendChild(checkTd);
    tr.appendChild(nameTd);
    tr.appendChild(linksTd);
    tr.appendChild(statusTd);
    tr.appendChild(sizeTd);
    tr.appendChild(dateTd);
    tr.appendChild(folderTd);

    return tr;
  }

  private _startRename(file: FileEntry, nameSpan: HTMLSpanElement, tr: HTMLTableRowElement): void {
    if (this._locked) return;
    if (tr.querySelector(".rename-input")) return;

    const input = document.createElement("input");
    input.className = "rename-input";
    input.type = "text";
    input.value = file.name;
    nameSpan.replaceWith(input);
    input.focus();
    input.select();

    const commit = (): void => {
      const newName = input.value.trim();
      if (newName && newName !== file.name) {
        sendRenameFile(file.id, newName);
      } else {
        input.replaceWith(nameSpan);
      }
    };

    input.addEventListener("blur", commit);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { input.blur(); }
      if (e.key === "Escape") { input.replaceWith(nameSpan); }
    });
  }

  private _showContextMenu(file: FileEntry, nameSpan: HTMLSpanElement, tr: HTMLTableRowElement, x: number, y: number): void {
    this._contextMenuFile = file;
    this._contextMenuNameSpan = nameSpan;
    this._contextMenuRow = tr;

    this._contextMenu.style.position = "fixed";
    this._contextMenu.style.left = "0";
    this._contextMenu.style.top = "0";
    this._contextMenu.classList.add("file-table__context-menu--visible");

    const bounds = this._contextMenu.getBoundingClientRect();
    const maximumLeft = Math.max(
      CONTEXT_MENU_VIEWPORT_MARGIN,
      window.innerWidth - bounds.width - CONTEXT_MENU_VIEWPORT_MARGIN,
    );
    const left = Math.min(Math.max(x, CONTEXT_MENU_VIEWPORT_MARGIN), maximumLeft);

    // Prefer opening below the pointer. If that would cross the bottom edge,
    // anchor the menu above the pointer instead, then clamp unusually tall menus.
    const preferredTop = y + bounds.height + CONTEXT_MENU_VIEWPORT_MARGIN <= window.innerHeight
      ? y
      : y - bounds.height;
    const maximumTop = Math.max(
      CONTEXT_MENU_VIEWPORT_MARGIN,
      window.innerHeight - bounds.height - CONTEXT_MENU_VIEWPORT_MARGIN,
    );
    const top = Math.min(Math.max(preferredTop, CONTEXT_MENU_VIEWPORT_MARGIN), maximumTop);

    this._contextMenu.style.left = `${left}px`;
    this._contextMenu.style.top = `${top}px`;

    // Attach event listeners to menu items
    const items = this._contextMenu.querySelectorAll<HTMLButtonElement>(".file-table__context-item");
    items.forEach(item => {
      item.removeEventListener("click", this._handleContextMenuClick);
      item.addEventListener("click", this._handleContextMenuClick);
    });
  }

  private _hideContextMenu(): void {
    this._contextMenu.classList.remove("file-table__context-menu--visible");
    this._contextMenuFile = null;
    this._contextMenuNameSpan = null;
    this._contextMenuRow = null;
  }

  private _handleContextMenuClick = (e: MouseEvent): void => {
    const button = e.target as HTMLButtonElement;
    const action = button.dataset["action"];

    if (this._locked) { this._hideContextMenu(); return; }
    if (!this._contextMenuFile || !this._contextMenuNameSpan || !this._contextMenuRow) return;

    if (action === "open-in-viewer") {
      sendOpenFileInViewer(this._contextMenuFile.id);
    } else if (action === "export") {
      sendExportFile(this._contextMenuFile.id);
    } else if (action === "rename") {
      this._startRename(this._contextMenuFile, this._contextMenuNameSpan, this._contextMenuRow);
    } else if (action === "delete") {
      sendRemoveFile(this._contextMenuFile.id);
    }

    this._hideContextMenu();
  };
}

/** Statuses that represent active CPU work; determinate updates replace the spinner. */
const ACTIVE_OCR_STATUSES = new Set(["processing"]);

function formatStatusLabel(status: string): string {
  switch (status) {
    case "ocr":        return "OCR";
    case "text":       return "Text";
    case "none":       return "None";
    case "queued":     return "Queued";
    case "processing": return "Processing";
    case "error":      return "Failed";
    default:           return status;
  }
}

/**
 * Builds the visible text for a row's status: the stage's name, plus the count
 * within that stage when the step exposes one ("Recognizing 7/40" is pages of
 * this OCR pass, not of the file).
 *
 * A stage the pane has no name for falls back to the plain status rather than
 * borrowing another stage's words. That fallback is why the vocabulary is
 * covered by a test: the previous version silently reported unknown stages as
 * "Starting", so a step added late in the pipeline announced that a run already
 * minutes old was just beginning.
 */
function formatProgressLabel(status: string, progress?: OcrProgress): string {
  const base = ACTIVE_OCR_STATUSES.has(status)
    ? stageLabel(progress?.stage)
    : null;
  const label = base ?? formatStatusLabel(status);
  if (
    typeof progress?.current === "number" &&
    typeof progress.total === "number" &&
    progress.total > 0
  ) {
    return `${label} ${Math.min(Math.max(progress.current, 0), progress.total)}/${progress.total}`;
  }
  return label;
}

/**
 * Builds a row's status cell.
 *
 * `fraction` is progress through the whole file, already held at its
 * high-water mark by the caller. The stage's own current/total is deliberately
 * not used here: those units change meaning between stages — pages, then
 * chunks, then pages again — so driving the bar with them filled and reset it
 * several times per file. It belongs in the label, where the unit is stated.
 */
function buildStatusIndicator(
  status: string,
  progress?: OcrProgress,
  fraction?: number,
): HTMLSpanElement {
  const root = document.createElement("span");
  root.className = `file-status file-status--${status}`;
  if (progress?.message) {
    root.title = progress.fileIndex && progress.fileCount
      ? `File ${progress.fileIndex} of ${progress.fileCount} — ${progress.message}`
      : progress.message;
  }

  const dot = document.createElement("span");
  dot.className = "file-status__dot";
  dot.setAttribute("aria-hidden", "true");
  root.appendChild(dot);

  const text = formatProgressLabel(status, progress);
  const label = document.createElement("span");
  label.className = "file-status__label";
  label.textContent = text;
  root.appendChild(label);

  if (ACTIVE_OCR_STATUSES.has(status)) {
    const determinate = typeof fraction === "number";

    const track = document.createElement("span");
    track.className = "file-status__track";
    track.setAttribute("aria-hidden", "true");

    const bar = document.createElement("span");
    bar.className = "file-status__bar";

    if (determinate) {
      const percent = Math.min(Math.max(fraction!, 0), 1) * 100;
      bar.style.width = `${percent}%`;
      root.setAttribute("role", "progressbar");
      root.setAttribute("aria-label", text);
      root.setAttribute("aria-valuemin", "0");
      root.setAttribute("aria-valuenow", String(Math.round(percent)));
      root.setAttribute("aria-valuemax", "100");
    } else {
      bar.classList.add("file-status__bar--indeterminate");
      root.setAttribute("role", "status");
      root.setAttribute("aria-live", "polite");
    }

    track.appendChild(bar);
    root.appendChild(track);
  }

  return root;
}

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

/** Avoids snapping a column wider when the task pane starts below its ideal width. */
function effectiveMinimumWidth(preferred: number, rendered: number): number {
  return rendered >= preferred ? preferred : Math.max(32, rendered * 0.6);
}
