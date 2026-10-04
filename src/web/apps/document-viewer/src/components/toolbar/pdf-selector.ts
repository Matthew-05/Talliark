import type { PdfEntry } from "../../types/index.js";
import { compareFileNamesAscending } from "@talliark/shared";

/** True when `entry` belongs to `folderId`; a null filter accepts every entry. */
export function matchesFolderFilter(entry: PdfEntry, folderId: string | null): boolean {
  return folderId === null || entry.folderId === folderId;
}

export class PdfSelector {
  readonly element: HTMLElement;

  private _entries: PdfEntry[] = [];
  private _folderFilterId: string | null = null;
  private _activeId: string | null = null;
  private _isOpen = false;

  private _triggerLabel: HTMLSpanElement;
  private _list: HTMLUListElement;
  private _searchInput: HTMLInputElement;
  private readonly _callbacks: Array<(entry: PdfEntry) => void> = [];
  private readonly _onOpenCallbacks: Array<() => void> = [];

  constructor() {
    this.element = document.createElement("div");
    this.element.className = "pdf-selector toolbar__slot";

    const trigger = document.createElement("button");
    trigger.className = "pdf-selector__trigger";
    trigger.addEventListener("click", (e) => {
      e.stopPropagation();
      this._toggle();
    });

    this._triggerLabel = document.createElement("span");
    this._triggerLabel.className = "pdf-selector__trigger-label";
    this._triggerLabel.textContent = "Select document…";

    const caret = document.createElement("span");
    caret.className = "pdf-selector__caret";
    caret.textContent = "▾";

    trigger.append(this._triggerLabel, caret);

    const dropdown = document.createElement("div");
    dropdown.className = "pdf-selector__dropdown";

    const searchWrapper = document.createElement("div");
    searchWrapper.className = "pdf-selector__search";

    this._searchInput = document.createElement("input");
    this._searchInput.className = "pdf-selector__search-input";
    this._searchInput.type = "search";
    this._searchInput.placeholder = "Search…";
    this._searchInput.addEventListener("input", () =>
      this._renderList(this._searchInput.value)
    );
    this._searchInput.addEventListener("click", (e) => e.stopPropagation());
    this._searchInput.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      this._close();
      this._searchInput.blur();
    });

    searchWrapper.appendChild(this._searchInput);

    this._list = document.createElement("ul");
    this._list.className = "pdf-selector__list";

    dropdown.append(searchWrapper, this._list);
    this.element.append(trigger, dropdown);

    document.addEventListener("click", () => this._close());
  }

  onSelect(cb: (entry: PdfEntry) => void): void {
    this._callbacks.push(cb);
  }

  onOpen(cb: () => void): void {
    this._onOpenCallbacks.push(cb);
  }

  close(): void {
    this._close();
  }

  /**
   * Opens the dropdown and puts the caret in its search box. Used by the
   * Ctrl+Shift+F hotkey; re-focuses the box if the dropdown is already open.
   */
  openWithSearchFocus(): void {
    if (this._isOpen) {
      this._searchInput.select();
      this._searchInput.focus();
      return;
    }
    this._open();
  }

  setEntries(entries: PdfEntry[]): void {
    this._entries = entries;
    this._renderList(this._searchInput.value);
  }

  /**
   * Restricts the dropdown to one folder. `null` shows every document.
   * The active document stays loaded even when it falls outside the filter.
   */
  setFolderFilter(folderId: string | null): void {
    this._folderFilterId = folderId;
    this._renderList(this._searchInput.value);
  }

  /** Entries permitted by the current folder filter, in list order. */
  getFilteredEntries(): PdfEntry[] {
    return this._entries.filter((entry) => matchesFolderFilter(entry, this._folderFilterId));
  }

  /** Replaces each entry's folder assignment after a host folder update. */
  updateFolderAssignments(assignments: Map<string, string | undefined>): void {
    for (const entry of this._entries) {
      entry.folderId = assignments.get(entry.id);
    }
    this._renderList(this._searchInput.value);
  }

  /** Replaces or appends a single entry after a targeted host update (e.g. OCR). */
  upsertEntry(entry: PdfEntry): void {
    const index = this._entries.findIndex((candidate) => candidate.id === entry.id);
    if (index >= 0) {
      this._entries[index] = entry;
    } else {
      this._entries.push(entry);
    }
    this._renderList(this._searchInput.value);
  }

  /** Updates the display name of an existing entry without touching its blob URL. */
  updateEntryName(id: string, name: string): void {
    const entry = this._entries.find((e) => e.id === id);
    if (!entry) return;
    entry.name = name;
    if (this._activeId === id) {
      this._triggerLabel.textContent = name;
    }
    // If dropdown is open, clear search to show all entries and display the renamed PDF
    if (this._isOpen) {
      this._searchInput.value = "";
    }
    this._renderList(this._searchInput.value);
  }

  updateLinkCounts(counts: Record<string, number>): void {
    for (const entry of this._entries) {
      entry.linkCount = counts[entry.id] ?? 0;
    }
    this._renderList(this._searchInput.value);
  }

  /** Merges updated page rotations into the matching entry so future loadDocument calls use them. */
  updatePdfRotations(pdfId: string, rotations: Record<number, number>): void {
    const entry = this._entries.find((e) => e.id === pdfId);
    if (!entry) return;
    if (!entry.pageRotations) entry.pageRotations = {};
    for (const [k, v] of Object.entries(rotations)) {
      const idx = Number(k);
      if (v === 0) delete entry.pageRotations[idx];
      else entry.pageRotations[idx] = v;
    }
    if (Object.keys(entry.pageRotations).length === 0) entry.pageRotations = undefined;
  }

  /** Removes an entry by id. Clears the active selection if it matches. */
  removeEntry(id: string): void {
    this._entries = this._entries.filter((e) => e.id !== id);
    if (this._activeId === id) {
      this._activeId = null;
      this._triggerLabel.textContent = "Select document…";
    }
    this._renderList(this._searchInput.value);
  }

  getEntry(id: string): PdfEntry | undefined {
    return this._entries.find((e) => e.id === id);
  }

  getEntries(): PdfEntry[] {
    return this._entries;
  }

  setActiveId(id: string): void {
    this._activeId = id;
    const entry = this._entries.find((e) => e.id === id);
    this._triggerLabel.textContent = entry?.name ?? "Select document…";
    this._renderList(this._searchInput.value);
  }

  private _toggle(): void {
    this._isOpen ? this._close() : this._open();
  }

  private _open(): void {
    this._isOpen = true;
    this.element.classList.add("pdf-selector--open");
    this._searchInput.value = "";
    this._renderList("");
    this._searchInput.focus();
    for (const cb of this._onOpenCallbacks) cb();
  }

  private _close(): void {
    this._isOpen = false;
    this.element.classList.remove("pdf-selector--open");
  }

  private _renderList(query: string): void {
    this._list.replaceChildren();

    const inFolder = this.getFilteredEntries();
    const trimmed = query.trim().toLowerCase();
    const filtered = trimmed
      ? inFolder.filter((e) => e.name.toLowerCase().includes(trimmed))
      : inFolder;
    filtered.sort((a, b) => compareFileNamesAscending(a.name, b.name));

    if (filtered.length === 0) {
      const empty = document.createElement("li");
      empty.className = "pdf-selector__empty";
      empty.textContent = this._folderFilterId === null
        ? "No documents found"
        : "No documents in this folder";
      this._list.appendChild(empty);
      return;
    }

    for (const entry of filtered) {
      const li = document.createElement("li");
      li.className = "pdf-selector__item";
      if (entry.id === this._activeId) {
        li.classList.add("pdf-selector__item--active");
      }
      li.title = entry.name;

      const nameSpan = document.createElement("span");
      nameSpan.className = "pdf-selector__name";
      nameSpan.textContent = entry.name;

      const n = entry.linkCount ?? 0;
      const countSpan = document.createElement("span");
      countSpan.className = "pdf-selector__link-count";
      countSpan.textContent = n === 1 ? "1 link" : `${n} links`;

      li.append(nameSpan, countSpan);
      li.addEventListener("click", (e) => {
        e.stopPropagation();
        this._select(entry);
      });
      this._list.appendChild(li);
    }
  }

  private _select(entry: PdfEntry): void {
    this.setActiveId(entry.id);
    this._close();
    for (const cb of this._callbacks) cb(entry);
  }
}
