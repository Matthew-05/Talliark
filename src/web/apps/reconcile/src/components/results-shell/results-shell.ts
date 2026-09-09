import type { ReconcileDocument } from "../../types/index.js";

export type ReviewMode = "mathematical" | "intra-document" | "inter-document";

export interface ResultsShellCallbacks {
  onRescan(pdfId: string): void;
  onModeChanged(mode: ReviewMode): void;
}

export interface ResultsShellOptions {
  readonly layout?: "review" | "overview";
  readonly showRescan?: boolean;
  readonly scanning?: boolean;
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
  readonly overviewSlot: HTMLElement | null;
  private readonly tabs = new Map<ReviewMode, HTMLButtonElement>();

  constructor(
    entry: ReconcileDocument,
    callbacks: ResultsShellCallbacks,
    options: ResultsShellOptions = {},
  ) {
    const layout = options.layout ?? "review";
    this.element = document.createElement("div");
    this.element.className = `results-shell results-shell--${layout}`;

    const topbar = document.createElement("header");
    topbar.className = "results-shell__topbar";
    const identity = document.createElement("div");
    identity.className = "results-shell__identity";
    const title = document.createElement("strong");
    title.className = "results-shell__title";
    title.textContent = entry.name;
    const badge = document.createElement("span");
    badge.className = `status-badge status-badge--${options.scanning ? "scanning" : entry.staleness}`;
    badge.textContent = options.scanning
      ? "Scanning"
      : entry.staleness === "stale" ? "Stale"
        : entry.staleness === "none" ? "Not scanned" : "Current";
    identity.append(title, badge);

    const actions = document.createElement("div");
    actions.className = "results-shell__actions";
    const rescan = document.createElement("button");
    rescan.type = "button";
    rescan.className = "button button--secondary";
    rescan.textContent = "Re-scan";
    rescan.addEventListener("click", () => callbacks.onRescan(entry.id));
    if (options.showRescan ?? layout === "review") actions.appendChild(rescan);
    topbar.append(identity, actions);

    this.sidebarSlot = document.createElement("div");
    this.sidebarSlot.className = "results-shell__sidebar-slot";
    this.viewerSlot = document.createElement("div");
    this.viewerSlot.className = "results-shell__viewer-slot";

    if (layout === "overview") {
      this.overviewSlot = document.createElement("main");
      this.overviewSlot.className = "results-shell__overview-slot";
      this.element.append(topbar, this.overviewSlot);
      return;
    }
    this.overviewSlot = null;

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

    const staleNotice = document.createElement("div");
    staleNotice.className = "stale-notice results-shell__stale";
    staleNotice.hidden = entry.staleness !== "stale";
    staleNotice.textContent = "This scan no longer matches the document’s current analysis data. Re-scan before relying on it.";

    const workspace = document.createElement("div");
    workspace.className = "results-shell__workspace";
    workspace.append(this.sidebarSlot, this.viewerSlot);
    this.element.append(topbar, navigation, staleNotice, workspace);
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
