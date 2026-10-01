import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

const textExtractorUrl = new URL("../src/text-extractor.ts", import.meta.url).href;
const zeroPlaceholderUrl = new URL("../src/zero-placeholder.ts", import.meta.url).href;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "./zero-placeholder.js" && context.parentURL === textExtractorUrl) {
      return { url: zeroPlaceholderUrl, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const { extractText } = await import(textExtractorUrl) as typeof import("../src/text-extractor.ts");

function entriesFromText(text: string) {
  return Array.from(text, (char, index) => ({
    char,
    lineIndex: 0,
    itemIndex: index,
    normLeft: index / text.length,
    normTop: 0,
    normRight: (index + 1) / text.length,
    normBottom: 0.05,
    spacesPrecomputed: true,
  }));
}

function entry(
  char: string,
  lineIndex: number,
  itemIndex: number,
  left: number,
  top: number,
  right: number,
  bottom: number,
) {
  return {
    char,
    lineIndex,
    itemIndex,
    normLeft: left,
    normTop: top,
    normRight: right,
    normBottom: bottom,
    spacesPrecomputed: true,
  };
}

const fullRect = { x: 0, y: 0, width: 1, height: 1 };

test("normalizes complete financial zero placeholders without changing other text", () => {
  assert.equal(extractText(entriesFromText("—"), fullRect), "0");
  assert.equal(extractText(entriesFromText("$ —"), fullRect), "$ 0");
  assert.equal(extractText(entriesFromText("$ 1"), fullRect), "$ 1");
  assert.equal(
    extractText(entriesFromText("Revenue — expense"), fullRect),
    "Revenue — expense",
  );
});

test("orders raised copyright and trademark signs by horizontal position", () => {
  for (const symbol of ["©", "®", "™"]) {
    const entries = [
      entry(symbol, 0, 0, 0.30, 0.01, 0.35, 0.05),
      entry("4", 1, 1, 0.10, 0.03, 0.20, 0.10),
      entry("R", 1, 2, 0.20, 0.03, 0.30, 0.10),
    ];

    assert.equal(extractText(entries, fullRect), `4R${symbol}`);
  }
});

test("keeps superscripts and subscripts inline with their base text", () => {
  const superscript = [
    entry("2", 0, 0, 0.20, 0.01, 0.24, 0.05),
    entry("x", 1, 1, 0.10, 0.03, 0.20, 0.10),
  ];
  const subscript = [
    entry("2", 0, 0, 0.20, 0.07, 0.24, 0.11),
    entry("H", 1, 1, 0.10, 0.03, 0.20, 0.10),
    entry("O", 1, 2, 0.24, 0.03, 0.34, 0.10),
  ];

  assert.equal(extractText(superscript, fullRect), "x2");
  assert.equal(extractText(subscript, fullRect), "H2O");
});

test("does not fold a separate small line into the line below it", () => {
  const entries = [
    entry("1", 0, 0, 0.10, 0.01, 0.14, 0.05),
    entry("A", 1, 1, 0.10, 0.08, 0.20, 0.15),
  ];

  assert.equal(extractText(entries, fullRect), "1 A");
});

test("does not let right-alignment padding split a number", () => {
  // Right-aligned columns carry a run of space glyphs whose last member
  // reaches the figure and overlaps its first digit. Emitting it would read
  // "3,016" as "3 ,016", and a sum tool then adds 3 and 16 separately.
  const entries = [
    entry("3", 0, 0, 0.60, 0.10, 0.61, 0.12),
    entry(",", 0, 1, 0.61, 0.10, 0.615, 0.12),
    entry("0", 0, 2, 0.615, 0.10, 0.625, 0.12),
    entry("1", 0, 3, 0.625, 0.10, 0.635, 0.12),
    entry("6", 0, 4, 0.635, 0.10, 0.645, 0.12),
    entry(" ", 1, 5, 0.55, 0.10, 0.555, 0.12),
    entry(" ", 1, 6, 0.56, 0.10, 0.565, 0.12),
    entry(" ", 1, 7, 0.60, 0.10, 0.605, 0.12),
  ];

  assert.equal(extractText(entries, fullRect), "  3,016");
});

test("keeps an ordinary word space that only grazes a neighbour", () => {
  const entries = [
    entry(",", 0, 0, 0.20, 0.10, 0.205, 0.12),
    entry(" ", 0, 1, 0.2046, 0.10, 0.2096, 0.12),
    entry("n", 0, 2, 0.2099, 0.10, 0.2169, 0.12),
  ];

  assert.equal(extractText(entries, fullRect), ", n");
});
