import assert from "node:assert/strict";
import test from "node:test";
import { TableSuggestionVisibility } from "../src/services/table-suggestion-visibility.ts";

test("enables detected-table suggestions for only one document", () => {
  const visibility = new TableSuggestionVisibility();

  visibility.setEnabled("pdf-a", true);

  assert.equal(visibility.isEnabled("pdf-a"), true);
  assert.equal(visibility.isEnabled("pdf-b"), false);
});

test("restores each document's state when switching between documents", () => {
  const visibility = new TableSuggestionVisibility();
  visibility.setEnabled("pdf-a", true);

  assert.equal(visibility.isEnabled("pdf-b"), false);
  visibility.setEnabled("pdf-b", true);
  visibility.setEnabled("pdf-b", false);

  assert.equal(visibility.isEnabled("pdf-a"), true);
  assert.equal(visibility.isEnabled("pdf-b"), false);
});

test("disabling one document leaves another document enabled", () => {
  const visibility = new TableSuggestionVisibility();
  visibility.setEnabled("pdf-a", true);
  visibility.setEnabled("pdf-b", true);

  visibility.setEnabled("pdf-b", false);

  assert.equal(visibility.isEnabled("pdf-a"), true);
  assert.equal(visibility.isEnabled("pdf-b"), false);
});
