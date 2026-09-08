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
