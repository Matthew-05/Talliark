import type { ReviewBounds, ReviewCell, ReviewDraft, ReviewEquation, ReviewOperation, ReviewRequest, ReviewResponse } from "../../types/reconcile-review.generated.js";
import { acceptDisplayed, reviewCounts, ReviewController } from "../../services/review-controller.js";
import { reviewPageGroups, reviewQueue, visibleSelection, type ReviewFilters } from "../../services/review-queue.js";
import { EquationEditor, type EquationEditorState } from "../equation-editor/equation-editor.js";

export interface EquationReviewCallbacks {
  onRequest(request: ReviewRequest): void;
  onFocus(cells: ReviewCell[], targetId: string, state: ReviewEquation["evaluation"]["state"], navigate?: boolean, equationId?: string): void;
  onOverview(equations: ReviewEquation[], evidence: ReviewCell[], select: ((id: string) => void) | null, clear: (() => void) | null): void;
  onPick(cells: ReviewCell[], select: ((cell: ReviewCell) => void) | null): void;
  onDraw(complete: ((pageIndex: number, bounds: ReviewBounds) => void) | null): void;
  onPage(pageIndex: number): void;
  onEditingChanged(busy: boolean): void;
}

/** Page-grouped review; all decisions automatically update the open workbook. */
export class EquationReview {
  readonly element = document.createElement("aside");
  readonly controller: ReviewController;
  private regionId = "";
  private filters: ReviewFilters = { result: "all", type: "all", statuses: ["unreviewed", "deferred", "accepted"] };
  private selectedId = "";
  private hoveredId = "";
  private hoverNeedsMove = false;
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
  private readonly openIssues = new Set<string>();

  constructor(pdfId: string, private readonly callbacks: EquationReviewCallbacks) {
    this.element.className = "equation-review";
    this.controller = new ReviewController(pdfId, callbacks.onRequest, () => this.render());
    this.element.addEventListener("pointermove", event => {
      if (!this.hoverNeedsMove) return;
      this.hoverNeedsMove = false;
      if (event.target instanceof Element) this.preview(event.target.closest<HTMLElement>(".equation-review__row")?.dataset.equationId ?? "");
    });
    this.element.addEventListener("click", event => {
      if (event.target instanceof Element && !event.target.closest(".equation-review__row, .equation-editor, .equation-review__missing, .equation-review__bulk, button, input, select, textarea, summary")) this.unselect();
    });
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
  clear(): void {
    this.callbacks.onPick([], null); this.callbacks.onDraw(null); this.drawing = false;
    this.callbacks.onOverview([], [], null, null); this.callbacks.onFocus([], "", "not-evaluable", false);
  }
  activate(): void { if (this.draft) this.pick(this.pickRole); else this.syncOverlay(); }
  saveWorkbook(): void { if (!this.editing) this.controller.request("save-workbook"); }
  get ready(): boolean { return this.controller.workspace !== null; }
  unselect(): void {
    if (this.locked || (!this.selectedId && !this.hoveredId)) return;
    this.selectedId = ""; this.hoveredId = ""; this.hoverNeedsMove = true; this.bulk = null; this.render();
  }
  receive(response: ReviewResponse): void {
    if (!this.controller.receive(response)) return;
    const applied = this.controller.lastAppliedOperations;
    if (applied.some(op => op.kind === "equation")) {
      const draft = this.draft;
      this.selectedId = this.controller.workspace?.equations.find(eq => draft && eq.targetId === draft.targetId
        && eq.axis === draft.axis && JSON.stringify(eq.terms) === JSON.stringify(draft.terms))?.id ?? "";
      this.draft = null; this.editingId = ""; this.clear();
      if (this.selectedId && !this.queue.some(eq => eq.id === this.selectedId)) {
        this.controller.notice = "Equation applied outside the current filters. Clear filters to review and approve it.";
      }
    }
    if (applied.some(op => op.kind === "value")) { this.missing = null; if (this.draft) this.pick(this.pickRole); }
    for (const op of applied) if (op.kind === "issue" && op.equationId) this.issueDrafts.delete(op.equationId);
    if (applied.some(op => op.kind === "decision")) {
      this.selectedId = ""; this.hoveredId = "";
    }
    if (response.status === "updated" || response.status === "loaded") this.bulk = null;
    if (applied.some(op => op.kind === "decision" || op.kind === "equation")) {
      const next = this.queue.find(eq => eq.id === this.selectedId); if (next) this.regionId = next.regionId;
    }
    this.render();
    if (applied.some(op => op.kind === "equation")) {
      const next = this.controller.workspace?.equations.find(eq => eq.id === this.selectedId);
      if (next) this.focus(next);
      this.focusRow();
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
  private get editing(): boolean { return this.controller.pending !== null || !!this.draft || !!this.missing || this.drawing || this.unsavedIssue; }
  private get locked(): boolean { return this.blocked || this.editing; }
  private commit(operations: ReviewOperation[]): void {
    if (this.blocked) return;
    if (operations.some(op => op.kind === "decision")) { this.hoverNeedsMove = true; this.hoveredId = ""; }
    this.controller.request("commit", operations);
  }
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
    this.callbacks.onEditingChanged(this.editing);
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
      this.filterControl("type", "Sum type", [["all", "All sum types"], ["vertical", "Foots (columns)"], ["cross", "Crossfoots (rows)"], ["manual", "Manual sums"]]));
    this.element.append(navigation, this.statusControls());
    const shown = this.queue;
    this.selectedId = visibleSelection(shown, this.selectedId);
    this.hoveredId = "";
    const groups = reviewPageGroups(workspace, shown);
    const summary = document.createElement("p"); summary.className = "equation-review__scope";
    summary.textContent = `${shown.length} matching sums · ${groups.size} ${groups.size === 1 ? "page" : "pages"} · whole document`;
    this.element.append(summary);
    if (this.filters.result !== "all" || this.filters.type !== "all" || this.filters.statuses.length !== 3) {
      summary.append(" · ", this.lockButton("Clear filters", () => {
        this.filters = { result: "all", type: "all", statuses: ["unreviewed", "deferred", "accepted"] }; this.changeFilters();
      }));
    }
    if (this.selectedId) summary.append(" · ", this.lockButton("Show all matching sums", () => this.unselect()));
    if (this.bulk) {
      const box = document.createElement("div"); box.className = "equation-review__bulk";
      const ids = new Set(this.bulk.equationIds);
      const selected = shown.filter(eq => ids.has(eq.id));
      const copy = document.createElement("p");
      copy.textContent = `Approve these ${selected.length} matching sums on ${this.location(selected[0]!)}? ${selected.filter(eq => eq.evaluation.state === "difference").length} have differences that will remain open. Already reviewed and unreadable sums are excluded.`;
      const list = document.createElement("ul");
      for (const eq of selected) { const li = document.createElement("li"); li.textContent = this.label(eq); list.append(li); }
      box.append(copy, list, this.button("Confirm approval", () => { if (this.bulk && !this.locked) this.commit([this.bulk]); }, this.locked),
        this.button("Cancel", () => { this.bulk = null; this.render(); }, this.busy)); this.element.append(box);
    }
    const rows = document.createElement("div"); rows.className = "equation-review__rows";
    for (const [pageIndex, equations] of groups) {
      const group = document.createElement("section"); group.className = "equation-review__group"; group.dataset.pageIndex = String(pageIndex);
      const heading = document.createElement("div"); heading.className = "equation-review__group-heading";
      const label = document.createElement("h3"); label.textContent = `Page ${pageIndex + 1} · ${equations.length} ${equations.length === 1 ? "sum" : "sums"}`;
      const approval = acceptDisplayed(workspace, equations.map(eq => eq.id));
      heading.append(label, this.lockButton("Approve matching sums…", () => {
        this.bulk = approval; this.bulkRevision = workspace.revision; this.bulkScanId = workspace.scanId;
        this.render(); this.element.querySelector(".equation-review__bulk")?.scrollIntoView({ block: "nearest" });
      }, !approval));
      group.append(heading);
      for (const eq of equations) group.append(this.equationRow(eq));
      rows.append(group);
    }
    if (!shown.length) {
      const empty = document.createElement("p"); empty.textContent = "No sums match these filters anywhere in the document."; rows.append(empty);
    }
    this.element.append(rows);
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
      this.lockButton("Undo last change", () => { if (this.controller.undoRevision !== null) this.commit([{ kind: "restore", revision: this.controller.undoRevision }]); }, this.controller.undoRevision === null));
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
      const submit = document.createElement("button"); submit.type = "submit"; submit.textContent = "Add anchored figure"; submit.disabled = this.busy;
      form.addEventListener("submit", event => { event.preventDefault(); this.commit([{ kind: "value", cell: { id: "", ...missing, text: text.value, value: value.value.trim(), label: reason.value, column: "", dash: false, origin: "manual" } }]); });
      form.append(title, text, value, reason, submit, this.button("Cancel figure", () => { this.missing = null; if (this.draft) this.pick(this.pickRole); this.render(); }, this.busy)); tools.append(form);
    }
    tools.append(this.lockButton(this.showHistory ? "Hide review history" : "Show review history", () => { this.showHistory = !this.showHistory; this.render(); }));
    if (this.showHistory) {
      const history = document.createElement("section"); history.className = "equation-review__history";
      for (const revision of [...workspace.history].reverse()) history.append(this.lockButton(`Restore review revision ${revision.revision} · ${revision.at}`, () => this.commit([{ kind: "restore", revision: revision.revision }])));
      for (const archive of workspace.archives) {
        const details = document.createElement("details");
        const summary = document.createElement("summary"); summary.textContent = `Previous scan · ${archive.equations.filter(eq => eq.decision === "accepted").length} approved relationships · retained evidence`; details.append(summary);
        for (const eq of archive.equations.filter(item => item.decision === "accepted" || item.decision === "deferred")) {
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
    this.syncOverlay();
  }
  private equationRow(eq: ReviewEquation): HTMLElement {
    const workspace = this.controller.workspace!;
    const row = document.createElement("section"); row.dataset.equationId = eq.id;
    row.className = `equation-review__row${eq.id === this.selectedId ? " equation-review__row--selected" : ""}`;
    row.setAttribute("aria-label", this.label(eq));
    const select = this.lockButton("", () => this.select(eq)); select.className = "equation-review__row-select";
    select.setAttribute("aria-pressed", String(eq.id === this.selectedId));
    const title = document.createElement("span"); title.className = "equation-review__row-title"; title.textContent = this.label(eq);
    const result = document.createElement("span"); result.className = `equation-review__result equation-review__result--${eq.evaluation.state}`;
    result.textContent = eq.evaluation.state === "exact-match" ? "Figures tie" : eq.evaluation.state === "difference" ? `Difference ${eq.evaluation.delta}` : "Cannot calculate";
    const decision = document.createElement("span"); decision.className = "equation-review__decision";
    decision.textContent = eq.decision === "accepted" ? "Approved" : eq.decision === "deferred" ? "Review later" : "Awaiting review";
    if (eq.decision === "accepted" && eq.evaluation.state === "difference") decision.textContent += ` · issue ${eq.issue}`;
    select.append(title, result, decision); row.append(select);
    const working = document.createElement("p"); working.className = "equation-review__working";
    const cells = new Map(workspace.evidence.map(cell => [cell.id, cell]));
    const corrections = new Map(workspace.corrections.map(item => [item.cellId, item.value]));
    const target = cells.get(eq.targetId);
    working.textContent = eq.terms.map(term => `${term.coefficient === 1 ? "+" : "−"} (${corrections.get(term.cellId) ?? cells.get(term.cellId)?.text ?? "?"})`).join(" ")
      + (eq.evaluation.state === "not-evaluable" ? ` · ${eq.evaluation.reason}` : ` = ${eq.evaluation.sum}; printed result ${target?.text ?? "?"}${corrections.has(eq.targetId) ? ` → ${corrections.get(eq.targetId)}` : ""}; difference ${eq.evaluation.delta}`);
    const actions = document.createElement("div"); actions.className = "equation-review__actions";
    const approve = this.lockButton("Approve", () => this.decide(eq.id, "accepted"), eq.evaluation.state === "not-evaluable" || eq.decision === "accepted");
    approve.classList.add("equation-review__primary"); approve.title = "Confirm the chosen operands. Any numerical difference stays open.";
    actions.append(approve, this.lockButton("Reject", () => this.decide(eq.id, "rejected")),
      this.lockButton("Later", () => this.decide(eq.id, "deferred")), this.lockButton("Edit", () => this.edit(eq)));
    row.append(working, actions);
    if (eq.decision === "accepted" && eq.evaluation.state === "difference") {
      const issue = document.createElement("details"); issue.className = "equation-review__issue"; issue.open = this.openIssues.has(eq.id);
      issue.addEventListener("toggle", () => { if (issue.isConnected) { if (issue.open) this.openIssues.add(eq.id); else this.openIssues.delete(eq.id); } });
      const summary = document.createElement("summary"); summary.textContent = "Explain difference";
      const draft = this.issueDrafts.get(eq.id) ?? { note: eq.note, issue: eq.issue };
      const note = document.createElement("textarea"); note.value = draft.note; note.placeholder = "Explain this difference"; note.setAttribute("aria-label", "Difference explanation");
      const disposition = document.createElement("select"); disposition.setAttribute("aria-label", "Issue disposition");
      for (const state of ["open", "explained", "resolved"]) disposition.add(new Option(state, state)); disposition.value = draft.issue;
      note.disabled = this.busy || !!this.draft; disposition.disabled = this.busy || !!this.draft;
      note.addEventListener("input", () => { draft.note = note.value; this.issueDrafts.set(eq.id, draft); this.updateLocks(); });
      disposition.addEventListener("change", () => { draft.issue = disposition.value as typeof draft.issue; this.issueDrafts.set(eq.id, draft); this.updateLocks(); });
      issue.append(summary, note, disposition, this.button("Record explanation", () => this.commit([{ kind: "issue", equationId: eq.id, issue: draft.issue, note: draft.note }]), this.busy || !!this.draft),
        this.button("Cancel explanation", () => { this.issueDrafts.delete(eq.id); this.render(); }, this.busy));
      const reminder = document.createElement("p"); reminder.className = "equation-review__scope equation-review__issue-reminder";
      reminder.textContent = this.unsavedIssue ? "Record or cancel the explanation before reviewing another sum." : ""; issue.append(reminder); row.append(issue);
    }
    row.addEventListener("pointerenter", () => this.preview(eq.id));
    row.addEventListener("pointerleave", () => this.preview(""));
    return row;
  }
  private statusControls(): HTMLElement {
    const fieldset = document.createElement("fieldset"); fieldset.className = "equation-review__statuses";
    const legend = document.createElement("legend"); legend.textContent = "Review status"; fieldset.append(legend);
    const statuses = [["unreviewed", "Awaiting review"], ["deferred", "Review later"], ["accepted", "Approved"]] as const;
    for (const [status, text] of statuses) {
      const label = document.createElement("label"); const checkbox = document.createElement("input"); checkbox.type = "checkbox";
      checkbox.checked = this.filters.statuses.includes(status); checkbox.disabled = this.locked; checkbox.dataset.reviewLock = "false";
      const count = reviewQueue(this.controller.workspace!, { ...this.filters, statuses: [status] }).length;
      checkbox.setAttribute("aria-label", text);
      checkbox.addEventListener("change", () => {
        this.filters = { ...this.filters, statuses: statuses.map(([id]) => id).filter(id => id === status ? checkbox.checked : this.filters.statuses.includes(id)) };
        this.changeFilters();
      }); label.append(checkbox, ` ${text} (${count})`); fieldset.append(label);
    }
    return fieldset;
  }
  private syncOverlay(): void {
    const workspace = this.controller.workspace;
    if (!workspace) return;
    const editing = !!this.draft || !!this.missing || this.drawing;
    this.callbacks.onOverview(editing ? [] : this.queue, workspace.evidence,
      id => { const eq = this.queue.find(eq => eq.id === id); if (eq) this.select(eq); }, () => this.unselect());
    if (this.draft) this.focusDraft(); else if (!editing) this.syncFocus();
  }
  private syncFocus(): void {
    const eq = this.queue.find(eq => eq.id === (this.hoveredId || this.selectedId));
    if (eq) this.focus(eq, false); else this.callbacks.onFocus([], "", "not-evaluable", false);
  }
  private preview(id: string): void {
    if (this.locked || this.hoveredId === id || (id && this.hoverNeedsMove)) return;
    this.hoveredId = id; this.syncFocus();
  }
  private focus(eq: ReviewEquation, navigate = true): void {
    const ids = new Set([eq.targetId, ...eq.terms.map(term => term.cellId)]);
    this.callbacks.onFocus(this.controller.workspace?.evidence.filter(cell => ids.has(cell.id)) ?? [], eq.targetId, eq.evaluation.state, navigate, eq.id);
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
    return `Page ${page + 1}`;
  }
  private filterControl(key: "result" | "type", label: string, options: string[][]): HTMLLabelElement {
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
    if (this.controller.notice.startsWith("Equation applied outside")) this.controller.notice = "";
    this.selectedId = ""; this.hoveredId = ""; this.render();
  }
  private pickInstruction(): string {
    return `PDF selection: ${this.pickRole === "target" ? "click the printed result" : "click operands to add or remove them"}. Apply the equation, then approve it separately.`;
  }
  private select(eq: ReviewEquation): void {
    if (this.locked) return;
    this.selectedId = this.selectedId === eq.id ? "" : eq.id; this.hoveredId = ""; this.hoverNeedsMove = !this.selectedId;
    this.regionId = eq.regionId; this.bulk = null; this.render();
    if (this.selectedId) this.focus(eq); this.focusRow();
  }
  private focusRow(): void {
    const row = this.element.querySelector<HTMLButtonElement>(".equation-review__row--selected .equation-review__row-select");
    row?.focus({ preventScroll: true });
    row?.scrollIntoView({ block: "nearest" });
  }
  /** Lock navigation without rebuilding a textarea while the reviewer is typing. */
  private updateLocks(): void {
    this.callbacks.onEditingChanged(this.editing);
    for (const control of this.element.querySelectorAll<HTMLButtonElement | HTMLSelectElement | HTMLInputElement>("[data-review-lock]")) {
      control.disabled = this.locked || control.dataset.reviewLock === "true";
    }
    for (const reminder of this.element.querySelectorAll(".equation-review__issue-reminder")) reminder.textContent = this.unsavedIssue ? "Record or cancel the explanation before reviewing another sum." : "";
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
