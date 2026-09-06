/**
 * The "add or select a document" state.
 *
 * Shown when the workbook holds no documents at all. Reconcile is not a one-off
 * run: it opens to a home when there is reconcile data and to this when there
 * is nothing to scan yet.
 */
export class Splash {
  private readonly element: HTMLElement;

  constructor(parent: HTMLElement) {
    this.element = document.createElement("section");
    this.element.className = "splash";
    this.element.innerHTML = `
      <h1 class="splash__title">Reconcile</h1>
      <p class="splash__lead">
        Check a document's totals against the sums it presents.
      </p>
      <p class="splash__hint">
        This workbook has no documents yet. Add one with Manage Files, then run
        a scan on it here.
      </p>
    `;
    parent.appendChild(this.element);
    this.setVisible(false);
  }

  setVisible(visible: boolean): void {
    this.element.hidden = !visible;
  }
}
