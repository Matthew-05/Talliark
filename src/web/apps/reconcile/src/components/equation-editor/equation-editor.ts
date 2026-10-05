import type { ReviewCell, ReviewCorrection, ReviewDraft, ReviewEvaluation, ReviewWorkspace } from "../../types/reconcile-review.generated.js";

export interface EquationEditorCallbacks {
  onChange(draft: ReviewDraft): void;
  onCalculate(): void;
  onSave(): void;
  onCancel(): void;
  onPick(role: "target" | "operand"): void;
  onFocus(cell: ReviewCell): void;
  onCorrection(correction: ReviewCorrection): void;
}

export interface EquationEditorState {
  page: number; search: string; selectedId: string;
  correctedValue: string; correctionReason: string; correctionOpen: boolean;
}

/** Explicit printed inputs and operation signs; computation stays in the host. */
export class EquationEditor {
  readonly element = document.createElement("section");
  constructor(workspace: ReviewWorkspace, draft: ReviewDraft, evaluation: ReviewEvaluation | null,
    disabled: boolean, state: EquationEditorState, callbacks: EquationEditorCallbacks) {
    this.element.className = "equation-editor";
    const cells = new Map(workspace.evidence.map(cell => [cell.id, cell]));
    const values = new Map(workspace.corrections.map(item => [item.cellId, item.value]));
    const heading = document.createElement("h3"); heading.textContent = "Edit relationship";
    const instruction = document.createElement("p");
    instruction.textContent = "Choose the result and operands from the list or document. Signs apply to the printed signed values.";
    const target = cells.get(draft.targetId);
    const result = document.createElement("div"); result.className = "equation-editor__result";
    result.textContent = target ? `Result: ${cellLabel(target, values)}` : "Choose a result figure";
    result.append(button("Pick result on PDF", () => callbacks.onPick("target"), disabled));
    if (target) result.append(button("Locate", () => callbacks.onFocus(target), false));
    this.element.append(heading, instruction, result);
    const operands = document.createElement("div");
    for (const [index, term] of draft.terms.entries()) {
      const cell = cells.get(term.cellId); if (!cell) continue;
      const row = document.createElement("div"); row.className = "equation-editor__operand";
      const sign = button(term.coefficient === 1 ? "+" : "−", () => {
        callbacks.onChange({ ...draft, terms: draft.terms.map((item, i) => i === index ? { ...item, coefficient: item.coefficient === 1 ? -1 as const : 1 as const } : item) });
      }, disabled);
      sign.setAttribute("aria-label", term.coefficient === 1 ? "Subtract this printed value" : "Add this printed value");
      row.append(sign, button(cellLabel(cell, values), () => callbacks.onFocus(cell), false),
        button("Remove", () => callbacks.onChange({ ...draft, terms: draft.terms.filter((_, i) => i !== index) }), disabled));
      operands.append(row);
    }
    this.element.append(operands, button("Pick operands on PDF", () => callbacks.onPick("operand"), disabled));
    const picker = document.createElement("div"); picker.className = "equation-editor__picker";
    const page = document.createElement("select"); page.setAttribute("aria-label", "Evidence page");
    for (let i = 0; i < workspace.pageCount; i++) page.add(new Option(`Page ${i + 1}`, String(i)));
    page.value = String(state.page);
    const search = document.createElement("input"); search.type = "search";
    search.placeholder = "Filter printed values or row labels"; search.setAttribute("aria-label", "Filter evidence");
    search.value = state.search;
    const select = document.createElement("select"); select.setAttribute("aria-label", "Printed value");
    const update = (): void => {
      select.replaceChildren(new Option("Choose a printed value…", ""));
      for (const cell of workspace.evidence.filter(item => item.pageIndex === Number(page.value)
        && `${item.text} ${item.label} ${item.column}`.toLowerCase().includes(search.value.toLowerCase()))) select.add(new Option(cellLabel(cell, values), cell.id));
      select.value = state.selectedId;
    };
    page.addEventListener("change", () => { state.page = Number(page.value); state.selectedId = ""; update(); });
    search.addEventListener("input", () => { state.search = search.value; update(); });
    select.addEventListener("change", () => { state.selectedId = select.value; }); update();
    const selected = (): ReviewCell | undefined => cells.get(select.value);
    picker.append(page, search, select,
      button("Use as result", () => { if (selected()) callbacks.onChange({ ...draft, targetId: select.value, terms: draft.terms.filter(term => term.cellId !== select.value) }); }, disabled),
      button("Add operand", () => { if (selected() && select.value !== draft.targetId && !draft.terms.some(term => term.cellId === select.value)) callbacks.onChange({ ...draft, terms: [...draft.terms, { cellId: select.value, coefficient: 1 }] }); }, disabled));
    this.element.append(picker);
    const correction = document.createElement("details");
    correction.open = state.correctionOpen;
    correction.addEventListener("toggle", () => { state.correctionOpen = correction.open; });
    const summary = document.createElement("summary"); summary.textContent = "Correct a misread value";
    const value = document.createElement("input"); value.placeholder = "Exact decimal, e.g. -1234.50"; value.setAttribute("aria-label", "Corrected value");
    const reason = document.createElement("input"); reason.placeholder = "What does the printed page show?"; reason.setAttribute("aria-label", "Correction reason");
    value.value = state.correctedValue; reason.value = state.correctionReason;
    value.addEventListener("input", () => { state.correctedValue = value.value; });
    reason.addEventListener("input", () => { state.correctionReason = reason.value; });
    const copy = document.createElement("p"); copy.textContent = "Select a value above. Corrections apply to this review and reset affected approvals. Leave the decimal empty to restore the recognized value.";
    correction.append(summary, copy, value, reason, button("Apply value correction", () => {
      if (selected() && reason.value.trim()) callbacks.onCorrection({ cellId: select.value, value: value.value.trim(), reason: reason.value.trim() });
    }, disabled));
    this.element.append(correction);
    const preview = document.createElement("p"); preview.className = "equation-editor__calculation"; preview.setAttribute("role", "status");
    preview.textContent = evaluation ? evaluation.state === "not-evaluable" ? evaluation.reason
      : `Selected operands = ${evaluation.sum}; difference = ${evaluation.delta}${evaluation.state === "exact-match" ? " · figures tie" : ""}` : "Calculate to preview the selected equation.";
    this.element.append(preview, button("Calculate", callbacks.onCalculate, disabled),
      button("Apply equation", callbacks.onSave, disabled), button("Cancel edit", callbacks.onCancel, disabled));
  }
}

function cellLabel(cell: ReviewCell, corrections: Map<string, string>): string {
  return `p${cell.pageIndex + 1} · ${cell.label || cell.text} ${cell.column ? `(${cell.column})` : ""} · ${cell.text}${corrections.has(cell.id) ? ` → ${corrections.get(cell.id)}` : ""}`;
}
function button(label: string, action: () => void, disabled: boolean): HTMLButtonElement {
  const button = document.createElement("button"); button.type = "button"; button.textContent = label; button.disabled = disabled;
  button.addEventListener("click", action); return button;
}
