/**
 * The Reconcile window.
 *
 * Per-workbook, one document at a time. R-0 is the skeleton: the app lists the
 * workbook's documents, starts and watches a scan, and shows the empty home and
 * the splash. Findings are R-2's — until then a scanned document reports what
 * was examined and how much of it tied, which is the whole of what the engine
 * currently knows.
 */
import {
  initHostBridge,
  sendCancelScan,
  sendRequestResult,
  sendRunScan,
} from "./host-bridge.js";
import { DocumentList } from "./components/document-list/document-list.js";
import { ScanProgressPanel } from "./components/scan-progress/scan-progress.js";
import { Splash } from "./components/splash/splash.js";
import type { ReconcileDocument, ScanProgress, ScanStatus } from "./types/index.js";

export function mountApp(root: HTMLElement): void {
  root.className = "reconcile";

  let documents: ReconcileDocument[] = [];
  let scanningId: string | null = null;

  const splash = new Splash(root);

  const documentList = new DocumentList(root, {
    onScan(pdfId: string) {
      // A scan never starts on its own. This is the only path that begins one.
      scanningId = pdfId;
      sendRunScan(pdfId);
      render();
    },
    onOpen(pdfId: string) {
      sendRequestResult(pdfId);
    },
  });

  const scanProgress = new ScanProgressPanel(root, {
    onCancel() {
      sendCancelScan();
    },
  });

  function nameOf(pdfId: string): string {
    return documents.find((entry) => entry.id === pdfId)?.name ?? "";
  }

  function render(): void {
    // An empty workbook gets the splash; anything else gets the home. A scan in
    // flight adds the progress panel above the list rather than replacing it,
    // so the other documents stay legible while one is being checked.
    const empty = documents.length === 0;
    splash.setVisible(empty);
    documentList.setVisible(!empty);
    documentList.setScanning(scanningId);
    scanProgress.setVisible(scanningId !== null);
  }

  initHostBridge({
    onDataLoaded(loaded, _folders, scanning) {
      documents = loaded;
      documentList.update(documents);
      // The host is the authority on whether a scan is running, so an app that
      // mounts mid-scan picks it up rather than showing an idle home.
      scanningId = scanning;
      render();
    },

    onScanStatus(pdfId: string, status: ScanStatus, progress: ScanProgress) {
      if (status === "complete" || status === "cancelled" || status === "error") {
        if (status === "error") {
          console.error(
            `[Talliark] reconcile scan failed for ${pdfId}:`,
            progress.message ?? "(no details)",
          );
        }
        // A cancelled or failed scan leaves any previously stored result
        // untouched; the host follows a completion with fresh data, so nothing
        // here edits the list.
        scanningId = null;
        render();
        return;
      }

      scanningId = pdfId;
      render();
      scanProgress.update(nameOf(pdfId), status, progress);
    },
  });

  render();
}
