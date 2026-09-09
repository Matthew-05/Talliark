import type {
  ReconcileDocument,
  ReconcileDocumentRole,
  ReconcileImportSource,
} from "../../types/index.js";

export interface SetupWizardCallbacks {
  onImport(role: ReconcileDocumentRole): void;
  onCopy(role: ReconcileDocumentRole, sourcePdfId: string): void;
  onComplete(projectName: string): void;
}

/** The minimum step setup needs before it is complete, from the documents. */
export function requiredSetupStep(documents: ReconcileDocument[]): number {
  if (!documents.some((document_) => document_.role === "primary")) return 1;
  if (!documents.some((document_) => document_.role === "comparison-1")) return 2;
  return 3;
}

/** The result the app should land on, or null while setup is incomplete. */
export function completedPrimary(
  documents: ReconcileDocument[],
  projectName: string,
): ReconcileDocument | null {
  if (!projectName.trim()) return null;
  return documents.find((entry) => entry.role === "primary") ?? null;
}

export class SetupWizard {
  private readonly element: HTMLElement;
  private readonly callbacks: SetupWizardCallbacks;
  private documents: ReconcileDocument[] = [];
  private sources: ReconcileImportSource[] = [];
  private step = 1;
  private projectName = "";
  private nameEdited = false;
  private readonly pendingSources: Partial<Record<"primary" | "comparison-1", string>> = {};

  constructor(parent: HTMLElement, callbacks: SetupWizardCallbacks) {
    this.callbacks = callbacks;
    this.element = document.createElement("section");
    this.element.className = "setup-wizard";
    parent.appendChild(this.element);
    this.setVisible(false);
  }

  update(documents: ReconcileDocument[], sources: ReconcileImportSource[], projectName: string): void {
    this.documents = documents;
    this.sources = sources;
    // Stepping between steps is always explicit: only a step's own Continue,
    // Skip or Back button moves the wizard, never the workbook state. A freshly
    // added statement completes that step in place and waits for the click.
    if (!this.nameEdited) this.projectName = projectName;
    this.render();
  }

  setVisible(visible: boolean): void {
    this.element.hidden = !visible;
  }

  private render(): void {
    this.element.replaceChildren();
    const header = document.createElement("header");
    header.className = "setup-wizard__header";
    header.innerHTML = `
      <div class="setup-wizard__eyebrow">New Reconcile project</div>
      <h1>Set up your statement review</h1>
      <p>Add the statements that define this review. Reconcile keeps its own snapshots in the workbook.</p>
    `;
    this.element.append(header, this.progress());
    if (this.step === 1) this.element.append(this.documentStep("primary"));
    else if (this.step === 2) this.element.append(this.documentStep("comparison-1"));
    else this.element.append(this.detailsStep());
  }

  private progress(): HTMLElement {
    const list = document.createElement("ol");
    list.className = "setup-progress";
    const labels = ["Current statement", "Prior statement", "Project details"];
    labels.forEach((label, index) => {
      const item = document.createElement("li");
      const number = index + 1;
      item.className = number === this.step ? "is-current" : number < this.step ? "is-complete" : "";
      item.innerHTML = `<span>${number < this.step ? "✓" : number}</span><strong>${label}</strong>`;
      list.appendChild(item);
    });
    return list;
  }

  private documentStep(role: "primary" | "comparison-1"): HTMLElement {
    const current = role === "primary";
    const selected = this.documents.find((document_) => document_.role === role);
    const pendingSource = this.pendingSources[role];
    const pendingName = this.sources.find((source) => source.id === pendingSource)?.name;
    const chosenName = pendingName ?? selected?.name;
    const panel = document.createElement("div");
    panel.className = "setup-panel";
    panel.innerHTML = `
      <div class="setup-panel__number">Step ${current ? 1 : 2} of 3</div>
      <h2>${current ? "Select the current financial statement" : "Select the prior-year financial statement"}</h2>
      <p>${current ? "Choose the statement you want Reconcile to check." : "Optional — this will be the comparison period for the project. You can skip it for a single-statement review, or add it later."}</p>
    `;

    const selection = document.createElement("div");
    selection.className = chosenName ? "statement-selection is-selected" : "statement-selection";
    selection.innerHTML = chosenName
      ? `<div class="statement-selection__icon" aria-hidden="true">PDF</div><div><strong></strong><span>${pendingName ? "Imported document selected" : `Selected ${current ? "current" : "prior"} statement`}</span></div>`
      : `<div class="statement-selection__icon" aria-hidden="true">PDF</div><div><strong>No statement selected</strong><span>PDF, Office document, image, or email</span></div>`;
    if (chosenName) selection.querySelector("strong")!.textContent = chosenName;
    const choose = document.createElement("button");
    choose.type = "button";
    choose.className = "button button--secondary";
    choose.textContent = chosenName ? "Choose a different file" : "Choose file";
    choose.addEventListener("click", () => {
      delete this.pendingSources[role];
      this.callbacks.onImport(role);
    });
    selection.appendChild(choose);
    panel.append(selection);

    if (this.sources.length > 0) panel.append(this.importedPicker(role));

    if (!current) {
      const future = document.createElement("div");
      future.className = "future-period";
      future.innerHTML = `<div><strong>Earlier comparison period</strong><span>Reserved for a future update</span></div><span class="future-period__badge">Coming later</span>`;
      panel.append(future);
    }

    const footer = document.createElement("div");
    footer.className = "setup-panel__footer";
    if (!current) {
      const back = document.createElement("button");
      back.type = "button";
      back.className = "button button--quiet";
      back.textContent = "Back";
      back.addEventListener("click", () => { this.step = 1; this.render(); });
      footer.append(back);
      const skip = document.createElement("button");
      skip.type = "button";
      skip.className = "button button--quiet";
      skip.textContent = "Skip";
      skip.addEventListener("click", () => { this.step = 3; this.render(); });
      footer.append(skip);
    }
    const next = document.createElement("button");
    next.type = "button";
    next.className = "button button--primary";
    next.textContent = "Continue";
    next.disabled = !selected && !pendingSource;
    next.addEventListener("click", () => {
      if (pendingSource) {
        delete this.pendingSources[role];
        this.callbacks.onCopy(role, pendingSource);
        return;
      }
      this.step = current ? 2 : 3;
      this.render();
    });
    footer.append(next);
    panel.append(footer);
    return panel;
  }

  private importedPicker(role: "primary" | "comparison-1"): HTMLElement {
    const row = document.createElement("div");
    row.className = "imported-picker";
    const label = document.createElement("label");
    label.textContent = "Or use a document already imported in this workbook";
    const select = document.createElement("select");
    select.setAttribute("aria-label", label.textContent);
    select.append(new Option("Select an imported document…", ""),
      ...this.sources.map((source) => new Option(source.name, source.id)));
    select.value = this.pendingSources[role] ?? "";
    select.addEventListener("change", () => {
      if (select.value) this.pendingSources[role] = select.value;
      else delete this.pendingSources[role];
      this.render();
    });
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "imported-picker__clear";
    clear.textContent = "Clear";
    clear.hidden = !this.pendingSources[role];
    clear.addEventListener("click", () => {
      delete this.pendingSources[role];
      this.render();
    });
    const controls = document.createElement("div");
    controls.append(select, clear);
    row.append(label, controls);
    return row;
  }

  private detailsStep(): HTMLElement {
    const current = this.documents.find((document_) => document_.role === "primary")!;
    const prior = this.documents.find((document_) => document_.role === "comparison-1");
    const panel = document.createElement("div");
    panel.className = "setup-panel";
    panel.innerHTML = `
      <div class="setup-panel__number">Step 3 of 3</div>
      <h2>Name this Reconcile project</h2>
      <p>Use a name that will make this review easy to recognize later.</p>
    `;
    const field = document.createElement("label");
    field.className = "project-name-field";
    field.innerHTML = `<span>Project name</span>`;
    const input = document.createElement("input");
    input.type = "text";
    input.maxLength = 120;
    input.placeholder = "e.g. FY 2026 annual statements";
    input.value = this.projectName;
    input.addEventListener("input", () => {
      this.nameEdited = true;
      this.projectName = input.value;
      scan.disabled = !input.value.trim();
    });
    field.append(input);

    const summary = document.createElement("div");
    summary.className = "setup-summary";
    summary.append(this.summaryRow("Current statement", current.name), this.summaryRow("Prior statement", prior ? prior.name : "Not selected"));

    const placeholder = document.createElement("div");
    placeholder.className = "setup-placeholder";
    placeholder.innerHTML = `<strong>Additional project details</strong><span>More setup options will be added here.</span>`;

    const footer = document.createElement("div");
    footer.className = "setup-panel__footer";
    const back = document.createElement("button");
    back.type = "button";
    back.className = "button button--quiet";
    back.textContent = "Back";
    back.addEventListener("click", () => { this.projectName = input.value; this.step = 2; this.render(); });
    const scan = document.createElement("button");
    scan.type = "button";
    scan.className = "button button--primary button--scan";
    scan.textContent = "Start scan";
    scan.disabled = !input.value.trim();
    scan.addEventListener("click", () => this.callbacks.onComplete(input.value.trim()));
    footer.append(back, scan);
    panel.append(field, summary, placeholder, footer);
    queueMicrotask(() => input.focus());
    return panel;
  }

  private summaryRow(label: string, name: string): HTMLElement {
    const row = document.createElement("div");
    row.innerHTML = `<span></span><strong></strong>`;
    row.querySelector("span")!.textContent = label;
    row.querySelector("strong")!.textContent = name;
    return row;
  }
}
