/**
 * The home: every document in the workbook, and what the workbook knows about
 * each one.
 *
 * The wording here carries the module's credibility, so it is deliberate in
 * three places.
 *
 * A document that has never been scanned says so. A window that renders an
 * unscanned document the same way it renders a clean one is asserting something
 * it does not know.
 *
 * A clean scan is a result, not silence: "14 totals verified, 0 exceptions" is
 * what makes a zero-finding scan trustworthy instead of indistinguishable from
 * a scan that never ran.
 *
 * And an unresolved total is never worded as a failure of the document. It
 * means the scan found nothing it could check, which is a statement about the
 * scan.
 */
import type { ReconcileDocument, ReconcileImportSource, ReconcileSummary } from "../../types/index.js";

export interface DocumentListCallbacks {
  onScan(pdfId: string): void;
  onOpen(pdfId: string): void;
  onImport(): void;
  onClipboard(): void;
  onCopy(sourcePdfId: string): void;
}

/** What a stored scan came to, in the sentence the home prints for it. */
export function summarySentence(summary: ReconcileSummary): string {
  if (summary.totalsNominated === 0) {
    // The correct outcome on an invoice or a scanned letter, and not an error
    // state — but the window must be honest about having found nothing to check
    // rather than implying the document passed.
    return summary.tablesExamined === 0
      ? "No tables found to check"
      : `${plural(summary.tablesExamined, "table")} examined, no totals to check`;
  }

  const parts = [`${plural(summary.confirmed, "total")} verified`];
  parts.push(
    summary.breaks === 0
      ? "0 exceptions"
      : `${plural(summary.breaks, "exception")}`,
  );
  if (summary.unresolved > 0) {
    parts.push(`${summary.unresolved} not resolved`);
  }
  return parts.join(", ");
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

export class DocumentList {
  private readonly element: HTMLElement;
  private readonly body: HTMLElement;
  private documents: ReconcileDocument[] = [];
  private scanningId: string | null = null;
  private sources: ReconcileImportSource[] = [];
  private readonly callbacks: DocumentListCallbacks;

  constructor(parent: HTMLElement, callbacks: DocumentListCallbacks) {
    this.callbacks = callbacks;
    this.element = document.createElement("section");
    this.element.className = "document-list";

    const heading = document.createElement("h1");
    heading.className = "document-list__title";
    heading.textContent = "Reconcile";

    this.body = document.createElement("div");
    this.body.className = "document-list__body";

    this.element.append(heading, this.body);
    parent.appendChild(this.element);
    this.setVisible(false);
  }

  setVisible(visible: boolean): void {
    this.element.hidden = !visible;
  }

  setScanning(pdfId: string | null): void {
    this.scanningId = pdfId;
    this.render();
  }

  update(documents: ReconcileDocument[]): void {
    this.documents = documents;
    this.render();
  }
  setSources(sources: ReconcileImportSource[]): void { this.sources = sources; this.render(); }

  private render(): void {
    this.body.replaceChildren();
    for (const document_ of this.documents) {
      this.body.appendChild(this.row(document_));
    }
    if (this.documents.length > 0) {
      const replace = document.createElement("div");
      replace.className = "document-row__actions";
      const button = document.createElement("button");
      button.type = "button"; button.className = "button button--secondary";
      button.textContent = "Replace primary…"; button.disabled = this.scanningId !== null;
      button.addEventListener("click", this.callbacks.onImport);
      const clipboard = document.createElement("button");
      clipboard.type = "button"; clipboard.className = "button button--secondary";
      clipboard.textContent = "Replace from clipboard"; clipboard.disabled = this.scanningId !== null;
      clipboard.addEventListener("click", this.callbacks.onClipboard);
      const select = document.createElement("select");
      select.append(new Option("Copy replacement from imported documents…", ""),
        ...this.sources.map((source) => new Option(source.name, source.id)));
      const copy = document.createElement("button");
      copy.type = "button"; copy.className = "button button--secondary"; copy.textContent = "Replace with copy";
      copy.disabled = this.scanningId !== null;
      copy.addEventListener("click", () => { if (select.value) this.callbacks.onCopy(select.value); });
      replace.append(button, clipboard, select, copy);
      this.body.appendChild(replace);
    }
  }

  private row(entry: ReconcileDocument): HTMLElement {
    const row = document.createElement("article");
    row.className = "document-row";
    if (entry.staleness === "stale") row.classList.add("document-row--stale");

    const name = document.createElement("div");
    name.className = "document-row__name";
    name.textContent = entry.name;

    const status = document.createElement("div");
    status.className = "document-row__status";
    status.textContent = this.statusText(entry);

    const actions = document.createElement("div");
    actions.className = "document-row__actions";

    if (entry.summary && entry.staleness !== "none") {
      const open = document.createElement("button");
      open.type = "button";
      open.className = "button button--secondary";
      open.textContent = "Open";
      open.addEventListener("click", () => this.callbacks.onOpen(entry.id));
      actions.appendChild(open);
    }

    const scan = document.createElement("button");
    scan.type = "button";
    scan.className = "button button--primary";
    // Only an explicit re-scan replaces a result, so the verb changes but the
    // action does not: nothing recomputes on its own.
    scan.textContent = entry.staleness === "none" ? "Scan" : "Re-scan";
    scan.disabled = this.scanningId !== null;
    scan.addEventListener("click", () => this.callbacks.onScan(entry.id));
    actions.appendChild(scan);

    row.append(name, status, actions);
    return row;
  }

  private statusText(entry: ReconcileDocument): string {
    if (entry.staleness === "none" || !entry.summary) return "Not scanned";
    const sentence = summarySentence(entry.summary);
    // A stale result is labelled stale and still shown. It is not recomputed
    // and not hidden: a reviewer who has read a finding must be able to find it
    // again, and must be told it no longer matches the document.
    return entry.staleness === "stale"
      ? `Stale — ${sentence}. Re-scan to refresh.`
      : sentence;
  }
}
