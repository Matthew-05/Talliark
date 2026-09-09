/**
 * The "add or select a document" state.
 *
 * Shown when the workbook holds no documents at all. Reconcile is not a one-off
 * run: it opens to a home when there is reconcile data and to this when there
 * is nothing to scan yet.
 */
import type { ReconcileImportSource } from "../../types/index.js";

export class Splash {
  private readonly element: HTMLElement;
  private readonly sources: HTMLSelectElement;

  constructor(parent: HTMLElement, callbacks: { onImport(): void; onClipboard(): void; onCopy(id: string): void }) {
    this.element = document.createElement("section");
    this.element.className = "splash";
    this.element.innerHTML = `
      <h1 class="splash__title">Reconcile</h1>
      <p class="splash__lead">
        Check a document's totals against the sums it presents.
      </p>
      <p class="splash__hint">
        Import a statement directly, or copy an ordinary imported document into
        an independent Reconcile snapshot.
      </p>
      <div class="document-row__actions">
        <button type="button" class="button button--primary" data-import>Import statement</button>
        <button type="button" class="button button--secondary" data-clipboard>Import clipboard</button>
        <select data-sources aria-label="Imported document to copy"><option value="">Copy from imported documents…</option></select>
        <button type="button" class="button button--secondary" data-copy>Copy</button>
      </div>
    `;
    this.sources = this.element.querySelector<HTMLSelectElement>("[data-sources]")!;
    this.element.querySelector<HTMLButtonElement>("[data-import]")!.addEventListener("click", callbacks.onImport);
    this.element.querySelector<HTMLButtonElement>("[data-clipboard]")!.addEventListener("click", callbacks.onClipboard);
    this.element.querySelector<HTMLButtonElement>("[data-copy]")!.addEventListener("click", () => {
      if (this.sources.value) callbacks.onCopy(this.sources.value);
    });
    parent.appendChild(this.element);
    this.setVisible(false);
  }

  setSources(sources: ReconcileImportSource[]): void {
    this.sources.replaceChildren(new Option("Copy from imported documents…", ""),
      ...sources.map((source) => new Option(source.name, source.id)));
  }

  setVisible(visible: boolean): void {
    this.element.hidden = !visible;
  }
}
