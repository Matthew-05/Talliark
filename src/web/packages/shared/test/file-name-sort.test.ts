import assert from "node:assert/strict";
import test from "node:test";

import { compareFileNamesAscending } from "../src/file-name-sort.ts";

test("sorts an original filename before its numbered duplicate imports", () => {
  const names = [
    "Apple (10).pdf",
    "Apple (2).pdf",
    "Apple (1).pdf",
    "Apple.pdf",
  ];

  names.sort(compareFileNamesAscending);

  assert.deepEqual(names, [
    "Apple.pdf",
    "Apple (1).pdf",
    "Apple (2).pdf",
    "Apple (10).pdf",
  ]);
});

test("keeps alphabetical family ordering ahead of filename length", () => {
  const names = ["Z.pdf", "Apple (1).pdf", "Apple.pdf"];

  names.sort(compareFileNamesAscending);

  assert.deepEqual(names, ["Apple.pdf", "Apple (1).pdf", "Z.pdf"]);
});
