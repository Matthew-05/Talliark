import {
  PdfViewer,
  applyNormalizedRectToElement,
  buildCharEntriesFromGeometry,
  buildSearchPageIndexFromEntries,
  extractTextGeometryFromPdfDocument,
  ensureOverlayLayer,
  normalizeSearchQuery,
  searchPageWithIndex,
} from "@talliark/shared";
import type { CharacterEntry, SearchMatch, SearchPageIndex } from "@talliark/shared";
import type { Bounds } from "../../types/index.js";

const SEARCH_DEBOUNCE_MS = 250;
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 4;

export interface ViewerFocus {
  readonly pageIndex: number;
  readonly bounds: Bounds[];
}

/**
 * Reconcile's read-only composition of the same shared PDF surface used by
 * rectangle linking. Only review/search overlays are added here.
 */
export class ReconcileViewer {
  readonly element: HTMLElement;

  private readonly viewer = new PdfViewer("Loading source document…");
  private readonly pageInput: HTMLInputElement;
  private readonly pageTotal: HTMLElement;
  private readonly zoomLabel: HTMLElement;
  private readonly searchInput: HTMLInputElement;
  private readonly searchCount: HTMLElement;
  private pdfName = "Document";
  private pdfId = "reconcile-document";
  private objectUrl: string | null = null;
  private currentPage = 1;
  private totalPages = 0;
  private zoom = 1;
  private searchTimer: ReturnType<typeof setTimeout> | null = null;
  private searchMatches: SearchMatch[] = [];
  private activeSearchIndex = -1;
  private readonly characters = new Map<number, CharacterEntry[]>();
  private readonly searchIndices = new Map<number, SearchPageIndex>();

  constructor() {
    this.element = document.createElement("section");
    this.element.className = "reconcile-viewer";

    const toolbar = document.createElement("div");
    toolbar.className = "reconcile-viewer__toolbar";
    const label = document.createElement("span");
    label.className = "reconcile-viewer__label";
    label.textContent = "Source document";

    const page = document.createElement("div");
    page.className = "page-controller toolbar__slot";
    this.pageInput = document.createElement("input");
    this.pageInput.className = "page-controller__input";
    this.pageInput.inputMode = "numeric";
    this.pageInput.value = "0";
    this.pageInput.setAttribute("aria-label", "Page number");
    this.pageInput.addEventListener("change", () => this.navigateFromPageInput());
    this.pageInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") this.navigateFromPageInput();
    });
    const separator = document.createElement("span");
    separator.className = "page-controller__separator";
    separator.textContent = "/";
    this.pageTotal = document.createElement("span");
    this.pageTotal.className = "page-controller__total";
    this.pageTotal.textContent = "0";
    page.append(this.pageInput, separator, this.pageTotal);

    const right = document.createElement("div");
    right.className = "reconcile-viewer__right";
    const zoomControls = document.createElement("div");
    zoomControls.className = "zoom-controller toolbar__slot";
    const zoomOut = this.control("−", "Zoom out", () => this.setZoom(this.zoom - 0.25));
    this.zoomLabel = document.createElement("span");
    this.zoomLabel.className = "zoom-controller__label";
    const zoomIn = this.control("+", "Zoom in", () => this.setZoom(this.zoom + 0.25));
    const fit = this.control("Fit", "Fit page", () => this.fitPage());
    fit.classList.add("zoom-controller__fit-btn");
    zoomControls.append(zoomOut, this.zoomLabel, zoomIn, fit);

    const search = document.createElement("div");
    search.className = "reconcile-search";
    this.searchInput = document.createElement("input");
    this.searchInput.type = "search";
    this.searchInput.className = "reconcile-search__input";
    this.searchInput.placeholder = "Indexing document…";
    this.searchInput.disabled = true;
    this.searchInput.addEventListener("input", () => this.scheduleSearch());
    this.searchInput.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      this.moveSearch(event.shiftKey ? -1 : 1);
    });
    this.searchCount = document.createElement("span");
    this.searchCount.className = "reconcile-search__count";
    const previous = this.control("‹", "Previous search result", () => this.moveSearch(-1));
    const next = this.control("›", "Next search result", () => this.moveSearch(1));
    search.append(this.searchInput, this.searchCount, previous, next);
    right.append(zoomControls, search);
    toolbar.append(label, page, right);
    this.element.append(toolbar, this.viewer.element);

    this.viewer.onLoaded((total) => {
      this.totalPages = total;
      this.currentPage = 1;
      this.pageInput.value = "1";
      this.pageInput.max = String(total);
      this.pageTotal.textContent = String(total);
    });
    this.viewer.element.addEventListener("scroll", () => this.updatePageFromScroll(), { passive: true });
    this.viewer.element.addEventListener("wheel", (event) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      const bounds = this.viewer.element.getBoundingClientRect();
      this.setZoom(this.zoom + (event.deltaY > 0 ? -0.1 : 0.1), {
        x: event.clientX - bounds.left,
        y: event.clientY - bounds.top,
      });
    }, { passive: false });
    this.updateZoomLabel();
  }

  async load(
    pdfBase64: string,
    pageRotations: Record<number, number>,
    pdfId = "reconcile-document",
    pdfName = "Document",
  ): Promise<void> {
    this.revokeObjectUrl();
    this.pdfId = pdfId;
    this.pdfName = pdfName;
    this.characters.clear();
    this.searchIndices.clear();
    this.clearSearch();
    this.searchInput.disabled = true;
    this.searchInput.placeholder = "Indexing document…";
    this.objectUrl = URL.createObjectURL(new Blob([decodeBase64(pdfBase64)], { type: "application/pdf" }));
    await this.viewer.loadDocument(this.objectUrl, pdfId, pageRotations);
    this.viewer.startBackgroundRender();
    const document_ = this.viewer.getDocument();
    if (!document_) return;
    try {
      const geometry = await extractTextGeometryFromPdfDocument(document_);
      const characters = buildCharEntriesFromGeometry(geometry);
      for (const [pageIndex, entries] of characters) {
        this.characters.set(pageIndex, entries);
        this.searchIndices.set(pageIndex, buildSearchPageIndexFromEntries(entries));
      }
      this.searchInput.disabled = false;
      this.searchInput.placeholder = "Search document…";
    } catch (error) {
      console.error("[Talliark] could not index Reconcile source PDF:", error);
      this.searchInput.placeholder = "Search unavailable";
    }
  }

  showUnavailable(): void {
    const state = document.createElement("div");
    state.className = "viewer__placeholder reconcile-viewer__state--error";
    state.textContent = "The source document is unavailable.";
    this.viewer.showEmptyState(state);
  }

  focus(focus: ViewerFocus): void {
    this.viewer.element.querySelectorAll(".reconcile-viewer__finding-highlight")
      .forEach((element) => element.remove());
    const page = this.viewer.element.querySelector<HTMLElement>(`[data-page="${focus.pageIndex + 1}"]`);
    if (!page) return;
    for (const bounds of focus.bounds) {
      const highlight = document.createElement("div");
      highlight.className = "reconcile-viewer__finding-highlight";
      applyNormalizedRectToElement(highlight, bounds);
      ensureOverlayLayer(page).appendChild(highlight);
    }
    void this.viewer.renderPageNow(focus.pageIndex + 1).then(() => {
      page.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  private scheduleSearch(): void {
    if (this.searchTimer !== null) clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => {
      this.searchTimer = null;
      this.runSearch();
    }, SEARCH_DEBOUNCE_MS);
  }

  private runSearch(): void {
    this.viewer.element.querySelectorAll(".reconcile-viewer__search-highlight")
      .forEach((element) => element.remove());
    this.searchMatches = [];
    this.activeSearchIndex = -1;
    const query = normalizeSearchQuery(this.searchInput.value);
    if (!query) {
      this.searchCount.textContent = "";
      return;
    }
    for (const [pageIndex, entries] of this.characters) {
      const index = this.searchIndices.get(pageIndex);
      if (!index) continue;
      this.searchMatches.push(...searchPageWithIndex(
        this.pdfId,
        this.pdfName,
        pageIndex,
        entries,
        index,
        query,
      ));
    }
    this.searchCount.textContent = this.searchMatches.length === 0
      ? "No results"
      : `1 / ${this.searchMatches.length}`;
    for (const match of this.searchMatches) {
      const page = this.viewer.element.querySelector<HTMLElement>(`[data-page="${match.pageIndex + 1}"]`);
      if (!page) continue;
      const highlight = document.createElement("div");
      highlight.className = "reconcile-viewer__search-highlight";
      highlight.dataset["searchMatchId"] = match.id;
      applyNormalizedRectToElement(highlight, match.highlightRect);
      ensureOverlayLayer(page).appendChild(highlight);
    }
    if (this.searchMatches.length > 0) {
      this.activeSearchIndex = 0;
      this.focusSearchMatch();
    }
  }

  private moveSearch(direction: number): void {
    if (this.searchMatches.length === 0) {
      this.runSearch();
      return;
    }
    this.activeSearchIndex = (
      this.activeSearchIndex + direction + this.searchMatches.length
    ) % this.searchMatches.length;
    this.focusSearchMatch();
  }

  private focusSearchMatch(): void {
    const match = this.searchMatches[this.activeSearchIndex];
    if (!match) return;
    this.viewer.element.querySelectorAll(".reconcile-viewer__search-highlight--active")
      .forEach((element) => element.classList.remove("reconcile-viewer__search-highlight--active"));
    const highlight = this.viewer.element.querySelector<HTMLElement>(
      `[data-search-match-id="${CSS.escape(match.id)}"]`,
    );
    highlight?.classList.add("reconcile-viewer__search-highlight--active");
    this.searchCount.textContent = `${this.activeSearchIndex + 1} / ${this.searchMatches.length}`;
    void this.viewer.renderPageNow(match.pageIndex + 1).then(() => {
      const page = this.viewer.element.querySelector<HTMLElement>(`[data-page="${match.pageIndex + 1}"]`);
      page?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  private clearSearch(): void {
    this.searchInput.value = "";
    this.searchCount.textContent = "";
    this.searchMatches = [];
    this.activeSearchIndex = -1;
  }

  private navigateFromPageInput(): void {
    const parsed = Number.parseInt(this.pageInput.value, 10);
    const page = Number.isNaN(parsed) ? this.currentPage : Math.max(1, Math.min(this.totalPages, parsed));
    this.currentPage = page;
    this.pageInput.value = String(page);
    this.viewer.scrollToPage(page);
    void this.viewer.renderPageNow(page);
  }

  private updatePageFromScroll(): void {
    const viewerBounds = this.viewer.element.getBoundingClientRect();
    let bestPage = this.currentPage;
    let bestVisible = -1;
    for (const { pageNumber, wrapper } of this.viewer.getPageLayout()) {
      const bounds = wrapper.getBoundingClientRect();
      const visible = Math.max(0, Math.min(bounds.bottom, viewerBounds.bottom) - Math.max(bounds.top, viewerBounds.top));
      if (visible > bestVisible) {
        bestVisible = visible;
        bestPage = pageNumber;
      }
    }
    if (bestPage !== this.currentPage) {
      this.currentPage = bestPage;
      this.pageInput.value = String(bestPage);
    }
  }

  private setZoom(next: number, anchor?: { x: number; y: number }): void {
    const zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, Math.round(next * 100) / 100));
    if (zoom === this.zoom) return;
    this.zoom = zoom;
    this.viewer.setZoom(zoom, anchor);
    this.updateZoomLabel();
  }

  private fitPage(): void {
    const scale = this.viewer.getPageFitScale(this.currentPage);
    if (scale !== null) this.setZoom(scale);
  }

  private updateZoomLabel(): void {
    this.zoomLabel.textContent = `${Math.round(this.zoom * 100)}%`;
  }

  private control(label: string, title: string, action: () => void): HTMLButtonElement {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "zoom-controller__btn";
    button.textContent = label;
    button.title = title;
    button.setAttribute("aria-label", title);
    button.addEventListener("click", action);
    return button;
  }

  private revokeObjectUrl(): void {
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = null;
  }
}

function decodeBase64(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}
