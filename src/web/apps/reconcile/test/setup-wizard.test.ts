import test from "node:test";
import assert from "node:assert/strict";
import { requiredSetupStep } from "../src/components/setup-wizard/setup-wizard.ts";
import type { ReconcileDocument, ReconcileDocumentRole } from "../src/types/index.ts";

function statement(role: ReconcileDocumentRole): ReconcileDocument {
  return {
    id: `${role}-id`,
    versionId: `${role}-version`,
    role,
    name: `${role}.pdf`,
    staleness: "none",
  };
}

test("neither statement is selected, so setup begins at the current statement", () => {
  assert.equal(requiredSetupStep([]), 1);
});

test("the prior statement step is required once the current statement is selected", () => {
  assert.equal(requiredSetupStep([statement("primary")]), 2);
});

test("project details are required once both statements are selected", () => {
  assert.equal(requiredSetupStep([
    statement("primary"),
    statement("comparison-1"),
  ]), 3);
});
