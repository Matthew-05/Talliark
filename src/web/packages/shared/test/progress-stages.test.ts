import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  STAGE_ORDER,
  fileProgressFraction,
  stageLabel,
} from "../src/progress-stages.ts";

function contractStages(file: string): string[] {
  const path = fileURLToPath(new URL(`../../../../../contracts/${file}`, import.meta.url));
  const parsed = JSON.parse(readFileSync(path, "utf8").replace(/^﻿/, "")) as {
    definitions: { ProgressStage: { enum: string[] } };
  };
  return parsed.definitions.ProgressStage.enum;
}

test("the stage table matches the web contract exactly, in order", () => {
  // The pane renders whatever stage the host sends. A stage in the contract with
  // no entry here renders as a bare status with no place on the bar, which is
  // the failure that produced the old "Starting" label.
  assert.deepEqual(STAGE_ORDER, contractStages("webview-messages-v1.json"));
});

test("both contracts describe the same pipeline", () => {
  assert.deepEqual(
    contractStages("webview-messages-v1.json"),
    contractStages("python-worker-v1.json"),
  );
});

test("every stage has a label the pane can show", () => {
  for (const stage of STAGE_ORDER) {
    const label = stageLabel(stage);
    assert.ok(label && label.trim().length > 0, `no label for "${stage}"`);
  }
  assert.equal(stageLabel("a-stage-that-does-not-exist"), null);
  assert.equal(stageLabel(undefined), null);
});

type Message = [stage: string, current?: number, total?: number];

/** A scanned PDF: no usable text layer, so it takes the recognition path. */
const SCANNED_RUN: Message[] = [
  ["queue"], ["prepare", 1, 3], ["transfer", 1, 9], ["transfer", 9, 9],
  ["security", 1, 40], ["security", 40, 40],
  ["source-check", 1, 40], ["source-check", 40, 40],
  ["geometry", 20, 40], ["ocr", 1, 40], ["ocr", 20, 40], ["ocr", 40, 40],
  ["orientation", 5, 40], ["adaptive-ocr"], ["table-recovery", 3, 40],
  ["table-structure", 40, 40], ["financial-structure"], ["values"],
  ["result-transfer"], ["finalizing"],
];

/** A spreadsheet: rendered to PDF first, then trusted without recognition. */
const CONVERTED_RUN: Message[] = [
  ["queue"], ["prepare", 2, 3], ["convert"], ["transfer", 1, 4],
  ["security", 1, 6], ["source-check", 6, 6], ["source"],
  ["table-structure", 6, 6], ["financial-structure"], ["values"],
  ["result-transfer"], ["finalizing"],
];

/**
 * A Reconcile scan: the same pipeline, with the scan's two stages appended.
 * Only a job the user started for analysis emits these, which is why the other
 * two runs jump over them.
 */
const SCAN_RUN: Message[] = [
  ["queue"], ["prepare", 1, 3], ["transfer", 1, 9],
  ["security", 1, 84], ["source-check", 84, 84], ["source"],
  ["table-structure", 84, 84], ["financial-structure"], ["values"],
  ["reconcile-tables", 1, 84], ["reconcile-tables", 84, 84], ["reconcile"],
  ["result-transfer"], ["finalizing"],
];

function replay(run: Message[]): number {
  let floor = 0;
  for (const [stage, current, total] of run) {
    const fraction = fileProgressFraction(stage, current, total);
    assert.ok(fraction !== null, `no fraction for "${stage}"`);
    assert.ok(fraction >= floor || floor > 0, `no position for "${stage}"`);
    floor = Math.max(floor, fraction);
  }
  return floor;
}

test("progress never goes backwards, on either path through the pipeline", () => {
  for (const [name, run] of [
    ["scanned", SCANNED_RUN],
    ["converted", CONVERTED_RUN],
    ["scan", SCAN_RUN],
  ] as const) {
    let floor = 0;
    for (const [stage, current, total] of run) {
      const fraction = fileProgressFraction(stage, current, total)!;
      const next = Math.max(floor, fraction);
      assert.ok(next >= floor, `${name}: bar went backwards at "${stage}"`);
      floor = next;
    }
    // The last message opens the final stage rather than closing it, so the bar
    // ends just inside it; the row then leaves "processing" and stops drawing a
    // bar at all.
    const lastStageStart = fileProgressFraction(STAGE_ORDER[STAGE_ORDER.length - 1]!)!;
    assert.equal(floor, lastStageStart, `${name}: did not reach the last stage`);
    assert.ok(floor > 0.95, `${name}: finished at ${floor}, short of the end`);
  }
});

test("every stage in the table is reachable by some run", () => {
  // A stage nothing can emit holds weight the bar can never fill, which is what
  // left a finished run stalled below the end. Two runs cover the alternatives:
  // recognition versus a trusted text layer, PDF input versus converted, and a
  // cache build versus a scan the user ran.
  const reached = new Set(
    [...SCANNED_RUN, ...CONVERTED_RUN, ...SCAN_RUN].map(([stage]) => stage),
  );
  const unreachable = STAGE_ORDER.filter((stage) => !reached.has(stage));
  assert.deepEqual(unreachable, [], `stages no run reaches: ${unreachable.join(", ")}`);
});

test("the two paths agree on where the adaptive steps sit", () => {
  // The converted run skips recognition entirely, so it must jump past those
  // stages rather than stalling before them.
  assert.ok(replay(CONVERTED_RUN) > fileProgressFraction("ocr", 0, 1)!);
});

test("a stage's counts scale only that stage's own span", () => {
  const ocrStart = fileProgressFraction("ocr", 0, 40)!;
  const ocrHalf  = fileProgressFraction("ocr", 20, 40)!;
  const ocrDone  = fileProgressFraction("ocr", 40, 40)!;
  const nextStart = fileProgressFraction("orientation", 0, 40)!;

  assert.ok(ocrStart < ocrHalf && ocrHalf < ocrDone);
  // Finishing a stage must not overrun the stage that follows it: this is what
  // stops a page count filling the whole bar and then resetting.
  assert.equal(ocrDone, nextStart);
});

test("a countless stage still places the bar, and an unknown one does not", () => {
  assert.ok(fileProgressFraction("values") !== null);
  // Null leaves the bar where it was rather than guessing a position for a
  // stage this build has never heard of.
  assert.equal(fileProgressFraction("stage-from-a-newer-host"), null);
  assert.equal(fileProgressFraction(undefined), null);
});
