# Values engine

> **Skeleton.** The headed sections below are the fixed shape every engine doc in
> this folder follows. Facts about where the code lives are filled in; the
> substance is not written yet. Delete this note once the doc says something.

| | |
| --- | --- |
| **Source** | `src/python/engines/values/` |
| **Config** | `contracts/value-recognition-config-v1.json` — the vocabulary, clickability policy, recognition tables and thresholds, shared with the frontend recognizer |
| **Contract** | `contracts/document-values-v1.json` |
| **Entry point** | `detect_values()` — `engines/values/detector.py`, versioned by `DETECTOR_VERSION` |
| **Frontend mirror** | `src/web/packages/shared/src/value-recognition/` — the same decision, in TypeScript, for documents never OCR'd |
| **Consumes** | `text-geometry-v1` |
| **Tests** | `src/python/tests/test_values.py`; fixtures in `src/python/tests/fixtures/values/` |

**Modules**

`categories.py`, `claims.py`, `config.py`, `context.py`, `detector.py`,
`evidence.py`, `ids.py`, `lines.py`, `lists.py`, `profile.py`, `spans.py`

The values engine runs on every document. A TypeScript mirror of it,
`src/web/packages/shared/src/value-recognition/`, runs the same decision in the
browser when a document has not been OCR'd; once OCR has run, its values are the
only source the viewer uses. Both read their shared vocabulary, policy, tables
and thresholds from `contracts/value-recognition-config-v1.json`. The port is
module-for-module so the two can be diffed side by side, and
`src/python/tests/fixtures/values/span-oracle.json` is scored by both test
suites, so drift fails a test rather than diverging silently.

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

The constants that could have been different numbers live in
`contracts/value-recognition-config-v1.json`, not in the modules. `config.py`
loads them once and the modules read them, so the Python engine and the
TypeScript mirror cannot drift on a threshold:

- **`confidence`** — the per-shape confidence a value is published with (a date's
  day precision, a comma-grouped number, a decimal fraction, a bare integer).
- **`evidence`** — `proseWords`, the line length above which a bare integer in
  prose is refused as `unsupported`.
- **`context`** — the scale phrases ("in millions") and the scale each expresses.
- **`profile`** — `superscriptRatio`, `representativeGlyphs`, `sequenceMinimum`,
  `yearRange`, `placeTolerance`, `repeatShare`, `repeatMinimum`, and the function
  words a period caption is allowed.
- **`lists`** — `minChain`, `columnTolerance`, `inlineReach`, `referenceReach`,
  `minIndicators`, `indicatorRatio`, `representativeLines`, `minProseWords`,
  `bandReach`.

Each value is the one the module carried before the config was extracted, so the
move changed no behaviour. The measurement or case behind each is recorded beside
its use in the module that reads it; the regexes that consume the shared tables
(month names, currency codes, magnitude words) stay in `spans.py` and the
TypeScript `spans.ts`, because Python and JavaScript regex dialects differ.

## 6. Failure modes

*What this engine gets wrong, and how it fails when it does. Distinguish the
cases it is designed to decline (and how a caller can tell) from the cases where
it produces a confident wrong answer. Record real observed failures from the
corpus, with the document that produced them.*

## 7. Tests and corpus

The Python engine and the TypeScript recognizer are held to the same two
fixtures, so a change to one that the other does not make fails a test on one
side:

- **`src/python/tests/fixtures/values/span-oracle.json`** — the recognizer stage:
  a line of text and the exact spans it must produce. `test_values.py` and the
  shared package's `value-recognition-spans.test.ts` both run it.
- **`src/python/tests/fixtures/values/detector-cases.json`** — the whole model:
  a geometry and the `document-values-v1` model the engine publishes for it
  (bounds, confidence, currency, magnitude, dates, references, structure and
  noise). `test_values.py` and `value-recognition-corpus.test.ts` both run it,
  comparing through a canonical form (`documents.canonical`, mirrored in the
  TypeScript test) that drops derived ids and the detector version. Rebuild it
  with `py scripts/make_value_fixtures.py`; the expected models are generated
  from this engine and hand-reviewed, not blessed automatically.

`py scripts/score_values.py` runs both gates with no arguments, then can score a
real PDF and report what was published, suppressed and why.

## 8. Related documents

- [`docs/value-types.md`](../../../value-types.md) — the reader-facing view of what the detector publishes and what each category means. That document is the public description; this one is the implementation record. Keep them from disagreeing.
- [`value-precision.md`](value-precision.md) — the precision work: phases, measurements and what each detector version changed.
