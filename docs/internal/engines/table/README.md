# Table engine

> **Skeleton.** The headed sections below are the fixed shape every engine doc in
> this folder follows. Facts about where the code lives are filled in; the
> substance is not written yet. Delete this note once the doc says something.

| | |
| --- | --- |
| **Source** | `src/python/engines/table/` |
| **Contract** | `contracts/table-structure-v1.json` |
| **Entry point** | `engines/table/detector.py` |
| **Consumes** | `text-geometry-v1`, page rulings |
| **Tests** | `src/python/tests/test_table_structure.py`, `test_table_detector.py`, `test_table_pipeline.py`, `test_table_overlay.py`, `test_table_benchmark.py`; fixtures in `src/python/tests/fixtures/tables/` |

**Modules**

`candidates.py`, `detector.py`, `grid.py`, `headers.py`, `layout.py`, `redesign.py`, `refine.py`, `rulings.py`, `scoring.py`

Related single-module engines that sit beside the package rather than inside it: `engines/table_cell_engine.py`, `engines/table_date_engine.py`.

## 1. Purpose

*What this engine is for, and the one job it owns that no sibling engine does.
Two or three sentences. If a reader only reads this section, they should know
when to reach for this engine and when not to.*

## 2. Inputs and outputs

*What comes in, what goes out, and which contract governs the boundary. Name the
contract fields that matter and say what the engine guarantees about them —
ordering, completeness, coordinate space, units. Anything a downstream engine is
allowed to rely on belongs here; anything it is not allowed to rely on belongs
here too, stated as such.*

## 3. Types

*The vocabulary. Every kind of thing the engine names — the categories, the
tags, the states — with the one-line definition that distinguishes each from its
neighbours. This is the section a reader comes back to; keep the definitions
sharp enough to settle an argument about which bucket a case falls in.*

## 4. Algorithm

*How it actually works, stage by stage, in the order the code runs. Say what each
stage decides and on what evidence. Where a stage rejects a candidate, say what
would have had to be true for it to pass. Prefer the shape of the reasoning over
a line-by-line narration of the source.*

## 5. Tuning and thresholds

*Every constant that could have been a different number, where it lives, and what
moves if it changes. For each, the measurement or the case that set it — a
threshold with no recorded reason is a threshold nobody can safely touch.*

## 6. Failure modes

*What this engine gets wrong, and how it fails when it does. Distinguish the
cases it is designed to decline (and how a caller can tell) from the cases where
it produces a confident wrong answer. Record real observed failures from the
corpus, with the document that produced them.*

## 7. Tests and corpus

*What the tests cover, what the fixtures are, and the bar the engine is held to.
Note anything the corpus exercises that the unit tests do not.*

## 8. Related documents

- [`../reconcile/README.md`](../reconcile/README.md) — the main consumer of the detected grid. Its §4.1 is the reason that consumption is now corroborating rather than load-bearing: the sum tree stands on a value-alignment lattice, and reads the grid only where it is confident. A recall gain here no longer buys arithmetic recall there.
