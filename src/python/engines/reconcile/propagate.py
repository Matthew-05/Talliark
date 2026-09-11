"""Verified-structure propagation: a confirmed total is evidence for its neighbours.

A total that foots in two independent columns proves the row structure -- which
rows are its addends and which signs are reversed. That proof is stronger than
anything a single column can offer, and this pass spends it:

* the same structure is tested in the remaining columns of the same row, even
  where the ordinary search could not reach it; and
* an unresolved total standing in an addend row of a confirmed total, and
  carrying a total's own label or rule, may use the corroborated floor.

The evidence is still structural. A propagated tie is confirmed only when the
structure is independently proved elsewhere; the pass never proposes a total on
its own and never accuses. A propagated run that misses is left unresolved and
recorded for development, because a total that foots everywhere except one
column is usually a rounding difference, and that is worth seeing even when it
is not proof.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from . import sums


@dataclass(frozen=True)
class Witness:
    """One column's confirmed structure for a total row."""

    column: int
    addend_rows: tuple[int, ...]
    negated_rows: tuple[int, ...]
    basis: str
    values: tuple[Decimal, ...]


def _witnesses(built, column_results) -> dict[int, list[Witness]]:
    """Every confirmed total's run, grouped by the row it belongs to."""
    by_row: dict[int, list[Witness]] = {}
    for column, results in column_results.items():
        for nomination, resolved in results:
            if resolved.get("outcome") != "confirmed":
                continue
            resolution = resolved.get("resolution") or {}
            addend_ids = resolution.get("addendCellIds") or []
            if len(addend_ids) < 2:
                continue
            negated = frozenset(resolution.get("negatedAddendCellIds") or [])
            rows: list[int] = []
            negated_rows: list[int] = []
            values: list[Decimal] = []
            complete = True
            for cell_id in addend_ids:
                cell = built.by_id.get(cell_id)
                if cell is None:
                    complete = False
                    break
                rows.append(int(cell["rowIndex"]))
                if cell_id in negated:
                    negated_rows.append(int(cell["rowIndex"]))
                values.append(Decimal(cell.get("normalizedValue", 0)))
            if not complete:
                continue
            by_row.setdefault(nomination.row_index, []).append(
                Witness(
                    column,
                    tuple(rows),
                    tuple(negated_rows),
                    resolution.get("basis", "leaves"),
                    tuple(values),
                )
            )
    return by_row


def _verified(
    witnesses: list[Witness],
) -> list[tuple[tuple[tuple[int, ...], tuple[int, ...]], Witness]]:
    """Signatures the same in at least two columns whose figures are independent.

    Two columns that agree only because one is a fixed multiple of the other are
    one piece of evidence wearing two hats, exactly as in `structures`; the
    proportionality test is what keeps a percentage column from corroborating
    the amounts it measures.
    """
    groups: dict[tuple[tuple[int, ...], tuple[int, ...]], list[Witness]] = {}
    for witness in witnesses:
        groups.setdefault((witness.addend_rows, witness.negated_rows), []).append(witness)
    verified: list[tuple[tuple[tuple[int, ...], tuple[int, ...]], Witness]] = []
    for signature, members in groups.items():
        independent: list[tuple[Decimal, ...]] = []
        columns: set[int] = set()
        for witness in members:
            if witness.column in columns:
                continue
            if any(
                sums.proportional_values(list(witness.values), list(prior))
                for prior in independent
            ):
                continue
            columns.add(witness.column)
            independent.append(witness.values)
        if len(independent) >= 2:
            verified.append((signature, members[0]))
    return verified


def _propagated_run(
    built, nomination, column: int, signature, witness: Witness
) -> sums.Run | None:
    """The verified structure read in this column, or nothing when it is absent."""
    addend_rows, negated_rows = signature
    cells = [built.cell(row, column) for row in addend_rows]
    if any(cell is None or sums.value_of(cell) is None for cell in cells):
        return None
    negated = set(negated_rows)
    indices = tuple(index for index, row in enumerate(addend_rows) if row in negated)
    total = sums.value_of(nomination.cell)
    if total is None:
        return None
    return sums.Run(
        witness.basis,
        tuple(cells),
        total,
        int(nomination.cell.get("decimals", 0)),
        indices,
    )


def _from_verified(
    built, nomination, column: int, verified, miss_log: list[dict]
) -> dict | None:
    """Confirm from a verified structure, or record why it did not tie."""
    best_miss: dict | None = None
    for signature, witness in verified:
        run = _propagated_run(built, nomination, column, signature, witness)
        if run is None:
            continue
        if run.delta == 0:
            return {
                "outcome": "confirmed",
                "resolution": run.as_dict(),
                "signals": [
                    {
                        "name": "column-corroboration",
                        "evidence": (
                            "the same rows foot independently in column "
                            f"{witness.column}"
                        ),
                    }
                ],
            }
        # A proved structure that does not tie here is recorded either way: a
        # miss inside the printed-decimal interval is the shape of an unrounded
        # source figure, and a larger miss says the columns disagree about the
        # row. Neither is shown to a reviewer; both are what development reads.
        candidate = {
            "label": nomination.cell.get("rowLabel", ""),
            "value": nomination.cell.get("normalizedValue"),
            "column": column,
            "delta": str(run.delta),
            "addendRows": list(signature[0]),
            "evidenceColumn": witness.column,
            "withinRounding": sums.rounding_can_explain(run),
        }
        if best_miss is None or abs(run.delta) < abs(Decimal(best_miss["delta"])):
            best_miss = candidate
    if best_miss is not None:
        miss_log.append(best_miss)
    return None


def _trusted_addend_cells(column_results) -> set[str]:
    """Cells that participate as addends of a confirmed total in their column."""
    trusted: set[str] = set()
    for results in column_results.values():
        for _nomination, resolved in results:
            if resolved.get("outcome") != "confirmed":
                continue
            resolution = resolved.get("resolution") or {}
            trusted.update(resolution.get("addendCellIds") or [])
    return trusted


def propagate(
    built,
    column_results,
    column_state=None,
    *,
    miss_log: list[dict] | None = None,
) -> set[str]:
    """Confirm unresolved totals from structure proved in parallel columns.

    Runs to a fixed point because a newly confirmed total is itself a witness:
    confirming a subtotal can prove the structure of the total above it, and
    confirming a row in one column can prove it in the next. The loop is bounded
    by the block's row count, so a non-monotone regression cannot spin.

    Returns the ids of the cells it confirmed, so the caller can count each once
    across the block's passes.
    """
    log = miss_log if miss_log is not None else []
    confirmed: set[str] = set()
    for _ in range(max(1, built.row_count)):
        by_row = _witnesses(built, column_results)
        changed = False

        # 1. The same row, in every remaining column.
        for column, results in column_results.items():
            for position, (nomination, resolved) in enumerate(results):
                if resolved.get("outcome") != "unresolved":
                    continue
                verified = _verified(by_row.get(nomination.row_index, []))
                if not verified:
                    continue
                upgraded = _from_verified(
                    built, nomination, column, verified, log
                )
                if upgraded is not None:
                    results[position] = (nomination, upgraded)
                    confirmed.add(nomination.cell["id"])
                    changed = True

        # 2. Nested addends: a confirmed total's addend rows are known to be
        # real, so an unresolved total standing there and carrying a total's own
        # label or rule may use the corroborated floor even where this column
        # alone could not establish it.
        if not changed and column_state is not None:
            trusted = _trusted_addend_cells(column_results)
            for column, results in column_results.items():
                nominated_rows, jump = column_state.get(column, (set(), {}))
                for position, (nomination, resolved) in enumerate(results):
                    if resolved.get("outcome") != "unresolved":
                        continue
                    if nomination.cell["id"] not in trusted:
                        continue
                    if not any(
                        signal["name"] in {"label-total", "double-rule-below"}
                        for signal in nomination.signals
                    ):
                        continue
                    upgraded = sums.resolve(
                        built,
                        nomination.cell,
                        len(nomination.signals),
                        nominated_rows,
                        jump,
                        floor=2,
                    )
                    if upgraded.get("outcome") != "confirmed":
                        continue
                    results[position] = (
                        nomination,
                        {
                            **upgraded,
                            "signals": [
                                {
                                    "name": "column-corroboration",
                                    "evidence": (
                                        "the row is an addend of a total confirmed "
                                        "in this column"
                                    ),
                                }
                            ],
                        },
                    )
                    confirmed.add(nomination.cell["id"])
                    changed = True

        if not changed:
            break
    return confirmed
