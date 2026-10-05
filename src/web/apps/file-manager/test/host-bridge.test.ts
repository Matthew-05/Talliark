import assert from "node:assert/strict";
import test from "node:test";

import { initHostBridge, sendOpenFileInViewer } from "../src/host-bridge.ts";

test("sends the explicit task-pane viewer command for a file", () => {
  const sent: string[] = [];
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      chrome: {
        webview: {
          postMessage(message: string) {
            sent.push(message);
          },
        },
      },
    },
  });

  try {
    sendOpenFileInViewer("pdf-123");
  } finally {
    delete (globalThis as { window?: unknown }).window;
  }

  assert.deepEqual(sent.map((message) => JSON.parse(message)), [
    { type: "open-file-in-viewer", id: "pdf-123" },
  ]);
});

test("dispatches native sidebar context-menu dismissal", () => {
  const webview = new EventTarget() as EventTarget & {
    postMessage(message: string): void;
  };
  webview.postMessage = () => {};

  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { chrome: { webview } },
  });

  let dismissed = 0;
  try {
    initHostBridge(
      () => {},
      undefined,
      undefined,
      undefined,
      () => { dismissed++; },
    );
    webview.dispatchEvent(new MessageEvent("message", {
      data: JSON.stringify({ type: "dismiss-context-menu" }),
    }));
  } finally {
    delete (globalThis as { window?: unknown }).window;
  }

  assert.equal(dismissed, 1);
});

test("dispatches the imported file selection", () => {
  const webview = new EventTarget() as EventTarget & {
    postMessage(message: string): void;
  };
  webview.postMessage = () => {};

  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { chrome: { webview } },
  });

  let selectedIds: string[] = [];
  try {
    initHostBridge(
      () => {},
      undefined,
      undefined,
      undefined,
      undefined,
      (ids) => { selectedIds = ids; },
    );
    webview.dispatchEvent(new MessageEvent("message", {
      data: JSON.stringify({ type: "select-files", ids: ["pdf-1", "pdf-2"] }),
    }));
  } finally {
    delete (globalThis as { window?: unknown }).window;
  }

  assert.deepEqual(selectedIds, ["pdf-1", "pdf-2"]);
});
