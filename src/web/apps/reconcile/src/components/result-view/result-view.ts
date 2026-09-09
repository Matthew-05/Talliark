import { FindingsSidebar } from "../findings-sidebar/findings-sidebar.js";
import { ReconcileViewer } from "../reconcile-viewer/reconcile-viewer.js";
import { ResultsShell, type ReviewMode } from "../results-shell/results-shell.js";
import { SumTree } from "../sum-tree/sum-tree.js";
import type {
  Bounds,
  ReconcileDocument,
  ReconcileFinding,
  ReconcileModel,
  ReconcileTable,
  ReconcileTotal,
} from "../../types/index.js";

export interface ResultViewCallbacks {
  onRescan(pdfId: string): void;
}

/** Opens a scan as a review workspace, rather than a terminal report. */
export class ResultView {
  private readonly element: HTMLElement;
  private viewer: ReconcileViewer | null = null;
  private sidebar: FindingsSidebar | null = null;
  private sumTree: SumTree | null = null;
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

  setScanning(scanning: boolean): void {
    for (const button of this.element.querySelectorAll<HTMLButtonElement>(
      ".result-state__action, .results-shell__actions .button",
    )) {
      button.disabled = scanning;
    }
  }

  showLoading(entry: ReconcileDocument): void {
    this.model = null;
    this.renderOverviewState(
      entry,
      "loading",
      "Opening your scan",
      "Loading the results and source document…",
    );
  }

  showInitialScan(entry: ReconcileDocument): HTMLElement {
    this.model = null;
    this.viewer = null;
    this.sidebar = null;
    this.sumTree = null;
    const shell = new ResultsShell(entry, {
      onRescan: (pdfId) => this.callbacks.onRescan(pdfId),
      onModeChanged: () => undefined,
    }, { layout: "overview", showRescan: false, scanning: true });
    this.element.replaceChildren(shell.element);
    return shell.overviewSlot!;
  }

  showError(entry: ReconcileDocument, message: string): void {
    this.model = null;
    const isEmpty = entry.staleness === "none";
    this.renderOverviewState(
      entry,
      isEmpty ? "empty" : "error",
      isEmpty ? "No scan results yet" : "We couldn’t open this scan",
      message,
    );
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

  private renderWorkspace(entry: ReconcileDocument): void {
    const shell = new ResultsShell(entry, {
      onRescan: (pdfId) => this.callbacks.onRescan(pdfId),
      onModeChanged: (mode) => this.showMode(mode),
    });
    this.viewer = new ReconcileViewer();
    shell.viewerSlot.appendChild(this.viewer.element);
    this.element.replaceChildren(shell.element);
  }

  private renderOverviewState(
    entry: ReconcileDocument,
    kind: "loading" | "empty" | "error",
    title: string,
    message: string,
  ): void {
    this.viewer = null;
    this.sidebar = null;
    this.sumTree = null;
    const shell = new ResultsShell(entry, {
      onRescan: (pdfId) => this.callbacks.onRescan(pdfId),
      onModeChanged: () => undefined,
    }, { layout: "overview", showRescan: false });
    shell.overviewSlot?.appendChild(this.state(entry, kind, title, message));
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
    this.sumTree = new SumTree({
      onSelect: (table, total, overview) => {
        if (overview) viewer.focusCategory(total.id, table.pageIndex);
        viewer.focus(focusForTotal(table, total));
      },
      onClear: () => viewer.clearFocus(),
      onOverview: (items) => viewer.showCategory(
        items,
        (totalId) => {
          this.sumTree?.focus(totalId);
          const target = totalById(model, totalId);
          if (target) viewer.focus(focusForTotal(target.table, target.total));
        },
      ),
    });
    if (mode === "mathematical") {
      slot.replaceChildren(this.sumTree.element);
      this.sumTree.show(model);
      return;
    }
    slot.replaceChildren(this.sidebar.element);
    this.sumTree.clear();
    this.sidebar.showUnavailable(
      mode === "intra-document"
        ? "Intra Document Consistency"
        : "Inter Document Consistency",
    );
  }

  private state(
    entry: ReconcileDocument,
    kind: "loading" | "empty" | "error",
    titleText: string,
    message: string,
  ): HTMLElement {
    const state = document.createElement("div");
    state.className = `result-state result-state--${kind}`;
    if (kind === "loading") {
      state.setAttribute("role", "status");
      state.setAttribute("aria-live", "polite");
    }

    const visual = document.createElement("div");
    visual.className = "result-state__visual";
    visual.setAttribute("aria-hidden", "true");
    const documentIcon = document.createElement("span");
    documentIcon.className = "result-state__document-icon";
    visual.appendChild(documentIcon);
    if (kind === "loading") {
      const spinner = document.createElement("span");
      spinner.className = "result-state__spinner";
      visual.appendChild(spinner);
    }

    const title = document.createElement("h2");
    title.textContent = titleText;
    const description = document.createElement("p");
    description.textContent = message;
    state.append(visual, title, description);

    if (kind !== "loading") {
      const scan = document.createElement("button");
      scan.type = "button";
      scan.className = "button button--primary result-state__action";
      scan.textContent = kind === "empty" ? "Scan document" : "Scan again";
      scan.addEventListener("click", () => this.callbacks.onRescan(entry.id));
      state.appendChild(scan);
    }
    return state;
  }
}

function focusForFinding(model: ReconcileModel, finding: ReconcileFinding): {
  pageIndex: number;
  outcome: ReconcileTotal["outcome"];
  rectangles: Array<{ bounds: Bounds; role: "addend" | "total" }>;
  footprint?: Bounds;
} {
  const table = model.tables.find((candidate) => candidate.id === finding.tableId);
  const total = table?.totals.find((candidate) => candidate.id === finding.totalId);
  if (!table || !total) {
    return { pageIndex: finding.pageIndex ?? 0, outcome: "unresolved", rectangles: [] };
  }
  return focusForTotal(table, total);
}

/**
 * A total and the cells that footed to it, drawn together.
 *
 * The same geometry serves a finding and an entry in the sum tree, because they
 * are the same claim seen from two sides: one is what the scan is prepared to
 * say, the other is the working behind it. A verified total is worth taking a
 * reviewer to for exactly the reason an exception is — it is the evidence that
 * the figure was checked rather than skipped.
 */
function focusForTotal(table: ReconcileTable, total: ReconcileTotal): {
  pageIndex: number;
  outcome: ReconcileTotal["outcome"];
  rectangles: Array<{ bounds: Bounds; role: "addend" | "total" }>;
  footprint?: Bounds;
} {
  const addends = new Set(total.resolution?.addendCellIds ?? []);
  const ids = new Set([total.cellId, ...addends]);
  const rectangles = table.cells
    .filter((cell) => ids.has(cell.id))
    .map((cell) => ({
      bounds: cell.bounds,
      role: cell.id === total.cellId ? "total" as const : "addend" as const,
    }));
  return {
    pageIndex: table.pageIndex,
    outcome: total.outcome,
    rectangles,
    footprint: footprintOf(rectangles.map((rectangle) => rectangle.bounds)),
  };
}

function totalById(
  model: ReconcileModel,
  totalId: string,
): { table: ReconcileTable; total: ReconcileTotal } | undefined {
  for (const table of model.tables) {
    const total = table.totals.find((candidate) => candidate.id === totalId);
    if (total) return { table, total };
  }
  return undefined;
}

function footprintOf(bounds: Bounds[]): Bounds | undefined {
  if (bounds.length < 2) return undefined;
  const x0 = Math.min(...bounds.map((rect) => rect.x));
  const y0 = Math.min(...bounds.map((rect) => rect.y));
  const x1 = Math.max(...bounds.map((rect) => rect.x + rect.width));
  const y1 = Math.max(...bounds.map((rect) => rect.y + rect.height));
  const padding = 0.003;
  const x = Math.max(0, x0 - padding);
  const y = Math.max(0, y0 - padding);
  return {
    x,
    y,
    width: Math.min(1 - x, x1 - x0 + padding * 2),
    height: Math.min(1 - y, y1 - y0 + padding * 2),
  };
}
