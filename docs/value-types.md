# Value types and categories

What the detector publishes for a document, and what each thing means. This
describes `document-values-v1` and the parts of `financial-structure-v1` that resolve
against it. The rules live in `src/python/engines/values/`; this document is the
reader's view of them, not a second copy.

## 1. The four categories

Every span the recognizer touches lands in exactly one category. The category is
a property of the span, never of the document: what a span is does not change
because it was printed on an invoice rather than a 10-K.

| Category | What it is | Published in | Clickable today |
|---|---|---|---|
| **value** | a measured quantity: an amount, a proportion, a point in time | `pages[].values` | yes |
| **reference** | printed data identifying something outside the document | `pages[].references` | yes |
| **structure** | the document indexing itself | `pages[].structure` | no |
| **noise** | damage, and spans nothing could identify | `pages[].noise` | never |

**Category and clickability are separate facts.** Every span carries a required
`clickable`, and that field — not the category — decides whether the viewer
creates a click target. A consumer must read it and never re-derive it, because
the separation is the point: a kind of span can become capturable by changing
one line in the detector, with no contract change and no viewer change.

The right-hand column above is therefore today's policy, not a rule. It lives in
`categories.CLICKABLE_BY_DEFAULT`, with `CLICKABLE_KINDS` there for a single kind
that should differ from its category — deliberately empty, because nothing needs
the exception yet.

Noise is the one category where clickability is not a policy: the contract pins
it to `false`, because a click target the detector could not identify would be a
button with nothing behind it. Noise is also the category that should *shrink*
as detection improves — it is the residue after the other three are taken.

A structure span is not the same thing as the heading it sits in. The heading is
one printed thing and lives in `financial-structure-v1` with its full text and geometry;
what appears here is the integer printed inside it, which has a place on the page
of its own.

## 2. Values

Three kinds, distinguished by what the text measures.

### 2.1 `number`

A quantity. Recognized from a digit run with optional grouping commas, an
optional decimal fraction, and any of: a leading currency symbol or code, a
sign, a magnitude word, or enclosing parentheses.

| Field | Meaning |
|---|---|
| `text` | the span exactly as printed |
| `normalizedValue` | exact canonical decimal, as a string — never a binary float. Includes the magnitude multiplier when one was read |
| `currency` | ISO code, when the span carries a symbol or code, or inherits one from its page or document |
| `magnitude` | the multiplier a modifier attached to *this* number expressed |
| `confidence` | see §2.4 |

**Negatives** come from two conventions and mean the same thing: a leading `-`,
and accounting parentheses. `(1,234)` normalizes to `-1234`. This is why an
unrefused footnote marker used to publish as `-1`, and why the ordinal apparatus
in `lists.py` exists.

**Currency** is read from `$ € £ ¥ ₹ ₩` or from the codes `USD EUR GBP JPY CAD
AUD CHF CNY INR KRW`. The four codes with no symbol in that list are readable
only in their code form.

**Magnitude** words attach to the number in front of them and are folded into
`normalizedValue`, so `$1.2 billion` normalizes to `1200000000` and carries
`magnitude: 1000000000`. The recognized forms are `k`, `th`, `ths`, `thou`,
`thousand(s)`, `m`, `mm`, `mn`, `mil`, `mln`, `million(s)`, `b`, `bn`, `bln`,
`bil`, `billion(s)`, `t`, `tn`, `trn`, `tril`, `trillion(s)`.

A magnitude may also arrive on the **next line**: when a number ends its line and
the following line opens with a magnitude word, the two are joined into one
value with two `segments`. That is the wrapped-magnitude case.

### 2.2 `percent`

A proportion, recognized from the same number pattern followed by `%` or the
word `percent`. `normalizedValue` is the printed figure — `5.2%` normalizes to
`5.2`, not `0.052`.

Two deliberate behaviours:

- A magnitude word after a percentage belongs to the surrounding prose, not to
  the percentage, so it is dropped rather than multiplied in.
- `(4)%` is read as a negative four percent. The closing parenthesis sits ahead
  of the sign in that convention, which earlier cost the percent sign entirely.

### 2.3 `date`

A point in time. Seven patterns are tried in order, and the first to claim a
span wins — which is what stops the year inside a full date being published a
second time on its own.

| Printed as | `datePrecision` | `dateOrder` | `normalizedValue` |
|---|---|---|---|
| `December 31, 2025` | `day` | `mdy` | `2025-12-31` |
| `31 December 2025` | `day` | `dmy` | `2025-12-31` |
| `2025-12-31` | `day` | `ymd` | `2025-12-31` |
| `12/31/2025`, `31.12.25` | `day` | `mdy` / `dmy` | `2025-12-31` |
| `December 2025` | `month` | `mdy` | `2025-12` |
| `Q3 2025`, `FY2025 Q3` | `quarter` | `ymd` | `2025-Q3` |
| `2025`, `FY2025` | `year` | `ymd` | `2025` |

**Ambiguity is reported, not guessed.** In an all-numeric date where both leading
parts could be a month — `03/04/2025` — `dateOrder` is `ambiguous` and
`normalizedValue` is **absent**. The span is still published, because the reader
can see which it is; nothing downstream may assume a reading the detector
refused to make.

A **two-digit year** resolves to the 2000s below 70 and the 1900s otherwise.
An impossible day-in-month is rejected outright rather than published wrong.

**Wrapped dates.** A column header often prints `December 31,` on one line and
`2025` on the tightly-spaced line below, horizontally aligned. Those are paired
into one day-precision value with two `segments`, so clicking either half links
the whole date.

### 2.4 Fields every value carries

| Field | Meaning |
|---|---|
| `id` | content-addressed span identity (§5) |
| `bounds` | the box enclosing the printing glyphs, normalized 0–1, top-left origin |
| `clickable` | whether the viewer makes a click target of it (§1) |
| `confidence` | how ornate the text is, **not** how sure we are it is a value |
| `segments` | present only when one logical value occupies more than one physical region — a wrapped date, a wrapped magnitude. `bounds` remains the first segment; clicking any segment creates the whole value |

The confidence ladder as it stands: `0.99` with a percent sign or currency,
`0.94` with comma grouping, `0.82` with a decimal point, `0.62` for a bare
integer; dates are `0.98` at day precision and `0.92` otherwise. This measures
*shape*. Rebasing it on evidence — how many independent supports hold — is
phase 3 of `docs/internal/engines/values/value-precision.md`, and the number changes meaning when
that lands.

## 3. References

Printed data that names something outside the document rather than measuring it.
A reference carries `id`, `kind`, `text`, `bounds` and `clickable`, and nothing
else: no normalized value, no confidence, because there is no reading to be sure
about.

| Kind | What it names | Reached by |
|---|---|---|
| `identifier` | an invoice, PO, account, check, form or document number | a token shape a value never takes, or a cue word before it |
| `phone` | a telephone or fax number | an unambiguous printed phone shape, or a `phone` / `telephone` / `tel` / `fax` cue |
| `postal` | a postal or ZIP code | a `zip` / `postal code` cue |
| `tax-id` | a tax or employer identification number | an `employer identification` cue |
| `security-id` | an ISIN, CUSIP, SEDOL or ticker | an `isin` / `cusip` / `sedol` / `ticker` / `symbol` cue |
| `note` | a citation naming a financial-statement note | the financial tier, resolved to the note catalogue |
| `item` | a citation naming a filing item | the financial tier, resolved to the item catalogue |

Two routes reach the first five, and they differ in what the recognizer saw.

**By token shape.** A value has to claim whole tokens, so a token bound together
by `-`, `/` or `:` between alphanumerics is never arithmetic: `10-K`,
`001-36743`, `123-456-7890`, `3:13`, `ASU 2024-03`. So is anything carrying `#`
or `№`, which names a thing rather than counting one, and anything mixing
letters and digits, like `BPXINV-00550`. These never parse as values at all.

**By cue.** A well-formed number preceded by one of the cue words above is a
reference to what the cue names. The cue is matched against the text *before*
the span, within a sentence, so a label cannot condemn a whole line: in
`Total 1,234 CUSIP 037833100`, the total stays a value.

The number mark is a cue as well as a shape. `#7` is one token and is refused on
shape; `No. 7`, `Nos. 3` and `# 7` are two, so the mark has to be read as the cue
it is -- and it answers only for the number standing immediately after it, which
is what leaves the `4` in `Nos. 3 and 4` a value.

A citation is one printed span resolved possibly twice — `Notes 3.1 and IV`
produces one reference and two catalogue entries, both pointing at it by
`spanId`.

## 4. Structure

The apparatus a document indexes itself with. Printed text with meaning, which
is what separates it from noise, and measuring nothing, which is what separates
it from a value. Six kinds.

| Kind | What it is |
|---|---|
| `note-header` | the number printed inside a financial-statement note heading |
| `item-header` | the number printed inside a filing-item heading |
| `item-toc-entry` | a number printed in a contents row — including the page number the row points at |
| `list-marker` | the ordinal opening a list item: a number *of* the list, not *in* it |
| `footnote-marker` | the ordinal opening a footnote below the text it explains |
| `footnote-reference` | an indicator pointing at a footnote printed nearby |

The last three are worth understanding as a group, because no token can be
assigned on its own. `(1)` under a compensation table and `(1)` in a tax schedule
are printed identically, and one of them is a real loss of one. What separates
them is the **chain**: ordinals that step through a list in order, each leading
prose, holding one left edge across pages. `lists.py` documents that machinery;
the categories here only record its verdict.

The first three arrive differently. The financial tier finds a heading or a
contents row, fences the range it occupies, and names the role; anything
value-shaped inside that range is published here rather than read as a figure.
The catalogue entry the span belongs to is joined by enclosure — the occurrence
in `financial-structure-v1` whose bounds contain it on the same page.

## 5. Noise

What is left once the other three are taken: damage, spans the detector refused
without being able to say what they were, and figures with nothing to say they
are figures. Five reasons.

| Reason | What it refuses |
|---|---|
| `partial-token` | a token that held a figure but did not parse in full. `1,2 34` publishes `34` and reports `1,2` — this is what makes OCR fragmentation visible instead of silently publishing a damaged figure |
| `page-furniture` | a line repeating across pages as a running header, footer or page number |
| `superscript` | a glyph set below 0.72 of the ordinary line height around it, so a mark rather than a figure |
| `citation-year` | a year reached through a citation — `the Act of 1934` names a law, not this document's structure |
| `unsupported` | a bare number standing in a sentence with nothing beside it to say it measures anything — `Rule 405`, `iOS 26`, `See note 2 to the financial statements; table 3` |

`unsupported` is the one reason that names an absence rather than a thing, and
so the one most likely to be wrong. Every other rule refuses a span for what it
is; this one refuses a figure the recognizer read in full, because nothing
spoke for it. Four things speak for a number and any one is enough: **how it is
written** (a currency, a percentage, a magnitude, a comma group or a decimal —
including a magnitude word that wrapped onto the next line), **where it sits**
(its own island of whitespace, which is a cell in a column whether or not table
detection resolved the table around it), **what precedes it** (period language —
`due`, `ended`, `maturing`), and failing all three, **the line it shares**: a
line of fewer than six words is a row, not a sentence, and its figures are left
alone.

Two deliberate limits. A bare *year* is out of scope — `During 2025, the Company
repurchased ...` is a period anchor printed identically to a statute year, and
separating them needs the section classifier rather than another lexical rule.
And table membership is not consulted: on the corpus CAFR only 314 of 772 values
sit inside a detected table, so requiring one would refuse the face of the
statements. The island test reaches the same figures without the dependency.

`page-furniture` is found by identity plus role rather than by position. A
running footer is found by its skeleton (the line with digit runs masked)
repeating across pages, or, for a bare page number, by the constant offset it
keeps from the page index. It sits here rather than in structure because it is
chrome the reader never cites, but it is the category boundary most open to
revisiting.

## 6. Identity

A span's id is a hash over its category, page index, normalized text and bounds
rounded to three decimals, prefixed by category:

```
val-3eebd72172e76020   ref-174abef2b5a3634c   str-31deb41d2a29791f   noi-c18c0788b2c1
```

Two consequences follow, and both are load-bearing.

**Review state survives a detector upgrade.** Positional ids renumber every span
after an insertion, so bumping the detector version would orphan anything a
workbook had attached to them. A hashed id does not move because something else
was published before it.

**Two engines agree without speaking.** The financial tier resolves a citation
before the value tier publishes it. Because the id is derived rather than
assigned, both arrive at the same one independently, and `spanId` joins the two
artifacts with no lookup table and no ordering guarantee.

The honest limit: this is stable across *detector* versions, not across a
re-OCR. Rebuilt geometry moves bounds, and a moved span is a new id.

## 7. Context

Each page and the document carry a `context`, holding what the figures on them
are denominated in.

| Field | Meaning |
|---|---|
| `currency` | the currency most often marked on the page, or across the document |
| `scale` | `1`, `1000`, `1000000` or `1000000000`, read from a caption like "in millions" |

A value's own `currency` wins over the page's; a bare number inherits the page's,
then the document's. `scale` is **not** folded into `normalizedValue` — it says
what the page's captions claim, while `magnitude` says what a modifier attached
to one number said. Reading a column's units from its own header, rather than
from a page-wide guess, is phase 3 work and is not done yet.

Note what this does not yet parse: "in thousands, **except per share data**".
The exception clause is invisible to the current inference, so a per-share row
under a thousands caption is a known gap rather than a bug to file.

## 8. Where each thing is defined

| Concern | Home |
|---|---|
| Contract shapes and closed enums | `contracts/document-values-v1.json`, `contracts/financial-structure-v1.json` |
| Category, kind and reason vocabulary, and the clickability policy | `src/python/engines/values/categories.py` |
| What a piece of text looks like | `src/python/engines/values/spans.py` |
| Which category a recognized span belongs to | `src/python/engines/values/evidence.py` |
| Document-level facts: furniture, glyph heights | `src/python/engines/values/profile.py` |
| Ordinal apparatus | `src/python/engines/values/lists.py` |
| Span identity | `src/python/engines/values/ids.py` |
| Note and item catalogues | `src/python/engines/financial/` |
| Reader-facing wording for every kind and reason | `src/web/packages/shared/src/span-describe.ts` |

The enums are closed on both sides, and a test reads the contract rather than
restating it: a reason added to the detector but not to the contract fails the
build, which is how a whole overlay layer once rendered nothing in silence.
