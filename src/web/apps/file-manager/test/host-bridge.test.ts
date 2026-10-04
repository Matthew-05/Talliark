import assert from "node:assert/strict";
import test from "node:test";

import { sendOpenFileInViewer } from "../src/host-bridge.ts";

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
