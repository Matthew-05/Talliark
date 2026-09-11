import type {
  ReconcileCell,
  ReconcileFinding,
  ReconcileModel,
  ReconcileOutcome,
  ReconcileSumRun,
  ReconcileTable,
  ReconcileTotal,
} from "../../types/index.js";

export interface SumTreeCallbacks {
  onSelect(table: ReconcileTable, total: ReconcileTotal, overview: boolean): void;
  onClear(): void;
  onOverview(items: readonly SumTreeOverviewItem[] | null): void;
}

export interface SumTreeOverviewItem {
  readonly id: string;
  readonly pageIndex: number;
  readonly bounds: ReconcileCell["bounds"];
  readonly outcome: ReconcileOutcome;
}

/**
 * Everything the scan checked, below the findings and collapsed by default.
 *
 * This is audit evidence rather than the primary queue. A reviewer opens it to
 * answer the third question a result has to answer — *what exactly was checked*
 * — after the scorecard has said whether the scan ran and the findings have
 * said what needs attention.
 *
 * Every nominated total appears, whatever the arithmetic made of it, and each
 * one shows its exact working: the addends as the page printed them, the sum
 * they make, and where a total misses, the difference. A confirmed total is the
 * reason a clean scan is affirmative instead of empty, so it is shown as fully
 * as an exception is.
 */
export class SumTree {
  readonly element: HTMLElement;
  private readonly body: HTMLElement;
  private selected: HTMLElement | null = null;
  private activeTab: ReconcileOutcome = "break";
  private overview = false;
  private model: ReconcileModel | null = null;
  private overviewButton: HTMLButtonElement | null = null;

  constructor(private readonly callbacks: SumTreeCallbacks) {
    this.element = document.createElement("aside");
    this.element.className = "sum-tree";
    this.body = document.createElement("div");
    this.body.className = "sum-tree__body";
  }

  show(model: ReconcileModel): void {
    this.model = model;
    const header = document.createElement("header");
    header.className = "sum-tree__header";
    const heading = document.createElement("h2");
    heading.textContent = "Footing results";
    const scope = document.createElement("p");
    scope.textContent = `${model.summary.tablesExamined} aligned ${model.summary.tablesExamined === 1 ? "block" : "blocks"} · ${model.summary.totalsNominated} nominated ${model.summary.totalsNominated === 1 ? "total" : "totals"}`;
    this.overviewButton = document.createElement("button");
    this.overviewButton.type = "button";
    this.overviewButton.className = "sum-tree__overview-toggle";
    this.overviewButton.title = "Show every total in this category on the document";
    this.overviewButton.addEventListener("click", () => {
      this.overview = !this.overview;
      this.updateOverviewButton();
      this.emitOverview();
    });
    this.updateOverviewButton();
    header.append(heading, scope, this.overviewButton);
    const tabs = document.createElement("div");
    tabs.className = "sum-tree__tabs";
    tabs.setAttribute("role", "tablist");
    const outcomes: ReadonlyArray<[ReconcileOutcome, string, number]> = [
      ["break", "Exceptions", model.summary.breaks],
      ["confirmed", "Verified", model.summary.confirmed],
      ["unresolved", "Not checked", model.summary.unresolved],
    ];
    const activeCount = outcomes.find(([outcome]) => outcome === this.activeTab)?.[2] ?? 0;
    if (activeCount === 0) {
      this.activeTab = outcomes.find(([, , count]) => count > 0)?.[0] ?? "break";
    }
    for (const [outcome, label, count] of outcomes) {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = `sum-tree__tab${outcome === this.activeTab ? " sum-tree__tab--active" : ""}`;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", String(outcome === this.activeTab));
      tab.dataset["outcome"] = outcome;
      const text = document.createElement("span");
      text.textContent = label;
      const badge = document.createElement("span");
      badge.className = "sum-tree__count";
      badge.textContent = String(count);
      tab.append(text, badge);
      tab.addEventListener("click", () => {
        this.activeTab = outcome;
        tabs.querySelectorAll<HTMLElement>(".sum-tree__tab").forEach((candidate) => {
          const active = candidate.dataset["outcome"] === outcome;
          candidate.classList.toggle("sum-tree__tab--active", active);
          candidate.setAttribute("aria-selected", String(active));
        });
        this.renderTab(model, outcome);
      });
      tabs.appendChild(tab);
    }
    this.element.replaceChildren(header, tabs, this.body);
    this.renderTab(model, this.activeTab);
  }

  clear(): void {
    this.model = null;
    this.callbacks.onOverview(null);
    this.element.replaceChildren();
  }

  focus(totalId: string): void {
    const target = this.body.querySelector<HTMLElement>(
      `[data-total-id="${CSS.escape(totalId)}"]`,
    );
    if (!target) return;
    this.selected?.classList.remove("sum-tree__total--selected");
    target.classList.add("sum-tree__total--selected");
    this.selected = target;
    target.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  private renderTab(model: ReconcileModel, outcome: ReconcileOutcome): void {
    this.selected = null;
    this.callbacks.onClear();
    const findings = new Map(model.findings.map((finding) => [finding.totalId, finding]));
    const withTotals = model.tables.filter(
      (table) => table.totals.some((total) => total.outcome === outcome),
    );
    this.body.replaceChildren(
      ...(withTotals.length === 0
        ? [this.nothing(model, outcome)]
        : withTotals.map((table) => this.table(table, outcome, findings))),
    );
    this.emitOverview();
  }

  private nothing(model: ReconcileModel, outcome: ReconcileOutcome): HTMLElement {
    const empty = document.createElement("p");
    empty.className = "sum-tree__empty";
    if (outcome === "break") empty.textContent = "No footing exceptions were found.";
    else if (outcome === "confirmed") empty.textContent = "No totals were verified.";
    else empty.textContent = model.summary.tablesExamined > 0
      ? "Every nominated total was resolved."
      : "No aligned numeric blocks were found in this document.";
    return empty;
  }

  private table(
    table: ReconcileTable,
    outcome: ReconcileOutcome,
    findings: Map<string, ReconcileFinding>,
  ): HTMLElement {
    const cells = new Map(table.cells.map((cell) => [cell.id, cell]));
    const group = document.createElement("section");
    group.className = "sum-tree__table";

    const heading = document.createElement("h3");
    heading.className = "sum-tree__table-heading";
    const page = document.createElement("span");
    page.textContent = `Page ${table.pageIndex + 1}`;
    const shape = document.createElement("span");
    shape.textContent = `${table.rowCount} ${table.rowCount === 1 ? "row" : "rows"} × ${table.columnCount}`;
    heading.append(page, shape);

    group.append(heading);
    for (const total of table.totals.filter((candidate) => candidate.outcome === outcome)) {
      group.appendChild(this.total(table, total, cells, findings.get(total.id)));
    }
    return group;
  }

  private total(
    table: ReconcileTable,
    total: ReconcileTotal,
    cells: Map<string, ReconcileCell>,
    finding: ReconcileFinding | undefined,
  ): HTMLElement {
    const cell = cells.get(total.cellId);
    const entry = document.createElement("button");
    entry.type = "button";
    entry.className = `sum-tree__total sum-tree__total--${total.outcome}`;
    entry.dataset["totalId"] = total.id;

    const top = document.createElement("span");
    top.className = "sum-tree__total-top";
    const label = document.createElement("span");
    label.className = "sum-tree__label";
    label.textContent = cell?.rowLabel ?? cell?.text ?? "(unlabelled)";
    const outcome = document.createElement("span");
    outcome.className = `sum-tree__outcome sum-tree__outcome--${total.outcome}`;
    outcome.textContent = outcomeLabel(total.outcome);
    top.append(label, outcome);
    entry.append(top);

    const column = columnLabel(table, total);
    if (column) {
      const scope = document.createElement("span");
      scope.className = "sum-tree__column";
      scope.textContent = column;
      entry.append(scope);
    }

    const run = total.resolution;
    if (run) {
      entry.append(this.working(run, cells, cell));
    } else {
      const why = document.createElement("span");
      why.className = "sum-tree__why";
      // The wording is the point. An unresolved total describes what the scan
      // could reach; it is never phrased as a fault in the document.
      why.textContent = unresolvedWording(total.unresolvedReason, cell);
      entry.append(why);
    }

    if (finding) {
      const related = document.createElement("span");
      related.className = "sum-tree__finding";
      related.textContent = finding.sentence;
      entry.append(related);
    }

    entry.addEventListener("click", () => {
      this.selected?.classList.remove("sum-tree__total--selected");
      entry.classList.add("sum-tree__total--selected");
      this.selected = entry;
      this.callbacks.onSelect(table, total, this.overview);
    });
    return entry;
  }

  private working(
    run: ReconcileSumRun,
    cells: Map<string, ReconcileCell>,
    total: ReconcileCell | undefined,
  ): HTMLElement {
    const working = document.createElement("span");
    working.className = "sum-tree__working";

    const addends = document.createElement("span");
    addends.className = "sum-tree__addends";
    const negated = new Set(run.negatedAddendCellIds);
    addends.textContent = run.addendCellIds
      .map((id, index) => {
        const printed = cells.get(id)?.text.trim() ?? "?";
        if (index === 0) return negated.has(id) ? `−${printed}` : printed;
        return `${negated.has(id) ? "−" : "+"}  ${printed}`;
      })
      .join("  ");

    const result = document.createElement("span");
    result.className = "sum-tree__result";
    const tied = run.delta === "0" || Number(run.delta) === 0;
    const printed = total?.text.trim() ?? total?.normalizedValue ?? "";
    result.textContent = tied
      ? `=  ${printed || run.sum}`
      : `=  ${run.sum}   ≠  ${printed || "the total"}`;

    working.append(addends, result);

    if (!tied) {
      const delta = document.createElement("span");
      delta.className = "sum-tree__delta";
      delta.textContent = run.diagnosis?.detail
        ? `Difference of ${strip(run.delta)} — ${run.diagnosis.detail}.`
        : `Difference of ${strip(run.delta)}.`;
      working.append(delta);
    }

    if (run.basis === "subtotals") {
      const basis = document.createElement("span");
      basis.className = "sum-tree__basis";
      // Where a total could be footed either way, the subtotals are the tree
      // the statement is asserting, and saying so is what stops a reviewer
      // reading the working as though it had skipped the leaf rows by accident.
      basis.textContent = "Footed on the subtotals beneath it";
      working.append(basis);
    }
    return working;
  }

  private emitOverview(): void {
    const model = this.model;
    if (!this.overview || !model) {
      this.callbacks.onOverview(null);
      return;
    }
    const items: SumTreeOverviewItem[] = [];
    for (const table of model.tables) {
      const cells = new Map(table.cells.map((cell) => [cell.id, cell]));
      for (const total of table.totals) {
        if (total.outcome !== this.activeTab) continue;
        const cell = cells.get(total.cellId);
        if (!cell) continue;
        items.push({
          id: total.id,
          pageIndex: table.pageIndex,
          bounds: cell.bounds,
          outcome: total.outcome,
        });
      }
    }
    this.callbacks.onOverview(items);
  }

  private updateOverviewButton(): void {
    if (!this.overviewButton) return;
    this.overviewButton.classList.toggle("sum-tree__overview-toggle--active", this.overview);
    this.overviewButton.setAttribute("aria-pressed", String(this.overview));
    this.overviewButton.textContent = this.overview ? "Showing all" : "Show all";
  }
}

function outcomeLabel(outcome: ReconcileTotal["outcome"]): string {
  switch (outcome) {
    case "confirmed": return "Verified";
    case "break": return "Exception";
    case "unresolved": return "Not checked";
  }
}

function columnLabel(table: ReconcileTable, total: ReconcileTotal): string {
  const header = table.headerLabels?.find(
    (entry) => entry.columnIndex === total.columnIndex,
  );
  return (header?.period ?? header?.text ?? "").trim();
}

function unresolvedWording(reason: string | undefined, cell: ReconcileCell | undefined): string {
  const value = cell?.text.trim() ?? "";
  const reads = value ? ` It reads ${value}.` : "";
  switch (reason) {
    case "no-candidate-run":
      return `Nothing stood above this cell that could be its addends.${reads}`;
    case "run-too-short":
      return `Only two rows stood above this cell, and a pair that happens to sum is not evidence enough to publish.${reads}`;
    case "mixed-decimals":
      return `The rows above this cell are printed to a different decimal place, so they are not figures of the same kind.${reads}`;
    case "rounding-indeterminate":
      return `The displayed figures differ only within their printed rounding precision, so the unrounded total cannot be checked exactly.${reads}`;
    case "no-plausible-run":
      return `A block stood above this cell but does not resemble its addends, so nothing is asserted about it either way.${reads}`;
    default:
      return `This total was not checked.${reads}`;
  }
}

function strip(delta: string): string {
  return delta.startsWith("-") ? delta.slice(1) : delta;
}
