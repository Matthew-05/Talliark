import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

const sharedTextSearcherUrl = new URL(
  "../../../packages/shared/src/text-searcher.ts",
  import.meta.url,
).href;
const sharedZeroPlaceholderUrl = new URL(
  "../../../packages/shared/src/zero-placeholder.ts",
  import.meta.url,
).href;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@talliark/shared") {
      return { url: sharedTextSearcherUrl, shortCircuit: true };
    }
    if (specifier === "./zero-placeholder.js" && context.parentURL === sharedTextSearcherUrl) {
      return { url: sharedZeroPlaceholderUrl, shortCircuit: true };
    }

    return nextResolve(specifier, context);
  },
});

const { buildSearchPageIndexFromEntries } = await import("@talliark/shared");
const { PdfTextSearcher } = await import("../src/components/viewer/pdf-text-searcher.ts");

function entriesFromText(text: string) {
  return Array.from(text, (char, i) => ({
    char,
    lineIndex: 0,
    itemIndex: i,
    normLeft: i / text.length,
    normTop: 0,
    normRight: (i + 1) / text.length,
    normBottom: 0.05,
  }));
}

test("incremental search returns later exact matches before earlier partial matches", () => {
  const pages = new Map([
    [0, entriesFromText("111")],
    [1, entriesFromText("11,")],
  ]);
  const cache = {
    has: (pdfId: string) => pdfId === "pdf-1",
    getPageIndices: () => [...pages.keys()],
    get: (_pdfId: string, pageIndex: number) => pages.get(pageIndex) ?? null,
    getSearchIndex: (_pdfId: string, pageIndex: number) => {
      const entries = pages.get(pageIndex);
      return entries ? buildSearchPageIndexFromEntries(entries) : null;
    },
  };

  const session = new PdfTextSearcher(cache as never).createSession("11", [
    { id: "pdf-1", name: "Invoice", folderId: null } as never,
  ]);

  const scanning = session.nextBatch(1, 1);
  assert.deepEqual(scanning.matches, []);
  assert.equal(scanning.hasMore, true);

  const exactBatch = session.nextBatch(1, 1);
  assert.deepEqual(exactBatch.matches.map((match) => match.contextText), ["11"]);
  assert.equal(exactBatch.matches[0]?.exactMatch, true);

  const partialBatch = session.nextBatch(1, 1);
  assert.deepEqual(partialBatch.matches.map((match) => match.contextText), ["111"]);
  assert.equal(partialBatch.matches[0]?.exactMatch, false);
});

test("incremental search reuses page matches when a result-heavy page spans batches", () => {
  const entries = entriesFromText("11 11 11");
  const searchIndex = buildSearchPageIndexFromEntries(entries);
  const cache = {
    has: () => true,
    getPageIndices: () => [0],
    get: () => entries,
    getSearchIndex: () => searchIndex,
  };
  let pageSearches = 0;
  const valuesCache = {
    logicalValuesOnPage: () => {
      pageSearches++;
      return [];
    },
  };
  const session = new PdfTextSearcher(cache as never, valuesCache as never).createSession("11", [
    { id: "pdf-1", name: "Invoice", folderId: null } as never,
  ]);

  assert.equal(session.nextBatch(1, 1).matches.length, 1);
  assert.equal(session.nextBatch(1, 1).matches.length, 1);
  assert.equal(pageSearches, 1, "the second batch should reuse computed matches instead of searching again");
});

test("magnitude aliases match both calculated and displayed values", () => {
  const entries = entriesFromText("$1");
  const cache = {
    has: () => true,
    getPageIndices: () => [0],
    get: () => entries,
    getSearchIndex: () => buildSearchPageIndexFromEntries(entries),
  };
  const valuesCache = {
    logicalValuesOnPage: () => [{
        id: "wrapped",
        kind: "number",
        text: "$1 million",
        normalizedValue: "1000000",
        magnitude: 1000000,
        bounds: { x: 0, y: 0, width: 1, height: 0.05 },
        confidence: 0.9,
    }],
  };
  const searcher = new PdfTextSearcher(cache as never, valuesCache as never);
  const pdf = { id: "pdf-1", name: "Statement", folderId: null } as never;

  assert.equal(searcher.searchPage("1000000", pdf, 0)[0]?.contextText, "$1 million");
  assert.equal(searcher.searchPage("1 million", pdf, 0)[0]?.contextText, "$1 million");

  const sameLineEntries = entriesFromText("$1 million");
  const sameLineCache = {
    ...cache,
    get: () => sameLineEntries,
    getSearchIndex: () => buildSearchPageIndexFromEntries(sameLineEntries),
  };
  const literalMatches = new PdfTextSearcher(sameLineCache as never, valuesCache as never)
    .searchPage("1 million", pdf, 0);
  assert.equal(literalMatches.length, 1, "the semantic alias must not duplicate the literal hit");
});
