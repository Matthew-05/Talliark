import type { ReconcileDocument } from "../../types/index.js";

export type ReviewMode = "mathematical" | "intra-document" | "inter-document";

export interface ResultsShellCallbacks {
  onBack(): void;
  onRescan(pdfId: string): void;
  onModeChanged(mode: ReviewMode): void;
}

const MODES: ReadonlyArray<{ id: ReviewMode; label: string }> = [
  { id: "mathematical", label: "Mathematical Accuracy" },
  { id: "intra-document", label: "Intra Document Consistency" },
  { id: "inter-document", label: "Inter Document Consistency" },
];

export class ResultsShell {
  readonly element: HTMLElement;
  readonly sidebarSlot: HTMLElement;
  readonly viewerSlot: HTMLElement;
  private readonly tabs = new Map<ReviewMode, HTMLButtonElement>();
  private readonly staleNotice: HTMLElement;

  constructor(entry: ReconcileDocument, callbacks: ResultsShellCallbacks) {
    this.element = document.createElement("div");
    this.element.className = "results-shell";

    const topbar = document.createElement("header");
    topbar.className = "results-shell__topbar";
    const identity = document.createElement("div");
    identity.className = "results-shell__identity";
    const back = document.createElement("button");
    back.type = "button";
    back.className = "results-shell__back";
    back.textContent = "← Documents";
    back.addEventListener("click", callbacks.onBack);
    const title = document.createElement("strong");
    title.className = "results-shell__title";
    title.textContent = entry.name;
    const badge = document.createElement("span");
    badge.className = `status-badge status-badge--${entry.staleness}`;
    badge.textContent = entry.staleness === "stale" ? "Stale" : "Current";
    identity.append(back, title, badge);

    const actions = document.createElement("div");
    actions.className = "results-shell__actions";
    const rescan = document.createElement("button");
    rescan.type = "button";
    rescan.className = "button button--secondary";
    rescan.textContent = "Re-scan";
    rescan.addEventListener("click", () => callbacks.onRescan(entry.id));
    actions.appendChild(rescan);
    topbar.append(identity, actions);

    const navigation = document.createElement("nav");
    navigation.className = "results-shell__tabs";
    navigation.setAttribute("aria-label", "Review views");
    for (const mode of MODES) {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = "results-shell__tab";
      tab.textContent = mode.label;
      tab.addEventListener("click", () => {
        this.setMode(mode.id);
        callbacks.onModeChanged(mode.id);
      });
      this.tabs.set(mode.id, tab);
      navigation.appendChild(tab);
    }

    this.staleNotice = document.createElement("div");
    this.staleNotice.className = "stale-notice results-shell__stale";
    this.staleNotice.hidden = entry.staleness !== "stale";
    this.staleNotice.textContent = "This scan no longer matches the document’s current analysis data. Re-scan before relying on it.";

    const workspace = document.createElement("div");
    workspace.className = "results-shell__workspace";
    this.sidebarSlot = document.createElement("div");
    this.sidebarSlot.className = "results-shell__sidebar-slot";
    this.viewerSlot = document.createElement("div");
    this.viewerSlot.className = "results-shell__viewer-slot";
    workspace.append(this.sidebarSlot, this.viewerSlot);
    this.element.append(topbar, navigation, this.staleNotice, workspace);
    this.setMode("mathematical");
  }

  setMode(mode: ReviewMode): void {
    for (const [id, tab] of this.tabs) {
      const active = id === mode;
      tab.classList.toggle("results-shell__tab--active", active);
      tab.setAttribute("aria-current", active ? "page" : "false");
    }
  }
}
