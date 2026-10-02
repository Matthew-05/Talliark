import assert from "node:assert/strict";
import test from "node:test";
import type { TextGeometry, TextGeometryCharacter } from "../src/geometry-decoder.ts";
import { detectValues } from "../src/value-recognition/detector.ts";
import type { DocumentValues } from "../src/document-values-decoder.ts";

interface CharOptions {
  y: number;
  lineIndex: number;
  height?: number;
  x?: number;
}

function chars(text: string, { y, lineIndex, height = 0.012, x = 0.02 }: CharOptions): TextGeometryCharacter[] {
  return [...text].map((char, index) => ({
    char,
    x: x + index * 0.008,
    y,
    width: 0.008,
    height,
    lineIndex,
  }));
}

function geometry(pages: TextGeometryCharacter[][]): TextGeometry {
  return {
    version: 1,
    coordinateSpace: "normalized",
    pages: pages.map((characters, index) => ({ pageIndex: index, characters })),
  };
}

async function detect(pages: TextGeometryCharacter[][]): Promise<DocumentValues> {
  return detectValues(geometry(pages));
}

function published(model: DocumentValues): string[] {
  return model.pages.flatMap((page) => page.values.map((value) => value.text));
}

function references(model: DocumentValues): Array<{ kind: string; text: string }> {
  return model.pages.flatMap((page) => page.references.map((entry) => ({ kind: entry.kind, text: entry.text })));
}

function noise(model: DocumentValues): Array<{ label: string; text: string }> {
  return model.pages.flatMap((page) =>
    page.noise.map((entry) => ({ label: entry.reason, text: entry.text })));
}

function structure(model: DocumentValues): Array<{ label: string; text: string }> {
  return model.pages.flatMap((page) =>
    page.structure.map((entry) => ({ label: entry.kind, text: entry.text })));
}

test("builds bounds, page context and currency", async () => {
  const model = await detect([
    chars("Amounts in millions USD  December 31, 2025  $ 1,200", { y: 0.1, lineIndex: 0, height: 0.02 }),
  ]);
  assert.equal(model.documentContext.currency, "USD");
  assert.equal(model.pages[0]?.context.scale, 1000000);
  assert.deepEqual(model.pages[0]?.values.map((v) => v.kind), ["date", "number"]);
  assert.equal(model.pages[0]?.values[1]?.normalizedValue, "1200");
});

test("attaches a wrapped modifier across pages", async () => {
  const model = await detect([
    chars("$1.0", { y: 0.1, lineIndex: 0 }),
    chars("million in revenue", { y: 0.1, lineIndex: 0 }),
  ]);
  const value = model.pages[0]?.values[0];
  assert.equal(value?.text, "$1.0 million");
  assert.equal(value?.normalizedValue, "1000000");
  assert.equal(value?.magnitude, 1000000);
  assert.deepEqual(value?.segments?.map((s) => s.pageIndex), [0, 1]);
});

test("a phone number never becomes a negative", async () => {
  const model = await detect([
    chars("Cupertino, California (408) 996-1010", { y: 0.1, lineIndex: 0 }),
  ]);
  assert.deepEqual(published(model), []);
  assert.ok(references(model).some((entry) => entry.kind === "phone"));
});

test("a citation year is not a period", async () => {
  const model = await detect([
    chars("of the Securities Exchange Act of 1934", { y: 0.1, lineIndex: 0 }),
  ]);
  assert.deepEqual(published(model), []);
});

test("a number in its own cell is never unsupported", async () => {
  const model = await detect([[
    ...chars("Weighted average shares outstanding, basic and diluted", { y: 0.1, lineIndex: 0, x: 0.02 }),
    ...chars("166", { y: 0.1, lineIndex: 0, x: 0.7 }),
  ]]);
  assert.deepEqual(published(model), ["166"]);
});

test("a running footer is not content", async () => {
  const pages = [0, 1, 2, 3].map((pageIndex) => [
    ...chars(`Net sales of 1,${pageIndex}00 in the period`, { y: 0.1, lineIndex: 0 }),
    ...chars("Apple Inc. | 2025 Form 10-K | 7", { y: 0.95, lineIndex: 1 }),
  ]);
  const model = await detect(pages);
  assert.ok(!published(model).includes("2025"));
  assert.deepEqual(new Set(noise(model).map((n) => n.label)), new Set(["page-furniture"]));
});

test("an exhibit column is refused whole", async () => {
  const rows: Array<[string, string]> = [
    ["3.1", "Restated Articles of Incorporation"],
    ["3.2", "Restated Bylaws of the Company"],
    ["4.1", "Description of Registered Securities"],
    ["10.1", "Incentive Compensation Plan"],
  ];
  const page: TextGeometryCharacter[] = [];
  rows.forEach(([marker, body], index) => {
    page.push(...chars(marker, { y: 0.1 + index * 0.05, lineIndex: index * 2, x: 0.03 }));
    page.push(...chars(body, { y: 0.1 + index * 0.05, lineIndex: index * 2 + 1, x: 0.15 }));
  });
  const model = await detect([page]);
  assert.deepEqual(published(model), []);
  assert.deepEqual(
    structure(model).map((s) => [s.label, s.text]),
    [["list-marker", "3.1"], ["list-marker", "3.2"], ["list-marker", "4.1"], ["list-marker", "10.1"]],
  );
});

test("ids are content addressed and carry their category", async () => {
  const page = chars("Total 1,234 of FORM 10-K", { y: 0.1, lineIndex: 0 });
  const model = await detect([page]);
  const value = model.pages[0]?.values[0];
  const reference = model.pages[0]?.references[0];
  assert.match(value!.id, /^val-[0-9a-f]{16}$/);
  assert.match(reference!.id, /^ref-[0-9a-f]{16}$/);
  const again = await detect([page]);
  assert.deepEqual(again.pages[0]?.values.map((v) => v.id), model.pages[0]?.values.map((v) => v.id));
});
