import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { ReviewController, acceptDisplayed, reviewCounts } from "../src/services/review-controller.ts";
import type { ReviewEquation, ReviewRequest, ReviewResponse, ReviewWorkspace } from "../src/types/reconcile-review.generated.ts";

function equation(id: string, decision: ReviewEquation["decision"] = "unreviewed", state: ReviewEquation["evaluation"]["state"] = "exact-match"): ReviewEquation {
  return { id, regionId: "table", targetId: id, terms: [], axis: "vertical", origin: "detected", decision,
    evaluation: { state, sum: "8", delta: state === "difference" ? "1" : "0", reason: "" }, issue: "open", note: "", signature: id };
}
function workspace(revision = 0): ReviewWorkspace {
  return { version: 1, documentId: "doc", scanId: "scan", geometryFingerprint: "geo", pageCount: 2, revision,
    evidence: [], regions: [], equations: [equation("a"), equation("b", "deferred", "difference"), equation("c", "rejected"), equation("d", "unreviewed", "not-evaluable")],
    corrections: [], history: [], archives: [], reviewedPages: [] };
}
function harness() {
  const sent: ReviewRequest[] = []; let changes = 0;
  const controller = new ReviewController("doc", request => sent.push(request), () => changes++, () => String(sent.length));
  const reply = (status: ReviewResponse["status"], state = workspace()): void => {
    controller.receive({ type: "reconcile-review-response", version: 1, requestId: sent.at(-1)!.requestId, pdfId: "doc", status, workspace: state });
  };
  return { controller, sent, reply, changes: () => changes };
}

test("all generated review bindings match the contract", () => {
  const script = fileURLToPath(new URL("../../../../../scripts/generate_reconcile_review.mjs", import.meta.url));
  const result = spawnSync(process.execPath, [script, "--check"], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
});

test("review requires a load acknowledgement before mutations", () => {
  const { controller, sent, reply } = harness();
  controller.request("commit", [{ kind: "decision", equationIds: ["a"], decision: "accepted" }]);
  assert.equal(sent.length, 0);
  controller.request("load"); assert.equal(controller.workspace, null); reply("loaded");
  assert.equal(controller.workspace?.scanId, "scan");
});

test("preview does not change saved state or undo history", () => {
  const { controller, reply } = harness(); controller.request("load"); reply("loaded");
  const saved = controller.workspace; const preview = workspace(); preview.equations[0]!.decision = "accepted";
  controller.request("preview", [{ kind: "decision", equationIds: ["a"], decision: "accepted" }]); reply("preview", preview);
  assert.equal(controller.workspace, saved); assert.equal(controller.preview, preview); assert.equal(controller.undoRevision, null);
});

test("direct XML update captures undo revision without a save notice", () => {
  const { controller, sent, reply } = harness(); controller.request("load"); reply("loaded", workspace(4));
  controller.request("commit", [{ kind: "decision", equationIds: ["a"], decision: "accepted" }]);
  assert.equal(controller.workspace?.revision, 4); assert.equal(sent.at(-1)!.expectedRevision, 4);
  assert.equal(controller.notice, "");
  reply("updated", workspace(5)); assert.equal(controller.workspace?.revision, 5); assert.equal(controller.undoRevision, 4);
  assert.equal(controller.lastAppliedOperations[0]?.kind, "decision");
  assert.equal(controller.notice, "");
});

test("failed XML update preserves state and does not apply pending operations", () => {
  const { controller, sent, reply } = harness(); controller.request("load"); reply("loaded");
  const saved = controller.workspace;
  controller.request("commit", [{ kind: "restore", revision: 0 }]);
  controller.receive({ type: "reconcile-review-response", version: 1, requestId: sent.at(-1)!.requestId, pdfId: "doc", status: "error", error: "Workbook protected" });
  assert.equal(controller.workspace, saved); assert.equal(controller.pending, null);
  assert.equal(controller.error, "Workbook protected"); assert.deepEqual(controller.lastAppliedOperations, []);
});

test("unrelated or late acknowledgement cannot replace state", () => {
  const { controller, sent, reply } = harness(); controller.request("load");
  const response: ReviewResponse = { type: "reconcile-review-response", version: 1, requestId: "old", pdfId: "doc", status: "loaded", workspace: workspace() };
  assert.equal(controller.receive(response), false); assert.ok(controller.pending);
  assert.equal(controller.receive({ ...response, requestId: sent[0]!.requestId, pdfId: "other" }), false);
  reply("loaded"); assert.equal(controller.receive(response), false);
});

test("only one request is outstanding", () => {
  const { controller, sent } = harness(); controller.request("load"); controller.request("load"); assert.equal(sent.length, 1);
});

test("bulk approval uses explicit displayed ids and excludes unreadable or rejected suggestions", () => {
  const state = workspace(); const ids = ["a", "b", "c", "d"];
  const operation = acceptDisplayed(state, ids); ids.push("later");
  assert.deepEqual(operation, { kind: "decision", equationIds: ["a", "b"], decision: "accepted" });
  assert.equal(acceptDisplayed(state, ["c", "d"]), null);
});

test("arithmetic ties are separate from approval and open issues", () => {
  const state = workspace(); assert.deepEqual(reviewCounts(state), { accepted: 0, pending: 3, differences: 0 });
  state.equations[1]!.decision = "accepted";
  assert.deepEqual(reviewCounts(state), { accepted: 1, pending: 2, differences: 1 });
  state.equations[1]!.issue = "explained"; assert.equal(reviewCounts(state).differences, 0);
});

test("reloading after a scan change resets undo", () => {
  const { controller, reply } = harness(); controller.request("load"); reply("loaded");
  controller.request("commit", [{ kind: "page", pageIndex: 0, reviewed: true }]); reply("updated", workspace(1));
  assert.equal(controller.undoRevision, 0);
  const state = workspace(2); state.scanId = "new-scan"; controller.request("load"); reply("loaded", state);
  assert.equal(controller.undoRevision, null);
});

test("unexpected acknowledgement does not apply data", () => {
  const { controller, reply } = harness(); controller.request("load"); reply("updated", workspace(8));
  assert.equal(controller.workspace, null); assert.match(controller.error, /Unexpected/);
});

test("Excel save is a separate host action and preserves review state and undo", () => {
  const { controller, sent, reply } = harness(); controller.request("load"); reply("loaded", workspace(4));
  controller.request("commit", [{ kind: "page", pageIndex: 0, reviewed: true }]); reply("updated", workspace(5));
  const state = controller.workspace;
  controller.request("save-workbook");
  assert.equal(sent.at(-1)!.mode, "save-workbook"); assert.deepEqual(sent.at(-1)!.operations, []);
  controller.receive({ type: "reconcile-review-response", version: 1, requestId: sent.at(-1)!.requestId, pdfId: "doc", status: "workbook-saved" });
  assert.equal(controller.workspace, state); assert.equal(controller.undoRevision, 4);
  assert.deepEqual(controller.lastAppliedOperations, []); assert.equal(controller.notice, "Workbook saved");
});

test("cancelled Excel save leaves the automatically recorded XML state available", () => {
  const { controller, sent, reply } = harness(); controller.request("load"); reply("loaded");
  const state = controller.workspace; controller.request("save-workbook");
  controller.receive({ type: "reconcile-review-response", version: 1, requestId: sent.at(-1)!.requestId, pdfId: "doc", status: "error", error: "Save cancelled" });
  assert.equal(controller.workspace, state); assert.match(controller.notice, /open workbook/); assert.equal(controller.error, "Save cancelled");
});
