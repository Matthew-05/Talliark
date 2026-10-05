import test from "node:test";
import assert from "node:assert/strict";
import { nextReviewSelection, reviewQueue, visibleSelection, type ReviewFilters } from "../src/services/review-queue.ts";
import { acceptDisplayed } from "../src/services/review-controller.ts";
import type { ReviewEquation, ReviewWorkspace } from "../src/types/reconcile-review.generated.ts";

const all: ReviewFilters = { result: "all", type: "all", status: "all" };
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
  assert.equal(queue.length, 5);
  assert.deepEqual(queue.filter(eq => eq.axis !== "manual").map(eq => eq.id), ["p1-foot", "p1-cross", "p2-cross", "p2-foot"]);
  assert.deepEqual(state.equations.map(eq => eq.id), original);
  state.equations.reverse(); assert.deepEqual(reviewQueue(state, all).map(eq => eq.id), queue.map(eq => eq.id));
});

test("result filtering spans pages and retains crossfoots as well as reviewed differences", () => {
  assert.deepEqual(reviewQueue(fixture(), { ...all, result: "difference" }).map(eq => eq.id), ["p1-cross", "p2-cross"]);
  assert.deepEqual(reviewQueue(fixture(), { ...all, result: "not-evaluable" }).map(eq => eq.id), ["p2-foot"]);
});

test("type and status filters intersect without an implicit page or table restriction", () => {
  assert.deepEqual(reviewQueue(fixture(), { ...all, type: "vertical", status: "pending" }).map(eq => eq.id), ["p1-foot", "p2-foot"]);
  assert.deepEqual(reviewQueue(fixture(), { ...all, type: "cross", status: "pending", result: "difference" }).map(eq => eq.id), ["p2-cross"]);
  assert.deepEqual(reviewQueue(fixture(), { ...all, type: "manual" }).map(eq => eq.id), ["manual"]);
});

test("a hidden selection is replaced and an empty filter has no actionable selection", () => {
  const queue = reviewQueue(fixture(), { ...all, result: "difference" });
  assert.equal(visibleSelection(queue, "p1-foot"), "p1-cross");
  assert.equal(visibleSelection(queue, "p2-cross"), "p2-cross");
  assert.equal(visibleSelection([], "p2-cross"), "");
});

test("saved decisions advance across pages within the same filter", () => {
  const state = fixture(), filters = { ...all, status: "pending" } as const;
  const before = reviewQueue(state, filters).map(eq => eq.id);
  state.equations.find(eq => eq.id === "p1-foot")!.decision = "accepted";
  assert.equal(nextReviewSelection(before, reviewQueue(state, filters), "p1-foot", ["p1-foot"]), "p2-cross");
});

test("deferred and bulk decisions do not immediately cycle back to saved ids", () => {
  const state = fixture(), before = reviewQueue(state, all).map(eq => eq.id);
  state.equations.find(eq => eq.id === "p2-cross")!.decision = "deferred";
  assert.equal(nextReviewSelection(before, reviewQueue(state, all), "p2-cross", ["p2-cross"]), "p2-foot");
  state.equations.find(eq => eq.id === "p1-foot")!.decision = "accepted";
  assert.equal(nextReviewSelection(before, reviewQueue(state, all), "p1-foot", ["p1-foot", "p2-cross"]), "p2-foot");
});

test("completing a pending filter clears selection without borrowing a hidden equation", () => {
  const state = fixture(), filters = { ...all, status: "unreviewed" } as const;
  const before = reviewQueue(state, filters).map(eq => eq.id);
  state.equations.filter(eq => eq.decision === "unreviewed").forEach(eq => eq.decision = "accepted");
  assert.equal(nextReviewSelection(before, reviewQueue(state, filters), "p1-foot", before), "");
});

test("table approval contains only matching readable pending ids from that table", () => {
  const state = fixture(), displayed = reviewQueue(state, { ...all, result: "difference" });
  assert.equal(acceptDisplayed(state, displayed.filter(eq => eq.regionId === "p1-table").map(eq => eq.id)), null);
  assert.deepEqual(acceptDisplayed(state, displayed.filter(eq => eq.regionId === "p2-table").map(eq => eq.id))?.equationIds, ["p2-cross"]);
  assert.equal(acceptDisplayed(state, ["p2-foot"]), null);
});
