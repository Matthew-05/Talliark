import { summarySentence } from "../document-list/document-list.js";
import type {
  ReconcileDocument,
  ReconcileFinding,
  ReconcileModel,
  ReconcileOutcome,
  ReconcileTable,
  ReconcileTotal,
} from "../../types/index.js";

export interface ResultViewCallbacks {
  onBack(): void;
  onRescan(pdfId: string): void;
}

export class ResultView {
  private readonly element: HTMLElement;

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
    this.renderShell(entry, this.state("Loading scan result…"));
  }

  showError(entry: ReconcileDocument, message: string): void {
    this.renderShell(entry, this.state(message, true));
  }

  showResult(entry: ReconcileDocument, model: ReconcileModel): void {
    const content = document.createElement("div");
    content.className = "result-view__content";
    content.append(
      this.overview(model),
      this.findings(model),
      this.checkedTables(model),
    );
    this.renderShell(entry, content);
  }

  private renderShell(entry: ReconcileDocument, content: HTMLElement): void {
    const header = document.createElement("header");
    header.className = "result-header";

    const back = document.createElement("button");
    back.type = "button";
    back.className = "result-header__back";
    back.textContent = "← All documents";
    back.addEventListener("click", () => this.callbacks.onBack());

    const titleBlock = document.createElement("div");
    titleBlock.className = "result-header__title-block";
    const eyebrow = document.createElement("div");
    eyebrow.className = "result-header__eyebrow";
    eyebrow.textContent = "Reconcile result";
    const title = document.createElement("h1");
    title.className = "result-header__title";
    title.textContent = entry.name;
    titleBlock.append(eyebrow, title);

    const badge = document.createElement("span");
    badge.className = `status-badge status-badge--${entry.staleness}`;
    badge.textContent = entry.staleness === "stale" ? "Stale result" : "Current";

    const rescan = document.createElement("button");
    rescan.type = "button";
    rescan.className = "button button--secondary";
    rescan.textContent = "Re-scan";
    rescan.addEventListener("click", () => this.callbacks.onRescan(entry.id));

    header.append(back, titleBlock, badge, rescan);

    const stale = document.createElement("div");
    stale.className = "stale-notice";
    stale.hidden = entry.staleness !== "stale";
    stale.textContent = "This result no longer matches the document’s current analysis data. Re-scan before relying on it.";

    this.element.replaceChildren(header, stale, content);
  }

  private overview(model: ReconcileModel): HTMLElement {
    const section = document.createElement("section");
    section.className = "result-section overview";

    const verdict = document.createElement("div");
    verdict.className = `overview__verdict ${model.summary.breaks > 0 ? "overview__verdict--attention" : "overview__verdict--clean"}`;
    const verdictTitle = document.createElement("h2");
    verdictTitle.textContent = model.summary.breaks > 0
      ? `${model.summary.breaks} ${model.summary.breaks === 1 ? "exception needs" : "exceptions need"} attention`
      : model.summary.totalsNominated > 0
        ? "No arithmetic exceptions found"
        : "Scan complete";
    const verdictText = document.createElement("p");
    verdictText.textContent = summarySentence(model.summary);
    verdict.append(verdictTitle, verdictText);

    const metrics = document.createElement("div");
    metrics.className = "metric-grid";
    metrics.append(
      this.metric(model.summary.totalsNominated, "Totals checked"),
      this.metric(model.summary.confirmed, "Verified", "success"),
      this.metric(model.summary.breaks, "Exceptions", model.summary.breaks > 0 ? "danger" : undefined),
      this.metric(model.summary.unresolved, "Not resolved", model.summary.unresolved > 0 ? "warning" : undefined),
    );

    const meta = document.createElement("p");
    meta.className = "overview__meta";
    const scannedAt = model.source.scannedAt ? formatDate(model.source.scannedAt) : null;
    meta.textContent = [
      `${model.summary.tablesExamined} ${model.summary.tablesExamined === 1 ? "table" : "tables"} examined`,
      `${model.source.pageCount} ${model.source.pageCount === 1 ? "page" : "pages"}`,
      scannedAt ? `Scanned ${scannedAt}` : null,
    ].filter((value): value is string => value !== null).join(" · ");

    section.append(verdict, metrics, meta);
    return section;
  }

  private metric(value: number, label: string, tone?: string): HTMLElement {
    const card = document.createElement("div");
    card.className = `metric${tone ? ` metric--${tone}` : ""}`;
    const number = document.createElement("strong");
    number.textContent = String(value);
    const caption = document.createElement("span");
    caption.textContent = label;
    card.append(number, caption);
    return card;
  }

  private findings(model: ReconcileModel): HTMLElement {
    const section = this.section("Findings", "What the scan is prepared to say about the document.");
    const body = document.createElement("div");
    body.className = "finding-list";

    if (model.findings.length === 0) {
      body.appendChild(this.emptyFinding(model));
    } else {
      for (const finding of model.findings) body.appendChild(this.finding(finding));
    }
    section.appendChild(body);
    return section;
  }

  private emptyFinding(model: ReconcileModel): HTMLElement {
    const empty = document.createElement("div");
    empty.className = "empty-result";
    const mark = document.createElement("span");
    mark.className = "empty-result__mark";
    mark.textContent = model.summary.confirmed > 0 ? "✓" : "—";
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = model.summary.confirmed > 0 ? "Nothing needs attention" : "No findings to show";
    const detail = document.createElement("p");
    detail.textContent = model.summary.confirmed > 0
      ? `The scan verified ${model.summary.confirmed} ${model.summary.confirmed === 1 ? "total" : "totals"} without an exception.`
      : model.summary.tablesExamined > 0
        ? "Tables were examined, but the scan found no totals it could check."
        : "The scan found no tables to check in this document.";
    copy.append(title, detail);
    empty.append(mark, copy);
    return empty;
  }

  private finding(finding: ReconcileFinding): HTMLElement {
    const card = document.createElement("article");
    card.className = `finding-card${finding.kind === "footing-unresolved" ? " finding-card--unresolved" : ""}`;
    const top = document.createElement("div");
    top.className = "finding-card__top";
    const kind = document.createElement("span");
    kind.className = "finding-card__kind";
    kind.textContent = findingLabel(finding.kind);
    const page = document.createElement("span");
    page.className = "finding-card__page";
    page.textContent = finding.pageIndex === undefined ? "" : `Page ${finding.pageIndex + 1}`;
    const sentence = document.createElement("p");
    sentence.className = "finding-card__sentence";
    sentence.textContent = finding.sentence;
    top.append(kind, page);
    card.append(top, sentence);
    return card;
  }

  private checkedTables(model: ReconcileModel): HTMLElement {
    const section = this.section("What was checked", "The complete sum tree retained with this scan.");
    const list = document.createElement("div");
    list.className = "table-list";
    if (model.tables.length === 0) {
      const note = document.createElement("p");
      note.className = "table-list__empty";
      note.textContent = model.summary.tablesExamined > 0
        ? "Table-level detail was not published by this detector version."
        : "No tables were available to inspect.";
      list.appendChild(note);
    } else {
      for (const [index, table] of model.tables.entries()) {
        list.appendChild(this.table(table, index));
      }
    }
    section.appendChild(list);
    return section;
  }

  private table(table: ReconcileTable, index: number): HTMLElement {
    const details = document.createElement("details");
    details.className = "checked-table";
    const summary = document.createElement("summary");
    const name = document.createElement("span");
    name.textContent = `Table ${index + 1} · Page ${table.pageIndex + 1}`;
    const count = document.createElement("span");
    count.className = "checked-table__count";
    count.textContent = `${table.totals.length} ${table.totals.length === 1 ? "total" : "totals"}`;
    summary.append(name, count);

    const totals = document.createElement("div");
    totals.className = "checked-table__totals";
    if (table.totals.length === 0) {
      const empty = document.createElement("p");
      empty.textContent = "No totals nominated in this table.";
      totals.appendChild(empty);
    } else {
      const cells = new Map(table.cells.map((cell) => [cell.id, cell]));
      for (const total of table.totals) {
        const row = document.createElement("div");
        row.className = "checked-total";
        const badge = document.createElement("span");
        badge.className = `outcome outcome--${total.outcome}`;
        badge.textContent = outcomeLabel(total.outcome);
        const copy = document.createElement("div");
        const totalCell = cells.get(total.cellId);
        const label = document.createElement("strong");
        label.textContent = totalCell?.rowLabel || totalCell?.text || `Row ${total.rowIndex + 1}`;
        const arithmetic = document.createElement("p");
        arithmetic.textContent = arithmeticText(total, totalCell?.normalizedValue);
        copy.append(label, arithmetic);
        row.append(badge, copy);
        totals.appendChild(row);
      }
    }
    details.append(summary, totals);
    return details;
  }

  private section(titleText: string, description: string): HTMLElement {
    const section = document.createElement("section");
    section.className = "result-section";
    const heading = document.createElement("div");
    heading.className = "result-section__heading";
    const title = document.createElement("h2");
    title.textContent = titleText;
    const detail = document.createElement("p");
    detail.textContent = description;
    heading.append(title, detail);
    section.appendChild(heading);
    return section;
  }

  private state(message: string, error = false): HTMLElement {
    const state = document.createElement("div");
    state.className = `result-state${error ? " result-state--error" : ""}`;
    state.textContent = message;
    return state;
  }
}

function outcomeLabel(outcome: ReconcileOutcome): string {
  if (outcome === "confirmed") return "Verified";
  if (outcome === "break") return "Exception";
  return "Not resolved";
}

function findingLabel(kind: ReconcileFinding["kind"]): string {
  switch (kind) {
    case "footing-break": return "Footing exception";
    case "footing-unresolved": return "Not resolved";
    case "cross-foot-break": return "Cross-foot exception";
    case "ruling-disagreement": return "Structure disagreement";
  }
}

function arithmeticText(total: ReconcileTotal, printedTotal?: string): string {
  if (!total.resolution) return "The scan could not identify a plausible run for this total.";
  const target = printedTotal ? `; printed total ${printedTotal}` : "";
  return `${total.resolution.addendCellIds.length} addends sum to ${total.resolution.sum}${target}; difference ${total.resolution.delta}.`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}
