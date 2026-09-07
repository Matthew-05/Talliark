import assert from "node:assert/strict";
import test from "node:test";
import { ValuesCache } from "../src/services/values-cache.ts";

test("indexes a stored model by document and page", async () => {
  const model = {
    version: 1, coordinateSpace: "normalized", detectorVersion: "test", documentContext: {},
    pages: [{ pageIndex: 2, context: {}, values: [{ id: "val-n", kind: "number", text: "100", bounds: { x: 0.1, y: 0.1, width: 0.1, height: 0.02 }, clickable: true, confidence: 0.9 }] }],
  };
  const cache = new ValuesCache(async () => model as never);
  await cache.build("pdf", "encoded");
  assert.equal(cache.valuesOnPage("pdf", 2)[0]?.text, "100");
  assert.equal(cache.valueCount("pdf"), 1);
});

test("does not synthesize values for a document with no analyzed artifact", async () => {
  const cache = new ValuesCache();
  await cache.build("native-pdf", undefined);
  assert.equal(cache.has("native-pdf"), true);
  assert.equal(cache.valueCount("native-pdf"), 0);
});

test("indexes every segment of one wrapped logical value", async () => {
  const model = {
    version: 1, coordinateSpace: "normalized", detectorVersion: "test", documentContext: {},
    pages: [{ pageIndex: 0, context: {}, values: [{
      id: "val-n", kind: "number", text: "$1 million", normalizedValue: "1000000", magnitude: 1000000,
      bounds: { x: 0.8, y: 0.9, width: 0.1, height: 0.02 }, clickable: true, confidence: 0.9,
      segments: [
        { pageIndex: 0, text: "$1", bounds: { x: 0.8, y: 0.9, width: 0.1, height: 0.02 } },
        { pageIndex: 1, text: "million", bounds: { x: 0.1, y: 0.1, width: 0.2, height: 0.02 } },
      ],
    }] }],
  };
  const cache = new ValuesCache(async () => model as never);
  await cache.build("pdf", "encoded");
  assert.equal(cache.valuesOnPage("pdf", 0)[0]?.bounds.x, 0.8);
  assert.equal(cache.valuesOnPage("pdf", 1)[0]?.bounds.x, 0.1);
  assert.equal(cache.valuesOnPage("pdf", 1)[0]?.text, "$1 million");
  assert.equal(cache.valueCount("pdf"), 1);
});

test("keeps refused spans separate from the values they were kept from", async () => {
  const model = {
    version: 1, coordinateSpace: "normalized", detectorVersion: "test", documentContext: {},
    pages: [{
      pageIndex: 0,
      context: {},
      values: [{ id: "val-v", kind: "number", text: "1,234", bounds: { x: 0.1, y: 0.1, width: 0.1, height: 0.02 }, clickable: true, confidence: 0.94 }],
      noise: [{ id: "noi-n", kind: "number", text: "10", bounds: { x: 0.5, y: 0.1, width: 0.02, height: 0.02 }, clickable: false, reason: "page-furniture" }],
      structure: [{ id: "str-n", kind: "note-header", text: "7", bounds: { x: 0.6, y: 0.1, width: 0.02, height: 0.02 }, clickable: false }],
    }],
  };
  const cache = new ValuesCache(async () => model as never);
  await cache.build("pdf", "encoded");
  assert.deepEqual(cache.valuesOnPage("pdf", 0).map((value) => value.text), ["1,234"]);
  assert.deepEqual(cache.noiseOnPage("pdf", 0).map((entry) => entry.text), ["10"]);
  assert.deepEqual(cache.structureOnPage("pdf", 0).map((entry) => entry.kind), ["note-header"]);
  assert.equal(cache.valueCount("pdf"), 1);
  assert.equal(cache.noiseCount("pdf"), 1);
  assert.equal(cache.structureCount("pdf"), 1);
  // Nothing outside the value and reference layers is a click target today,
  // and the field says so rather than the layer implying it.
  assert.equal(cache.structureOnPage("pdf", 0)[0]?.clickable, false);
});

test("reports no noise for a document with no analyzed artifact", async () => {
  const cache = new ValuesCache();
  await cache.build("native-pdf", undefined);
  assert.equal(cache.noiseCount("native-pdf"), 0);
  assert.deepEqual(cache.noiseOnPage("native-pdf", 0), []);
});

test("keeps references clickable and joins them to the structure by span id", async () => {
  const bounds = { x: 0.1, y: 0.1, width: 0.2, height: 0.02 };
  const values = {
    version: 1, coordinateSpace: "normalized", detectorVersion: "test", documentContext: {},
    pages: [{
      pageIndex: 1,
      context: {},
      values: [],
      references: [
        { id: "ref-aaaaaaaaaaaaaaaa", kind: "note", text: "Note 3.1", bounds, clickable: true },
        { id: "ref-bbbbbbbbbbbbbbbb", kind: "identifier", text: "BPXINV-00550", bounds, clickable: true },
      ],
    }],
  };
  const structure = {
    version: 1, coordinateSpace: "normalized", detectorVersion: "test",
    documentClass: "statement",
    apparatus: {
      notes: { searched: true, found: 1 },
      items: { searched: true, found: 0 },
      contents: { searched: true, found: 0 },
      parts: { searched: true, found: 0 },
    },
    notes: [{
      id: "note-3.1", identifier: "3.1", description: "Revenue Recognition",
      headers: [{ id: "note-3.1-h0", pageIndex: 2, text: "Note 3.1 — Revenue Recognition", bounds, continuation: false }],
    }],
    noteReferences: [{
      id: "noteref-0-0", spanId: "ref-aaaaaaaaaaaaaaaa", noteId: "note-3.1", identifier: "3.1",
      description: "Revenue Recognition", pageIndex: 1, descriptionPresent: false,
    }],
    items: [],
    itemReferences: [],
  };
  const cache = new ValuesCache(async () => values as never, async () => structure as never);
  await cache.build("pdf", "values", "structure");

  assert.equal(cache.referenceCount("pdf"), 2);
  assert.deepEqual(cache.referencesOnPage("pdf", 1).map((entry) => entry.kind), ["note", "identifier"]);
  assert.equal(cache.documentClass("pdf"), "statement");
  assert.equal(cache.notes("pdf")[0]?.description, "Revenue Recognition");
  const citation = cache.referencesOnPage("pdf", 1)[0];
  assert.equal(
    cache.noteReferences("pdf").find((entry) => entry.spanId === citation?.id)?.noteId,
    "note-3.1",
  );
});

test("retains the canonical item catalogue and its resolved references", async () => {
  const bounds = { x: 0.1, y: 0.1, width: 0.2, height: 0.02 };
  const structure = {
    version: 1, coordinateSpace: "normalized", detectorVersion: "test",
    documentClass: "filing",
    apparatus: {
      notes: { searched: true, found: 0 },
      items: { searched: true, found: 1 },
      contents: { searched: true, found: 1 },
      parts: { searched: true, found: 1 },
    },
    notes: [],
    noteReferences: [],
    items: [{
      id: "item-II-7A", identifier: "7A", part: "II",
      description: "Quantitative and Qualitative Disclosures About Market Risk",
      descriptionSource: "toc",
      headers: [{ id: "ih0", pageIndex: 29, text: "Item 7A. Quantitative and Qualitative Disclosures About Market Risk", bounds, continuation: false }],
      tocEntries: [{ id: "it0", pageIndex: 2, text: "Item 7A. Quantitative and Qualitative Disclosures About Market Risk 27", bounds, corroborated: true, printedPage: "27" }],
    }],
    itemReferences: [{
      id: "itemref-0", spanId: "ref-cccccccccccccccc", itemId: "item-II-7A", identifier: "7A",
      description: "Quantitative and Qualitative Disclosures About Market Risk",
      pageIndex: 26, descriptionPresent: false,
    }],
  };
  const cache = new ValuesCache(undefined, async () => structure as never);
  await cache.build("pdf", undefined, "structure");

  assert.equal(cache.items("pdf")[0]?.part, "II");
  assert.equal(cache.items("pdf")[0]?.tocEntries[0]?.printedPage, "27");
  assert.equal(cache.itemReferences("pdf")[0]?.itemId, "item-II-7A");
});

test("a document outside the financial tier carries no structure, and says so", async () => {
  const cache = new ValuesCache();
  await cache.build("invoice", undefined, undefined);
  assert.deepEqual(cache.items("invoice"), []);
  assert.deepEqual(cache.itemReferences("invoice"), []);
  assert.equal(cache.documentClass("invoice"), "neither");
  // Unsearched, not "searched and found nothing" — the distinction a check
  // needs before it can report not applicable.
  assert.equal(cache.apparatus("invoice").notes.searched, false);
});

test("a stale values decode cannot replace a newer OCR model", async () => {
  let releaseOld!: (value: never) => void;
  const oldModel = new Promise<never>((resolve) => { releaseOld = resolve; });
  const model = (text: string) => ({
    version: 1,
    coordinateSpace: "normalized",
    detectorVersion: "test",
    documentContext: {},
    pages: [{
      pageIndex: 0,
      context: {},
      values: [{
        id: `value-${text}`,
        kind: "number",
        text,
        bounds: { x: 0.1, y: 0.1, width: 0.1, height: 0.02 },
        clickable: true,
        confidence: 1,
      }],
    }],
  });
  const cache = new ValuesCache((encoded) => (
    encoded === "old" ? oldModel : Promise.resolve(model("fresh") as never)
  ));

  const oldBuild = cache.build("pdf", "old");
  await cache.build("pdf", "fresh");
  releaseOld(model("stale") as never);
  await oldBuild;

  assert.deepEqual(cache.valuesOnPage("pdf", 0).map((entry) => entry.text), ["fresh"]);
});
