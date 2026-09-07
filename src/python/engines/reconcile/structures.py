"""Discover row structures by leave-one-out parallel-column corroboration."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from . import labels, sums


MAX_HYPOTHESES_PER_BLOCK = 5000


@dataclass(frozen=True)
class Evaluation:
    column: int
    total_cell: dict
    run: sums.Run

    @property
    def ties(self) -> bool:
        return self.run.delta == 0


@dataclass(frozen=True)
class Structure:
    top: int
    total_row: int
    evaluations: tuple[Evaluation, ...]


def _evaluation(block, column: int, top: int, total_row: int) -> Evaluation | None:
    header = next((item for item in block.header_labels if item["columnIndex"] == column), None)
    if header and labels.is_non_additive_column(header.get("text", "")):
        return None
    total = block.cell(total_row, column)
    if total is None or sums.value_of(total) in (None, Decimal(0)):
        return None
    decimals = int(total.get("decimals", -1))
    cells = [block.cell(row, column) for row in range(top, total_row)]
    if any(cell is None or sums.value_of(cell) is None or not sums._eligible(cell, decimals) for cell in cells):
        return None
    concrete = [cell for cell in cells if cell is not None]
    nonzero = sum(1 for cell in concrete if sums.value_of(cell) != 0)
    if len(concrete) < 2 or nonzero < 2:
        return None
    run = sums.Run("leaves", tuple(concrete), sums.value_of(total), decimals)
    if sums.double_counts(run):
        return None
    return Evaluation(column, total, run)


def _proportional(first: Evaluation, second: Evaluation) -> bool:
    left = [sums.value_of(cell) or Decimal(0) for cell in first.run.cells]
    right = [sums.value_of(cell) or Decimal(0) for cell in second.run.cells]
    pairs = [(a, b) for a, b in zip(left, right) if a != 0 or b != 0]
    if not pairs:
        return True
    anchor_a, anchor_b = pairs[0]
    return all(a * anchor_b == b * anchor_a for a, b in pairs[1:])


def _independent(evaluations: list[Evaluation]) -> list[Evaluation]:
    representatives: list[Evaluation] = []
    for evaluation in evaluations:
        if not any(_proportional(evaluation, prior) for prior in representatives):
            representatives.append(evaluation)
    return representatives


def discover(block) -> tuple[list[Structure], int]:
    """Return hypotheses retained by at least one exact, non-degenerate tie."""
    retained: list[Structure] = []
    tested = 0
    for total_row in range(2, block.row_count):
        for top in range(total_row - 2, -1, -1):
            if tested >= MAX_HYPOTHESES_PER_BLOCK:
                return retained, tested
            tested += 1
            evaluations = [
                evaluation
                for column in range(1, block.column_count)
                if (evaluation := _evaluation(block, column, top, total_row)) is not None
            ]
            if any(evaluation.ties for evaluation in evaluations):
                retained.append(Structure(top, total_row, tuple(evaluations)))
    return retained, tested


def resolve(block) -> tuple[list[dict], dict]:
    """Publish the strongest proven structure for each candidate total cell."""
    structures, tested = discover(block)
    established_columns = {
        evaluation.column
        for structure in structures
        for evaluation in structure.evaluations
        if evaluation.ties and len(_independent([item for item in structure.evaluations if item.ties])) >= 2
    }
    proposals: dict[str, list[tuple[tuple[int, int, int], dict]]] = {}
    for structure in structures:
        tied = [evaluation for evaluation in structure.evaluations if evaluation.ties]
        agreeing = _independent(tied)
        agreeing_columns = {evaluation.column for evaluation in agreeing}
        for evaluation in structure.evaluations:
            own_label = labels.is_total_label(evaluation.total_cell.get("rowLabel", ""))
            other_agreement = len([column for column in agreeing_columns if column != evaluation.column])
            if evaluation.ties:
                publish = len(agreeing) >= 2 or (len(agreeing) >= 1 and own_label)
            else:
                publish = other_agreement >= 2 or (
                    other_agreement >= 1
                    and (own_label or evaluation.column in established_columns)
                )
            if not publish or (not evaluation.ties and not sums._plausible(evaluation.run)):
                continue
            signals = []
            if own_label:
                label = evaluation.total_cell.get("rowLabel", "")
                signals.append({"name": "label-total", "evidence": f'row label reads "{label}"'})
            if other_agreement:
                columns = ", ".join(str(column) for column in sorted(agreeing_columns - {evaluation.column}))
                signals.append({
                    "name": "column-corroboration",
                    "evidence": f"the same rows foot independently in column{'s' if other_agreement != 1 else ''} {columns}",
                })
            if not signals:
                continue
            outcome = "confirmed" if evaluation.ties else "break"
            payload = {
                "cell": evaluation.total_cell,
                "signals": signals,
                "outcome": outcome,
                "resolution": evaluation.run.as_dict(),
                "top": structure.top,
            }
            score = (other_agreement, len(evaluation.run.cells), -structure.top)
            proposals.setdefault(evaluation.total_cell["id"], []).append((score, payload))
    chosen = [max(candidates, key=lambda item: item[0])[1] for candidates in proposals.values()]
    chosen.sort(key=lambda item: (item["cell"]["rowIndex"], item["cell"]["columnIndex"]))
    return chosen, {"hypotheses": tested, "retained": len(structures)}
