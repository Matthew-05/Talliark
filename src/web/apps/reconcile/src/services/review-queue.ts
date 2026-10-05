import type { ReviewEquation, ReviewWorkspace } from "../types/reconcile-review.generated.js";

/** Local view preferences, never part of the persisted review contract. */
export interface ReviewFilters {
  result: "all" | ReviewEquation["evaluation"]["state"];
  type: "all" | ReviewEquation["axis"];
  status: "all" | "pending" | ReviewEquation["decision"];
}

export function reviewQueue(workspace: ReviewWorkspace, filters: ReviewFilters): ReviewEquation[] {
  const cells = new Map(workspace.evidence.map(cell => [cell.id, cell]));
  const regions = new Map(workspace.regions.map(region => [region.id, region]));
  return workspace.equations.filter(eq => (filters.result === "all" || eq.evaluation.state === filters.result)
    && (filters.type === "all" || eq.axis === filters.type)
    && (filters.status === "all" || eq.decision === filters.status
      || (filters.status === "pending" && (eq.decision === "unreviewed" || eq.decision === "deferred"))))
    .sort((a, b) => {
      const ar = regions.get(a.regionId), br = regions.get(b.regionId);
      const ac = cells.get(a.targetId), bc = cells.get(b.targetId);
      return (ac?.pageIndex ?? ar?.pageIndex ?? 0) - (bc?.pageIndex ?? br?.pageIndex ?? 0)
        || (ar?.bounds.y ?? 0) - (br?.bounds.y ?? 0) || (ar?.bounds.x ?? 0) - (br?.bounds.x ?? 0)
        || a.regionId.localeCompare(b.regionId)
        || (ac?.bounds.y ?? 0) - (bc?.bounds.y ?? 0) || (ac?.bounds.x ?? 0) - (bc?.bounds.x ?? 0)
        || a.axis.localeCompare(b.axis) || a.id.localeCompare(b.id);
    });
}

export function visibleSelection(queue: readonly ReviewEquation[], selectedId: string): string {
  return queue.some(eq => eq.id === selectedId) ? selectedId : queue[0]?.id ?? "";
}

/** Advance in the pre-save order, but only to pending sums still in the active filters.
 * Excluding all saved ids prevents deferred/bulk decisions from immediately repeating. */
export function nextReviewSelection(before: readonly string[], after: readonly ReviewEquation[], selectedId: string,
  decidedIds: readonly string[]): string {
  const excluded = new Set(decidedIds);
  const current = before.indexOf(selectedId);
  const order = [...before.slice(current + 1), ...before.slice(0, current + 1)];
  const pending = new Set(after.filter(eq => !excluded.has(eq.id)
    && (eq.decision === "unreviewed" || eq.decision === "deferred")).map(eq => eq.id));
  return order.find(id => pending.has(id)) ?? visibleSelection(after, selectedId);
}
