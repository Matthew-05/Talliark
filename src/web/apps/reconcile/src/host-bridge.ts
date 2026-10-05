/**
 * The WebView2 postMessage channel, as contracts/webview-messages-v1.json
 * defines it for this app.
 *
 * The window opens against the workbook — not a sheet, not a selection, not the
 * viewer — so nothing about Excel's current state crosses this boundary. As in
 * every Talliark web app, the host does the work: this file sends intent and
 * receives facts, and never touches a file path or reaches for Python.
 */
import type {
  ReconcileDocument,
  ScanProgress,
  ScanStatus,
  Staleness,
  ReconcileImportSource,
  ReconcileDocumentRole,
} from "./types/index.js";
import type { ReviewRequest, ReviewResponse } from "./types/reconcile-review.generated.js";

// ── Inbound (host → web) ─────────────────────────────────────────────────────

interface ReconcileDataLoadedMessage {
  type: "reconcile-data-loaded";
  projectName?: string;
  documents: ReconcileDocument[];
  sources?: ReconcileImportSource[];
  /** Set when a scan is already running as this app mounts. */
  scanning?: string;
}

interface ReconcileScanStatusMessage {
  type: "reconcile-scan-status";
  pdfId: string;
  status: ScanStatus;
  message?: string;
  stage?: string;
  current?: number;
  total?: number;
  unit?: string;
}

interface ReconcileResultLoadedMessage {
  type: "reconcile-result-loaded";
  pdfId: string;
  pdfBase64?: string;
  pageRotations?: Record<number, number>;
  reconcileBase64?: string;
  staleness?: Staleness;
}

type HostMessage =
  | ReconcileDataLoadedMessage
  | ReconcileScanStatusMessage
  | ReconcileResultLoadedMessage;

// ── Outbound (web → host) ────────────────────────────────────────────────────

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

export interface HostHandlers {
  onReviewResponse?(response: ReviewResponse): void;
  onDataLoaded(
    documents: ReconcileDocument[],
    sources: ReconcileImportSource[],
    scanning: string | null,
    projectName: string,
  ): void;
  onScanStatus(pdfId: string, status: ScanStatus, progress: ScanProgress): void;
  onResultLoaded?(
    pdfId: string,
    pdfBase64: string | null,
    pageRotations: Record<number, number>,
    reconcileBase64: string | null,
    staleness: Staleness,
  ): void;
}

export function initHostBridge(handlers: HostHandlers): void {
  const webview = getWebView();
  if (!webview) return;

  webview.addEventListener("message", (event: Event) => {
    const raw = (event as MessageEvent<unknown>).data;
    let msg: HostMessage | ReviewResponse;
    try {
      const parsed: unknown =
        typeof raw === "string" ? (JSON.parse(raw) as unknown) : raw;
      if (typeof parsed !== "object" || parsed === null) return;
      msg = parsed as HostMessage | ReviewResponse;
    } catch {
      return;
    }

    if (msg.type === "reconcile-review-response") {
      handlers.onReviewResponse?.(msg);
    } else if (msg.type === "reconcile-data-loaded") {
      handlers.onDataLoaded(
        msg.documents ?? [],
        msg.sources ?? [],
        msg.scanning ?? null,
        msg.projectName ?? "",
      );
    } else if (msg.type === "reconcile-scan-status") {
      handlers.onScanStatus(msg.pdfId, msg.status, {
        message: msg.message,
        stage: msg.stage,
        current: msg.current,
        total: msg.total,
        unit: msg.unit,
      });
    } else if (msg.type === "reconcile-result-loaded") {
      handlers.onResultLoaded?.(
        msg.pdfId,
        msg.pdfBase64 ?? null,
        msg.pageRotations ?? {},
        msg.reconcileBase64 ?? null,
        msg.staleness ?? "none",
      );
    }
  });

  send({ type: "reconcile-ready" });
}

/**
 * Starts a scan of one document.
 *
 * A scan never starts on its own — this call is the only thing that begins one,
 * and it exists because the user pressed a button.
 */
export function sendRunScan(pdfId: string): void {
  send({ type: "run-reconcile-scan", pdfId });
}

export function sendReviewRequest(request: ReviewRequest): void {
  send(request);
}

/** Cancels the running scan. A cancelled scan leaves a stored result untouched. */
export function sendCancelScan(): void {
  send({ type: "cancel-reconcile-scan" });
}

/**
 * Asks for one document's stored sum tree.
 *
 * The workspace snapshot carries only counts, so the full tree is fetched only
 * when the completed project opens its result.
 */
export function sendRequestResult(pdfId: string): void {
  send({ type: "request-reconcile-result", pdfId });
}

export function sendImportDocument(role: ReconcileDocumentRole): void {
  send({ type: "import-reconcile-document", role });
}
export function sendCopyDocument(role: ReconcileDocumentRole, sourcePdfId: string): void {
  send({ type: "copy-reconcile-document", role, sourcePdfId });
}
export function sendCompleteSetup(projectName: string): void {
  send({ type: "complete-reconcile-setup", projectName });
}
