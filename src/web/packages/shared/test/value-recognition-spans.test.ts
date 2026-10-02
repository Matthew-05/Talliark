import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { recognizeSpans } from "../src/value-recognition/spans.ts";

const ORACLE = new URL(
  "../../../../../src/python/tests/fixtures/values/span-oracle.json",
  import.meta.url,
);

interface OracleCase {
  text: string;
  spans: Array<Record<string, unknown>>;
}

function describeSpan(span: ReturnType<typeof recognizeSpans>[number]): Record<string, unknown> {
  const item: Record<string, unknown> = { kind: span.kind, text: span.text };
  if (span.normalizedValue) item.normalizedValue = span.normalizedValue;
  if (span.currency) item.currency = span.currency;
  if (span.magnitude) item.magnitude = span.magnitude;
  if (span.datePrecision) item.datePrecision = span.datePrecision;
  if (span.dateOrder === "ambiguous") item.dateOrder = span.dateOrder;
  return item;
}

test("the frontend recognizer matches the Python span oracle", async () => {
  const cases = JSON.parse(await readFile(ORACLE, "utf8")) as OracleCase[];
  for (const entry of cases) {
    const actual = recognizeSpans(entry.text).map(describeSpan);
    assert.deepEqual(actual, entry.spans, `for text ${JSON.stringify(entry.text)}`);
  }
});

test("dates win over the numbers inside them", () => {
  const spans = recognizeSpans("At December 31, 2025, cash was $ 1,200 or 4.5%.");
  assert.deepEqual(spans.map((span) => span.kind), ["date", "number", "percent"]);
});
