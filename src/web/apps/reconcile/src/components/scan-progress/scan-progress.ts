/**
 * One scan's progress, and the only control that can stop it.
 *
 * The bar is driven by the stage the host states, never by the message beside
 * it. `current`/`total` count within a stage only, which is why they scale that
 * stage's own span rather than driving the bar — the counts arriving during
 * recognition are pages and during transfer are chunks, and feeding either
 * straight to a bar makes it fill and reset several times.
 */
import { fileProgressFraction, stageLabel } from "@talliark/shared";

import type { ScanProgress, ScanStatus } from "../../types/index.js";

export interface ScanProgressCallbacks {
  onCancel(): void;
}

export class ScanProgressPanel {
  private readonly floatingParent: HTMLElement;
  private readonly element: HTMLElement;
  private readonly nameEl: HTMLElement;
  private readonly stageEl: HTMLElement;
  private readonly detailEl: HTMLElement;
  private readonly percentEl: HTMLElement;
  private readonly trackEl: HTMLElement;
  private readonly fillEl: HTMLElement;
  private readonly cancelButton: HTMLButtonElement;

  constructor(parent: HTMLElement, callbacks: ScanProgressCallbacks) {
    this.floatingParent = parent;
    this.element = document.createElement("section");
    this.element.className = "scan-progress";
    this.element.setAttribute("aria-labelledby", "scan-progress-name");

    const header = document.createElement("div");
    header.className = "scan-progress__header";

    const activity = document.createElement("span");
    activity.className = "scan-progress__activity";
    activity.setAttribute("aria-hidden", "true");

    const identity = document.createElement("div");
    identity.className = "scan-progress__identity";

    const eyebrow = document.createElement("div");
    eyebrow.className = "scan-progress__eyebrow";
    eyebrow.textContent = "Reconcile is scanning";

    this.nameEl = document.createElement("div");
    this.nameEl.className = "scan-progress__name";
    this.nameEl.id = "scan-progress-name";
    identity.append(eyebrow, this.nameEl);

    this.percentEl = document.createElement("div");
    this.percentEl.className = "scan-progress__percent";
    header.append(activity, identity, this.percentEl);

    this.stageEl = document.createElement("div");
    this.stageEl.className = "scan-progress__stage";
    this.stageEl.setAttribute("aria-live", "polite");

    this.trackEl = document.createElement("div");
    this.trackEl.className = "scan-progress__track";
    this.trackEl.setAttribute("role", "progressbar");
    this.trackEl.setAttribute("aria-valuemin", "0");
    this.trackEl.setAttribute("aria-valuemax", "100");
    this.fillEl = document.createElement("div");
    this.fillEl.className = "scan-progress__fill";
    this.trackEl.appendChild(this.fillEl);

    this.detailEl = document.createElement("div");
    this.detailEl.className = "scan-progress__detail";

    this.cancelButton = document.createElement("button");
    this.cancelButton.type = "button";
    this.cancelButton.className = "button button--quiet scan-progress__cancel";
    this.cancelButton.textContent = "Cancel scan";
    this.cancelButton.addEventListener("click", () => callbacks.onCancel());

    const footer = document.createElement("div");
    footer.className = "scan-progress__footer";
    const safetyNote = document.createElement("p");
    safetyNote.textContent = "Saved results stay available until this finishes.";
    footer.append(safetyNote, this.cancelButton);

    this.element.append(
      header,
      this.stageEl,
      this.trackEl,
      this.detailEl,
      footer,
    );
    parent.appendChild(this.element);
    this.setVisible(false);
  }

  showInline(parent: HTMLElement): void {
    this.element.classList.add("scan-progress--inline");
    parent.appendChild(this.element);
  }

  showFloating(): void {
    this.element.classList.remove("scan-progress--inline");
    this.floatingParent.appendChild(this.element);
  }

  begin(documentName: string): void {
    this.nameEl.textContent = documentName || "Selected document";
    this.stageEl.textContent = "Starting scan";
    this.detailEl.textContent = "Preparing the document…";
    this.percentEl.textContent = "0%";
    this.fillEl.style.width = "0%";
    this.trackEl.setAttribute("aria-valuenow", "0");
    this.trackEl.setAttribute("aria-valuetext", "Starting scan");
  }

  setVisible(visible: boolean): void {
    this.element.hidden = !visible;
    if (!visible) {
      this.fillEl.style.width = "0%";
      this.trackEl.setAttribute("aria-valuenow", "0");
    }
  }

  update(documentName: string, status: ScanStatus, progress: ScanProgress): void {
    this.nameEl.textContent = documentName;

    if (status === "queued") {
      this.stageEl.textContent = "Waiting to start";
      this.detailEl.textContent = "Your scan is queued and will begin shortly.";
      this.percentEl.textContent = "0%";
      this.trackEl.setAttribute("aria-valuenow", "0");
      this.trackEl.setAttribute("aria-valuetext", "Waiting to start");
      return;
    }

    // A stage the app has no entry for leaves the bar where it was rather than
    // guessing at a position for it.
    const label = stageLabel(progress.stage);
    this.stageEl.textContent = label ?? "Scanning";
    this.detailEl.textContent = progressDetail(progress);

    const fraction = fileProgressFraction(
      progress.stage,
      progress.current,
      progress.total,
    );
    if (fraction !== null) {
      const percent = Math.round(fraction * 100);
      this.fillEl.style.width = `${(fraction * 100).toFixed(1)}%`;
      this.percentEl.textContent = `${percent}%`;
      this.trackEl.setAttribute("aria-valuenow", String(percent));
      this.trackEl.setAttribute("aria-valuetext", `${label ?? "Scanning"}, ${percent}%`);
    } else {
      this.percentEl.textContent = "";
      this.trackEl.removeAttribute("aria-valuenow");
      this.trackEl.setAttribute("aria-valuetext", label ?? "Scanning");
    }
  }
}

function progressDetail(progress: ScanProgress): string {
  if (typeof progress.current !== "number" || typeof progress.total !== "number" || progress.total <= 0) {
    return "Working through the document…";
  }

  const unit = progress.unit?.trim();
  if (!unit) return `${progress.current} of ${progress.total}`;
  const suffix = progress.total === 1 || unit.endsWith("s") ? unit : `${unit}s`;
  return `${progress.current} of ${progress.total} ${suffix}`;
}
