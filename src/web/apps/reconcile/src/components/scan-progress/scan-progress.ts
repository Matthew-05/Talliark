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
  private readonly element: HTMLElement;
  private readonly nameEl: HTMLElement;
  private readonly stageEl: HTMLElement;
  private readonly fillEl: HTMLElement;
  private readonly cancelButton: HTMLButtonElement;

  constructor(parent: HTMLElement, callbacks: ScanProgressCallbacks) {
    this.element = document.createElement("section");
    this.element.className = "scan-progress";

    this.nameEl = document.createElement("div");
    this.nameEl.className = "scan-progress__name";

    this.stageEl = document.createElement("div");
    this.stageEl.className = "scan-progress__stage";

    const track = document.createElement("div");
    track.className = "scan-progress__track";
    this.fillEl = document.createElement("div");
    this.fillEl.className = "scan-progress__fill";
    track.appendChild(this.fillEl);

    this.cancelButton = document.createElement("button");
    this.cancelButton.type = "button";
    this.cancelButton.className = "button button--secondary";
    this.cancelButton.textContent = "Cancel scan";
    this.cancelButton.addEventListener("click", () => callbacks.onCancel());

    this.element.append(this.nameEl, this.stageEl, track, this.cancelButton);
    parent.appendChild(this.element);
    this.setVisible(false);
  }

  setVisible(visible: boolean): void {
    this.element.hidden = !visible;
    if (!visible) this.fillEl.style.width = "0%";
  }

  update(documentName: string, status: ScanStatus, progress: ScanProgress): void {
    this.nameEl.textContent = documentName;

    if (status === "queued") {
      this.stageEl.textContent = "Queued";
      return;
    }

    // A stage the app has no entry for leaves the bar where it was rather than
    // guessing at a position for it.
    const label = stageLabel(progress.stage);
    this.stageEl.textContent = label ?? "Scanning";

    const fraction = fileProgressFraction(
      progress.stage,
      progress.current,
      progress.total,
    );
    if (fraction !== null) {
      this.fillEl.style.width = `${(fraction * 100).toFixed(1)}%`;
    }
  }
}
