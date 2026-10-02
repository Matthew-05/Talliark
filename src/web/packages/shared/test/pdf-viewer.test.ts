import assert from "node:assert/strict";
import test from "node:test";

import { PdfViewer } from "../src/pdf-viewer.ts";

class FakeElement {
  className = "";
  width = 0;
  height = 0;
  readonly style: Record<string, string> = {};
  readonly children: FakeElement[] = [];
  parent: FakeElement | null = null;

  appendChild(child: FakeElement): FakeElement {
    child.parent = this;
    this.children.push(child);
    return child;
  }

  prepend(child: FakeElement): void {
    child.parent = this;
    this.children.unshift(child);
  }

  remove(): void {
    if (!this.parent) return;
    const index = this.parent.children.indexOf(this);
    if (index >= 0) this.parent.children.splice(index, 1);
    this.parent = null;
  }

  querySelector(selector: string): FakeElement | null {
    const className = selector.startsWith(".") ? selector.slice(1) : selector;
    for (const child of this.children) {
      if (child.className.split(/\s+/).includes(className)) return child;
      const descendant = child.querySelector(selector);
      if (descendant) return descendant;
    }
    return null;
  }

  getContext(): object {
    return {};
  }
}

interface Rect {
  top: number;
  bottom: number;
  left: number;
  right: number;
  width: number;
  height: number;
}

/** A FakeElement that reports a real position so visibility can be measured. */
class MeasurableElement extends FakeElement {
  rect: Rect = { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 };

  getBoundingClientRect(): Rect {
    return this.rect;
  }
}

interface MutableViewerState {
  document: object;
  pages: Array<{
    wrapper: FakeElement;
    baseWidth: number;
    baseHeight: number;
    renderedScale: number | null;
    nativeRotation: number;
    rotation: number;
  }>;
  scale: number;
  renderingQueue: Promise<void>;
  zoomDebounce: null;
  renderGeneration: number;
}

function createViewerState(render: () => Promise<void>): {
  viewer: PdfViewer;
  wrapper: FakeElement;
  oldCanvas: FakeElement;
} {
  const wrapper = new FakeElement();
  const oldCanvas = new FakeElement();
  oldCanvas.className = "viewer__canvas";
  wrapper.appendChild(oldCanvas);

  const overlay = new FakeElement();
  overlay.className = "viewer__overlays";
  wrapper.appendChild(overlay);

  const page = {
    getViewport: () => ({ width: 800, height: 1000 }),
    render: () => ({ promise: render() }),
    cleanup: () => undefined,
  };
  const documentProxy = { getPage: async () => page };

  const viewer = Object.create(PdfViewer.prototype) as PdfViewer;
  const state = viewer as unknown as MutableViewerState;
  state.document = documentProxy;
  state.pages = [{
    wrapper,
    baseWidth: 800,
    baseHeight: 1000,
    renderedScale: 1,
    nativeRotation: 0,
    rotation: 0,
  }];
  state.scale = 1;
  state.renderingQueue = Promise.resolve();
  state.zoomDebounce = null;
  state.renderGeneration = 0;

  return { viewer, wrapper, oldCanvas };
}

test("refreshRendering repaints a canvas still marked at the current scale", async () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement: () => new FakeElement(),
  } as unknown as Document;

  let renderCount = 0;
  const { viewer, wrapper, oldCanvas } = createViewerState(async () => {
    renderCount++;
  });

  try {
    await viewer.refreshRendering(1);

    assert.equal(renderCount, 1);
    assert.equal(oldCanvas.parent, null);
    assert.equal(wrapper.children[0]?.className, "viewer__canvas");
    assert.equal(wrapper.children[1]?.className, "viewer__overlays");
    assert.equal(viewer.getCurrentZoom(), 1);
  } finally {
    globalThis.document = originalDocument;
  }
});

test("renderPageNow repairs a missing canvas despite current scale bookkeeping", async () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement: () => new FakeElement(),
  } as unknown as Document;

  let renderCount = 0;
  const { viewer, wrapper, oldCanvas } = createViewerState(async () => {
    renderCount++;
  });
  oldCanvas.remove();

  try {
    await viewer.renderPageNow(1);
    assert.equal(renderCount, 1);
    assert.equal(wrapper.children[0]?.className, "viewer__canvas");
  } finally {
    globalThis.document = originalDocument;
  }
});

test("a failed repaint does not poison later rendering", async () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement: () => new FakeElement(),
  } as unknown as Document;

  let shouldFail = true;
  let renderCount = 0;
  const { viewer } = createViewerState(async () => {
    renderCount++;
    if (shouldFail) throw new Error("damaged render");
  });

  try {
    await assert.rejects(viewer.refreshRendering(1), /damaged render/);
    shouldFail = false;
    await viewer.refreshRendering(1);
    assert.equal(renderCount, 2);
  } finally {
    globalThis.document = originalDocument;
  }
});

test("background render repairs a missing canvas despite current scale bookkeeping", async () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement: () => new FakeElement(),
  } as unknown as Document;

  let renderCount = 0;
  const { viewer, wrapper, oldCanvas } = createViewerState(async () => {
    renderCount++;
  });
  oldCanvas.remove();

  try {
    viewer.startBackgroundRender();
    const state = viewer as unknown as MutableViewerState;
    await state.renderingQueue;

    assert.equal(renderCount, 1);
    assert.equal(wrapper.children[0]?.className, "viewer__canvas");
  } finally {
    globalThis.document = originalDocument;
  }
});

test("one damaged page does not stop the pages after it from rendering", async () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement: () => new FakeElement(),
  } as unknown as Document;

  const wrappers = [new FakeElement(), new FakeElement()];
  const renderCounts = [0, 0];
  const pages = wrappers.map((_wrapper, index) => ({
    getViewport: () => ({ width: 800, height: 1000 }),
    render: () => ({
      promise: (async () => {
        renderCounts[index]!++;
        if (index === 0) throw new Error("damaged render");
      })(),
    }),
    cleanup: () => undefined,
  }));
  const documentProxy = { getPage: async (number: number) => pages[number - 1] };

  const viewer = Object.create(PdfViewer.prototype) as PdfViewer;
  const state = viewer as unknown as MutableViewerState;
  state.document = documentProxy;
  state.pages = wrappers.map((wrapper) => ({
    wrapper,
    baseWidth: 800,
    baseHeight: 1000,
    renderedScale: null,
    nativeRotation: 0,
    rotation: 0,
  }));
  state.scale = 1;
  state.renderingQueue = Promise.resolve();
  state.zoomDebounce = null;
  state.renderGeneration = 0;

  try {
    viewer.startBackgroundRender();
    await state.renderingQueue;

    assert.equal(renderCounts[0], 1);
    assert.equal(renderCounts[1], 1);
    assert.equal(wrappers[1]!.children[0]?.className, "viewer__canvas");
  } finally {
    globalThis.document = originalDocument;
  }
});

function createTriggeredViewer(): {
  focus: () => void;
  show: () => void;
  renderCounts: number[];
  state: MutableViewerState;
} {
  const focusListeners: Array<() => void> = [];
  const visibilityListeners: Array<() => void> = [];

  const fakeWindow = {
    addEventListener: (_type: string, callback: () => void) => {
      if (_type === "focus") focusListeners.push(callback);
    },
  };
  const fakeDocument = {
    visibilityState: "visible",
    createElement: () => new MeasurableElement(),
    addEventListener: (_type: string, callback: () => void) => {
      if (_type === "visibilitychange") visibilityListeners.push(callback);
    },
  };

  globalThis.document = fakeDocument as unknown as Document;
  (globalThis as unknown as { window: unknown }).window = fakeWindow;

  const viewer = new PdfViewer();
  const viewerElement = viewer.element as unknown as MeasurableElement;
  viewerElement.rect = { top: 0, bottom: 1000, left: 0, right: 800, width: 800, height: 1000 };

  const wrappers = [new MeasurableElement(), new MeasurableElement()];
  wrappers[0]!.rect = { top: 0, bottom: 500, left: 0, right: 800, width: 800, height: 500 };
  wrappers[1]!.rect = { top: 2000, bottom: 2500, left: 0, right: 800, width: 800, height: 500 };
  for (const wrapper of wrappers) {
    const canvas = new MeasurableElement();
    canvas.className = "viewer__canvas";
    wrapper.appendChild(canvas);
  }

  const renderCounts = [0, 0];
  const pages = wrappers.map((_wrapper, index) => ({
    getViewport: () => ({ width: 800, height: 1000 }),
    render: () => ({ promise: Promise.resolve().then(() => { renderCounts[index]!++; }) }),
    cleanup: () => undefined,
  }));
  const documentProxy = { getPage: async (number: number) => pages[number - 1] };

  const state = viewer as unknown as MutableViewerState;
  state.document = documentProxy;
  state.pages = wrappers.map((wrapper) => ({
    wrapper,
    baseWidth: 800,
    baseHeight: 1000,
    renderedScale: 1,
    nativeRotation: 0,
    rotation: 0,
  }));
  state.scale = 1;
  state.renderingQueue = Promise.resolve();
  state.zoomDebounce = null;
  state.renderGeneration = 0;

  return {
    focus: () => focusListeners[0]?.(),
    show: () => visibilityListeners[0]?.(),
    renderCounts,
    state,
  };
}

test("refocus and visibility repaint only the pages in the viewport", async () => {
  const originalDocument = globalThis.document;
  const originalWindow = (globalThis as unknown as { window: unknown }).window;
  const flush = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

  try {
    const setup = createTriggeredViewer();

    setup.focus();
    await flush();
    assert.equal(setup.renderCounts[0], 1);
    assert.equal(setup.renderCounts[1], 0);

    setup.renderCounts[0] = 0;
    setup.renderCounts[1] = 0;
    for (const page of setup.state.pages) page.renderedScale = 1;

    setup.show();
    await flush();
    assert.equal(setup.renderCounts[0], 1);
    assert.equal(setup.renderCounts[1], 0);
  } finally {
    globalThis.document = originalDocument;
    (globalThis as unknown as { window: unknown }).window = originalWindow;
  }
});
