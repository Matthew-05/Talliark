/**
 * The pipeline's steps, and how far through a file each one is.
 *
 * The order here mirrors the `ProgressStage` enum in
 * contracts/webview-messages-v1.json, which is the source of the vocabulary —
 * `test/progress-stages.test.ts` fails if the two drift apart. The host states
 * which step a message belongs to, so nothing here reads the message text.
 *
 * This lives in the shared package because two apps render the same pipeline:
 * the file manager watches a cache build, and Reconcile watches a scan, which
 * is that same pipeline with two stages appended. A second copy of the table
 * would drift from the contract independently of the first.
 *
 * Weights are this file's own, and they are presentation: they say how much of
 * a file's wait each step tends to be, which is what makes one bar advance
 * evenly instead of restarting per step. They are rough by nature; only their
 * proportions matter.
 */

export interface StageDefinition {
  /** What the pane calls this step. */
  readonly label: string;
  /** Share of a file's total wait, relative to the other stages. */
  readonly weight: number;
}

/**
 * Declared in pipeline order. A run skips steps — a document with a trusted
 * text layer never reaches `ocr` — so this is a ranking, not a sequence to
 * expect in full, and progress jumps over what did not run.
 */
export const STAGES: ReadonlyMap<string, StageDefinition> = new Map([
  ["queue",           { label: "Queued",            weight: 0 }],
  ["prepare",         { label: "Preparing",         weight: 1 }],
  ["convert",         { label: "Converting",        weight: 3 }],
  ["transfer",        { label: "Sending",           weight: 3 }],
  ["security",        { label: "Securing",          weight: 4 }],
  ["source-check",    { label: "Checking text",     weight: 5 }],
  ["source",          { label: "Using text",        weight: 1 }],
  ["geometry",        { label: "Reading text",      weight: 6 }],
  ["ocr",             { label: "Recognizing",       weight: 40 }],
  ["orientation",     { label: "Checking rotation", weight: 4 }],
  ["adaptive-ocr",    { label: "Refining",          weight: 15 }],
  ["table-recovery",  { label: "Reading tables",    weight: 8 }],
  ["table-structure", { label: "Finding tables",    weight: 6 }],
  ["financial-structure",    { label: "Reading structure", weight: 3 }],
  ["values",          { label: "Finding values",    weight: 3 }],
  // The scan's own stages. They run only when a job asked for analysis, so an
  // ordinary cache build jumps straight over them — which is the gap the
  // contract already tells every consumer to tolerate.
  ["reconcile-tables", { label: "Finding tables",    weight: 10 }],
  ["reconcile",       { label: "Reconciling",       weight: 12 }],
  ["result-transfer", { label: "Saving",            weight: 3 }],
  ["finalizing",      { label: "Finalizing",        weight: 2 }],
]);

export const STAGE_ORDER: readonly string[] = Array.from(STAGES.keys());

const TOTAL_WEIGHT = Array.from(STAGES.values()).reduce((sum, s) => sum + s.weight, 0);

/** Weight of every stage declared before this one. */
const WEIGHT_BEFORE: ReadonlyMap<string, number> = (() => {
  const before = new Map<string, number>();
  let running = 0;
  for (const [stage, definition] of STAGES) {
    before.set(stage, running);
    running += definition.weight;
  }
  return before;
})();

/** The pane's name for a stage, or null when the host names one we don't know. */
export function stageLabel(stage: string | undefined): string | null {
  if (!stage) return null;
  return STAGES.get(stage)?.label ?? null;
}

/**
 * How far through the whole file a stage sits, as 0–1.
 *
 * `current`/`total` count within the stage only, so they scale that stage's own
 * span rather than driving the bar directly. That is the difference that
 * matters: the counts arriving during OCR are pages, during transfer they are
 * chunks, and feeding either straight to the bar is what made it fill and reset
 * several times per file.
 *
 * Returns null for a stage we have no weight for, so the caller can leave the
 * bar where it was instead of guessing.
 */
export function fileProgressFraction(
  stage: string | undefined,
  current?: number,
  total?: number,
): number | null {
  if (!stage) return null;
  const definition = STAGES.get(stage);
  const before = WEIGHT_BEFORE.get(stage);
  if (!definition || before === undefined || TOTAL_WEIGHT <= 0) return null;

  let within = 0;
  if (typeof current === "number" && typeof total === "number" && total > 0) {
    within = Math.min(Math.max(current / total, 0), 1);
  }

  return Math.min(Math.max((before + definition.weight * within) / TOTAL_WEIGHT, 0), 1);
}
