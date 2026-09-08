import type {
  PdfEntry,
  FolderEntry,
  LinkRectPayload,
  LinkRectUpdatedPayload,
  LinkedRectEntry,
  LinkSelectionEntry,
  LinkType,
  NormalizedRect,
  TableSelectionCopyTarget,
  TableGridData,
} from "./types/index.js";

/**
 * Callbacks invoked for each host→viewer message type.
 * Every handler except `onPdfsLoaded` is optional; unhandled messages are ignored.
 */
export interface HostMessageHandlers {
  onPdfsLoaded: (entries: PdfEntry[], folders: FolderEntry[]) => void;
  onViewerSurfaceShown?: () => void;
  onFoldersUpdated?: (folders: FolderEntry[], assignments: Map<string, string | undefined>) => void;
  onLinkedRectangles?: (rects: LinkedRectEntry[]) => void;
  onLinkedRectangleAdded?: (rect: LinkedRectEntry) => void;
  onNavigateToRectangle?: (id: string, pdfId: string, page: number) => void;
  onClearRectangleHighlight?: () => void;
  onHighlightRectangle?: (id: string) => void;
  onLinkSelectionChanged?: (entries: LinkSelectionEntry[]) => void;
  onSetSearchQuery?: (query: string) => void;
  onSetCharBboxesVisible?: (visible: boolean) => void;
  onSetValuesVisible?: (visible: boolean) => void;
  onSetReferencesVisible?: (visible: boolean) => void;
  onSetStructureVisible?: (visible: boolean) => void;
  onSetValueNoiseVisible?: (visible: boolean) => void;
  onPdfUpdated?: (entry: PdfEntry) => void;
  onLinkRectanglesRemoved?: (ids: string[]) => void;
  onPdfNameUpdated?: (id: string, name: string) => void;
  onPdfRemoved?: (id: string) => void;
  onShowPdf?: (pdfId: string) => void;
  onPageRotationsUpdated?: (pdfId: string, rotations: Record<number, number>) => void;
}

interface PdfPayload {
  id: string;
  name: string;
  base64: string;
  folderId?: string;
  geometryBase64?: string;
  tableStructureBase64?: string;
  documentValuesBase64?: string;
  financialStructureBase64?: string;
  pageRotations?: Record<string, number>;
}

interface FolderPayload {
  id: string;
  name: string;
}

interface PdfsLoadedMessage {
  type: "pdfs-loaded";
  pdfs: PdfPayload[];
  folders?: FolderPayload[];
}

interface ViewerFoldersUpdatedMessage {
  type: "viewer-folders-updated";
  folders?: FolderPayload[];
  assignments?: Array<{ pdfId: string; folderId?: string }>;
}

interface PdfUpdatedMessage {
  type: "pdf-updated";
  pdf: PdfPayload;
}

interface PdfAddedMessage {
  type: "pdf-added";
  pdf: PdfPayload;
}

interface PdfNameUpdatedMessage {
  type: "pdf-name-updated";
  id: string;
  name: string;
}

interface PdfRemovedMessage {
  type: "pdf-removed";
  id: string;
}

interface ShowPdfMessage {
  type: "show-pdf";
  pdfId: string;
}

interface LinkedRectPayload {
  id: string;
  pdfId: string;
  page: number;
  rect: { x: number; y: number; width: number; height: number };
  linkType?: unknown;
  table?: TableGridData;
}

interface LinkedRectanglesLoadedMessage {
  type: "linked-rectangles-loaded";
  rectangles: LinkedRectPayload[];
}

interface LinkedRectangleAddedMessage {
  type: "linked-rectangle-added";
  rectangle: LinkedRectPayload;
}

interface NavigateToRectangleMessage {
  type: "navigate-to-rectangle";
  id: string;
  pdfId: string;
  page: number;
}

interface LinkRectanglesRemovedMessage {
  type: "link-rectangles-removed";
  ids: string[];
}

interface HighlightRectangleMessage {
  type: "highlight-rectangle";
  id: string;
}

interface LinkSelectionEntryPayload {
  id: string;
  pdfId: string;
  pdfName: string;
  page: number;
  value: string;
  valueCount: number;
  cellAddress: string;
  cellValue: string;
}

interface LinkSelectionChangedMessage {
  type: "link-selection-changed";
  entries: LinkSelectionEntryPayload[];
}

interface PageRotationsUpdatedMessage {
  type: "page-rotations-updated";
  pdfId: string;
  rotations: Record<string, number>;
}

/** Tracks object URLs by PDF id so single-document updates can revoke safely. */
const _urlsByPdfId = new Map<string, string>();

function revokeAllUrls(): void {
  for (const url of _urlsByPdfId.values()) {
    URL.revokeObjectURL(url);
  }
  _urlsByPdfId.clear();
}

function revokePdfUrl(pdfId: string): void {
  const existing = _urlsByPdfId.get(pdfId);
  if (!existing) return;
  URL.revokeObjectURL(existing);
  _urlsByPdfId.delete(pdfId);
}

function base64ToObjectUrl(base64: string): string {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  const blob = new Blob([bytes], { type: "application/pdf" });
  return URL.createObjectURL(blob);
}

function toPdfEntry(pdf: PdfPayload): PdfEntry {
  revokePdfUrl(pdf.id);
  const url = base64ToObjectUrl(pdf.base64);
  _urlsByPdfId.set(pdf.id, url);

  let pageRotations: Record<number, number> | undefined;
  if (pdf.pageRotations) {
    const entries = Object.entries(pdf.pageRotations).filter(([, v]) => v !== 0);
    if (entries.length > 0) {
      pageRotations = {};
      for (const [k, v] of entries) pageRotations[parseInt(k, 10)] = v;
    }
  }

  return {
    id:   pdf.id,
    name: pdf.name || pdf.id,
    url,
    ...(pdf.folderId !== undefined ? { folderId: pdf.folderId } : {}),
    ...(pdf.geometryBase64 !== undefined ? { geometryBase64: pdf.geometryBase64 } : {}),
    ...(pdf.tableStructureBase64 !== undefined ? { tableStructureBase64: pdf.tableStructureBase64 } : {}),
    ...(pdf.documentValuesBase64 !== undefined ? { documentValuesBase64: pdf.documentValuesBase64 } : {}),
    ...(pdf.financialStructureBase64 !== undefined ? { financialStructureBase64: pdf.financialStructureBase64 } : {}),
    ...(pageRotations !== undefined ? { pageRotations } : {}),
  };
}

function toFolderEntries(folders: FolderPayload[] | undefined): FolderEntry[] {
  return (folders ?? []).map((folder) => ({
    id:   folder.id,
    name: folder.name || folder.id,
  }));
}

function normalizeLinkType(value: unknown): LinkType {
  return value === "raw" || value === "sum" || value === "table" ? value : "auto";
}

interface SetSearchQueryMessage {
  type: "set-search-query";
  query: string;
}

interface SetCharBboxesVisibleMessage {
  type: "set-char-bboxes-visible";
  visible: boolean;
}

interface SetValuesVisibleMessage {
  type: "set-values-visible";
  visible: boolean;
}

interface SetReferencesVisibleMessage {
  type: "set-references-visible";
  visible?: boolean;
}

interface SetStructureVisibleMessage {
  type: "set-structure-visible";
  visible?: boolean;
}

interface SetValueNoiseVisibleMessage {
  type: "set-value-noise-visible";
  visible: boolean;
}

function toLinkedRectEntry(rect: LinkedRectPayload): LinkedRectEntry {
  return {
    id: rect.id,
    pdfId: rect.pdfId,
    page: rect.page,
    rect: rect.rect as NormalizedRect,
    linkType: normalizeLinkType(rect.linkType),
    ...(rect.table ? { table: rect.table } : {}),
  };
}

function handleMessage(raw: unknown, handlers: HostMessageHandlers): void {
  const {
    onPdfsLoaded,
    onViewerSurfaceShown,
    onFoldersUpdated,
    onLinkedRectangles,
    onLinkedRectangleAdded,
    onNavigateToRectangle,
    onClearRectangleHighlight,
    onHighlightRectangle,
    onLinkSelectionChanged,
    onSetSearchQuery,
    onSetCharBboxesVisible,
    onSetValuesVisible,
    onSetReferencesVisible,
    onSetStructureVisible,
    onSetValueNoiseVisible,
    onPdfUpdated,
    onLinkRectanglesRemoved,
    onPdfNameUpdated,
    onPdfRemoved,
    onShowPdf,
    onPageRotationsUpdated,
  } = handlers;

  try {
    const parsed: unknown =
      typeof raw === "string" ? (JSON.parse(raw) as unknown) : raw;

    if (
      typeof parsed !== "object" ||
      parsed === null
    ) {
      return;
    }

    const type = (parsed as { type?: unknown }).type;

    if (type === "viewer-surface-shown") {
      onViewerSurfaceShown?.();
      return;
    }

    if (type === "pdfs-loaded") {
      const msg = parsed as PdfsLoadedMessage;

      revokeAllUrls();
      const entries: PdfEntry[] = msg.pdfs.map((pdf) => toPdfEntry(pdf));
      onPdfsLoaded(entries, toFolderEntries(msg.folders));
      return;
    }

    if (type === "viewer-folders-updated") {
      if (!onFoldersUpdated) return;
      const msg = parsed as ViewerFoldersUpdatedMessage;
      const assignments = new Map<string, string | undefined>();
      for (const assignment of msg.assignments ?? []) {
        assignments.set(assignment.pdfId, assignment.folderId);
      }
      onFoldersUpdated(toFolderEntries(msg.folders), assignments);
      return;
    }

    if (type === "pdf-updated") {
      if (!onPdfUpdated) return;
      const msg = parsed as PdfUpdatedMessage;
      onPdfUpdated(toPdfEntry(msg.pdf));
      return;
    }

    if (type === "linked-rectangles-loaded") {
      if (!onLinkedRectangles) return;
      const lrMsg = parsed as LinkedRectanglesLoadedMessage;
      const rects = lrMsg.rectangles.map(toLinkedRectEntry);
      onLinkedRectangles(rects);
      return;
    }

    if (type === "linked-rectangle-added") {
      if (!onLinkedRectangleAdded) return;
      const added = parsed as LinkedRectangleAddedMessage;
      onLinkedRectangleAdded(toLinkedRectEntry(added.rectangle));
      return;
    }

    if (type === "navigate-to-rectangle") {
      if (!onNavigateToRectangle) return;
      const navMsg = parsed as NavigateToRectangleMessage;
      onNavigateToRectangle(navMsg.id, navMsg.pdfId, navMsg.page);
      return;
    }

    if (type === "highlight-rectangle") {
      if (!onHighlightRectangle) return;
      const msg = parsed as HighlightRectangleMessage;
      onHighlightRectangle(msg.id);
      return;
    }

    if (type === "link-selection-changed") {
      if (!onLinkSelectionChanged) return;
      const msg = parsed as LinkSelectionChangedMessage;
      const entries: LinkSelectionEntry[] = (msg.entries ?? []).map((e) => ({
        id:          e.id,
        pdfId:       e.pdfId,
        pdfName:     e.pdfName,
        page:        e.page,
        value:       e.value,
        valueCount:  e.valueCount,
        cellAddress: e.cellAddress,
        cellValue:   e.cellValue,
      }));
      onLinkSelectionChanged(entries);
      return;
    }

    if (type === "set-search-query") {
      if (!onSetSearchQuery) return;
      const msg = parsed as SetSearchQueryMessage;
      onSetSearchQuery(typeof msg.query === "string" ? msg.query : "");
      return;
    }

    if (type === "set-char-bboxes-visible") {
      if (!onSetCharBboxesVisible) return;
      const msg = parsed as SetCharBboxesVisibleMessage;
      onSetCharBboxesVisible(msg.visible === true);
      return;
    }

    if (type === "set-values-visible") {
      if (!onSetValuesVisible) return;
      const msg = parsed as SetValuesVisibleMessage;
      onSetValuesVisible(msg.visible === true);
      return;
    }

    if (type === "set-references-visible") {
      if (!onSetReferencesVisible) return;
      const msg = parsed as SetReferencesVisibleMessage;
      onSetReferencesVisible(msg.visible === true);
      return;
    }

    if (type === "set-structure-visible") {
      if (!onSetStructureVisible) return;
      const msg = parsed as SetStructureVisibleMessage;
      onSetStructureVisible(msg.visible === true);
      return;
    }

    if (type === "set-value-noise-visible") {
      if (!onSetValueNoiseVisible) return;
      const msg = parsed as SetValueNoiseVisibleMessage;
      onSetValueNoiseVisible(msg.visible === true);
      return;
    }

    if (type === "clear-rectangle-highlight") {
      onClearRectangleHighlight?.();
      return;
    }

    if (type === "link-rectangles-removed") {
      if (!onLinkRectanglesRemoved) return;
      const removedMsg = parsed as LinkRectanglesRemovedMessage;
      onLinkRectanglesRemoved(removedMsg.ids);
      return;
    }

    if (type === "pdf-added") {
      if (!onPdfUpdated) return;
      const msg = parsed as PdfAddedMessage;
      onPdfUpdated(toPdfEntry(msg.pdf));
      return;
    }

    if (type === "pdf-name-updated") {
      const msg = parsed as PdfNameUpdatedMessage;
      onPdfNameUpdated?.(msg.id, msg.name);
      return;
    }

    if (type === "pdf-removed") {
      const msg = parsed as PdfRemovedMessage;
      revokePdfUrl(msg.id);
      onPdfRemoved?.(msg.id);
      return;
    }

    if (type === "show-pdf") {
      const msg = parsed as ShowPdfMessage;
      onShowPdf?.(msg.pdfId);
      return;
    }

    if (type === "page-rotations-updated") {
      if (!onPageRotationsUpdated) return;
      const msg = parsed as PageRotationsUpdatedMessage;
      const rotations: Record<number, number> = {};
      for (const [k, v] of Object.entries(msg.rotations)) rotations[parseInt(k, 10)] = v;
      onPageRotationsUpdated(msg.pdfId, rotations);
      return;
    }
  } catch {
    // Malformed JSON or unexpected shape — silently ignore.
  }
}

interface WebView2Bridge extends EventTarget {
  postMessage(message: string): void;
}

let _webview: WebView2Bridge | null = null;

function postToHost(message: object): void {
  _webview?.postMessage(JSON.stringify(message));
}

/** Keep detector-only editing hints inside the viewer; the host contract stores the grid. */
function toHostTable(table: TableGridData): TableGridData {
  return {
    columnBoundaries: [...table.columnBoundaries],
    rowBoundaries: [...table.rowBoundaries],
    ...(table.cells ? { cells: table.cells.map((row) => [...row]) } : {}),
  };
}

/**
 * Registers a listener for host messages from the WebView2 C# add-in.
 * The provided callback is invoked with a fresh set of PdfEntry objects
 * each time the host pushes an updated PDF list.
 *
 * Sends a `viewer-ready` handshake immediately after registering so the host
 * knows JavaScript is initialized and can safely post the initial PDF list.
 *
 * Safe to call in non-WebView2 environments — does nothing when
 * `window.chrome.webview` is absent.
 */
export function initHostBridge(handlers: HostMessageHandlers): void {
  const webview = (
    window as unknown as { chrome?: { webview?: WebView2Bridge } }
  ).chrome?.webview;

  if (!webview) {
    return;
  }

  _webview = webview;

  webview.addEventListener("message", (event: Event) => {
    handleMessage((event as MessageEvent<unknown>).data, handlers);
  });

  postToHost({ type: "viewer-ready" });
}

export function sendLinkRectangleClicked(id: string): void {
  postToHost({ type: "link-rectangle-clicked", id });
}

export function sendLinkRectangleDeleted(id: string, deleteCellData: boolean): void {
  postToHost({ type: "link-rectangle-deleted", id, deleteCellData });
}

export function sendCopyTableSelection(
  id: string,
  targets: TableSelectionCopyTarget[],
): void {
  postToHost({ type: "copy-table-selection", id, targets });
}

export function sendLinkRectangleCreated(payload: LinkRectPayload): void {
  postToHost({
    type:     "link-rectangle-created",
    pdfId:    payload.pdfId,
    page:     payload.page,
    rect:     payload.rect,
    text:     payload.text,
    linkType: payload.linkType ?? "auto",
    appendToActiveSum: payload.appendToActiveSum === true,
    ...(payload.table ? { table: toHostTable(payload.table) } : {}),
  });
}

export function sendLinkRectangleUpdated(payload: LinkRectUpdatedPayload): void {
  postToHost({
    type:  "link-rectangle-updated",
    id:    payload.id,
    pdfId: payload.pdfId,
    page:  payload.page,
    rect:  payload.rect,
    text:  payload.text,
    ...(payload.table ? { table: toHostTable(payload.table) } : {}),
  });
}

/**
 * Forwards a cell-navigation keystroke to the Excel grid. The viewer reports only
 * which key was pressed; the host resolves direction, the move-after-return setting,
 * the Tab-run anchor and multi-cell selection cycling.
 */
export function sendExcelNavigate(motion: "tab" | "enter", reverse: boolean): void {
  postToHost({ type: "excel-navigate", motion, reverse });
}

/** Asks the host to undo Excel's latest native action or eligible link creation. */
export function sendUndoLinkCreation(): void {
  postToHost({ type: "undo-link-creation" });
}

export function sendRotatePage(pdfId: string, page: number, direction: "cw" | "ccw"): void {
  postToHost({ type: "rotate-page", pdfId, page, direction });
}

export function sendCacheBuildStarted(): void {
  postToHost({ type: "cache-build-started" });
}

export function sendCacheBuildComplete(): void {
  postToHost({ type: "cache-build-complete" });
}

export function sendViewerContentReady(): void {
  postToHost({ type: "viewer-content-ready" });
}

export function sendOpenFileManager(): void {
  postToHost({ type: "open-file-manager" });
}
