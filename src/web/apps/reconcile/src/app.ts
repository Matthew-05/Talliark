/**
 * The Reconcile window.
 *
 * Per-workbook, one primary document at a time. An incomplete workspace opens
 * in setup; a completed workspace opens its stored result as the starting point
 * for review. The review workspace keeps its findings beside the source PDF and
 * leaves room for the later consistency views.
 */
import {
  initHostBridge,
  sendCancelScan,
  sendRequestResult,
  sendRunScan,
  sendImportDocument,
  sendCopyDocument,
  sendCompleteSetup,
} from "./host-bridge.js";
import { ScanProgressPanel } from "./components/scan-progress/scan-progress.js";
import { completedPrimary, SetupWizard } from "./components/setup-wizard/setup-wizard.js";
import { ResultView } from "./components/result-view/result-view.js";
import { decodeReconcileResult } from "./services/reconcile-result-decoder.js";
import type { ReconcileDocument, ReconcileDocumentRole, ReconcileImportSource, ScanProgress, ScanStatus } from "./types/index.js";

export function mountApp(root: HTMLElement): void {
  root.className = "reconcile";

  let documents: ReconcileDocument[] = [];
  let scanningId: string | null = null;
  let selectedId: string | null = null;
  let projectName = "";

  const intake = {
    onImport: (role: ReconcileDocumentRole) => sendImportDocument(role),
    onCopy: (role: ReconcileDocumentRole, sourcePdfId: string) => sendCopyDocument(role, sourcePdfId),
  };
  const setupWizard = new SetupWizard(root, {
    ...intake,
    onComplete: sendCompleteSetup,
  });

  const scanProgress = new ScanProgressPanel(root, {
    onCancel() {
      sendCancelScan();
    },
  });

  const resultView = new ResultView(root, {
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
    // There is no project home between setup and review. Once setup has been
    // completed, the primary statement's result is the app's landing screen.
    const setupComplete = completedPrimary(documents, projectName) !== null;
    setupWizard.setVisible(!setupComplete);
    resultView.setVisible(setupComplete);
    scanProgress.setVisible(scanningId !== null);
  }

  initHostBridge({
    onDataLoaded(loaded, sources: ReconcileImportSource[], scanning, loadedProjectName) {
      documents = loaded;
      projectName = loadedProjectName;
      setupWizard.update(documents, sources, projectName);
      // The host is the authority on whether a scan is running, so an app that
      // mounts mid-scan picks it up rather than showing an idle home.
      scanningId = scanning;
      const primary = completedPrimary(documents, projectName);
      if (!primary) {
        selectedId = null;
      } else if (selectedId !== primary.id) {
        selectedId = primary.id;
        if (primary.summary && primary.staleness !== "none") {
          resultView.showLoading(primary);
          sendRequestResult(primary.id);
        } else {
          resultView.showError(primary, "No stored scan result is available for this document. Re-scan to create one.");
        }
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
