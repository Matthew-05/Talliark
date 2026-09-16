# Reconcile goldens

One golden per corpus document, listing every total the scan nominated and what
it made of each. The source reports are local, gitignored development inputs;
run `scripts/setup-development-corpus.ps1` to fetch them, and see the corpus
README for what each one exercises.

## Current goldens

All eight corpus documents carry a **full-document** golden:
`apple-10k.json`, `quest-10k.json`, `disney-10k.json`, `amazon-ar.json`,
`boeing-2023-annual-report.json`, `cafr1112bfs.json`, `roycarver-2014.json`, and
`sample-financial-statements-1.json`. Each lists every total on every page that
nominated one, with the outcome and, for a confirmation, the addends the printed
statement foots on. Every entry was read against the rendered page; a confirmed
entry whose addends are not the statement's own rows was corrected or
downgraded. The goldens score **zero false ties** across the corpus. Their
`confirmed` entries the engine does not confirm are its recall worklist.

**The detector-11 updates are approved.** `cafr1112bfs.json` records six labels
in the page's own words (`Federal`, `Interest`, `Other`, `Principal`, `Other
purposes`) where it had carried an enriched description the engine never
prints; `boeing-2023-annual-report.json` records the wrapped `net of tax` label
and the p109 cross-foot the old engine could not reach; `roycarver-2014.json`
records `Net Assets, unrestricted`. Each was read against the rendered page,
and the corpus now scores zero false ties across all eight documents at the
higher detector-11 recall.

**The detector-12 updates are approved too.** `amazon-ar.json` records the
maturity schedule's year totals under the page's own column headers (`2018`
through `2022`, `Total`), the two cross-foots whose labels wrap onto a second
line, and the prior-year fair-value total the old engine could not reach. All
were read against the rendered page; the page's header row prints exactly those
years and that Total.

A golden is an **independent oracle or it is nothing**. `scripts/score_reconcile.py`
can write candidates, but it writes them with `"approved": false`, and it refuses
to score against a golden that a person has not marked approved. A golden derived
from detector output and blessed by the same detector measures precisely nothing.

```powershell
# Write candidates for review
py scripts/score_reconcile.py "sample-document-corpus/financial-statements/apple 10k.pdf" `
  --propose-golden src/python/tests/fixtures/reconcile/apple-10k.json

# Score against the approved golden — non-zero exit on a false tie
py scripts/score_reconcile.py "sample-document-corpus/financial-statements/apple 10k.pdf" `
  --golden src/python/tests/fixtures/reconcile/apple-10k.json
```

## Approving one

Each entry is keyed by page, column header, row label and value, so it can be
checked against the printed page without opening the model.

Confirmed entries also list `addends` and `negatedAddends`. The latter are the
printed figures whose sign the run reverses, so approving a subtraction checks
the equation rather than merely approving that the same rows were selected.

1. Read every entry against the page and correct any `outcome` the scan got
   wrong.
2. List in `pages` the page numbers you checked **in full**.
3. Set `"approved": true`.

Step 2 is what gives the hard bar its teeth: anything the scan confirms on a
listed page that is absent from `totals` scores as a false tie. Without it the
scorer can only check the entries it was given, and a total invented on a page
nobody covered would pass unnoticed.

## Reviewing with the tool

`scripts/review_reconcile_golden.py` renders a **review PDF** — every page that
carries a golden entry or an engine result, with the total and its addends
outlined and captioned by what the comparison found — and prints the same facts
as a terminal sheet and JSON. Green agrees, blue is a golden confirmation the
engine did not reach, red is an engine assertion the golden does not know
about, orange is a confirmation reached through different addends.

```powershell
# Render the sheet and report, change nothing
py scripts/review_reconcile_golden.py "sample-document-corpus/financial-statements/apple 10k.pdf" `
  --golden src/python/tests/fixtures/reconcile/apple-10k.json

# Record the pages checked in full, add the engine confirmations reviewed on
# them, then approve — refused while false ties or wrong addend sets remain
py scripts/review_reconcile_golden.py "…/apple 10k.pdf" `
  --golden src/python/tests/fixtures/reconcile/apple-10k.json `
  --pages 22,25,26-28 --accept-missing --approve

# Replace one entry with the engine's working, or drop one that is wrong
py scripts/review_reconcile_golden.py "…/apple 10k.pdf" `
  --golden src/python/tests/fixtures/reconcile/apple-10k.json `
  --accept-engine "109|(loss)/income|5474" --drop "85|BBB|13"
```

`--accept-engine` and `--drop` take `PAGE|LABEL|VALUE`. `--dry-run` prints the
edits without writing, `--no-render` skips the PDF, and `--model`/`--write-model`
cache the scan so repeated review runs do not re-read the document. The approval
guard judges the golden as it will be written, so `--accept-missing` can resolve
the very false ties a first review run reports.

The bar itself: zero false ties anywhere in
the document is the hard number; every labelled total in the primary statements
nominated and confirmed is the soft one. Recall may lag — a single confidently
wrong finding costs more than ten missed totals.
