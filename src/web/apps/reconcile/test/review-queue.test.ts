import test from "node:test";
import assert from "node:assert/strict";
import { reviewPageGroups, reviewQueue, visibleSelection, type ReviewFilters } from "../src/services/review-queue.ts";
import { acceptDisplayed } from "../src/services/review-controller.ts";
import type { ReviewEquation, ReviewWorkspace } from "../src/types/reconcile-review.generated.ts";

const all: ReviewFilters = { result: "all", type: "all", statuses: ["unreviewed", "deferred", "accepted"] };
function fixture(): ReviewWorkspace {
  const equations: ReviewEquation[] = [
    { id: "p2-cross", regionId: "p2-table", targetId: "p2-cross", terms: [], axis: "cross", origin: "detected", decision: "unreviewed", evaluation: { state: "difference", sum: "7", delta: "1", reason: "" }, issue: "open", note: "", signature: "a" },
    { id: "p1-foot", regionId: "p1-table", targetId: "p1-foot", terms: [], axis: "vertical", origin: "detected", decision: "unreviewed", evaluation: { state: "exact-match", sum: "7", delta: "0", reason: "" }, issue: "open", note: "", signature: "b" },
    { id: "p2-foot", regionId: "p2-table", targetId: "p2-foot", terms: [], axis: "vertical", origin: "detected", decision: "deferred", evaluation: { state: "not-evaluable", sum: "", delta: "", reason: "No operands" }, issue: "open", note: "", signature: "c" },
    { id: "p1-cross", regionId: "p1-table", targetId: "p1-cross", terms: [], axis: "cross", origin: "detected", decision: "accepted", evaluation: { state: "difference", sum: "7", delta: "2", reason: "" }, issue: "open", note: "", signature: "d" },
    { id: "manual", regionId: "page-0", targetId: "manual", terms: [], axis: "manual", origin: "manual", decision: "rejected", evaluation: { state: "exact-match", sum: "7", delta: "0", reason: "" }, issue: "open", note: "", signature: "e" },
  ];
  return { version: 1, documentId: "doc", scanId: "scan", geometryFingerprint: "geo", pageCount: 2, revision: 0,
    equations, evidence: equations.map((eq, i) => ({ id: eq.targetId, pageIndex: eq.id.startsWith("p2") ? 1 : 0,
      bounds: { x: .5, y: .1 + .1 * i, width: .1, height: .02 }, text: "8", value: "8", label: eq.id, column: "2025", dash: false, origin: "scan" })),
    regions: [0, 1].map(page => ({ id: `p${page + 1}-table`, pageIndex: page, bounds: { x: .1, y: .1, width: .8, height: .6 }, label: `Page ${page + 1}` })),
    corrections: [], reviewedPages: [], archives: [], history: [] };
}

test("all results include both axes across every page in stable source order", () => {
  const state = fixture(), original = state.equations.map(eq => eq.id);
  const queue = reviewQueue(state, all);
  assert.equal(queue.length, 4);
  assert.deepEqual(queue.filter(eq => eq.axis !== "manual").map(eq => eq.id), ["p1-foot", "p1-cross", "p2-cross", "p2-foot"]);
  assert.deepEqual(state.equations.map(eq => eq.id), original);
  state.equations.reverse(); assert.deepEqual(reviewQueue(state, all).map(eq => eq.id), queue.map(eq => eq.id));
});

test("result filtering spans pages and retains crossfoots as well as reviewed differences", () => {
  assert.deepEqual(reviewQueue(fixture(), { ...all, result: "difference" }).map(eq => eq.id), ["p1-cross", "p2-cross"]);
  assert.deepEqual(reviewQueue(fixture(), { ...all, result: "not-evaluable" }).map(eq => eq.id), ["p2-foot"]);
});

test("type and status filters intersect without an implicit page or table restriction", () => {
  assert.deepEqual(reviewQueue(fixture(), { ...all, type: "vertical", statuses: ["unreviewed", "deferred"] }).map(eq => eq.id), ["p1-foot", "p2-foot"]);
  assert.deepEqual(reviewQueue(fixture(), { ...all, type: "cross", statuses: ["unreviewed", "deferred"], result: "difference" }).map(eq => eq.id), ["p2-cross"]);
  assert.deepEqual(reviewQueue(fixture(), { ...all, type: "manual" }).map(eq => eq.id), []);
});

test("a hidden selection is cleared and an empty filter has no actionable selection", () => {
  const queue = reviewQueue(fixture(), { ...all, result: "difference" });
  assert.equal(visibleSelection(queue, "p1-foot"), "");
  assert.equal(visibleSelection(queue, "p2-cross"), "p2-cross");
  assert.equal(visibleSelection([], "p2-cross"), "");
});

test("checkbox statuses form a union within the other filters", () => {
  const state = fixture();
  assert.deepEqual(reviewQueue(state, { ...all, statuses: ["deferred", "accepted"] }).map(eq => eq.id), ["p1-cross", "p2-foot"]);
  assert.deepEqual(reviewQueue(state, { ...all, statuses: ["deferred", "accepted"], type: "vertical" }).map(eq => eq.id), ["p2-foot"]);
  assert.deepEqual(reviewQueue(state, { ...all, statuses: [] }), []);
});

test("rejection disappears from every user filter while backend evidence is retained", () => {
  const state = fixture();
  const rejected = state.equations.find(eq => eq.id === "p1-foot")!; rejected.decision = "rejected";
  assert.ok(!reviewQueue(state, all).some(eq => eq.id === rejected.id));
  assert.equal(state.equations.find(eq => eq.id === rejected.id)?.decision, "rejected");
  assert.ok(state.evidence.some(cell => cell.id === rejected.targetId));
});

test("page groups merge tables and manual regions in printed row order", () => {
  const state = fixture();
  const manual = state.equations.find(eq => eq.id === "manual")!; manual.decision = "unreviewed";
  const another = { ...manual, id: "another-table", targetId: "another-table", regionId: "another-region" };
  state.regions.push({ ...state.regions[0]!, id: "another-region", bounds: { x: .1, y: .01, width: .2, height: .2 } });
  state.equations.push(another);
  state.evidence.push({ ...state.evidence[0]!, id: another.targetId, pageIndex: 0, bounds: { x: .5, y: .35, width: .1, height: .02 } });
  const groups = reviewPageGroups(state, reviewQueue(state, all));
  assert.deepEqual([...groups.keys()], [0, 1]);
  assert.deepEqual(groups.get(0)!.map(eq => eq.id), ["p1-foot", "another-table", "p1-cross", "manual"]);
  assert.equal(groups.get(0)!.length, 4);
});

test("loading and completing a decision do not implicitly focus another sum", () => {
  const state = fixture();
  assert.equal(visibleSelection(reviewQueue(state, all), ""), "");
  state.equations.find(eq => eq.id === "p1-foot")!.decision = "accepted";
  assert.equal(visibleSelection(reviewQueue(state, { ...all, statuses: ["unreviewed"] }), "p1-foot"), "");
});

test("page approval contains only matching readable pending ids from that page", () => {
  const state = fixture(), displayed = reviewQueue(state, { ...all, result: "difference" });
  assert.equal(acceptDisplayed(state, reviewPageGroups(state, displayed).get(0)!.map(eq => eq.id)), null);
  assert.deepEqual(acceptDisplayed(state, reviewPageGroups(state, displayed).get(1)!.map(eq => eq.id))?.equationIds, ["p2-cross"]);
  assert.equal(acceptDisplayed(state, ["p2-foot"]), null);
});
