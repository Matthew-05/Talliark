import type { ReviewOperation, ReviewRequest, ReviewResponse, ReviewWorkspace } from "../types/reconcile-review.generated.js";

/** Authoritative host acknowledgements own all persisted state. A preview is never a save. */
export class ReviewController {
  workspace: ReviewWorkspace | null = null;
  preview: ReviewWorkspace | null = null;
  error = "";
  notice = "Loading relationship review…";
  pending: ReviewRequest | null = null;
  undoRevision: number | null = null;
  lastSavedOperations: ReviewOperation[] = [];
  readonly pdfId: string;
  private readonly send: (request: ReviewRequest) => void;
  private readonly changed: () => void;
  private readonly requestId: () => string;
  constructor(pdfId: string, send: (request: ReviewRequest) => void,
    changed: () => void, requestId: () => string = () => crypto.randomUUID()) {
    this.pdfId = pdfId; this.send = send; this.changed = changed; this.requestId = requestId;
  }

  request(mode: ReviewRequest["mode"], operations: ReviewOperation[] = []): void {
    if (this.pending) return;
    if (mode !== "load" && !this.workspace) return;
    const request: ReviewRequest = {
      type: "reconcile-review-request", version: 1, requestId: this.requestId(), pdfId: this.pdfId,
      mode, scanId: this.workspace?.scanId ?? "", expectedRevision: this.workspace?.revision ?? 0, operations,
    };
    this.pending = request;
    this.error = "";
    this.preview = null;
    this.notice = mode === "load" ? "Loading relationship review…" : mode === "preview" ? "Calculating…" : "Saving review to workbook…";
    this.changed();
    this.send(request);
  }

  receive(response: ReviewResponse): boolean {
    if (response.version !== 1 || response.pdfId !== this.pdfId || response.requestId !== this.pending?.requestId) return false;
    const request = this.pending;
    this.pending = null;
    this.lastSavedOperations = [];
    if (response.status === "error" || !response.workspace) {
      this.error = response.error ?? "The host did not return review state.";
      this.notice = "Review was not saved. Your draft is still available.";
    } else if (response.status === "preview" && request.mode === "preview") {
      this.preview = response.workspace;
      this.notice = "Calculation preview · not saved";
    } else if ((response.status === "saved" && request.mode === "commit") || (response.status === "loaded" && request.mode === "load")) {
      if (response.status === "saved") {
        this.undoRevision = this.workspace?.revision ?? null;
        this.lastSavedOperations = request.operations;
      } else if (response.workspace.scanId !== this.workspace?.scanId) this.undoRevision = null;
      this.workspace = response.workspace;
      this.notice = response.status === "saved" ? "Review recorded in workbook · save Excel to keep it on disk" : "Review loaded";
    } else {
      this.error = "Unexpected review acknowledgement. Reload the review.";
    }
    this.changed();
    return true;
  }
}

export function reviewCounts(workspace: ReviewWorkspace): { accepted: number; pending: number; differences: number } {
  return {
    accepted: workspace.equations.filter(eq => eq.decision === "accepted").length,
    pending: workspace.equations.filter(eq => eq.decision === "unreviewed" || eq.decision === "deferred").length,
    differences: workspace.equations.filter(eq => eq.decision === "accepted" && eq.evaluation.state === "difference" && eq.issue === "open").length,
  };
}

/** Bulk scope is captured by explicit ids; later filters/host updates cannot expand it. */
export function acceptDisplayed(workspace: ReviewWorkspace, equationIds: readonly string[]): ReviewOperation | null {
  const wanted = new Set(equationIds);
  const ids = workspace.equations.filter(eq => wanted.has(eq.id) && eq.evaluation.state !== "not-evaluable"
    && (eq.decision === "unreviewed" || eq.decision === "deferred")).map(eq => eq.id);
  return ids.length ? { kind: "decision", equationIds: ids, decision: "accepted" } : null;
}
