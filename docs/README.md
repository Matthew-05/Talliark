# Talliark docs

| Folder | Audience | Holds |
| --- | --- | --- |
| `docs/` (root) | Anyone reading the product | Reader-facing reference. Today: `value-types.md`. |
| `docs/internal/` | Developers and agents working on the code | How the system works: `architecture.md`, and `engines/<name>/`, one folder per algorithm package. Reviewed with the code it describes. |

Everything here describes code that exists. A document in this tree stands on
its own and on its siblings — it never defers to a document a reader cannot
open.

## Decisions

There is no decisions folder and no ADR set. A decision that is implemented is
written into the affected engine's `README.md` as description of what the code
does: the outcome, and the measurements that justify it. The options weighed
along the way are not part of that record. A decision that is not implemented is
not documented here at all — the engine docs describe built behaviour only, so a
reader can trust that everything in them is true of the code today.

## Engines

Each of `docs/internal/engines/{values,table,financial,reconcile}/` holds a
`README.md` in one fixed eight-section shape: purpose, inputs and outputs, types,
algorithm, tuning and thresholds, failure modes, tests, related documents. When
an engine's behaviour, thresholds or types change, its `README.md` changes in the
same commit. A threshold with no recorded reason is a threshold nobody can safely
touch.

See [`internal/engines/README.md`](internal/engines/README.md) for the index and
for what each engine decides.

## Layout

```
docs/
  value-types.md              reader-facing reference
  internal/
    architecture.md           scope tiers, the two runtimes, what P1 landed
    engines/
      README.md               index
      values/  table/  financial/  reconcile/
```
