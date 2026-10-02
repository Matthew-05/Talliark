/**
 * Decide which of the four categories a recognized span belongs to, mirroring
 * Python `engines/values/evidence.py`. Every rule either refuses a span or
 * redirects it; nothing promotes.
 */
import {
  CITATION_YEAR,
  IDENTIFIER,
  NOISE,
  PHONE,
  REFERENCE,
  SUPERSCRIPT,
  UNSUPPORTED,
  VALUE,
} from "./categories.ts";
import {
  CITATION_CONTEXT_FRAGMENT,
  IDENTIFIER_CUES as CUE_FRAGMENTS,
  NUMBER_MARK_FRAGMENT,
  PERIOD_CONTEXT_FRAGMENT,
  PROSE_WORDS,
} from "./config.ts";
import type { DocumentProfile } from "./profile.ts";
import type { RecognizedSpan } from "./spans.ts";

// A verdict is the category and, with it, the reference kind or the noise reason.
export type Verdict = [string, string];

export const KEEP: Verdict = [VALUE, ""];

// Only the two phone shapes that cannot be mistaken for data.
const PHONE_RE = /(?:\+\d{1,3}[\s.\-]\d[\d\s.\-]{5,}\d|\(\d{3}\)\s?\d{3}[\s.\-]\d{4})/g;

interface IdentifierCue {
  kind: string;
  re: RegExp;
}

const IDENTIFIER_CUES: IdentifierCue[] = [
  ...CUE_FRAGMENTS.map(({ kind, fragment }) => ({
    kind,
    re: new RegExp(`\\b(?:${fragment})\\b[^.]{0,30}$`, "i"),
  })),
  { kind: IDENTIFIER, re: new RegExp(`(?:\\b${NUMBER_MARK_FRAGMENT})\\s*$`, "i") },
];

// What a figure written like a figure carries on its face.
const INTRINSIC_MARK_RE = /[,%]|\.\d/;

// Language that puts a number in a period rather than in a sentence.
const PERIOD_CONTEXT_RE = new RegExp(`\\b(?:${PERIOD_CONTEXT_FRAGMENT})\\b[^.]{0,20}$`, "i");

const WORD_RE = /[^\W\d_]{2,}/g;

// A year reached through a citation is naming a law, not a period.
const CITATION_CONTEXT_RE = new RegExp(`\\b(?:${CITATION_CONTEXT_FRAGMENT})\\b[^.]{0,25}$`, "i");

function phoneOverlaps(line: string, start: number, end: number): boolean {
  let match: RegExpExecArray | null;
  PHONE_RE.lastIndex = 0;
  while ((match = PHONE_RE.exec(line)) !== null) {
    if (start < match.index + match[0].length && end > match.index) return true;
  }
  return false;
}

/** The most specific thing a reference-shaped token could be identifying. */
export function referenceKind(line: string, start: number, end: number): string {
  if (phoneOverlaps(line, start, end)) return PHONE;
  const before = line.slice(0, start);
  for (const { kind, re } of IDENTIFIER_CUES) {
    if (re.test(before)) return kind;
  }
  return IDENTIFIER;
}

function unsupported(
  span: RecognizedSpan,
  line: string,
  start: number,
  isolated: boolean,
  modifierFollows: boolean,
): boolean {
  if (span.kind !== "number") return false;
  if (span.currency || span.magnitude || INTRINSIC_MARK_RE.test(span.text)) return false;
  if (modifierFollows) return false;
  if (isolated) return false;
  if (PERIOD_CONTEXT_RE.test(line.slice(0, start))) return false;
  return (line.match(WORD_RE) ?? []).length >= PROSE_WORDS;
}

/**
 * What this span is, and why. `start` and `end` locate the span within `line`.
 * `isolated` is whether the span sits in its own island of whitespace, measured
 * by the caller, as `glyphHeight` is.
 */
export function classify(
  span: RecognizedSpan,
  line: string,
  start: number,
  end: number,
  pageIndex: number,
  top: number,
  glyphHeight: number,
  isolated: boolean,
  modifierFollows: boolean,
  profile: DocumentProfile,
): Verdict {
  const furniture = profile.furnitureReason(line, pageIndex, top);
  if (furniture) return [NOISE, furniture];
  if (phoneOverlaps(line, start, end)) return [REFERENCE, PHONE];
  const before = line.slice(0, start);
  for (const { kind, re } of IDENTIFIER_CUES) {
    if (re.test(before)) return [REFERENCE, kind];
  }
  if (profile.isSuperscript(pageIndex, glyphHeight)) return [NOISE, SUPERSCRIPT];
  if (span.kind === "date" && span.datePrecision === "year" && CITATION_CONTEXT_RE.test(before)) {
    return [NOISE, CITATION_YEAR];
  }
  if (unsupported(span, line, start, isolated, modifierFollows)) {
    return [NOISE, UNSUPPORTED];
  }
  return KEEP;
}
