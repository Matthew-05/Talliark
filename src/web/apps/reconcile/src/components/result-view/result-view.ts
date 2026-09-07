import { FindingsSidebar } from "../findings-sidebar/findings-sidebar.js";
import { ReconcileViewer } from "../reconcile-viewer/reconcile-viewer.js";
import { ResultsShell, type ReviewMode } from "../results-shell/results-shell.js";
import type {
  Bounds,
  ReconcileDocument,
  ReconcileFinding,
  ReconcileModel,
} from "../../types/index.js";

export interface ResultViewCallbacks {
  onBack(): void;
  onRescan(pdfId: string): void;
}

/** Opens a scan as a review workspace, rather than a terminal report. */
export class ResultView {
  private readonly element: HTMLElement;
  private viewer: ReconcileViewer | null = null;
  private sidebar: FindingsSidebar | null = null;
  private model: ReconcileModel | null = null;

  constructor(parent: HTMLElement, private readonly callbacks: ResultViewCallbacks) {
    this.element = document.createElement("section");
    this.element.className = "result-view";
    parent.appendChild(this.element);
    this.setVisible(false);
  }

  setVisible(visible: boolean): void {
    this.element.hidden = !visible;
  }

  showLoading(entry: ReconcileDocument): void {
    this.model = null;
    this.renderWorkspace(entry, this.state("Loading scan and source document…"));
  }

  showError(entry: ReconcileDocument, message: string): void {
    this.model = null;
    this.renderWorkspace(entry, this.state(message, true));
    this.viewer?.showUnavailable();
  }

  showResult(
    entry: ReconcileDocument,
    model: ReconcileModel,
    pdfBase64: string | null,
    pageRotations: Record<number, number>,
  ): void {
    this.model = model;
    this.renderWorkspace(entry);
    this.showMode("mathematical");
    if (pdfBase64) void this.viewer?.load(pdfBase64, pageRotations, entry.id, entry.name);
    else this.viewer?.showUnavailable();
  }

  private renderWorkspace(entry: ReconcileDocument, content?: HTMLElement): void {
    const shell = new ResultsShell(entry, {
      onBack: () => this.callbacks.onBack(),
      onRescan: (pdfId) => this.callbacks.onRescan(pdfId),
      onModeChanged: (mode) => this.showMode(mode),
    });
    this.viewer = new ReconcileViewer();
    shell.viewerSlot.appendChild(this.viewer.element);
    if (content) shell.sidebarSlot.appendChild(content);
    this.element.replaceChildren(shell.element);
  }

  private showMode(mode: ReviewMode): void {
    const model = this.model;
    const viewer = this.viewer;
    const slot = this.element.querySelector<HTMLElement>(".results-shell__sidebar-slot");
    if (!model || !viewer || !slot) return;

    this.sidebar = new FindingsSidebar({
      onSelect: (finding) => viewer.focus(focusForFinding(model, finding)),
    });
    slot.replaceChildren(this.sidebar.element);
    if (mode === "mathematical") {
      this.sidebar.show(model);
      return;
    }
    this.sidebar.showUnavailable(
      mode === "intra-document"
        ? "Intra Document Consistency"
        : "Inter Document Consistency",
    );
  }

  private state(message: string, error = false): HTMLElement {
    const state = document.createElement("div");
    state.className = `result-state${error ? " result-state--error" : ""}`;
    state.textContent = message;
    return state;
  }
}

function focusForFinding(model: ReconcileModel, finding: ReconcileFinding): {
  pageIndex: number;
  bounds: Bounds[];
} {
  const table = model.tables.find((candidate) => candidate.id === finding.tableId);
  const total = table?.totals.find((candidate) => candidate.id === finding.totalId);
  if (!table || !total) {
    return { pageIndex: finding.pageIndex ?? 0, bounds: [] };
  }
  const ids = new Set([total.cellId, ...(total.resolution?.addendCellIds ?? [])]);
  return {
    pageIndex: table.pageIndex,
    bounds: table.cells.filter((cell) => ids.has(cell.id)).map((cell) => cell.bounds),
  };
}
