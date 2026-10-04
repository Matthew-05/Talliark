import assert from "node:assert/strict";
import test from "node:test";

import { createFitMode } from "../src/components/viewer/fit-mode.ts";

class FakeResizeObserver {
  static instances: FakeResizeObserver[] = [];

  readonly callback: ResizeObserverCallback;
  observed: Element | null = null;
  disconnected = false;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    FakeResizeObserver.instances.push(this);
  }

  observe(target: Element): void { this.observed = target; }
  unobserve(): void {}
  disconnect(): void { this.disconnected = true; }
}

function createViewerHarness(): {
  viewer: Parameters<typeof createFitMode>[0];
  setFitScale(scale: number | null): void;
  setZoom(scale: number): void;
  loadDocument(pdfId: string): void;
  scrollCount(): number;
} {
  let fitScale: number | null = 0.75;
  let zoom = 1;
  let scrolled = 0;
  let activePdfId: string | null = null;
  let loaded: ((totalPages: number) => void) | null = null;
  const wrapper = {
    scrollIntoView: () => { scrolled++; },
  };
  const element = {
    querySelector: () => wrapper,
  };

  return {
    viewer: {
      element,
      getPageFitScale: () => fitScale,
      getCurrentZoom: () => zoom,
      getActivePdfId: () => activePdfId,
      onLoaded: (callback: (totalPages: number) => void) => { loaded = callback; },
    } as unknown as Parameters<typeof createFitMode>[0],
    setFitScale: (scale) => { fitScale = scale; },
    setZoom: (scale) => { zoom = scale; },
    loadDocument: (pdfId) => {
      activePdfId = pdfId;
      loaded?.(1);
    },
    scrollCount: () => scrolled,
  };
}

test("each new document starts active and refits for its own page size", () => {
  const originalResizeObserver = globalThis.ResizeObserver;
  globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
  FakeResizeObserver.instances = [];

  try {
    const harness = createViewerHarness();
    const applied: number[] = [];
    const mode = createFitMode(
      harness.viewer,
      (scale) => {
        applied.push(scale);
        harness.setZoom(scale);
      },
      () => 1,
    );

    assert.equal(mode.isActive(), true);
    harness.loadDocument("pdf-a");
    assert.deepEqual(applied, [0.75]);

    harness.setFitScale(2);
    harness.loadDocument("pdf-b");
    assert.deepEqual(applied, [0.75, 2]);
    assert.equal(harness.scrollCount(), 2);
  } finally {
    globalThis.ResizeObserver = originalResizeObserver;
  }
});

test("fit choices are retained independently for each document", () => {
  const originalResizeObserver = globalThis.ResizeObserver;
  globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
  FakeResizeObserver.instances = [];

  try {
    const harness = createViewerHarness();
    const applied: number[] = [];
    const mode = createFitMode(
      harness.viewer,
      (scale) => {
        applied.push(scale);
        harness.setZoom(scale);
      },
      () => 1,
    );

    harness.loadDocument("pdf-a");
    mode.exit();
    harness.setFitScale(2);
    harness.loadDocument("pdf-b");

    assert.equal(mode.isActive(), true);
    assert.deepEqual(applied, [0.75, 2]);

    harness.setFitScale(0.5);
    harness.loadDocument("pdf-a");

    assert.equal(mode.isActive(), false);
    assert.deepEqual(applied, [0.75, 2]);
    assert.equal(harness.scrollCount(), 2);
  } finally {
    globalThis.ResizeObserver = originalResizeObserver;
  }
});

test("an inactive document restores its explicit zoom only when requested", () => {
  const originalResizeObserver = globalThis.ResizeObserver;
  globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
  FakeResizeObserver.instances = [];

  try {
    const harness = createViewerHarness();
    const applied: number[] = [];
    const mode = createFitMode(
      harness.viewer,
      (scale) => {
        applied.push(scale);
        harness.setZoom(scale);
      },
      () => 1,
    );

    harness.loadDocument("pdf-a");
    harness.setZoom(1.5);
    mode.exit(1.5);

    harness.setFitScale(2);
    harness.loadDocument("pdf-b");
    harness.setZoom(0.8);
    mode.exit(0.8);

    harness.setFitScale(0.5);
    harness.loadDocument("pdf-a");

    assert.deepEqual(applied, [0.75, 2]);
    assert.equal(mode.restoreExplicitZoom(), true);
    assert.deepEqual(applied, [0.75, 2, 1.5]);

    harness.loadDocument("pdf-b");
    assert.deepEqual(applied, [0.75, 2, 1.5]);
    assert.equal(mode.restoreExplicitZoom(), true);
    assert.deepEqual(applied, [0.75, 2, 1.5, 0.8]);

    mode.enter();
    assert.equal(mode.restoreExplicitZoom(), false);
    assert.deepEqual(applied, [0.75, 2, 1.5, 0.8]);
  } finally {
    globalThis.ResizeObserver = originalResizeObserver;
  }
});
