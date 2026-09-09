import test from "node:test";
import assert from "node:assert/strict";
import { completedPrimary } from "../src/components/setup-wizard/setup-wizard.ts";
import type { ReconcileDocument } from "../src/types/index.ts";

const primary: ReconcileDocument = {
  id: "primary-id",
  versionId: "primary-version",
  role: "primary",
  name: "current.pdf",
  staleness: "current",
};

test("an empty workspace opens setup", () => {
  assert.equal(completedPrimary([], ""), null);
});

test("an imported document remains in setup until the wizard is completed", () => {
  assert.equal(completedPrimary([primary], ""), null);
});

test("a completed workspace opens the primary result", () => {
  assert.equal(completedPrimary([primary], "FY 2026"), primary);
});
