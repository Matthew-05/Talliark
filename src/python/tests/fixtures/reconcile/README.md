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

**Three goldens carry proposed detector-11 updates and are marked unapproved
until a person reviews them.** `cafr1112bfs.json` corrects six labels to the
page's own words (`Federal`, `Interest`, `Other`, `Principal`, `Other
purposes`), where the golden had carried an enriched description the engine
never prints; `boeing-2023-annual-report.json` corrects one label and adds one
real cross-foot the old engine could not reach; `roycarver-2014.json` corrects
one label. Each change was read against the rendered page before it was
written, and approving the three restores the zero-false-tie bar at the higher
detector-11 recall. The other five remain approved.

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

The bar itself: zero false ties anywhere in
the document is the hard number; every labelled total in the primary statements
nominated and confirmed is the soft one. Recall may lag — a single confidently
wrong finding costs more than ten missed totals.
