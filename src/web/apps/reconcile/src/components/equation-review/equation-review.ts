import type { ReviewBounds, ReviewCell, ReviewDraft, ReviewEquation, ReviewOperation, ReviewRequest, ReviewResponse } from "../../types/reconcile-review.generated.js";
import { acceptDisplayed, reviewCounts, ReviewController } from "../../services/review-controller.js";
import { nextReviewSelection, reviewQueue, visibleSelection, type ReviewFilters } from "../../services/review-queue.js";
import { EquationEditor, type EquationEditorState } from "../equation-editor/equation-editor.js";

export interface EquationReviewCallbacks {
  onRequest(request: ReviewRequest): void;
  onFocus(cells: ReviewCell[], targetId: string, state: ReviewEquation["evaluation"]["state"], navigate?: boolean): void;
  onPick(cells: ReviewCell[], select: ((cell: ReviewCell) => void) | null): void;
  onDraw(complete: ((pageIndex: number, bounds: ReviewBounds) => void) | null): void;
  onPage(pageIndex: number): void;
  onEditingChanged(busy: boolean): void;
}

/** Document-wide review queue; table actions capture explicit filtered equation ids. */
export class EquationReview {
  readonly element = document.createElement("aside");
  readonly controller: ReviewController;
  private regionId = "";
  private filters: ReviewFilters = { result: "all", type: "all", status: "all" };
  private selectedId = "";
  private draft: ReviewDraft | null = null;
  private editingId = "";
  private pickRole: "target" | "operand" = "operand";
  private blocked = false;
  private showHistory = false;
  private showTools = false;
  private bulk: ReviewOperation | null = null;
  private bulkRevision = -1;
  private bulkScanId = "";
  private missing: { pageIndex: number; bounds: ReviewBounds } | null = null;
  private missingText = "";
  private missingValue = "";
  private missingReason = "";
  private drawing = false;
  private editorState: EquationEditorState = { page: 0, search: "", selectedId: "", correctedValue: "", correctionReason: "", correctionOpen: false };
  private readonly issueDrafts = new Map<string, { note: string; issue: "open" | "explained" | "resolved" }>();

  constructor(pdfId: string, private readonly callbacks: EquationReviewCallbacks) {
    this.element.className = "equation-review";
    this.controller = new ReviewController(pdfId, callbacks.onRequest, () => this.render());
    this.element.addEventListener("keydown", event => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement || event.target instanceof HTMLTextAreaElement || this.locked) return;
      const decisions = { a: "accepted", r: "rejected", d: "deferred" } as const;
      const key = event.key.toLowerCase() as keyof typeof decisions;
      const selected = this.queue.find(eq => eq.id === this.selectedId);
      if (!event.altKey || !(key in decisions) || !selected || (key === "a" && selected.evaluation.state === "not-evaluable")) return;
      event.preventDefault(); this.decide(this.selectedId, decisions[key]);
    });
  }
  load(): void { this.controller.request("load"); }
  setBlocked(blocked: boolean): void { this.blocked = blocked; this.render(); }
  clear(): void { this.callbacks.onPick([], null); this.callbacks.onDraw(null); this.drawing = false; }
  activate(): void { if (this.draft) this.pick(this.pickRole); }
  receive(response: ReviewResponse): void {
    const before = this.queue.map(eq => eq.id);
    const previousId = this.selectedId;
    if (!this.controller.receive(response)) return;
    const saved = this.controller.lastSavedOperations;
    if (saved.some(op => op.kind === "equation")) {
      const draft = this.draft;
      this.selectedId = this.controller.workspace?.equations.find(eq => draft && eq.targetId === draft.targetId
        && eq.axis === draft.axis && JSON.stringify(eq.terms) === JSON.stringify(draft.terms))?.id ?? "";
      this.draft = null; this.editingId = ""; this.clear();
      if (this.selectedId && !this.queue.some(eq => eq.id === this.selectedId)) {
        this.controller.notice = "Equation saved outside the current filters. Clear filters to review and approve it.";
      }
    }
    if (saved.some(op => op.kind === "value")) { this.missing = null; if (this.draft) this.pick(this.pickRole); }
    for (const op of saved) if (op.kind === "issue" && op.equationId) this.issueDrafts.delete(op.equationId);
    if (saved.some(op => op.kind === "decision")) {
      this.selectedId = nextReviewSelection(before, this.queue, previousId,
        saved.filter(op => op.kind === "decision").flatMap(op => op.equationIds ?? []));
    }
    if (response.status === "saved" || response.status === "loaded") this.bulk = null;
    if (saved.some(op => op.kind === "decision" || op.kind === "equation")) {
      const next = this.queue.find(eq => eq.id === this.selectedId); if (next) this.regionId = next.regionId;
    }
    this.render();
    if (response.status === "loaded" || saved.some(op => op.kind === "decision" || op.kind === "equation")) {
      const next = this.controller.workspace?.equations.find(eq => eq.id === this.selectedId);
      if (next) this.focus(next);
      if (response.status !== "loaded") this.focusRow();
    }
    if (response.status === "preview") this.focusDraft();
  }
  private get busy(): boolean { return this.blocked || this.controller.pending !== null; }
  private get queue(): ReviewEquation[] { return this.controller.workspace ? reviewQueue(this.controller.workspace, this.filters) : []; }
  private get unsavedIssue(): boolean {
    return this.controller.workspace?.equations.some(eq => {
      const draft = this.issueDrafts.get(eq.id); return draft && (draft.note !== eq.note || draft.issue !== eq.issue);
    }) ?? false;
  }
  private get locked(): boolean { return this.busy || !!this.draft || !!this.missing || this.drawing || this.unsavedIssue; }
  private commit(operations: ReviewOperation[]): void { if (!this.blocked) this.controller.request("commit", operations); }
  private decide(id: string, decision: "accepted" | "rejected" | "deferred"): void {
    const selected = this.queue.find(eq => eq.id === id);
    if (this.locked || !selected || (decision === "accepted" && selected.evaluation.state === "not-evaluable")) return;
    if (decision !== "deferred" && selected.decision === decision) return;
    this.commit([{ kind: "decision", equationIds: [id], decision }]);
  }
  private edit(eq?: ReviewEquation): void {
    if (this.busy) return;
    this.bulk = null;
    this.editingId = eq?.id ?? "";
    this.draft = eq ? { regionId: eq.regionId, targetId: eq.targetId, terms: eq.terms.map(term => ({ ...term })), axis: eq.axis }
      : { regionId: this.regionId, targetId: "", terms: [], axis: "manual" };
    this.editorState = { page: this.controller.workspace?.regions.find(region => region.id === this.draft!.regionId)?.pageIndex ?? 0,
      search: "", selectedId: "", correctedValue: "", correctionReason: "", correctionOpen: false };
    this.pickRole = eq ? "operand" : "target";
    this.render(); this.pick(this.pickRole);
  }
  private pick(role: "target" | "operand"): void {
    this.pickRole = role;
    const instruction = this.element.querySelector(".equation-review__pick-instruction");
    if (instruction) instruction.textContent = this.pickInstruction();
    this.callbacks.onPick(this.controller.workspace?.evidence ?? [], cell => {
      if (!this.draft || this.busy) return;
      if (this.pickRole === "target") {
        this.draft = { ...this.draft, targetId: cell.id, terms: this.draft.terms.filter(term => term.cellId !== cell.id) }; this.pickRole = "operand";
      } else if (cell.id !== this.draft.targetId) {
        this.draft = { ...this.draft, terms: this.draft.terms.some(term => term.cellId === cell.id)
          ? this.draft.terms.filter(term => term.cellId !== cell.id) : [...this.draft.terms, { cellId: cell.id, coefficient: 1 }] };
      }
      this.controller.preview = null; this.render(); this.pick(this.pickRole);
      this.focusDraft();
    });
  }
  private render(): void {
    const workspace = this.controller.workspace;
    const scroll = this.element.scrollTop;
    const queueScroll = this.element.querySelector(".equation-review__rows")?.scrollTop ?? 0;
    this.callbacks.onEditingChanged(this.controller.pending !== null || !!this.draft || !!this.missing || this.drawing || this.unsavedIssue);
    const header = document.createElement("header");
    const title = document.createElement("h2"); title.textContent = "Review sums";
    const status = document.createElement("p"); status.setAttribute("role", "status");
    status.textContent = this.controller.error || this.controller.notice;
    status.className = this.controller.error ? "equation-review__error" : "equation-review__notice";
    header.append(title, status); this.element.replaceChildren(header);
    if (!workspace) { header.append(this.button("Reload review", () => this.load(), this.controller.pending !== null)); return; }
    if (this.bulk && (this.bulkRevision !== workspace.revision || this.bulkScanId !== workspace.scanId)) this.bulk = null;
    const counts = reviewCounts(workspace);
    const progress = document.createElement("p");
    progress.textContent = `${counts.pending} awaiting review · ${counts.accepted} approved${counts.differences ? ` · ${counts.differences} approved differences open` : ""}`;
    const scope = document.createElement("p"); scope.className = "equation-review__scope";
    scope.textContent = `${workspace.reviewedPages.length}/${workspace.pageCount} pages marked reviewed. Suggestions do not establish complete document coverage.`;
    header.append(progress);
    if (!workspace.regions.some(region => region.id === this.regionId)) this.regionId = workspace.regions.find(item => item.id === "page-0")?.id ?? workspace.regions[0]?.id ?? "";
    const navigation = document.createElement("div"); navigation.className = "equation-review__navigation";
    navigation.append(this.filterControl("result", "Arithmetic result", [["all", "All results"], ["exact-match", "Figures tie"], ["difference", "Figures differ"], ["not-evaluable", "Cannot calculate"]]),
      this.filterControl("type", "Sum type", [["all", "All sum types"], ["vertical", "Foots (columns)"], ["cross", "Crossfoots (rows)"], ["manual", "Manual sums"]]),
      this.filterControl("status", "Review status", [["all", "All statuses"], ["pending", "Awaiting review"], ["unreviewed", "Not yet reviewed"], ["deferred", "Review later"], ["accepted", "Approved"], ["rejected", "Rejected"]]));
    this.element.append(navigation);
    const shown = this.queue;
    this.selectedId = visibleSelection(shown, this.selectedId);
    const summary = document.createElement("p"); summary.className = "equation-review__scope";
    const pages = new Set(shown.map(eq => workspace.evidence.find(cell => cell.id === eq.targetId)?.pageIndex ?? workspace.regions.find(region => region.id === eq.regionId)?.pageIndex));
    summary.textContent = `${shown.length} of ${workspace.equations.length} sums · ${pages.size} ${pages.size === 1 ? "page" : "pages"} · whole document`;
    this.element.append(summary);
    if (this.filters.result !== "all" || this.filters.type !== "all" || this.filters.status !== "all") {
      summary.append(" · ", this.lockButton("Clear filters", () => { this.filters = { result: "all", type: "all", status: "all" }; this.changeFilters(); }));
    }
    if (this.bulk) {
      const box = document.createElement("div"); box.className = "equation-review__bulk";
      const ids = new Set(this.bulk.equationIds);
      const selected = workspace.equations.filter(eq => ids.has(eq.id));
      const copy = document.createElement("p");
      copy.textContent = `Approve these ${selected.length} matching sums in ${this.location(selected[0]!)}? ${selected.filter(eq => eq.evaluation.state === "difference").length} have differences that will remain open. Already reviewed and unreadable sums are excluded.`;
      const list = document.createElement("ul");
      for (const eq of selected) { const li = document.createElement("li"); li.textContent = this.location(eq) + " · " + this.label(eq); list.append(li); }
      box.append(copy, list, this.button("Confirm approval", () => { if (this.bulk && !this.locked) this.commit([this.bulk]); }, this.locked),
        this.button("Cancel", () => { this.bulk = null; this.render(); }, this.busy)); this.element.append(box);
    }
    const rows = document.createElement("div"); rows.className = "equation-review__rows";
    const groups = new Map<string, ReviewEquation[]>();
    for (const eq of shown) groups.set(eq.regionId, [...(groups.get(eq.regionId) ?? []), eq]);
    for (const [regionId, equations] of groups) {
      const group = document.createElement("section"); group.className = "equation-review__group";
      const heading = document.createElement("div"); heading.className = "equation-review__group-heading";
      const label = document.createElement("h3"); label.textContent = `${this.location(equations[0]!)} · ${equations.length} ${equations.length === 1 ? "sum" : "sums"}`;
      const approval = acceptDisplayed(workspace, equations.map(eq => eq.id));
      heading.append(label, this.lockButton("Approve matching sums…", () => {
        this.bulk = approval; this.bulkRevision = workspace.revision; this.bulkScanId = workspace.scanId;
        this.render(); this.element.querySelector(".equation-review__bulk")?.scrollIntoView({ block: "nearest" });
      }, !approval));
      group.dataset.regionId = regionId; group.append(heading);
      for (const eq of equations) {
        const row = this.lockButton("", () => { this.select(eq); this.focusRow(); });
        row.className = `equation-review__row${eq.id === this.selectedId ? " equation-review__row--selected" : ""}`;
        row.setAttribute("aria-pressed", String(eq.id === this.selectedId)); row.dataset.equationId = eq.id;
        const title = document.createElement("span"); title.className = "equation-review__row-title"; title.textContent = this.label(eq);
        const result = document.createElement("span"); result.className = `equation-review__result equation-review__result--${eq.evaluation.state}`;
        result.textContent = eq.evaluation.state === "exact-match" ? "Figures tie" : eq.evaluation.state === "difference" ? `Difference ${eq.evaluation.delta}` : "Cannot calculate";
        const decision = document.createElement("span"); decision.className = "equation-review__decision";
        decision.textContent = ({ unreviewed: "Awaiting review", accepted: "Approved", rejected: "Rejected", deferred: "Review later" })[eq.decision];
        if (eq.decision === "accepted" && eq.evaluation.state === "difference") decision.textContent += ` · issue ${eq.issue}`;
        row.append(title, result, decision); group.append(row);
      }
      rows.append(group);
    }
    if (!shown.length) {
      const empty = document.createElement("p"); empty.textContent = "No sums match these filters anywhere in the document.";
      rows.append(empty);
      if (!this.draft) this.callbacks.onFocus([], "", "not-evaluable", false);
    }
    this.element.append(rows);
    const selected = shown.find(eq => eq.id === this.selectedId);
    if (selected && !this.draft) {
      const details = document.createElement("section"); details.className = "equation-review__details";
      const heading = document.createElement("h3"); heading.textContent = this.label(selected);
      const context = document.createElement("p"); context.className = "equation-review__scope";
      context.textContent = `${this.location(selected)} · result ${workspace.evidence.find(cell => cell.id === selected.targetId)?.text ?? "?"}`;
      const source = document.createElement("div"); source.className = "equation-review__source";
      source.append(context, this.lockButton("Show on PDF", () => this.focus(selected))); details.append(heading, source);
      const working = document.createElement("p");
      const corrections = new Map(workspace.corrections.map(item => [item.cellId, item.value]));
      working.textContent = selected.terms.map(term => `${term.coefficient === 1 ? "+" : "−"} (${corrections.get(term.cellId) ?? workspace.evidence.find(cell => cell.id === term.cellId)?.text ?? "?"})`).join(" ")
        + (selected.evaluation.state === "not-evaluable" ? ` · ${selected.evaluation.reason}` : ` = ${selected.evaluation.sum}; target − sum = ${selected.evaluation.delta}`);
      const actions = document.createElement("div"); actions.className = "equation-review__actions";
      const approve = this.lockButton("Approve", () => this.decide(selected.id, "accepted"), selected.evaluation.state === "not-evaluable" || selected.decision === "accepted");
      approve.setAttribute("aria-label", "Approve sum");
      approve.classList.add("equation-review__primary");
      approve.title = "Confirm the chosen operands. Any numerical difference stays open.";
      const reject = this.lockButton("Reject", () => this.decide(selected.id, "rejected"), selected.decision === "rejected"); reject.setAttribute("aria-label", "Reject suggestion");
      const later = this.lockButton("Later", () => this.decide(selected.id, "deferred")); later.setAttribute("aria-label", "Review later");
      const edit = this.lockButton("Edit", () => this.edit(selected)); edit.setAttribute("aria-label", "Edit equation");
      actions.append(approve, reject, later, edit);
      const help = document.createElement("p"); help.className = "equation-review__scope";
      help.textContent = selected.evaluation.state === "not-evaluable" ? "Check the result and operands before approval." : selected.evaluation.state === "difference" ? "Approval confirms operands; this difference stays open." : "Shortcuts: Alt+A approve · Alt+R reject · Alt+D later";
      details.append(working, actions, help);
      if (selected.decision === "accepted" && selected.evaluation.state === "difference") {
        const issueDraft = this.issueDrafts.get(selected.id) ?? { note: selected.note, issue: selected.issue };
        this.issueDrafts.set(selected.id, issueDraft);
        const note = document.createElement("textarea"); note.value = issueDraft.note; note.placeholder = "Explain this difference"; note.setAttribute("aria-label", "Difference explanation");
        const disposition = document.createElement("select"); disposition.setAttribute("aria-label", "Issue disposition");
        for (const state of ["open", "explained", "resolved"]) disposition.add(new Option(state, state)); disposition.value = issueDraft.issue;
        note.disabled = this.busy; disposition.disabled = this.busy;
        note.addEventListener("input", () => { issueDraft.note = note.value; this.updateLocks(); });
        disposition.addEventListener("change", () => { issueDraft.issue = disposition.value as typeof issueDraft.issue; this.updateLocks(); });
        details.append(note, disposition, this.button("Save issue disposition", () => this.commit([{ kind: "issue", equationId: selected.id, issue: disposition.value as "open" | "explained" | "resolved", note: note.value }]), this.busy));
        details.append(this.button("Cancel issue edit", () => { this.issueDrafts.delete(selected.id); this.render(); }, this.busy));
        const reminder = document.createElement("p"); reminder.className = "equation-review__scope equation-review__issue-reminder";
        reminder.textContent = this.unsavedIssue ? "Save or cancel the difference explanation before reviewing another sum." : "";
        details.append(reminder);
      }
      this.element.append(details);
    }
    if (this.draft) {
      const draft = this.draft;
      const preview = this.controller.preview?.equations.find(eq => eq.targetId === draft.targetId && eq.axis === draft.axis
        && JSON.stringify(eq.terms) === JSON.stringify(draft.terms))?.evaluation ?? null;
      const operation = (): ReviewOperation => ({ kind: "equation", ...(this.editingId ? { equationId: this.editingId } : {}), equation: draft });
      this.element.append(new EquationEditor(workspace, draft, preview, this.busy || !!this.missing || this.drawing, this.editorState, {
        onChange: updated => {
          if (updated.targetId && updated.targetId !== this.draft?.targetId) this.pickRole = "operand";
          this.draft = updated; this.controller.preview = null; this.render(); this.focusDraft();
        },
        onCalculate: () => this.controller.request("preview", [operation()]), onSave: () => this.commit([operation()]),
        onCancel: () => { this.draft = null; this.editingId = ""; this.clear(); this.render(); },
        onPick: role => this.pick(role), onFocus: cell => this.callbacks.onFocus([cell], cell.id, "not-evaluable"),
        onCorrection: correction => this.commit([{ kind: "correction", correction }]),
      }).element);
    }
    if (this.draft) {
      const instruction = document.createElement("p"); instruction.className = "equation-review__scope equation-review__pick-instruction";
      instruction.textContent = this.pickInstruction();
      this.element.append(instruction);
    }
    const tools = document.createElement("details"); tools.className = "equation-review__tools"; tools.open = this.showTools || !!this.draft || !!this.missing || this.drawing;
    tools.addEventListener("toggle", () => { if (tools.isConnected) this.showTools = tools.open; });
    const toolsTitle = document.createElement("summary"); toolsTitle.textContent = "Add missed sums & review tools"; tools.append(toolsTitle, scope);
    const pageSelect = document.createElement("select"); pageSelect.setAttribute("aria-label", "Page for manual review");
    for (const region of workspace.regions.filter(item => item.id === `page-${item.pageIndex}`)) pageSelect.add(new Option(`Page ${region.pageIndex + 1}`, region.id));
    const region = workspace.regions.find(item => item.id === this.regionId);
    pageSelect.value = `page-${region?.pageIndex ?? 0}`; pageSelect.disabled = this.locked; pageSelect.dataset.reviewLock = "false";
    pageSelect.addEventListener("change", () => { this.regionId = pageSelect.value; this.callbacks.onPage(Number(pageSelect.value.slice(5))); this.render(); });
    const pageLabel = document.createElement("label"); pageLabel.textContent = "Manual review page (does not filter sums)"; pageLabel.append(pageSelect); tools.append(pageLabel);
    tools.append(this.lockButton("Add sum on this page", () => { this.regionId = pageSelect.value; this.edit(); }),
      this.lockButton("Reload review", () => { this.clear(); this.controller.request("load"); }),
      this.lockButton("Undo last save", () => { if (this.controller.undoRevision !== null) this.commit([{ kind: "restore", revision: this.controller.undoRevision }]); }, this.controller.undoRevision === null));
    if (region) tools.append(this.lockButton(workspace.reviewedPages.includes(region.pageIndex) ? "Reopen page review" : "Mark this page reviewed", () => this.commit([{ kind: "page", pageIndex: region.pageIndex, reviewed: !workspace.reviewedPages.includes(region.pageIndex) }])));
    tools.append(this.button("Locate missing figure on PDF", () => {
      this.bulk = null; this.drawing = true; this.callbacks.onPick([], null);
      this.callbacks.onDraw((pageIndex, bounds) => {
        this.missing = { pageIndex, bounds }; this.missingText = ""; this.missingValue = ""; this.missingReason = "";
        this.drawing = false; this.callbacks.onDraw(null); this.render();
      }); this.render();
    }, this.busy || this.drawing || !!this.missing || this.unsavedIssue));
    if (this.drawing) {
      const hint = document.createElement("p"); hint.textContent = "Drag a rectangle around the missing printed figure on the PDF.";
      tools.append(hint, this.button("Cancel selection", () => { this.clear(); if (this.draft) this.pick(this.pickRole); this.render(); }, false));
    }
    if (this.missing) {
      const missing = this.missing;
      const form = document.createElement("form"); form.className = "equation-review__missing";
      const title = document.createElement("p"); title.textContent = `Figure selected on page ${missing.pageIndex + 1}. Enter what is printed there.`;
      const text = document.createElement("input"); text.placeholder = "Printed text"; text.required = true; text.setAttribute("aria-label", "Missing figure printed text");
      const value = document.createElement("input"); value.placeholder = "Exact signed decimal"; value.required = true; value.setAttribute("aria-label", "Missing figure value");
      const reason = document.createElement("input"); reason.placeholder = "Reason / row label"; reason.required = true; reason.setAttribute("aria-label", "Missing figure reason");
      text.value = this.missingText; value.value = this.missingValue; reason.value = this.missingReason;
      text.addEventListener("input", () => { this.missingText = text.value; });
      value.addEventListener("input", () => { this.missingValue = value.value; });
      reason.addEventListener("input", () => { this.missingReason = reason.value; });
      const submit = document.createElement("button"); submit.type = "submit"; submit.textContent = "Save anchored figure"; submit.disabled = this.busy;
      form.addEventListener("submit", event => { event.preventDefault(); this.commit([{ kind: "value", cell: { id: "", ...missing, text: text.value, value: value.value.trim(), label: reason.value, column: "", dash: false, origin: "manual" } }]); });
      form.append(title, text, value, reason, submit, this.button("Cancel figure", () => { this.missing = null; if (this.draft) this.pick(this.pickRole); this.render(); }, this.busy)); tools.append(form);
    }
    tools.append(this.button(this.showHistory ? "Hide review history" : "Show review history", () => { this.showHistory = !this.showHistory; this.render(); }, false));
    if (this.showHistory) {
      const history = document.createElement("section"); history.className = "equation-review__history";
      for (const revision of [...workspace.history].reverse()) history.append(this.lockButton(`Restore review revision ${revision.revision} · ${revision.at}`, () => this.commit([{ kind: "restore", revision: revision.revision }])));
      for (const archive of workspace.archives) {
        const details = document.createElement("details");
        const summary = document.createElement("summary"); summary.textContent = `Previous scan · ${archive.equations.filter(eq => eq.decision === "accepted").length} approved relationships · retained evidence`; details.append(summary);
        for (const eq of archive.equations.filter(item => item.decision !== "unreviewed")) {
          const entry = document.createElement("p"); const cell = archive.evidence.find(item => item.id === eq.targetId);
          const corrected = new Map(archive.corrections.map(item => [item.cellId, item.value]));
          const working = eq.terms.map(term => `${term.coefficient === 1 ? "+" : "−"} (${corrected.get(term.cellId) ?? archive.evidence.find(item => item.id === term.cellId)?.text ?? "?"})`).join(" ");
          entry.textContent = `p${(cell?.pageIndex ?? 0) + 1} · ${cell?.label || cell?.text} · ${eq.decision} · ${working} · ${eq.evaluation.state} · delta ${eq.evaluation.delta} · ${eq.issue}${eq.note ? ` · ${eq.note}` : ""}`; details.append(entry);
        }
        history.append(details);
      }
      tools.append(history);
    }
    this.element.append(tools);
    rows.scrollTop = queueScroll; this.element.scrollTop = scroll;
  }
  private focus(eq: ReviewEquation): void {
    const ids = new Set([eq.targetId, ...eq.terms.map(term => term.cellId)]);
    this.callbacks.onFocus(this.controller.workspace?.evidence.filter(cell => ids.has(cell.id)) ?? [], eq.targetId, eq.evaluation.state);
  }
  private focusDraft(): void {
    if (!this.draft) return;
    const draft = this.draft;
    const ids = new Set([draft.targetId, ...draft.terms.map(term => term.cellId)]);
    const evaluation = this.controller.preview?.equations.find(eq => eq.targetId === draft.targetId && eq.axis === draft.axis
      && JSON.stringify(eq.terms) === JSON.stringify(draft.terms))?.evaluation;
    this.callbacks.onFocus(this.controller.workspace?.evidence.filter(cell => ids.has(cell.id)) ?? [], draft.targetId, evaluation?.state ?? "not-evaluable", false);
  }
  private label(eq: ReviewEquation): string {
    const cell = this.controller.workspace?.evidence.find(item => item.id === eq.targetId);
    return `${cell?.label || cell?.text || "Result"}${cell?.column ? ` · ${cell.column}` : ""} · ${eq.axis === "cross" ? "Crossfoot" : eq.axis === "vertical" ? "Foot" : "Manual sum"}`;
  }
  private location(eq: ReviewEquation): string {
    const workspace = this.controller.workspace!;
    const region = workspace.regions.find(item => item.id === eq.regionId);
    const page = workspace.evidence.find(cell => cell.id === eq.targetId)?.pageIndex ?? region?.pageIndex ?? 0;
    const tables = workspace.regions.filter(item => item.pageIndex === page && item.id !== `page-${page}`)
      .sort((a, b) => a.bounds.y - b.bounds.y || a.bounds.x - b.bounds.x || a.id.localeCompare(b.id));
    const table = tables.findIndex(item => item.id === eq.regionId);
    return `Page ${page + 1} · ${table < 0 ? "Manual sums" : `Table ${table + 1}`}`;
  }
  private filterControl(key: keyof ReviewFilters, label: string, options: string[][]): HTMLLabelElement {
    const wrapper = document.createElement("label"); wrapper.textContent = label;
    const select = document.createElement("select"); select.setAttribute("aria-label", label);
    for (const [id, text] of options) {
      const count = reviewQueue(this.controller.workspace!, { ...this.filters, [key]: id } as ReviewFilters).length;
      select.add(new Option(`${text} (${count})`, id));
    }
    select.value = this.filters[key]; select.disabled = this.locked; select.dataset.reviewLock = "false";
    select.addEventListener("change", () => {
      this.filters = { ...this.filters, [key]: select.value } as ReviewFilters; this.changeFilters();
    }); wrapper.append(select); return wrapper;
  }
  private changeFilters(): void {
    this.bulk = null;
    if (this.controller.notice.startsWith("Equation saved outside")) this.controller.notice = "Equation saved · save Excel to keep it on disk";
    const first = this.queue[0]; this.selectedId = first?.id ?? ""; if (first) this.regionId = first.regionId;
    this.render(); if (first) this.focus(first);
  }
  private pickInstruction(): string {
    return `PDF selection: ${this.pickRole === "target" ? "click the printed result" : "click operands to add or remove them"}. Save the equation, then approve it separately.`;
  }
  private select(eq: ReviewEquation): void {
    if (this.locked) return;
    this.selectedId = eq.id; this.regionId = eq.regionId; this.bulk = null; this.render(); this.focus(eq);
  }
  private focusRow(): void {
    const row = this.element.querySelector<HTMLButtonElement>(".equation-review__row--selected");
    row?.focus({ preventScroll: true });
    row?.scrollIntoView({ block: "nearest" });
  }
  /** Lock navigation without rebuilding a textarea while the reviewer is typing. */
  private updateLocks(): void {
    this.callbacks.onEditingChanged(this.controller.pending !== null || !!this.draft || !!this.missing || this.drawing || this.unsavedIssue);
    for (const control of this.element.querySelectorAll<HTMLButtonElement | HTMLSelectElement>("[data-review-lock]")) {
      control.disabled = this.locked || control.dataset.reviewLock === "true";
    }
    const reminder = this.element.querySelector(".equation-review__issue-reminder");
    if (reminder) reminder.textContent = this.unsavedIssue ? "Save or cancel the difference explanation before reviewing another sum." : "";
  }
  private lockButton(label: string, action: () => void, disabled = false): HTMLButtonElement {
    const button = this.button(label, () => { if (!this.locked) action(); }, this.locked || disabled);
    button.dataset.reviewLock = String(disabled); return button;
  }
  private button(label: string, action: () => void, disabled: boolean): HTMLButtonElement {
    const button = document.createElement("button"); button.type = "button"; button.textContent = label;
    button.disabled = disabled; button.addEventListener("click", action); return button;
  }
}
