import type { FileEntry } from "./types/index.js";
import type { FileTable } from "./components/file-table/file-table.js";
import type { TableToolbar } from "./components/table-toolbar/table-toolbar.js";
import { registerUiResetHandler } from "./host-bridge.js";

export interface FileManagerUiHandles {
  fileTable: FileTable;
  toolbar: TableToolbar;
  setSelectedFolderId: (folderId: string | null) => void;
  getCurrentFiles: () => FileEntry[];
}

/**
 * Clears transient UI state (selection, filters, folder pick) back to defaults.
 *
 * The native sidebar is reset host-side in the same breath, so the next `folder-selected`
 * this app receives will already be the All Files row. The local clear keeps the table
 * correct in the window before that message arrives.
 */
export function resetFileManagerUi(handles: FileManagerUiHandles): void {
  handles.setSelectedFolderId(null);
  handles.fileTable.reset();
  handles.toolbar.reset();
  handles.fileTable.update(handles.getCurrentFiles(), null);
}

/** Listens for the host `reset-ui` message and runs {@link resetFileManagerUi}. */
export function wireFileManagerUiReset(handles: FileManagerUiHandles): void {
  registerUiResetHandler(() => resetFileManagerUi(handles));
}
