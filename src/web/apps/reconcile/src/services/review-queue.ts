import type { ReviewEquation, ReviewWorkspace } from "../types/reconcile-review.generated.js";

/** Local view preferences, never part of the persisted review contract. */
export interface ReviewFilters {
  result: "all" | ReviewEquation["evaluation"]["state"];
  type: "all" | ReviewEquation["axis"];
  statuses: Array<Exclude<ReviewEquation["decision"], "rejected">>;
}

export function reviewQueue(workspace: ReviewWorkspace, filters: ReviewFilters): ReviewEquation[] {
  const cells = new Map(workspace.evidence.map(cell => [cell.id, cell]));
  const regions = new Map(workspace.regions.map(region => [region.id, region]));
  return workspace.equations.filter(eq => eq.decision !== "rejected"
    && filters.statuses.includes(eq.decision)
    && (filters.result === "all" || eq.evaluation.state === filters.result)
    && (filters.type === "all" || eq.axis === filters.type)
    )
    .sort((a, b) => {
      const ar = regions.get(a.regionId), br = regions.get(b.regionId);
      const ac = cells.get(a.targetId), bc = cells.get(b.targetId);
      return (ac?.pageIndex ?? ar?.pageIndex ?? 0) - (bc?.pageIndex ?? br?.pageIndex ?? 0)
        || (ac?.bounds.y ?? 0) - (bc?.bounds.y ?? 0) || (ac?.bounds.x ?? 0) - (bc?.bounds.x ?? 0)
        || a.axis.localeCompare(b.axis) || a.id.localeCompare(b.id);
    });
}

export function visibleSelection(queue: readonly ReviewEquation[], selectedId: string): string {
  return queue.some(eq => eq.id === selectedId) ? selectedId : "";
}

/** Source pages own presentation groups; regions remain backend equation anchors. */
export function reviewPageGroups(workspace: ReviewWorkspace, queue: readonly ReviewEquation[]): Map<number, ReviewEquation[]> {
  const cells = new Map(workspace.evidence.map(cell => [cell.id, cell]));
  const regions = new Map(workspace.regions.map(region => [region.id, region]));
  const groups = new Map<number, ReviewEquation[]>();
  for (const eq of queue) {
    const page = cells.get(eq.targetId)?.pageIndex ?? regions.get(eq.regionId)?.pageIndex ?? 0;
    const group = groups.get(page);
    if (group) group.push(eq); else groups.set(page, [eq]);
  }
  return groups;
}
