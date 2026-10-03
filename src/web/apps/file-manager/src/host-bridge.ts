import type { FileEntry, FolderEntry } from "./types/index.js";

// ── Inbound (host → web) ──────────────────────────────────────────────────────

interface FilesLoadedMessage {
  type: "files-loaded";
  folders: FolderEntry[];
  files: FileEntry[];
}

interface OcrStatusMessage {
  type: "ocr-status";
  pdfId: string;
  status: "queued" | "processing" | "ocr" | "error" | "none" | "text";
  message?: string;
  stage?: string;
  current?: number;
  total?: number;
  unit?: string;
  fileIndex?: number;
  fileCount?: number;
}

/**
 * The user picked a row in the native folder sidebar. `folderId` is absent for All Files,
 * which filters to every document rather than to none.
 */
interface FolderSelectedMessage {
  type: "folder-selected";
  folderId?: string;
}

/**
 * Progress for one file, as webview-messages-v1 defines it.
 *
 * `stage` is stated by the host; `message` is prose beside it and must not be
 * parsed. `current`/`total` count within `stage` only — they are not progress
 * through the file. `fileIndex`/`fileCount` describe the run.
 */
export interface OcrProgress {
  message?: string | undefined;
  stage?: string | undefined;
  current?: number | undefined;
  total?: number | undefined;
  unit?: string | undefined;
  fileIndex?: number | undefined;
  fileCount?: number | undefined;
}

interface ResetUiMessage {
  type: "reset-ui";
}

/**
 * The host has finished the row drag the file table asked for — moved, refused or
 * abandoned. The table cannot detect this itself: from the moment the drag starts the host
 * holds the mouse, so no further web event reports the release.
 */
interface RowDragEndedMessage {
  type: "row-drag-ended";
}

type HostMessage =
  | FilesLoadedMessage
  | FolderSelectedMessage
  | OcrStatusMessage
  | ResetUiMessage
  | RowDragEndedMessage;

// ── Outbound (web → host) ─────────────────────────────────────────────────────

interface WebView2Bridge extends EventTarget {
  postMessage(message: string): void;
}

function getWebView(): WebView2Bridge | null {
  return (
    (window as unknown as { chrome?: { webview?: WebView2Bridge } }).chrome
      ?.webview ?? null
  );
}

function send(msg: object): void {
  getWebView()?.postMessage(JSON.stringify(msg));
}

let _onResetUi: (() => void) | null = null;

// ── Public API ────────────────────────────────────────────────────────────────

export function registerUiResetHandler(handler: () => void): void {
  _onResetUi = handler;
}

export function initHostBridge(
  onFilesLoaded: (folders: FolderEntry[], files: FileEntry[]) => void,
  onFolderSelected?: (folderId: string | null) => void,
  onOcrStatus?: (pdfId: string, status: string, progress: OcrProgress) => void,
  onRowDragEnded?: () => void
): void {
  const webview = getWebView();
  if (!webview) return;

  webview.addEventListener("message", (event: Event) => {
    const raw = (event as MessageEvent<unknown>).data;
    let msg: HostMessage;
    try {
      const parsed: unknown =
        typeof raw === "string" ? (JSON.parse(raw) as unknown) : raw;
      if (typeof parsed !== "object" || parsed === null) return;
      msg = parsed as HostMessage;
    } catch {
      return;
    }

    if (msg.type === "files-loaded") {
      console.log(`[Talliark] files-loaded received: ${msg.files.length} files`);
      onFilesLoaded(msg.folders, msg.files);
    } else if (msg.type === "folder-selected" && onFolderSelected) {
      onFolderSelected(msg.folderId ?? null);
    } else if (msg.type === "ocr-status" && onOcrStatus) {
      onOcrStatus(msg.pdfId, msg.status, {
        message: msg.message,
        stage: msg.stage,
        current: msg.current,
        total: msg.total,
        unit: msg.unit,
        fileIndex: msg.fileIndex,
        fileCount: msg.fileCount,
      });
    } else if (msg.type === "reset-ui") {
      _onResetUi?.();
    } else if (msg.type === "row-drag-ended") {
      onRowDragEnded?.();
    }
  });

  send({ type: "manager-ready" });
}

export function sendRenameFile(id: string, newName: string): void {
  console.time("[Talliark] rename round-trip");
  send({ type: "rename-file", id, newName });
}

export function sendRemoveFile(id: string): void {
  send({ type: "remove-file", id });
}

export function sendExportFile(id: string): void {
  send({ type: "export-file", id });
}

/**
 * Tells the host which single file the user just selected, so an open document
 * viewer can swap to it. Sent on selection only — never on deselection, and not
 * for multi-row range selections, where no single document is implied.
 */
export function sendSelectFile(id: string): void {
  send({ type: "select-file", id });
}

export function sendMoveFile(id: string, folderId: string | null): void {
  const msg: Record<string, unknown> = { type: "move-file", id };
  if (folderId) msg["folderId"] = folderId;
  send(msg);
}

/**
 * Reports a drag of file rows toward the native folder sidebar.
 *
 * This is a notice, not a request for a result: the host takes the mouse, decides the
 * destination, performs the move itself and answers with `row-drag-ended`. A drag started
 * while the host refuses it — OCR running, nothing selected — ends in the same message, so
 * the table never has to guess whether its drag is still live.
 */
export function sendRowDragStarted(ids: string[]): void {
  send({ type: "row-drag-started", ids });
}

export function sendOcrPdfs(pdfIds: string[]): void {
  send({ type: "ocr-pdfs", pdfIds });
}

export function sendCancelOcr(): void {
  send({ type: "cancel-ocr" });
}
