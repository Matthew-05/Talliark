import { FindingsSidebar } from "../findings-sidebar/findings-sidebar.js";
import { ReconcileViewer } from "../reconcile-viewer/reconcile-viewer.js";
import { ResultsShell, type ReviewMode } from "../results-shell/results-shell.js";
import { EquationReview } from "../equation-review/equation-review.js";
import type { ReviewRequest, ReviewResponse } from "../../types/reconcile-review.generated.js";
import type { ReconcileDocument, ReconcileModel } from "../../types/index.js";

export interface ResultViewCallbacks {
  onRescan(pdfId: string): void;
  onReviewRequest(request: ReviewRequest): void;
}

/** Opens a scan as a review workspace, rather than a terminal report. */
export class ResultView {
  private readonly element: HTMLElement;
  private viewer: ReconcileViewer | null = null;
  private sidebar: FindingsSidebar | null = null;
  private model: ReconcileModel | null = null;
  private review: EquationReview | null = null;
  private stale = false;
  private scanning = false;
  private reviewBusy = false;

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
    this.scanning = scanning;
    this.review?.setBlocked(scanning || this.stale);
    this.updateActions();
  }

  private updateActions(): void {
    for (const button of this.element.querySelectorAll<HTMLButtonElement>(
      ".result-state__action, .results-shell__actions .button",
    )) {
      button.disabled = this.scanning || this.reviewBusy;
      button.title = this.reviewBusy ? "Finish or cancel your edit before re-scanning" : "";
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
    this.review?.clear(); this.review = null; this.reviewBusy = false; this.viewer?.dispose();
    this.model = null;
    this.viewer = null;
    this.sidebar = null;
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
    this.stale = entry.staleness === "stale";
    this.renderWorkspace(entry);
    const viewer = this.viewer!;
    this.review = new EquationReview(entry.id, {
      onRequest: request => this.callbacks.onReviewRequest(request),
      onFocus: (cells, target, state, navigate) => { void viewer.focusReview(cells, target, state, navigate); },
      onPick: (cells, select) => viewer.pickEvidence(cells, select),
      onDraw: complete => viewer.drawValue(complete),
      onPage: pageIndex => { void viewer.showReviewPage(pageIndex); },
      onEditingChanged: busy => { this.reviewBusy = busy; this.updateActions(); },
    });
    this.review.setBlocked(this.stale);
    this.showMode("mathematical");
    if (pdfBase64) void this.viewer?.load(pdfBase64, pageRotations, entry.id, entry.name);
    else this.viewer?.showUnavailable();
    this.review.load();
  }

  receiveReview(response: ReviewResponse): void { this.review?.receive(response); }

  private renderWorkspace(entry: ReconcileDocument): void {
    this.review?.clear(); this.viewer?.dispose();
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
    this.review?.clear(); this.review = null; this.reviewBusy = false; this.viewer?.dispose();
    this.viewer = null;
    this.sidebar = null;
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

    if (mode === "mathematical" && this.review) {
      slot.replaceChildren(this.review.element);
      this.review.activate();
      return;
    }
    this.review?.clear();

    this.sidebar = new FindingsSidebar({ onSelect: () => undefined });
    slot.replaceChildren(this.sidebar.element);
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
