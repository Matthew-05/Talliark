import test from "node:test";
import assert from "node:assert/strict";
import { summarySentence } from "../src/components/document-list/document-list.ts";

test("a successful singleton scan reports verified totals and exceptions", () => {
  assert.equal(summarySentence({
    tablesExamined: 8,
    totalsNominated: 5,
    confirmed: 4,
    breaks: 1,
    unresolved: 0,
  }), "4 totals verified, 1 exception");
});

test("a scan that finds no totals does not imply that the statement passed", () => {
  assert.equal(summarySentence({
    tablesExamined: 0,
    totalsNominated: 0,
    confirmed: 0,
    breaks: 0,
    unresolved: 0,
  }), "No tables found to check");
});
