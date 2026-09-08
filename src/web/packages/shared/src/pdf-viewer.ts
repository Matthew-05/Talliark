import * as pdfjsLib from "pdfjs-dist";

pdfjsLib.GlobalWorkerOptions.workerSrc = "pdf.worker.min.mjs";

const ZOOM_DEBOUNCE_MS = 300;

interface PageEntry {
  wrapper: HTMLDivElement;
  baseWidth: number;
  baseHeight: number;
  renderedScale: number | null;
  nativeRotation: number;
  rotation: number;
}

/**
 * Shared PDF surface used by review and linking experiences.
 *
 * This class owns only document loading, page layout, rendering, rotation,
 * zoom and navigation. Consumers add search, findings, or linking overlays to
 * the stable page wrappers without those concerns entering the viewer core.
 */
export class PdfViewer {
  readonly element: HTMLElement;

  private document: pdfjsLib.PDFDocumentProxy | null = null;
  private activePdfId: string | null = null;
  private scale = 1;
  private pages: PageEntry[] = [];
  private readonly pageRotations = new Map<number, number>();
  private renderingQueue: Promise<void> = Promise.resolve();
  private zoomDebounce: ReturnType<typeof setTimeout> | null = null;
  private loadGeneration = 0;
  private renderGeneration = 0;
  private loadComplete: Promise<void> = Promise.resolve();
  private hasDocument = false;
  private readonly loadedCallbacks: Array<(totalPages: number) => void> = [];
  private readonly documentChangedCallbacks: Array<() => void> = [];
  private readonly availabilityCallbacks: Array<(hasDocument: boolean) => void> = [];

  constructor(placeholder = "Talliark Initializing…") {
    this.element = document.createElement("div");
    this.element.className = "viewer";
    const state = document.createElement("div");
    state.className = "viewer__placeholder";
    state.textContent = placeholder;
    this.element.appendChild(state);
  }

  onLoaded(callback: (totalPages: number) => void): void {
    this.loadedCallbacks.push(callback);
  }

  onDocumentChanged(callback: () => void): void {
    this.documentChangedCallbacks.push(callback);
  }

  onDocumentAvailabilityChanged(callback: (hasDocument: boolean) => void): void {
    this.availabilityCallbacks.push(callback);
    callback(this.hasDocument);
  }

  getDocument(): pdfjsLib.PDFDocumentProxy | null { return this.document; }
  getActivePdfId(): string | null { return this.activePdfId; }
  getCurrentZoom(): number { return this.scale; }
  waitForLoad(): Promise<void> { return this.loadComplete; }

  showEmptyState(content: HTMLElement): void {
    ++this.loadGeneration;
    ++this.renderGeneration;
    this.cancelZoomDebounce();
    this.activePdfId = null;
    this.pages = [];
    this.pageRotations.clear();
    void this.document?.destroy();
    this.document = null;
    this.element.classList.add("viewer--empty");
    this.element.replaceChildren(content);
    this.setAvailability(false);
  }

  getPageLayout(): Array<{ pageNumber: number; wrapper: HTMLDivElement }> {
    return this.pages.map((entry, index) => ({ pageNumber: index + 1, wrapper: entry.wrapper }));
  }

  async loadDocument(
    url: string,
    pdfId?: string,
    rotations?: Record<number, number>,
  ): Promise<void> {
    const generation = ++this.loadGeneration;
    ++this.renderGeneration;
    this.cancelZoomDebounce();
    this.activePdfId = pdfId ?? null;
    this.pages = [];
    this.pageRotations.clear();
    for (const [key, value] of Object.entries(rotations ?? {})) {
      if (value !== 0) this.pageRotations.set(Number(key), value);
    }
    this.element.classList.remove("viewer--empty");
    this.element.replaceChildren();

    let resolveLoad!: () => void;
    this.loadComplete = new Promise<void>((resolve) => { resolveLoad = resolve; });
    const previous = this.document;
    this.document = null;
    await previous?.destroy();
    const document_ = await pdfjsLib.getDocument(url).promise;
    if (generation !== this.loadGeneration) {
      await document_.destroy();
      resolveLoad();
      return;
    }
    this.document = document_;
    this.setAvailability(true);

    const pdfPages = await Promise.all(
      Array.from({ length: document_.numPages }, (_, index) => document_.getPage(index + 1)),
    );
    if (generation !== this.loadGeneration) {
      resolveLoad();
      return;
    }
    const dimensions = pdfPages.map((page, index) => {
      const nativeRotation = normalizeRotation(page.rotate);
      const rotation = resolvePageRotation(nativeRotation, this.pageRotations.get(index) ?? 0);
      const viewport = page.getViewport({ scale: 1, rotation });
      page.cleanup();
      return { baseWidth: viewport.width, baseHeight: viewport.height, nativeRotation, rotation };
    });
    this.createPageWrappers(dimensions);
    for (const callback of this.loadedCallbacks) callback(document_.numPages);
    for (const callback of this.documentChangedCallbacks) callback();
    resolveLoad();
  }

  setZoom(scale: number, anchor?: { x: number; y: number }): void {
    const oldScale = this.scale;
    if (oldScale === scale) return;
    for (const page of this.pages) {
      page.wrapper.style.width = `${page.baseWidth * scale}px`;
      page.wrapper.style.height = `${page.baseHeight * scale}px`;
    }
    const x = anchor?.x ?? this.element.clientWidth / 2;
    const y = anchor?.y ?? this.element.clientHeight / 2;
    const ratio = scale / oldScale;
    this.element.scrollLeft = Math.max(0, (this.element.scrollLeft + x) * ratio - x);
    this.element.scrollTop = Math.max(0, (this.element.scrollTop + y) * ratio - y);
    this.scale = scale;
    this.cancelZoomDebounce();
    this.zoomDebounce = setTimeout(() => {
      this.zoomDebounce = null;
      void this.enqueueRender(() => this.renderAll());
    }, ZOOM_DEBOUNCE_MS);
  }

  scrollToPage(pageNumber: number): void {
    this.pages[pageNumber - 1]?.wrapper.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  getPageFitScale(pageNumber: number): number | null {
    const page = this.pages[pageNumber - 1];
    if (!page) return null;
    const documentElement = this.element.querySelector<HTMLElement>(".viewer__document");
    const style = documentElement ? getComputedStyle(documentElement) : null;
    const horizontal = style ? (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0) : 0;
    const vertical = style ? (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0) : 0;
    const width = this.element.clientWidth - horizontal;
    const height = this.element.clientHeight - vertical;
    return width > 0 && height > 0 ? Math.min(width / page.baseWidth, height / page.baseHeight) : null;
  }

  startBackgroundRender(): void {
    const generation = ++this.renderGeneration;
    void this.enqueueRender(() => this.renderPages(generation));
  }

  async renderPageNow(pageNumber: number): Promise<void> {
    const document_ = this.document;
    const page = this.pages[pageNumber - 1];
    if (!document_ || !page) return;
    const canvas = page.wrapper.querySelector<HTMLCanvasElement>(".viewer__canvas");
    if (page.renderedScale === this.scale && canvas) return;
    const generation = ++this.renderGeneration;
    await this.enqueueRender(async () => {
      if (generation === this.renderGeneration && document_ === this.document) {
        await renderPdfPage(document_, pageNumber, page, this.scale, generation, () => this.renderGeneration);
      }
    });
  }

  /**
   * Repaints an already-loaded document without changing viewer state.
   *
   * A native host may hide WebView2 while this JavaScript context remains alive.
   * Chromium can then return with a blank canvas even though `renderedScale` still
   * says that page is current. Clear only that paint bookkeeping, render the page
   * the user is looking at first, then repair the rest in the background.
   */
  async refreshRendering(pageNumber: number = 1): Promise<void> {
    const document_ = this.document;
    if (!document_ || this.pages.length === 0) return;

    this.cancelZoomDebounce();
    ++this.renderGeneration;
    for (const page of this.pages) page.renderedScale = null;

    const targetPage = Math.max(1, Math.min(this.pages.length, pageNumber));
    await this.renderPageNow(targetPage);
    if (document_ === this.document) this.startBackgroundRender();
  }

  setPageRotation(pageIndex: number, storedRotation: number): void {
    this.pageRotations.set(pageIndex, storedRotation);
    const page = this.pages[pageIndex];
    const document_ = this.document;
    if (!page || !document_) return;
    const generation = ++this.renderGeneration;
    const oldRotation = page.rotation;
    page.rotation = resolvePageRotation(page.nativeRotation, storedRotation);
    const delta = normalizeRotation(page.rotation - oldRotation);
    if (delta === 90 || delta === 270) {
      [page.baseWidth, page.baseHeight] = [page.baseHeight, page.baseWidth];
    }
    page.renderedScale = null;
    page.wrapper.style.width = `${page.baseWidth * this.scale}px`;
    page.wrapper.style.height = `${page.baseHeight * this.scale}px`;
    void this.enqueueRender(() =>
      renderPdfPage(document_, pageIndex + 1, page, this.scale, generation, () => this.renderGeneration),
    );
  }

  private createPageWrappers(dimensions: Array<Omit<PageEntry, "wrapper" | "renderedScale">>): void {
    const documentElement = document.createElement("div");
    documentElement.className = "viewer__document";
    this.pages = dimensions.map((dimension, index) => {
      const wrapper = document.createElement("div");
      wrapper.className = "viewer__page";
      wrapper.dataset["page"] = String(index + 1);
      wrapper.style.width = `${dimension.baseWidth * this.scale}px`;
      wrapper.style.height = `${dimension.baseHeight * this.scale}px`;
      documentElement.appendChild(wrapper);
      return { ...dimension, wrapper, renderedScale: null };
    });
    this.element.replaceChildren(documentElement);
  }

  private async renderPages(generation: number): Promise<void> {
    const document_ = this.document;
    if (!document_) return;
    for (let index = 0; index < this.pages.length; index += 1) {
      if (generation !== this.renderGeneration || document_ !== this.document) return;
      const page = this.pages[index]!;
      if (page.renderedScale !== this.scale) {
        await renderPdfPage(document_, index + 1, page, this.scale, generation, () => this.renderGeneration);
      }
    }
  }

  private async renderAll(): Promise<void> {
    const generation = ++this.renderGeneration;
    await this.renderPages(generation);
  }

  /** A failed PDF.js render must not permanently poison every later queue entry. */
  private enqueueRender(work: () => Promise<void>): Promise<void> {
    const run = this.renderingQueue.catch(() => undefined).then(work);
    this.renderingQueue = run.catch(() => undefined);
    return run;
  }

  private cancelZoomDebounce(): void {
    if (this.zoomDebounce !== null) clearTimeout(this.zoomDebounce);
    this.zoomDebounce = null;
  }

  private setAvailability(value: boolean): void {
    if (this.hasDocument === value) return;
    this.hasDocument = value;
    for (const callback of this.availabilityCallbacks) callback(value);
  }
}

async function renderPdfPage(
  document_: pdfjsLib.PDFDocumentProxy,
  pageNumber: number,
  entry: PageEntry,
  scale: number,
  generation: number,
  currentGeneration: () => number,
): Promise<void> {
  const page = await document_.getPage(pageNumber);
  const viewport = page.getViewport({ scale, rotation: entry.rotation });
  const canvas = document.createElement("canvas");
  canvas.className = "viewer__canvas";
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  const context = canvas.getContext("2d");
  try {
    if (context) await page.render({ canvasContext: context, viewport }).promise;
  } catch (error) {
    page.cleanup();
    if ((error as { name?: string })?.name === "RenderingCancelledException") return;
    throw error;
  }
  page.cleanup();
  if (!context || generation !== currentGeneration()) return;
  entry.wrapper.style.width = `${viewport.width}px`;
  entry.wrapper.style.height = `${viewport.height}px`;
  entry.wrapper.querySelector(".viewer__canvas")?.remove();
  entry.wrapper.prepend(canvas);
  entry.baseWidth = viewport.width / scale;
  entry.baseHeight = viewport.height / scale;
  entry.renderedScale = scale;
  ensureOverlayLayer(entry.wrapper);
}

export function ensureOverlayLayer(wrapper: HTMLElement): HTMLDivElement {
  let layer = wrapper.querySelector<HTMLDivElement>(".viewer__overlays");
  if (!layer) {
    layer = document.createElement("div");
    layer.className = "viewer__overlays";
    wrapper.appendChild(layer);
  }
  return layer;
}

export function applyNormalizedRectToElement(
  element: HTMLElement,
  rect: { x: number; y: number; width: number; height: number },
): void {
  element.style.left = `${rect.x * 100}%`;
  element.style.top = `${rect.y * 100}%`;
  element.style.width = `${rect.width * 100}%`;
  element.style.height = `${rect.height * 100}%`;
}

function normalizeRotation(rotation: number): number {
  return ((rotation % 360) + 360) % 360;
}

function resolvePageRotation(nativeRotation: number, storedRotation: number): number {
  return normalizeRotation(nativeRotation + storedRotation);
}
