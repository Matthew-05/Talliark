/**
 * The Reconcile window.
 *
 * Per-workbook, one document at a time. The app lists the workbook's documents,
 * starts and watches a scan, and opens the stored result as the starting point
 * for a document review. The review workspace keeps its findings beside the
 * source PDF and leaves room for the later consistency views.
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
import { ResultView } from "./components/result-view/result-view.js";
import { decodeReconcileResult } from "./services/reconcile-result-decoder.js";
import type { ReconcileDocument, ScanProgress, ScanStatus } from "./types/index.js";

export function mountApp(root: HTMLElement): void {
  root.className = "reconcile";

  let documents: ReconcileDocument[] = [];
  let scanningId: string | null = null;
  let selectedId: string | null = null;

  const splash = new Splash(root);

  const documentList = new DocumentList(root, {
    onScan(pdfId: string) {
      // A scan never starts on its own. This is the only path that begins one.
      scanningId = pdfId;
      sendRunScan(pdfId);
      render();
    },
    onOpen(pdfId: string) {
      selectedId = pdfId;
      const entry = documents.find((document_) => document_.id === pdfId);
      if (entry) resultView.showLoading(entry);
      sendRequestResult(pdfId);
      render();
    },
  });

  const scanProgress = new ScanProgressPanel(root, {
    onCancel() {
      sendCancelScan();
    },
  });

  const resultView = new ResultView(root, {
    onBack() {
      selectedId = null;
      render();
    },
    onRescan(pdfId: string) {
      scanningId = pdfId;
      sendRunScan(pdfId);
      render();
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
    const showingResult = selectedId !== null && !empty;
    splash.setVisible(empty);
    documentList.setVisible(!empty && !showingResult);
    resultView.setVisible(showingResult);
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
      if (selectedId !== null && !documents.some((entry) => entry.id === selectedId)) {
        selectedId = null;
      }
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
        if (status === "complete") {
          selectedId = pdfId;
          const entry = documents.find((document_) => document_.id === pdfId);
          if (entry) resultView.showLoading(entry);
          sendRequestResult(pdfId);
        }
        render();
        return;
      }

      scanningId = pdfId;
      render();
      scanProgress.update(nameOf(pdfId), status, progress);
    },

    async onResultLoaded(pdfId, pdfBase64, pageRotations, reconcileBase64, staleness) {
      if (selectedId !== pdfId) return;
      const existing = documents.find((entry) => entry.id === pdfId);
      if (!existing) return;
      const entry: ReconcileDocument = { ...existing, staleness };
      if (!reconcileBase64) {
        resultView.showError(entry, "No stored scan result is available for this document.");
        return;
      }
      try {
        resultView.showResult(
          entry,
          await decodeReconcileResult(reconcileBase64),
          pdfBase64,
          pageRotations,
        );
      } catch (error) {
        console.error("[Talliark] could not decode Reconcile result:", error);
        resultView.showError(entry, "The stored scan result could not be opened.");
      }
    },
  });

  render();
}
