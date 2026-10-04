import assert from "node:assert/strict";
import test from "node:test";

import { retainVisibleFileSelection } from "../src/file-selection.ts";

test("changing folders deselects documents filtered out of the table", () => {
  const selectedIds = new Set(["folder-a", "folder-b"]);

  const changed = retainVisibleFileSelection(selectedIds, [
    { id: "folder-a", folderId: "a" },
    { id: "folder-b", folderId: "b" },
  ], "b");

  assert.equal(changed, true);
  assert.deepEqual([...selectedIds], ["folder-b"]);
});

test("switching to All Files retains every existing selection", () => {
  const selectedIds = new Set(["folder-a", "folder-b"]);

  const changed = retainVisibleFileSelection(selectedIds, [
    { id: "folder-a", folderId: "a" },
    { id: "folder-b", folderId: "b" },
  ], null);

  assert.equal(changed, false);
  assert.deepEqual([...selectedIds], ["folder-a", "folder-b"]);
});
