import assert from "node:assert/strict";
import test from "node:test";
import { TableNoticeDismissals } from "../src/services/table-notice-dismissals.ts";

class MemoryStorage {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

test("dismisses the detected-tables notice for only one document", () => {
  const dismissals = new TableNoticeDismissals(new MemoryStorage());

  dismissals.dismiss("pdf-a", 3);

  assert.equal(dismissals.shouldShow("pdf-a", 3), false);
  assert.equal(dismissals.shouldShow("pdf-b", 3), true);
});

test("persists dismissals across viewer instances", () => {
  const storage = new MemoryStorage();
  new TableNoticeDismissals(storage).dismiss("pdf-a", 3);

  const reloaded = new TableNoticeDismissals(storage);

  assert.equal(reloaded.shouldShow("pdf-a", 3), false);
  assert.equal(reloaded.shouldShow("pdf-b", 3), true);
});

test("shows the notice again only when more tables are discovered", () => {
  const dismissals = new TableNoticeDismissals(new MemoryStorage());
  dismissals.dismiss("pdf-a", 3);

  assert.equal(dismissals.shouldShow("pdf-a", 2), false);
  assert.equal(dismissals.shouldShow("pdf-a", 3), false);
  assert.equal(dismissals.shouldShow("pdf-a", 4), true);
});

test("merges another viewer's per-document dismissal before persisting", () => {
  const storage = new MemoryStorage();
  const firstViewer = new TableNoticeDismissals(storage);
  const secondViewer = new TableNoticeDismissals(storage);

  firstViewer.dismiss("pdf-a", 2);
  secondViewer.dismiss("pdf-b", 4);

  const reloaded = new TableNoticeDismissals(storage);
  assert.equal(reloaded.shouldShow("pdf-a", 2), false);
  assert.equal(reloaded.shouldShow("pdf-b", 4), false);
});

test("keeps the highest acknowledged count when viewer instances overlap", () => {
  const storage = new MemoryStorage();
  const firstViewer = new TableNoticeDismissals(storage);
  const secondViewer = new TableNoticeDismissals(storage);

  firstViewer.dismiss("pdf-a", 5);
  secondViewer.dismiss("pdf-a", 3);

  assert.equal(new TableNoticeDismissals(storage).shouldShow("pdf-a", 5), false);
});

test("corrupt persistent state fails open", () => {
  const storage = new MemoryStorage();
  storage.setItem("talliark.document-viewer.table-notice-dismissals.v1", "not json");

  const dismissals = new TableNoticeDismissals(storage);

  assert.equal(dismissals.shouldShow("pdf-a", 1), true);
  assert.doesNotThrow(() => dismissals.dismiss("pdf-a", 1));
});
