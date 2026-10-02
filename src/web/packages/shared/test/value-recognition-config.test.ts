import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  CATEGORIES,
  CLICKABLE_BY_DEFAULT,
  CURRENCY_CODES,
  NOISE_REASONS,
  REFERENCE_KINDS,
  STRUCTURE_KINDS,
} from "../src/value-recognition/config.ts";

/**
 * The recognizer config and the contract are two statements of one vocabulary.
 * The contract pins the closed enums; this asserts the shared config -- read by
 * both the Python engine and the TypeScript recognizer -- still agrees, so a
 * change made in one place cannot silently diverge from the contract.
 */
interface Contract {
  definitions: {
    Noise: { properties: { reason: { enum: string[] } } };
    Reference: { properties: { kind: { enum: string[] } } };
    Structure: { properties: { kind: { enum: string[] } } };
    Context: { properties: { currency: { enum: string[] } } };
  };
}

async function contract(): Promise<Contract> {
  return JSON.parse(
    await readFile(
      new URL("../../../../../contracts/document-values-v1.json", import.meta.url),
      "utf8",
    ),
  ) as Contract;
}

test("the recognizer vocabulary matches the document-values contract", async () => {
  const definitions = (await contract()).definitions;
  assert.deepEqual([...REFERENCE_KINDS].sort(), [...definitions.Reference.properties.kind.enum].sort());
  assert.deepEqual([...STRUCTURE_KINDS].sort(), [...definitions.Structure.properties.kind.enum].sort());
  assert.deepEqual([...NOISE_REASONS].sort(), [...definitions.Noise.properties.reason.enum].sort());
  assert.deepEqual(
    Object.keys(CURRENCY_CODES).sort(),
    [...definitions.Context.properties.currency.enum].sort(),
  );
});

test("clickability is stated for every category", () => {
  assert.deepEqual(Object.keys(CLICKABLE_BY_DEFAULT).sort(), [...CATEGORIES].sort());
});
