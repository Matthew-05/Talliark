import type { FileEntry, FolderEntry } from "./types/index.js";
import { initHostBridge, sendRemoveFile, sendMoveFile, sendOcrPdfs, sendCancelOcr } from "./host-bridge.js";
import type { OcrProgress } from "./host-bridge.js";
import { FileTable } from "./components/file-table/file-table.js";
import { TableToolbar } from "./components/table-toolbar/table-toolbar.js";
import { wireFileManagerUiReset } from "./reset-ui.js";

export function mountApp(root: HTMLElement): void {
  root.className = "file-manager";

  // The folder list is a native WinForms panel docked beside this view, so there is one
  // column here: the toolbar and the table. Selection arrives as `folder-selected`.
  const content = document.createElement("div");
  content.className = "file-manager__content";
  root.appendChild(content);

  let selectedFolderId: string | null = null;
  let currentFolders: FolderEntry[] = [];
  let selectedIds: string[] = [];

  const activeOcrStatuses = new Set(["queued", "processing"]);

  function computeToolbarState(): { selectedHasActiveOcr: boolean; anyOcrRunning: boolean } {
    const selectedHasActiveOcr = selectedIds.some((id) => {
      const f = currentFiles.find((f) => f.id === id);
      return f !== undefined && activeOcrStatuses.has(f.status);
    });
    const anyOcrRunning = currentFiles.some((f) => activeOcrStatuses.has(f.status));
    return { selectedHasActiveOcr, anyOcrRunning };
  }

  const toolbar = new TableToolbar(content, {
    onRemoveSelected() {
      const ids = fileTable.getSelectedIds();
      for (const id of ids) sendRemoveFile(id);
    },
    onMoveSelected(folderId: string | null) {
      const ids = fileTable.getSelectedIds();
      for (const id of ids) sendMoveFile(id, folderId);
      fileTable.clearSelection();
    },
    onProcessSelected() {
      const ids = fileTable.getSelectedIds();
      if (ids.length > 0) {
        sendOcrPdfs(ids);
        applyOcrLock(true);
      }
    },
    onCancelOcr() {
      sendCancelOcr();
    },
    onFilterChange(text: string) {
      fileTable.setFilter(text);
    },
  });

  const fileTable = new FileTable(content, {
    onSelectionChange(ids: string[]) {
      selectedIds = ids;
      const { selectedHasActiveOcr, anyOcrRunning } = computeToolbarState();
      toolbar.update(ids.length, selectedHasActiveOcr, anyOcrRunning);
    },
  });

  /** Locks/unlocks every mutating control in this view. Folder CRUD is native and locked host-side. */
  function applyOcrLock(locked: boolean): void {
    fileTable.setLocked(locked);
  }

  let currentFiles: FileEntry[] = [];

  function onFilesLoaded(folders: FolderEntry[], files: FileEntry[]): void {
    console.timeEnd("[Talliark] rename round-trip");
    currentFiles = files;
    currentFolders = folders;
    const t0 = performance.now();
    fileTable.update(files, selectedFolderId);
    fileTable.updateFolders(folders);
    toolbar.updateFolders(currentFolders);
    console.log(`[Talliark] DOM update: ${(performance.now() - t0).toFixed(1)}ms`);
  }

  /** The user picked a row in the native sidebar; refilter without touching the folder list. */
  function onFolderSelected(folderId: string | null): void {
    selectedFolderId = folderId;
    fileTable.update(currentFiles, selectedFolderId);
  }

  function onOcrStatus(
    pdfId: string,
    status: string,
    progress: OcrProgress
  ): void {
    if (status === "error") {
      console.error(`[Talliark] OCR error for pdf ${pdfId}:`, progress.message ?? "(no details)");
    }
    // Patch one row so determinate stage progress stays smooth and other rows
    // retain their queued state without a full-table repaint.
    fileTable.updateStatus(pdfId, status, progress);

    const { selectedHasActiveOcr, anyOcrRunning } = computeToolbarState();
    toolbar.update(selectedIds.length, selectedHasActiveOcr, anyOcrRunning);
    applyOcrLock(anyOcrRunning);
  }

  initHostBridge(onFilesLoaded, onFolderSelected, onOcrStatus);

  wireFileManagerUiReset({
    fileTable,
    toolbar,
    setSelectedFolderId: (folderId) => { selectedFolderId = folderId; },
    getCurrentFiles: () => currentFiles,
  });
}
