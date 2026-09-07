import { PdfViewer as SharedPdfViewer } from "@talliark/shared";
import { createEmptyState } from "../empty-state/empty-state.js";

/** The shared PDF surface with the linking app's manage-files empty state. */
export class PdfViewer extends SharedPdfViewer {
  private readonly manageFilesCallbacks: Array<() => void> = [];

  onManageFilesRequested(callback: () => void): void {
    this.manageFilesCallbacks.push(callback);
  }

  showNoPdfsState(): void {
    this.showEmptyState(createEmptyState(() => {
      for (const callback of this.manageFilesCallbacks) callback();
    }));
  }
}
