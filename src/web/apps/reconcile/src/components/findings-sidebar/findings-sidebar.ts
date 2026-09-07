import type { ReconcileFinding, ReconcileModel } from "../../types/index.js";

export interface FindingsSidebarCallbacks {
  onSelect(finding: ReconcileFinding): void;
}

export class FindingsSidebar {
  readonly element: HTMLElement;

  constructor(private readonly callbacks: FindingsSidebarCallbacks) {
    this.element = document.createElement("aside");
    this.element.className = "findings-sidebar";
  }

  show(model: ReconcileModel): void {
    const header = document.createElement("header");
    header.className = "findings-sidebar__header";
    const heading = document.createElement("h2");
    heading.textContent = "Findings";
    const count = document.createElement("span");
    count.className = "findings-sidebar__count";
    count.textContent = String(model.findings.length);
    const context = document.createElement("p");
    context.textContent = model.summary.breaks > 0
      ? `${model.summary.breaks} arithmetic ${model.summary.breaks === 1 ? "exception" : "exceptions"} need review.`
      : `${model.summary.confirmed} ${model.summary.confirmed === 1 ? "total was" : "totals were"} mathematically verified.`;
    header.append(heading, count, context);

    const list = document.createElement("div");
    list.className = "findings-sidebar__list";
    if (model.findings.length === 0) {
      list.appendChild(this.empty(model));
    } else {
      for (const finding of model.findings) list.appendChild(this.finding(finding));
    }
    this.element.replaceChildren(header, list);
  }

  showUnavailable(title: string): void {
    const state = document.createElement("div");
    state.className = "findings-sidebar__future";
    const heading = document.createElement("h2");
    heading.textContent = title;
    const copy = document.createElement("p");
    copy.textContent = "This review mode has not been implemented yet.";
    state.append(heading, copy);
    this.element.replaceChildren(state);
  }

  private finding(finding: ReconcileFinding): HTMLElement {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `findings-sidebar__finding${finding.kind === "footing-unresolved" ? " findings-sidebar__finding--unresolved" : ""}`;
    const top = document.createElement("span");
    top.className = "findings-sidebar__finding-top";
    const kind = document.createElement("span");
    kind.textContent = findingLabel(finding.kind);
    const page = document.createElement("span");
    page.textContent = finding.pageIndex === undefined ? "" : `Page ${finding.pageIndex + 1}`;
    const sentence = document.createElement("span");
    sentence.className = "findings-sidebar__sentence";
    sentence.textContent = finding.sentence;
    top.append(kind, page);
    button.append(top, sentence);
    button.addEventListener("click", () => {
      this.element.querySelectorAll(".findings-sidebar__finding--selected")
        .forEach((element) => element.classList.remove("findings-sidebar__finding--selected"));
      button.classList.add("findings-sidebar__finding--selected");
      this.callbacks.onSelect(finding);
    });
    return button;
  }

  private empty(model: ReconcileModel): HTMLElement {
    const empty = document.createElement("div");
    empty.className = "findings-sidebar__empty";
    const mark = document.createElement("span");
    mark.textContent = model.summary.confirmed > 0 ? "✓" : "—";
    const title = document.createElement("strong");
    title.textContent = model.summary.confirmed > 0 ? "No exceptions found" : "Nothing to review yet";
    const detail = document.createElement("p");
    detail.textContent = model.summary.confirmed > 0
      ? "The scan found no mathematical exceptions. Use the document as the starting point for your review."
      : model.summary.tablesExamined > 0
        ? "Tables were found, but no totals could be checked."
        : "No tables were available to inspect.";
    empty.append(mark, title, detail);
    return empty;
  }
}

function findingLabel(kind: ReconcileFinding["kind"]): string {
  switch (kind) {
    case "footing-break": return "Footing exception";
    case "footing-unresolved": return "Not resolved";
    case "cross-foot-break": return "Cross-foot exception";
    case "ruling-disagreement": return "Structure disagreement";
  }
}
