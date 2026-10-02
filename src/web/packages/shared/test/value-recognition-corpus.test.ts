import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import type { DocumentValues } from "../src/document-values-decoder.ts";
import type { TextGeometry } from "../src/geometry-decoder.ts";
import { detectValues } from "../src/value-recognition/detector.ts";

/**
 * The shared geometry-to-model corpus the Python engine also scores against
 * (`src/python/tests/fixtures/values/detector-cases.json`, rebuilt by
 * `scripts/make_value_fixtures.py`). One file, two runners: this asserts the
 * TypeScript recognizer publishes exactly what the Python reference does, so a
 * change to either that the other does not make fails a test here or there.
 */
const CORPUS = new URL(
  "../../../../../src/python/tests/fixtures/values/detector-cases.json",
  import.meta.url,
);

interface CompactPage {
  pageIndex: number;
  /** `[char, x, y, width, height, lineIndex]` per character. */
  characters: Array<[string, number, number, number, number, number]>;
}

interface CompactGeometry {
  version: 1;
  coordinateSpace: "normalized";
  pages: CompactPage[];
}

interface Case {
  name: string;
  geometry: CompactGeometry;
  expect: unknown;
}

/** Expand the corpus's compact character arrays, mirroring `documents.expand_geometry`. */
function expandGeometry(compact: CompactGeometry): TextGeometry {
  return {
    version: 1,
    coordinateSpace: "normalized",
    pages: compact.pages.map((page) => ({
      pageIndex: page.pageIndex,
      characters: page.characters.map(([char, x, y, width, height, lineIndex]) => ({
        char, x, y, width, height, lineIndex,
      })),
    })),
  };
}

/** The cross-runtime comparable form, mirroring `documents.canonical` in Python. */
function canonical(model: DocumentValues): unknown {
  const strip = <T extends { id: string }>(span: T): Omit<T, "id"> => {
    const { id: _id, ...rest } = span;
    return rest;
  };
  return {
    documentContext: model.documentContext,
    pages: model.pages.map((page) => ({
      pageIndex: page.pageIndex,
      context: page.context,
      values: page.values.map((span) => strip(span)),
      references: (page.references ?? []).map((span) => strip(span)),
      structure: (page.structure ?? []).map((span) => strip(span)),
      noise: (page.noise ?? []).map((span) => strip(span)),
    })),
  };
}

test("the frontend recognizer matches the shared detector corpus", async () => {
  const cases = JSON.parse(await readFile(CORPUS, "utf8")) as Case[];
  assert.ok(cases.length > 0);
  for (const entry of cases) {
    const model = await detectValues(expandGeometry(entry.geometry));
    assert.deepEqual(canonical(model), entry.expect, `for case ${entry.name}`);
  }
});
